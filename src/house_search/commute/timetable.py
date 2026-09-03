"""NAVITIME から採った実ダイヤをDBへ落とす層（Phase 5D）。

``navitime`` は取得と解析（ネットワークとHTML）、この層がDBとの橋渡しを持つ。
``resolve`` が駅の同定を担うのと同じ分け方。

書き込む先は3つ。

- ``t_navitime_routes`` … 経路の**原文**。パーサを直したら再取得せず作り直せる
- ``t_rail_segments`` … 乗車区間（駅間）の実所要時間。目的地を変えたときに
  取得をやり直さずダイクストラを回し直すための素材
- ``t_station_commutes`` … 採点が読む所要時間キャッシュ（``source='navitime'``）

⚠ **回帰式（``rail_graph``）で実ダイヤの行を上書きしない。** ``resolve-commutes`` は
掲載が挙げる駅を全部まとめて書き直すので、素朴に流すと苦労して採った実測値が
見積もりへ戻る。``origins_with_source`` で除外する。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, text

from house_search.commute.matcher import StationIndex, candidate_variants
from house_search.commute.navitime import Route, RouteSearch, station_query_name
from house_search.commute.normalize import normalize_key

SOURCE_NAVITIME = "navitime"


@dataclass(frozen=True)
class OriginStation:
    """取得対象の出発駅。"""

    station_g_cd: int
    station_name: str
    prefecture: str | None
    alias_names: tuple[str, ...] = ()

    @property
    def match_names(self) -> tuple[str, ...]:
        """NAVITIME の応答と照合してよい表記の集合。

        ⚠ **代表名だけで照合しない。** 駅グループは同一駅の別表記を束ねており
        （``町屋`` / ``町屋駅前``、``本八幡`` / ``京成八幡``）、NAVITIME が
        別表記を返した時点で同じ駅を取りこぼす。
        """
        return (self.station_name, *self.alias_names)

    @property
    def query_name(self) -> str:
        """NAVITIME へ渡す検索語。

        ⚠ **都道府県を必ず添える。** 添えないと同名異駅が黙って別の駅として
        処理され、HTTP 200 で普通の結果が返る（``大久保`` → ``大久保（東京都）``）。
        """
        return station_query_name(self.station_name, self.prefecture)


@dataclass(frozen=True)
class SegmentObservation:
    """乗車区間1本の観測。"""

    from_station_g_cd: int
    to_station_g_cd: int
    line_name: str
    minutes: int
    is_walk: bool


_PREFECTURE_OF_STATION = """
    SELECT DISTINCT ON (s.station_g_cd)
           s.station_g_cd,
           s.station_name,
           c.prefecture,
           (SELECT array_agg(DISTINCT a.station_name)
              FROM m_stations a
             WHERE a.station_g_cd = s.station_g_cd) AS alias_names
      FROM m_stations s
      LEFT JOIN LATERAL (
           SELECT prefecture
             FROM m_cities
            WHERE jis_code IS NOT NULL
              AND CAST(LEFT(jis_code, 2) AS INTEGER) = s.pref_cd
            LIMIT 1
      ) c ON TRUE
     WHERE s.station_g_cd = ANY(:groups)
     ORDER BY s.station_g_cd, s.station_cd
"""


def origin_stations(conn: Connection, groups: Sequence[int]) -> tuple[OriginStation, ...]:
    """駅グループコードから、検索に使う駅名と都道府県を引く。

    グループには路線ごとの行が並ぶので ``station_cd`` の小さいものを代表にする。
    ⚠ **同じグループでも ``station_name`` が同じとは限らない**（``町屋`` / ``町屋駅前``）。
    代表は検索語に使い、照合には ``alias_names`` を含めた全表記を使う。
    """
    if not groups:
        return ()
    rows = conn.execute(text(_PREFECTURE_OF_STATION), {"groups": list(groups)}).all()
    return tuple(
        OriginStation(
            station_g_cd=int(row[0]),
            station_name=row[1],
            prefecture=row[2],
            alias_names=tuple(row[3] or ()),
        )
        for row in rows
    )


def fetched_origins(
    conn: Connection,
    *,
    destination_g_cd: int,
    depart_on: dt.date,
    depart_at: dt.time,
) -> frozenset[int]:
    """その条件で既に取得済みの出発駅。再開したときに二度取りしないための鍵。"""
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT origin_station_g_cd
              FROM t_navitime_routes
             WHERE destination_station_g_cd = :destination
               AND depart_on = :depart_on
               AND depart_at = :depart_at
            """
        ),
        {"destination": destination_g_cd, "depart_on": depart_on, "depart_at": depart_at},
    ).scalars()
    return frozenset(int(code) for code in rows)


