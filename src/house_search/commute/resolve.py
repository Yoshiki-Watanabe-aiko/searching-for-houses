"""掲載 → 駅の同定結果をDBへ落とす層。

``matcher`` は純関数、``stations`` はマスタの同期、この層がその2つとDBを繋ぐ。
ネットワークには触らない（Routes API は ``routes_api`` の担当）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, text

from house_search.commute.matcher import (
    MATCH_AMBIGUOUS,
    MATCH_MATCHED,
    MATCH_UNMATCHED,
    StationIndex,
    match_stations,
)

# 一度にDBへ書き込む掲載数。1万件規模でもメモリと往復回数の折り合いが付く。
CHUNK_SIZE = 500


@dataclass(frozen=True)
class SiteResolveStat:
    """サイト別の同定実績。"""

    site_code: str
    listings: int
    with_station: int
    matched_rows: int
    ambiguous_rows: int
    unmatched_rows: int

    @property
    def rate(self) -> float:
        """1件以上の駅を同定できた掲載の割合。"""
        return self.with_station / self.listings * 100 if self.listings else 0.0


@dataclass(frozen=True)
class ResolveStats:
    """``resolve-stations`` の実績。"""

    per_site: tuple[SiteResolveStat, ...]

    @property
    def listings(self) -> int:
        return sum(stat.listings for stat in self.per_site)

    @property
    def with_station(self) -> int:
        return sum(stat.with_station for stat in self.per_site)

    @property
    def rate(self) -> float:
        return self.with_station / self.listings * 100 if self.listings else 0.0


def listing_prefecture_codes(conn: Connection) -> tuple[int, ...]:
    """掲載が存在する都道府県コードを集める。

    駅の索引はここに絞って作る。全国で引くと同名異駅（日本橋＝東京/大阪）が
    曖昧になるだけで、掲載の無い都道府県の駅は同定先になりえない。
    """
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT CAST(LEFT(c.jis_code, 2) AS INTEGER) AS pref_cd
              FROM t_listings l
              JOIN m_cities c ON c.id = l.city_id
             WHERE c.jis_code IS NOT NULL
             ORDER BY 1
            """
        )
    ).scalars()
    return tuple(int(code) for code in rows)


def load_station_index(conn: Connection, prefecture_codes: Sequence[int]) -> StationIndex:
    """駅マスタから照合索引を作る。"""
    if not prefecture_codes:
        return StationIndex.build(())
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
    return StationIndex.build((row[0], int(row[1]), int(row[2])) for row in rows)


_TARGETS = """
    SELECT l.id,
           s.code AS site_code,
           l.station_info,
           CAST(LEFT(c.jis_code, 2) AS INTEGER) AS pref_cd
      FROM t_listings l
      JOIN m_sites s ON s.id = l.site_id
      LEFT JOIN m_cities c ON c.id = l.city_id
"""

_INSERT = text(
    """
    INSERT INTO t_listing_stations (
        listing_id, position, raw_station_name, station_g_cd, match_status,
        created_at, updated_at
    )
    VALUES (:listing_id, :position, :raw_station_name, :station_g_cd, :match_status,
            now(), now())
    """
)


def resolve_listing_stations(
    conn: Connection,
    index: StationIndex,
    *,
    listing_ids: Sequence[int] | None = None,
    active_only: bool = True,
) -> ResolveStats:
    """掲載の駅表記を同定して ``t_listing_stations`` へ書き込む。

    掲載単位の DELETE → INSERT で冪等にする（``save_features`` と同じ形）。
    ``listing_ids`` を渡すとその掲載だけを対象にする（``scan`` からの増分用）。
    """
    sql = _TARGETS
    params: dict[str, object] = {}
    conditions = []
    if active_only:
        conditions.append("l.status = 'active'")
    if listing_ids is not None:
        if not listing_ids:
            return ResolveStats(per_site=())
        conditions.append("l.id = ANY(:ids)")
        params["ids"] = list(listing_ids)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    rows = conn.execute(text(sql), params).all()

    stats: dict[str, dict[str, int]] = {}
    buffer: list[dict[str, object]] = []
    buffered_ids: list[int] = []

    def flush() -> None:
        if not buffered_ids:
            return
        conn.execute(
            text("DELETE FROM t_listing_stations WHERE listing_id = ANY(:ids)"),
            {"ids": buffered_ids},
        )
        if buffer:
            conn.execute(_INSERT, buffer)
        buffer.clear()
        buffered_ids.clear()

    for listing_id, site_code, station_info, pref_cd in rows:
        counter = stats.setdefault(
            site_code,
            {"listings": 0, "with_station": 0, "matched": 0, "ambiguous": 0, "unmatched": 0},
        )
        counter["listings"] += 1
        matches = match_stations(station_info, index, pref_cd)
        for match in matches:
            if match.match_status == MATCH_MATCHED:
                counter["matched"] += 1
            elif match.match_status == MATCH_AMBIGUOUS:
                counter["ambiguous"] += 1
            elif match.match_status == MATCH_UNMATCHED:
                counter["unmatched"] += 1
            buffer.append(
                {
                    "listing_id": listing_id,
                    "position": match.position,
                    "raw_station_name": match.raw_name[:100],
                    "station_g_cd": match.station_g_cd,
                    "match_status": match.match_status,
                }
            )
        if any(m.match_status == MATCH_MATCHED for m in matches):
            counter["with_station"] += 1
        buffered_ids.append(listing_id)
        if len(buffered_ids) >= CHUNK_SIZE:
            flush()
    flush()

    return ResolveStats(
        per_site=tuple(
            SiteResolveStat(
                site_code=site_code,
                listings=values["listings"],
                with_station=values["with_station"],
                matched_rows=values["matched"],
                ambiguous_rows=values["ambiguous"],
                unmatched_rows=values["unmatched"],
            )
            for site_code, values in sorted(stats.items())
        )
    )


