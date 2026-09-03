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
from house_search.commute.navitime import (
    Route,
    RouteLeg,
    RouteSearch,
    parse_calendar_text,
    station_query_name,
    strip_station_note,
)
from house_search.commute.normalize import normalize_key
from house_search.commute.resolve import STATUS_OK

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
        """報告に使う代表的な検索語（実際の問い合わせは query_candidates）。"""
        return self.station_name

    @property
    def query_candidates(self) -> tuple[str, ...]:
        """NAVITIME へ渡す検索語を、試す順に返す。

        ⚠ **駅名だけを先に試す。都道府県を添えるのは同名異駅で外したときだけ。**
        当初は「同名異駅を避けるため必ず県を添える」設計だったが、実測で
        **添えることが誤りの原因になる**と分かった。

        | 検索語 | 解決された駅 |
        |---|---|
        | ``松田（神奈川県）`` | **新松田**（誤り） |
        | ``松田`` | ``松田``（正しい） |
        | ``厚木（神奈川県）`` | **本厚木**（誤り） |
        | ``厚木`` | ``厚木``（正しい） |

        NAVITIME が ``駅名（都道府県）`` の表記を使うのは**同名異駅があるときだけ**で、
        同名駅の無い駅に県を添えると完全一致に失敗し、近い名前の別駅へ落ちる。
        逆に ``大久保`` のような同名駅は県付きの表記でしか一意に指せない。
        そこで**駅名 → 県付き**の順に試し、照合を通った方を採る。
        """
        bare = (self.station_name,)
        if not self.prefecture:
            return bare
        return (*bare, station_query_name(self.station_name, self.prefecture))


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


def note_variants(name: str) -> tuple[str, ...]:
    """駅名から、末尾の注記を落とした形も候補に加える。

    ⚠ **経路の駅名には注記が付く。** NAVITIME は乗換駅に路線注記を付け
    （``本八幡〔新宿線〕`` / ``溝の口〔東急線〕``）、副名称を角括弧で返すことがある
    （``押上[スカイツリー前]``）。落とさないと索引を引けず、その駅に接する区間が
    まるごと捨てられる（実測で 20,189本中 942本がこれだけで救えた）。

    ⚠ **原文を先に試す。** マスタ側に括弧付きの駅名が実在するため
    （``成田空港（第１旅客ターミナル）``）、落とした形だけを見ると取りこぼす。
    """
    stripped = strip_station_note(name)
    return (name,) if stripped == name or not stripped else (name, stripped)


