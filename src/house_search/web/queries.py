"""閲覧画面の読み出し SQL。

⚠ **順位は保存済みの ``t_listing_scores`` をそのまま使い、画面で採点し直さない。**
一覧は ``rank_in_pattern IS NOT NULL`` で引けば「グループ代表＋未グループ物件」に
自動的に閉じる（``persist.update_ranks`` が非代表の順位を NULL に落としているため）。

⚠ 徒歩・通勤の絞り込みは**採点に使った値そのもの**（``score_breakdown`` の ``value``）で行う。
``t_listings.walk_minutes`` はバス停からの徒歩が入りうるので使わない（→ 課題#58）。

⚠ 利用者の値はすべてバインド変数。SQL に埋め込むのは下の固定の表から選んだ式だけ。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from house_search.marks import mark_exists_sql
from house_search.web.filters import PAGE_SIZE, ListingFilter

#: 価格の絞り込み・並べ替えに使う列。⚠ 賃貸は MUST と同じ「賃料＋管理費」
_PRICE_COLUMN: dict[str, str] = {
    "CHINTAI": "r.rent_total",
    "MANSION_BUY": "r.price",
    "KODATE_BUY": "r.price",
    "TOCHI_BUY": "r.price",
}
#: 面積の絞り込み・並べ替えに使う列（ファミリで意味が変わる）
_AREA_COLUMN: dict[str, str] = {
    "CHINTAI": "r.area_sqm",
    "MANSION_BUY": "r.area_sqm",
    "KODATE_BUY": "r.building_area_sqm",
    "TOCHI_BUY": "r.land_area_sqm",
}
PRICE_LABELS: dict[str, str] = {
    "CHINTAI": "賃料＋管理費",
    "MANSION_BUY": "価格",
    "KODATE_BUY": "価格",
    "TOCHI_BUY": "価格",
}
AREA_LABELS: dict[str, str] = {
    "CHINTAI": "専有面積",
    "MANSION_BUY": "専有面積",
    "KODATE_BUY": "建物面積",
    "TOCHI_BUY": "土地面積",
}
_ORDER_BY: dict[str, str] = {
    "rank": "r.rank_in_pattern ASC",
    "price_asc": "{price} ASC NULLS LAST, r.rank_in_pattern ASC",
    "price_desc": "{price} DESC NULLS LAST, r.rank_in_pattern ASC",
    "area_desc": "{area} DESC NULLS LAST, r.rank_in_pattern ASC",
    "walk_asc": "r.walk_value ASC NULLS LAST, r.rank_in_pattern ASC",
    "commute_asc": "r.commute_value ASC NULLS LAST, r.rank_in_pattern ASC",
    "newest": "r.first_seen_at DESC, r.rank_in_pattern ASC",
}


def _breakdown_value(code: str) -> str:
    # code は下の呼び出しの固定値だけ
    return (
        "(SELECT (e ->> 'value')::numeric FROM jsonb_array_elements(s.score_breakdown) e"
        f" WHERE e ->> 'code' = '{code}' LIMIT 1)"
    )


_RANKED = f"""
    SELECT s.listing_id, s.rank_in_pattern, s.score, s.must_result, s.config_hash,
           s.score_breakdown,
           p.group_id, p.city_id, p.price, p.rent_total,
           p.area_sqm, p.building_area_sqm, p.land_area_sqm, p.first_seen_at,
           st.code AS site_code,
           {_breakdown_value("walk_minutes")} AS walk_value,
           {_breakdown_value("commute_minutes")} AS commute_value,
           {mark_exists_sql("is_favorite")} AS is_favorite,
           {mark_exists_sql("is_excluded")} AS is_excluded
    FROM t_listing_scores s
    JOIN t_listings p ON p.id = s.listing_id
    JOIN m_sites st ON st.id = p.site_id
    WHERE s.pattern_name = :pattern_name
      AND s.rank_in_pattern IS NOT NULL
      AND p.status = 'active'