def unmatched_station_names(conn: Connection, limit: int = 30) -> tuple[tuple[str, int], ...]:
    """同定できなかった駅表記を出現回数順に返す（規則を直す材料）。"""
    rows = conn.execute(
        text(
            """
            SELECT raw_station_name, count(*) AS n
              FROM t_listing_stations
             WHERE match_status <> 'matched'
             GROUP BY raw_station_name
             ORDER BY n DESC, raw_station_name
             LIMIT :limit
            """
        ),
        {"limit": limit},
    ).all()
    return tuple((row[0], int(row[1])) for row in rows)


# --- 通勤時間キャッシュの解決 ---------------------------------------------

# 算出元。将来ほかの手段（実ダイヤのAPI等）を混ぜたときに行を見分けるための印。
SOURCE_RAIL_GRAPH = "rail_graph"

STATUS_OK = "ok"
STATUS_NO_ROUTE = "no_route"


@dataclass(frozen=True)
class CommuteSummary:
    """キャッシュの状態。``resolve-commutes`` の報告に使う。"""

    ok: int
    no_route: int

    @property
    def total(self) -> int:
        return self.ok + self.no_route


def prefecture_code_of(conn: Connection, prefecture_name: str) -> int | None:
    """都道府県名を JIS コード（上位2桁）に直す。"""
    code = conn.execute(
        text(
            """
            SELECT CAST(LEFT(jis_code, 2) AS INTEGER)
              FROM m_cities
             WHERE prefecture = :name AND jis_code IS NOT NULL
             LIMIT 1
            """
        ),
        {"name": prefecture_name},
    ).scalar()
    return int(code) if code is not None else None


def referenced_station_groups(
    conn: Connection, *, pattern_name: str | None = None
) -> tuple[int, ...]:
    """掲載が最寄り駅として挙げている駅グループを集める。

    所要時間はダイクストラ1回で全国ぶん出るので計算量の都合ではなく、
    **使わない駅の行をDBに溜めない**ための絞り込み。
    """
    sql = """
        SELECT DISTINCT ls.station_g_cd
          FROM t_listing_stations ls
          JOIN t_listings l ON l.id = ls.listing_id AND l.status = 'active'
         WHERE ls.station_g_cd IS NOT NULL
    """
    params: dict[str, object] = {}
    if pattern_name:
        sql += """
           AND EXISTS (
               SELECT 1 FROM t_listing_scores s
                WHERE s.listing_id = ls.listing_id AND s.pattern_name = :pattern
           )
        """
        params["pattern"] = pattern_name
    sql += " ORDER BY 1"
    return tuple(int(code) for code in conn.execute(text(sql), params).scalars())


def load_station_nodes(conn: Connection) -> tuple[tuple[int, int, float, float], ...]:
    """グラフのノードになる駅を読む（``station_cd, station_g_cd, lat, lon``）。"""
    rows = conn.execute(
        text("SELECT station_cd, station_g_cd, lat, lon FROM m_stations")
    ).all()
    return tuple((int(r[0]), int(r[1]), float(r[2]), float(r[3])) for r in rows)


_UPSERT_COMMUTE = text(
    """
    INSERT INTO t_station_commutes (
        origin_station_g_cd, destination_station_g_cd, status,
        commute_minutes, transfers, distance_km, source, computed_at,
        created_at, updated_at
    )
    VALUES (
        :origin, :destination, :status,
        :commute_minutes, :transfers, :distance_km, :source, now(),
        now(), now()
    )
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


def save_commutes(
    conn: Connection,
    *,
    destination_g_cd: int,
    rows: Sequence[tuple[int, str, int | None, int | None, float | None]],
    source: str = SOURCE_RAIL_GRAPH,
) -> int:
    """算出結果をまとめてキャッシュへ書く。

    ``rows`` は ``(出発駅グループ, status, 分, 乗換, 距離km)``。
    """
    if not rows:
        return 0
    conn.execute(
        _UPSERT_COMMUTE,
        [
            {
                "origin": origin,
                "destination": destination_g_cd,
                "status": status,
                "commute_minutes": minutes,
                "transfers": transfers,
                "distance_km": distance,
                "source": source,
            }
            for origin, status, minutes, transfers, distance in rows
        ],
    )
    return len(rows)


def commute_summary(conn: Connection, destination_g_cd: int) -> CommuteSummary:
    """キャッシュの内訳を数える。"""
    counts = dict(
        conn.execute(
            text(
                """
                SELECT status, count(*)
                  FROM t_station_commutes
                 WHERE destination_station_g_cd = :destination
                 GROUP BY status
                """
            ),
            {"destination": destination_g_cd},
        ).all()
    )
    return CommuteSummary(
        ok=int(counts.get(STATUS_OK, 0)), no_route=int(counts.get(STATUS_NO_ROUTE, 0))
    )


def commute_distribution(
    conn: Connection, destination_g_cd: int
) -> tuple[int, int, int, int] | None:
    """所要時間の四分位（最小・中央・75%・最大）を返す。"""
    row = conn.execute(
        text(
            """
            SELECT min(commute_minutes),
                   CAST(percentile_cont(0.5) WITHIN GROUP (ORDER BY commute_minutes) AS INTEGER),
                   CAST(percentile_cont(0.75) WITHIN GROUP (ORDER BY commute_minutes) AS INTEGER),
                   max(commute_minutes)
              FROM t_station_commutes
             WHERE destination_station_g_cd = :destination AND status = 'ok'
            """
        ),
        {"destination": destination_g_cd},
    ).first()
    if row is None or row[0] is None:
        return None
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])