def build_station_resolver(conn: Connection, prefecture_codes: Sequence[int]) -> StationResolver:
    """経路に出てくる駅名を駅グループコードへ直す関数を作る。

    ⚠ **``prefecture_codes`` は用途に合わせて渡す。** 掲載のある都道府県で固定すると
    その外の地方では1本も結び付かず（沖縄で区間72本すべてを捨てた）、逆に全国へ広げると
    同名異駅が一意でなくなって解決率が落ちる。範囲の決め方は cli の
    ``_segment_index_prefectures`` に置いてある。
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
    return make_station_resolver(index)


def make_station_resolver(index: StationIndex) -> StationResolver:
    """索引から解決関数を作る（DBに触らない）。

    ⚠ **一意に決まらない名前は None を返す。** 適当に1つ選ぶと、辺の重みが
    別の路線のものになっても誰も気づけない（同名異駅は実在する）。
    """

    def resolve(name: str) -> int | None:
        for candidate in note_variants(name):
            for variant in candidate_variants(candidate):
                groups = index.lookup(normalize_key(variant), None)
                if len(groups) == 1:
                    return next(iter(groups))
        return None

    return resolve


def harvest_segments(
    route: Route, resolve: StationResolver
) -> tuple[tuple[SegmentObservation, ...], int]:
    """経路から乗車区間を採る。``(採れた区間, 駅名を解決できず捨てた数)``。"""
    return harvest_leg_segments(route.legs, resolve)


def harvest_leg_segments(
    legs: Sequence[RouteLeg], resolve: StationResolver
) -> tuple[tuple[SegmentObservation, ...], int]:
    """区間の並びから乗車区間を採る。``re-segment`` は経路の原文からここへ直接入る。

    ⚠ **区間の分に待ち時間は入っていない**（発→着はひと続きの乗車のため）。
    足し合わせても二重計上にならない代わりに、乗換の待ちは別に足す必要がある。
    """
    found: list[SegmentObservation] = []
    dropped = 0
    for leg in legs:
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


@dataclass(frozen=True)
class RebuildResult:
    """``re-segment`` の実績。"""

    routes: int
    saved: int
    dropped: int
    failed: int


_RESTORE_COMMUTES = text(
    """
    INSERT INTO t_station_commutes (
        origin_station_g_cd, destination_station_g_cd, status,
        commute_minutes, transfers, distance_km, source, computed_at,
        created_at, updated_at
    )
    SELECT DISTINCT ON (r.origin_station_g_cd)
           r.origin_station_g_cd, r.destination_station_g_cd, :status,
           r.total_minutes, r.transfers, r.distance_km, :source, now(),
           now(), now()
      FROM t_navitime_routes r
     WHERE r.destination_station_g_cd = :dest
       AND r.total_minutes IS NOT NULL
     ORDER BY r.origin_station_g_cd, r.total_minutes, r.rank
    ON CONFLICT (origin_station_g_cd, destination_station_g_cd) DO UPDATE SET
        status          = EXCLUDED.status,
        commute_minutes = EXCLUDED.commute_minutes,
        transfers       = EXCLUDED.transfers,
        distance_km     = EXCLUDED.distance_km,
        source          = EXCLUDED.source,
        computed_at     = now(),
        updated_at      = now()
    """
)


def restore_commutes(conn: Connection, *, destination_g_cd: int) -> int:
    """保存済みの経路原文から所要時間キャッシュを作り直す（ネットワーク不要）。

    ⚠ **回帰式に上書きされた実ダイヤを取り戻すための復旧口。** 取り直すと
    1駅15秒（芝公園ゆき1,155駅で4.8時間）かかるが、``t_navitime_routes`` に
    候補ごとの所要時間が残っているので**再取得は要らない**。

    ⚠ **候補の中から最短を採る**（``fetch-commutes`` と同じ規則）。
    NAVITIME の並び順の1本目が最短とは限らない（実測で rank 1 が43分・
    別候補が39分）ため、``rank`` ではなく ``total_minutes`` で選ぶ。
    """
    result = conn.execute(
        _RESTORE_COMMUTES,
        {"dest": destination_g_cd, "status": STATUS_OK, "source": SOURCE_NAVITIME},
    )
    return int(result.rowcount or 0)


def rebuild_segments(
    conn: Connection,
    *,
    destination_g_cd: int,
    resolve: StationResolver,
    observed_at: dt.datetime,
) -> RebuildResult:
    """保存済みの経路原文から乗車区間を作り直す（ネットワーク不要）。

    ⚠ **設備の ``re-extract`` と同じ考え方。** 原文を残してあるので、駅名の照合を
    直したら取り直さずに反映できる。取り直すと1駅15秒で、芝公園ゆき1,155駅なら
    4.8時間かかる（→ ADR 0017 が原文を残している理由そのもの）。

    ⚠ **区間だけを作り直す。** 所要時間（``t_station_commutes``）は経路の解析結果が
    変わらない限り同じなので触らない。
    """
    rows = (
        conn.execute(
            text(
                """
                SELECT route_text FROM t_navitime_routes
                 WHERE destination_station_g_cd = :dest AND route_text IS NOT NULL
                 ORDER BY id
                """
            ),
            {"dest": destination_g_cd},
        )
        .scalars()
        .all()
    )
    observations: list[SegmentObservation] = []
    dropped = failed = 0
    for route_text in rows:
        try:
            _, _, legs = parse_calendar_text(route_text)
        except Exception:  # noqa: BLE001 — 1件の解析失敗で全体を止めない
            failed += 1
            continue
        found, missed = harvest_leg_segments(legs, resolve)
        observations.extend(found)
        dropped += missed
    saved = save_segments(conn, merge_observations(observations), observed_at=observed_at)
    return RebuildResult(routes=len(rows), saved=saved, dropped=dropped, failed=failed)