"""


@dataclass(frozen=True, slots=True)
class RankingPage:
    rows: list[Any]
    total: int


def _filter_sql(flt: ListingFilter, family: str) -> tuple[list[str], dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    price = _PRICE_COLUMN[family]
    area = _AREA_COLUMN[family]
    if flt.price_min is not None:
        where.append(f"{price} >= :price_min")
        params["price_min"] = round(flt.price_min * 10_000)
    if flt.price_max is not None:
        where.append(f"{price} <= :price_max")
        params["price_max"] = round(flt.price_max * 10_000)
    if flt.area_min is not None:
        where.append(f"{area} >= :area_min")
        params["area_min"] = flt.area_min
    if flt.walk_max is not None:
        where.append("r.walk_value <= :walk_max")
        params["walk_max"] = flt.walk_max
    if flt.commute_max is not None:
        where.append("r.commute_value <= :commute_max")
        params["commute_max"] = flt.commute_max
    if flt.must != "all":
        where.append("r.must_result = :must")
        params["must"] = flt.must
    if flt.site:
        where.append("r.site_code = :site")
        params["site"] = flt.site
    if flt.city is not None:
        where.append("r.city_id = :city")
        params["city"] = flt.city
    if flt.favorites:
        where.append("r.is_favorite")
    if not flt.include_excluded:
        where.append("NOT r.is_excluded")
    return where, params


def ranking_page(
    conn: Connection, *, pattern_name: str, family: str, flt: ListingFilter
) -> RankingPage:
    """ランキング一覧の1ページぶんと、絞り込み後の総件数。"""
    where, params = _filter_sql(flt, family)
    params["pattern_name"] = pattern_name
    condition = " AND ".join(where) if where else "TRUE"
    order = _ORDER_BY[flt.sort].format(price=_PRICE_COLUMN[family], area=_AREA_COLUMN[family])
    total = conn.execute(
        text(f"WITH r AS ({_RANKED}) SELECT count(*) FROM r WHERE {condition}"), params
    ).scalar_one()
    rows = conn.execute(
        text(
            f"WITH r AS ({_RANKED}) SELECT * FROM r WHERE {condition} "
            f"ORDER BY {order} LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": PAGE_SIZE, "offset": flt.offset},
    ).all()
    return RankingPage(rows=list(rows), total=int(total))


@dataclass(frozen=True, slots=True)
class PatternSummary:
    ranked: int
    favorites: int
    excluded: int
    stale: int
    scored_at: Any


def pattern_summary(conn: Connection, *, pattern_name: str, config_hash: str) -> PatternSummary:
    """トップページに出す件数（順位付き・お気に入り・除外・採点が古い行）。"""
    row = conn.execute(
        text(
            f"""
            WITH r AS ({_RANKED})
            SELECT count(*) AS ranked,
                   count(*) FILTER (WHERE r.is_favorite) AS favorites,
                   count(*) FILTER (WHERE r.is_excluded) AS excluded,
                   count(*) FILTER (WHERE r.config_hash <> :config_hash) AS stale,
                   (SELECT max(scored_at) FROM t_listing_scores
                    WHERE pattern_name = :pattern_name) AS scored_at
            FROM r
            """
        ),
        {"pattern_name": pattern_name, "config_hash": config_hash},
    ).one()
    return PatternSummary(
        ranked=row.ranked,
        favorites=row.favorites,
        excluded=row.excluded,
        stale=row.stale,
        scored_at=row.scored_at,
    )


def site_options(conn: Connection, *, pattern_name: str) -> list[Any]:
    """絞り込みのサイトの選択肢（順位付きの掲載があるサイトだけ）。"""
    return list(
        conn.execute(
            text(
                """
                SELECT st.code, st.name, count(*) AS listings
                FROM t_listing_scores s
                JOIN t_listings p ON p.id = s.listing_id
                JOIN m_sites st ON st.id = p.site_id
                WHERE s.pattern_name = :pattern_name AND s.rank_in_pattern IS NOT NULL
                  AND p.status = 'active'
                GROUP BY st.code, st.name
                ORDER BY st.code
                """
            ),
            {"pattern_name": pattern_name},
        )
    )


def city_options(conn: Connection, *, pattern_name: str) -> list[Any]:
    """絞り込みの市区の選択肢。⚠ 名前ではなくIDで絞る（「緑区」は横浜・さいたま・相模原にある）。"""
    return list(
        conn.execute(
            text(
                """
                SELECT c.id, c.prefecture, c.canonical_name, count(*) AS listings
                FROM t_listing_scores s
                JOIN t_listings p ON p.id = s.listing_id
                JOIN m_cities c ON c.id = p.city_id
                WHERE s.pattern_name = :pattern_name AND s.rank_in_pattern IS NOT NULL
                  AND p.status = 'active'
                GROUP BY c.id, c.prefecture, c.canonical_name, c.jis_code
                ORDER BY c.jis_code NULLS LAST, c.id
                """
            ),
            {"pattern_name": pattern_name},
        )
    )


def listing_row(conn: Connection, listing_id: int) -> Any | None:
    """詳細画面の見出しに使う掲載1件（掲載終了も含む）。"""
    return conn.execute(
        text(
            """
            SELECT p.id, p.group_id, p.status, p.url, p.title, p.address, p.prefecture,
                   p.raw_features_text, p.type_specific_attrs, p.detail_fetched_at,
                   p.first_seen_at, p.last_seen_at, p.price_prev,
                   st.code AS site_code, st.name AS site_name,
                   pt.code AS property_type_code, pt.name AS property_type_name,
                   c.canonical_name AS city_name,
                   g.representative_listing_id, g.member_count
            FROM t_listings p
            JOIN m_sites st ON st.id = p.site_id
            JOIN m_property_types pt ON pt.id = p.property_type_id
            LEFT JOIN m_cities c ON c.id = p.city_id
            LEFT JOIN t_listing_groups g ON g.id = p.group_id
            WHERE p.id = :listing_id
            """
        ),
        {"listing_id": listing_id},
    ).first()


def listing_exists(conn: Connection, listing_id: int) -> bool:
    return (
        conn.execute(
            text("SELECT 1 FROM t_listings WHERE id = :listing_id"), {"listing_id": listing_id}
        ).first()
        is not None
    )


def score_row(conn: Connection, *, pattern_name: str, listing_id: int) -> Any | None:
    """その掲載のそのパターンでの保存済み採点。"""
    return conn.execute(
        text(
            """
            SELECT rank_in_pattern, score, must_result, config_hash, score_breakdown, scored_at
            FROM t_listing_scores
            WHERE pattern_name = :pattern_name AND listing_id = :listing_id
            """
        ),
        {"pattern_name": pattern_name, "listing_id": listing_id},
    ).first()


def group_members(conn: Connection, *, pattern_name: str, listing_id: int) -> Sequence[Any]:
    """同じ名寄せグループの掲載（未グループなら自身だけ）。掲載終了も含めて出す。"""
    return conn.execute(
        text(
            """
            SELECT m.id, st.code AS site_code, st.name AS site_name, m.url, m.title,
                   m.price, m.mgmt_fee_monthly, m.rent_total, m.status,
                   m.detail_fetched_at, m.first_seen_at, m.last_seen_at, m.raw_features_text,
                   (g.representative_listing_id = m.id) AS is_representative,
                   sc.rank_in_pattern, sc.score
            FROM t_listings target
            JOIN t_listings m ON m.id = target.id OR m.group_id = target.group_id
            JOIN m_sites st ON st.id = m.site_id
            LEFT JOIN t_listing_groups g ON g.id = m.group_id
            LEFT JOIN t_listing_scores sc
              ON sc.listing_id = m.id AND sc.pattern_name = :pattern_name
            WHERE target.id = :listing_id
            ORDER BY (m.status = 'active') DESC,
                     (g.representative_listing_id = m.id) DESC NULLS LAST,
                     m.id
            """
        ),
        {"pattern_name": pattern_name, "listing_id": listing_id},
    ).all()