def origins_with_source(conn: Connection, *, destination_g_cd: int, source: str) -> frozenset[int]:
    """指定した算出元でキャッシュ済みの出発駅。

    ``resolve-commutes``（回帰式）が実ダイヤの行を踏み潰さないために使う。
    """
    rows = conn.execute(
        text(
            """
            SELECT origin_station_g_cd
              FROM t_station_commutes
             WHERE destination_station_g_cd = :destination AND source = :source
            """
        ),
        {"destination": destination_g_cd, "source": source},
    ).scalars()
    return frozenset(int(code) for code in rows)


_UPSERT_ROUTE = text(
    """
    INSERT INTO t_navitime_routes (
        origin_station_g_cd, destination_station_g_cd, depart_on, depart_at, rank,
        total_minutes, transfers, distance_km, fare_yen,
        route_depart_at, route_arrive_at,
        origin_label, destination_label, origin_node_code, destination_node_code,
        route_text, fetched_at, created_at, updated_at
    )
    VALUES (
        :origin, :destination, :depart_on, :depart_at, :rank,
        :total_minutes, :transfers, :distance_km, :fare_yen,
        :route_depart_at, :route_arrive_at,
        :origin_label, :destination_label, :origin_node_code, :destination_node_code,
        :route_text, :fetched_at, now(), now()
    )
    ON CONFLICT (origin_station_g_cd, destination_station_g_cd, depart_on, depart_at, rank)
    DO UPDATE SET
        total_minutes         = EXCLUDED.total_minutes,
        transfers             = EXCLUDED.transfers,
        distance_km           = EXCLUDED.distance_km,
        fare_yen              = EXCLUDED.fare_yen,
        route_depart_at       = EXCLUDED.route_depart_at,
        route_arrive_at       = EXCLUDED.route_arrive_at,
        origin_label          = EXCLUDED.origin_label,
        destination_label     = EXCLUDED.destination_label,
        origin_node_code      = EXCLUDED.origin_node_code,
        destination_node_code = EXCLUDED.destination_node_code,
        route_text            = EXCLUDED.route_text,
        fetched_at            = EXCLUDED.fetched_at,
        updated_at            = now()
    """
)


def save_routes(
    conn: Connection,
    *,
    origin_g_cd: int,
    destination_g_cd: int,
    depart_on: dt.date,
    depart_at: dt.time,
    search: RouteSearch,
    fetched_at: dt.datetime,
) -> int:
    """経路候補をまとめて保存する（原文つき）。"""
    if not search.routes:
        return 0
    conn.execute(
        _UPSERT_ROUTE,
        [
            {
                "origin": origin_g_cd,
                "destination": destination_g_cd,
                "depart_on": depart_on,
                "depart_at": depart_at,
                "rank": route.rank,
                "total_minutes": route.total_minutes,
                "transfers": route.transfers,
                "distance_km": route.distance_km,
                "fare_yen": route.fare_yen,
                "route_depart_at": route.depart_at,
                "route_arrive_at": route.arrive_at,
                "origin_label": search.origin_label[:100],
                "destination_label": search.destination_label[:100],
                "origin_node_code": search.origin_code,
                "destination_node_code": search.destination_code,
                "route_text": route.raw_text,
                "fetched_at": fetched_at,
            }
            for route in search.routes
        ],
    )
    return len(search.routes)


StationResolver = Callable[[str], int | None]


def build_station_resolver(conn: Connection, prefecture_codes: Sequence[int]) -> StationResolver:
    """経路に出てくる駅名を駅グループコードへ直す関数を作る。

    ⚠ **一意に決まらない名前は None を返す。** 適当に1つ選ぶと、辺の重みが
    別の路線のものになっても誰も気づけない（同名異駅は実在する）。
    """
    if not prefecture_codes:
        index = StationIndex.build(())
    else:
        rows = conn.execute(
            text(
                """
                SELECT station_name_key, station_g_cd, pref_cd
                  FROM m_stations
                 WHERE pref_cd = ANY(:prefs)
                """
            ),
            {"prefs": list(prefecture_codes)},
        ).all()
        index = StationIndex.build((row[0], int(row[1]), int(row[2])) for row in rows)

    def resolve(name: str) -> int | None:
        for variant in candidate_variants(name):
            groups = index.lookup(normalize_key(variant), None)
            if len(groups) == 1:
                return next(iter(groups))
        return None

    return resolve


def harvest_segments(
    route: Route, resolve: StationResolver
) -> tuple[tuple[SegmentObservation, ...], int]:
    """経路から乗車区間を採る。``(採れた区間, 駅名を解決できず捨てた数)``。

    ⚠ **区間の分に待ち時間は入っていない**（発→着はひと続きの乗車のため）。
    足し合わせても二重計上にならない代わりに、乗換の待ちは別に足す必要がある。
    """
    found: list[SegmentObservation] = []
    dropped = 0
    for leg in route.legs:
        from_code, to_code = resolve(leg.from_name), resolve(leg.to_name)
        if from_code is None or to_code is None or from_code == to_code:
            dropped += 1
            continue
        found.append(
            SegmentObservation(
                from_station_g_cd=from_code,
                to_station_g_cd=to_code,
                line_name=leg.line_name[:100],
                minutes=leg.minutes,
                is_walk=leg.is_walk,
            )
        )
    return tuple(found), dropped


_UPSERT_SEGMENT = text(
    """
    INSERT INTO t_rail_segments (
        from_station_g_cd, to_station_g_cd, line_name,
        ride_minutes, ride_minutes_max, samples, is_walk, source, observed_at,
        created_at, updated_at
    )
    VALUES (
        :from_cd, :to_cd, :line_name,
        :minutes, :minutes, 1, :is_walk, :source, :observed_at, now(), now()
    )
    ON CONFLICT (from_station_g_cd, to_station_g_cd, line_name) DO UPDATE SET
        ride_minutes     = LEAST(t_rail_segments.ride_minutes, EXCLUDED.ride_minutes),
        ride_minutes_max = GREATEST(
            t_rail_segments.ride_minutes_max, EXCLUDED.ride_minutes_max
        ),
        samples          = t_rail_segments.samples + 1,
        observed_at      = EXCLUDED.observed_at,
        updated_at       = now()
    """
)


def save_segments(
    conn: Connection,
    observations: Iterable[SegmentObservation],
    *,
    observed_at: dt.datetime,
    source: str = SOURCE_NAVITIME,
) -> int:
    """乗車区間を upsert する。同じ区間は最小・最大・観測回数へ畳む。

    ⚠ **1回の呼び出しに同じ区間を2つ渡さない。** PostgreSQL の ``ON CONFLICT`` は
    同一コマンド内で同じ行を2度更新できず ``cardinality violation`` になる。
    呼び出し前に区間キーで畳んでおく。
    """
    rows = list(observations)
    if not rows:
        return 0
    conn.execute(
        _UPSERT_SEGMENT,
        [
            {
                "from_cd": row.from_station_g_cd,
                "to_cd": row.to_station_g_cd,
                "line_name": row.line_name,
                "minutes": row.minutes,
                "is_walk": row.is_walk,
                "source": source,
                "observed_at": observed_at,
            }
            for row in rows
        ],
    )
    return len(rows)


def merge_observations(
    observations: Iterable[SegmentObservation],
) -> tuple[SegmentObservation, ...]:
    """同じ区間の観測を1件に畳む（最小の分を残す）。

    ``save_segments`` の ``ON CONFLICT`` が同一コマンド内の重複で落ちるのを防ぐ。
    """
    best: dict[tuple[int, int, str], SegmentObservation] = {}
    for row in observations:
        key = (row.from_station_g_cd, row.to_station_g_cd, row.line_name)
        current = best.get(key)
        if current is None or row.minutes < current.minutes:
            best[key] = row
    return tuple(best[key] for key in sorted(best))


def segment_stats(conn: Connection) -> tuple[int, int, int]:
    """``(区間数, 乗車区間, 徒歩区間)`` を数える。"""
    row = conn.execute(
        text(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE NOT is_walk),
                   count(*) FILTER (WHERE is_walk)
              FROM t_rail_segments
            """
        )
    ).first()
    return (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)
