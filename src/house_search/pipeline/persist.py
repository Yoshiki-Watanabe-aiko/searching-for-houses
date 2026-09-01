"""スクレイプ結果のDB永続化。

upsert はすべて冪等にしてある。中断・再開は「``detail_fetched_at IS NULL``
の物件を詳細取得キューとして引く」というSQLの自然な帰結になる。

``updated_at`` を明示的にセットしているのは、``ON CONFLICT DO UPDATE`` では
SQLAlchemy の ``onupdate`` が発火しないため（db/base.py の注記）。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

from house_search.scoring.property_view import PropertyView
from house_search.scrape.base import ScrapedDetail, ScrapedListing

# 通知種別。
NEW = "new"
SOLD = "sold"
PRICE_UP = "price_up"
PRICE_DOWN = "price_down"


@dataclass(frozen=True, slots=True)
class UpsertOutcome:
    """1掲載ぶんの upsert 結果。通知の要否判定に使う。"""

    property_id: int
    external_id: str
    is_new: bool
    is_reinstated: bool
    price_event: str | None
    price_prev: int | None

    @property
    def notification_type(self) -> str | None:
        """この掲載について送るべき通知種別。無ければ None。"""
        if self.is_new or self.is_reinstated:
            return NEW
        return self.price_event


def load_lookup(conn: Connection, table: str) -> dict[str, int]:
    """``code -> id`` のマスタ引き当て表を作る。"""
    return {code: row_id for code, row_id in conn.execute(text(f"SELECT code, id FROM {table}"))}


def load_city_index(conn: Connection) -> list[tuple[str, str, int]]:
    """住所から市区町村IDを引くための索引。

    ``(都道府県, 正規名, city_id)`` を正規名の長い順に並べる。
    「横浜市西区」と「西区」のように短い名前が先に当たると誤判定するため。
    """
    rows = conn.execute(
        text(
            "SELECT prefecture, canonical_name, id FROM m_cities "
            "ORDER BY length(canonical_name) DESC"
        )
    ).all()
    return [(pref, name, city_id) for pref, name, city_id in rows]


def resolve_city(
    address: str | None, index: list[tuple[str, str, int]]
) -> tuple[str | None, int | None]:
    """住所から都道府県名と市区町村IDを解決する。

    都道府県から始まらない住所（賃貸EX は「足立区竹の塚６」と書く）にも効くよう、
    前置の都道府県が無いときは**市区名が全国で一意なものだけ**を引き当てる。
    「北区」「西区」のように複数県にある名前は取り違えるので解決しない。
    """
    if not address:
        return None, None
    for prefecture, canonical, city_id in index:
        if address.startswith(prefecture) and canonical in address:
            return prefecture, city_id
    for prefecture, _canonical, _city_id in index:
        if address.startswith(prefecture):
            return prefecture, None

    unique = _unique_city_names(index)
    for prefecture, canonical, city_id in index:
        if canonical in unique and canonical in address:
            return prefecture, city_id
    return None, None


def _unique_city_names(index: list[tuple[str, str, int]]) -> frozenset[str]:
    """全国で1つしか存在しない市区町村名の集合。"""
    counts: dict[str, int] = {}
    for _prefecture, canonical, _city_id in index:
        counts[canonical] = counts.get(canonical, 0) + 1
    return frozenset(name for name, count in counts.items() if count == 1)


_SELECT_EXISTING = text(
    "SELECT external_id, id, price, status FROM t_properties "
    "WHERE site_id = :site_id AND external_id = ANY(:external_ids)"
)

_UPSERT = text(
    """
    INSERT INTO t_properties (
        site_id, property_type_id, external_id, url, title,
        price, price_prev, mgmt_fee_monthly, deposit_amount, key_money_amount,
        area_sqm, layout, floor_num, total_floors, age_years,
        address, prefecture, city_id, station_info, walk_minutes, image_url,
        status, first_seen_at, last_seen_at, created_at, updated_at
    ) VALUES (
        :site_id, :property_type_id, :external_id, :url, :title,
        :price, :price_prev, :mgmt_fee_monthly, :deposit_amount, :key_money_amount,
        :area_sqm, :layout, :floor_num, :total_floors, :age_years,
        :address, :prefecture, :city_id, :station_info, :walk_minutes, :image_url,
        'active', now(), now(), now(), now()
    )
    ON CONFLICT (site_id, external_id) DO UPDATE SET
        url = EXCLUDED.url,
        title = COALESCE(EXCLUDED.title, t_properties.title),
        price = EXCLUDED.price,
        price_prev = EXCLUDED.price_prev,
        mgmt_fee_monthly = EXCLUDED.mgmt_fee_monthly,
        deposit_amount = EXCLUDED.deposit_amount,
        key_money_amount = EXCLUDED.key_money_amount,
        area_sqm = COALESCE(EXCLUDED.area_sqm, t_properties.area_sqm),
        layout = COALESCE(EXCLUDED.layout, t_properties.layout),
        floor_num = COALESCE(EXCLUDED.floor_num, t_properties.floor_num),
        total_floors = COALESCE(EXCLUDED.total_floors, t_properties.total_floors),
        age_years = COALESCE(EXCLUDED.age_years, t_properties.age_years),
        address = COALESCE(EXCLUDED.address, t_properties.address),
        prefecture = COALESCE(EXCLUDED.prefecture, t_properties.prefecture),
        city_id = COALESCE(EXCLUDED.city_id, t_properties.city_id),
        station_info = COALESCE(EXCLUDED.station_info, t_properties.station_info),
        walk_minutes = COALESCE(EXCLUDED.walk_minutes, t_properties.walk_minutes),
        image_url = COALESCE(EXCLUDED.image_url, t_properties.image_url),
        status = 'active',
        last_seen_at = now(),
        updated_at = now()
    RETURNING id
    """
)


def upsert_listings(
    conn: Connection,
    listings: list[ScrapedListing],
    *,
    site_id: int,
    property_type_id: int,
    city_index: list[tuple[str, str, int]],
) -> list[UpsertOutcome]:
    """一覧の掲載をまとめて upsert し、新着・再掲載・価格変動を判定する。

    既存行の取得を1クエリにまとめてあるのは、掲載1件ごとに SELECT すると
    ページあたり数十回の往復になるため。
    """
    if not listings:
        return []

    external_ids = [listing.external_id for listing in listings]
    existing = {
        external_id: (row_id, price, status)
        for external_id, row_id, price, status in conn.execute(
            _SELECT_EXISTING, {"site_id": site_id, "external_ids": external_ids}
        )
    }

    outcomes: list[UpsertOutcome] = []
    for listing in listings:
        previous = existing.get(listing.external_id)
        is_new = previous is None
        old_price = previous[1] if previous else None
        old_status = previous[2] if previous else None
        is_reinstated = old_status in ("sold", "removed")

        price_event: str | None = None
        price_prev = old_price
        if not is_new and old_price is not None and listing.price is not None:
            if listing.price < old_price:
                price_event = PRICE_DOWN
            elif listing.price > old_price:
                price_event = PRICE_UP
            else:
                # 変化していないなら直前価格は据え置く（差分表示が消えないように）
                price_prev = old_price

        prefecture, city_id = resolve_city(listing.address, city_index)
        row_id = conn.execute(
            _UPSERT,
            {
                "site_id": site_id,
                "property_type_id": property_type_id,
                "external_id": listing.external_id,
                "url": listing.url,
                "title": listing.title,
                "price": listing.price,
                "price_prev": price_prev,
                "mgmt_fee_monthly": listing.mgmt_fee_monthly,
                "deposit_amount": listing.deposit_amount,
                "key_money_amount": listing.key_money_amount,
                "area_sqm": listing.area_sqm,
                "layout": listing.layout,
                "floor_num": listing.floor_num,
                "total_floors": listing.total_floors,
                "age_years": listing.age_years,
                "address": listing.address,
                "prefecture": prefecture,
                "city_id": city_id,
                "station_info": listing.station_info,
                "walk_minutes": listing.walk_minutes,
                "image_url": listing.image_url,
            },
        ).scalar_one()

        outcomes.append(
            UpsertOutcome(
                property_id=row_id,
                external_id=listing.external_id,
                is_new=is_new,
                is_reinstated=is_reinstated,
                price_event=price_event,
                price_prev=old_price,
            )
        )
    return outcomes


def save_detail(conn: Connection, property_id: int, detail: ScrapedDetail) -> None:
    """詳細ページ由来の情報を書き戻し、詳細取得済みにする。"""
    conn.execute(
        text(
            """
            UPDATE t_properties SET
                raw_features_text = COALESCE(:raw_features_text, raw_features_text),
                built_on = COALESCE(:built_on, built_on),
                floor_num = COALESCE(:floor_num, floor_num),
                total_floors = COALESCE(:total_floors, total_floors),
                mgmt_fee_monthly = COALESCE(:mgmt_fee_monthly, mgmt_fee_monthly),
                deposit_amount = COALESCE(:deposit_amount, deposit_amount),
                key_money_amount = COALESCE(:key_money_amount, key_money_amount),
                address = COALESCE(:address, address),
                walk_minutes = COALESCE(:walk_minutes, walk_minutes),
                type_specific_attrs = COALESCE(
                    t_properties.type_specific_attrs, '{}'::jsonb
                ) || CAST(:type_specific_attrs AS jsonb),
                detail_fetched_at = now(),
                updated_at = now()
            WHERE id = :property_id
            """
        ),
        {
            "property_id": property_id,
            "raw_features_text": detail.raw_features_text,
            "built_on": detail.built_on,
            "floor_num": detail.floor_num,
            "total_floors": detail.total_floors,
            "mgmt_fee_monthly": detail.mgmt_fee_monthly,
            "deposit_amount": detail.deposit_amount,
            "key_money_amount": detail.key_money_amount,
            "address": detail.address,
            "walk_minutes": detail.walk_minutes,
            "type_specific_attrs": json.dumps(detail.type_specific_attrs, ensure_ascii=False),
        },
    )


def save_features(
    conn: Connection,
    property_id: int,
    features: tuple,
    condition_ids: dict[str, int],
) -> int:
    """抽出結果を保存する。

    再抽出できるよう、その物件の既存行をいったん消してから入れ直す
    （辞書から外れた条件が残らないようにする）。
    """
    conn.execute(
        text("DELETE FROM t_property_features WHERE property_id = :property_id"),
        {"property_id": property_id},
    )
    rows = [
        {
            "property_id": property_id,
            "condition_id": condition_ids[feature.code],
            "source": feature.source,
            "matched_text": feature.matched_text,
        }
        for feature in features
        if feature.code in condition_ids
    ]
    if rows:
        conn.execute(
            text(
                "INSERT INTO t_property_features "
                "(property_id, condition_id, source, matched_text, extracted_at, "
                " created_at, updated_at) "
                "VALUES (:property_id, :condition_id, :source, :matched_text, now(), now(), now())"
            ),
            rows,
        )
    return len(rows)


def save_unknown_tokens(
    conn: Connection,
    tokens: tuple[str, ...],
    *,
    site_id: int,
    property_family: str,
    sample_url: str | None,
) -> None:
    """辞書未登録の表記を出現回数付きで記録する。"""
    if not tokens:
        return
    conn.execute(
        text(
            """
            INSERT INTO t_unknown_tokens (
                token, site_id, property_family, occurrence_count, sample_url,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :token, :site_id, :property_family, 1, :sample_url,
                now(), now(), now(), now()
            )
            ON CONFLICT (token, site_id) DO UPDATE SET
                occurrence_count = t_unknown_tokens.occurrence_count + 1,
                last_seen_at = now(),
                updated_at = now()
            """
        ),
        [
            {
                "token": token,
                "site_id": site_id,
                "property_family": property_family,
                "sample_url": sample_url,
            }
            for token in tokens
        ],
    )


def save_score(
    conn: Connection,
    *,
    property_id: int,
    pattern_name: str,
    must_result: str,
    score: float | None,
    breakdown: list[dict[str, Any]],
    config_hash: str,
) -> None:
    """採点結果を保存する（順位は全件確定後に別途更新する）。"""
    conn.execute(
        text(
            """
            INSERT INTO t_property_scores (
                property_id, pattern_name, must_result, score, score_breakdown,
                config_hash, scored_at, created_at, updated_at
            ) VALUES (
                :property_id, :pattern_name, :must_result, :score,
                CAST(:breakdown AS jsonb), :config_hash, now(), now(), now()
            )
            ON CONFLICT (property_id, pattern_name) DO UPDATE SET
                must_result = EXCLUDED.must_result,
                score = EXCLUDED.score,
                score_breakdown = EXCLUDED.score_breakdown,
                config_hash = EXCLUDED.config_hash,
                scored_at = now(),
                updated_at = now()
            """
        ),
        {
            "property_id": property_id,
            "pattern_name": pattern_name,
            "must_result": must_result,
            "score": score,
            "breakdown": json.dumps(breakdown, ensure_ascii=False),
            "config_hash": config_hash,
        },
    )


def update_ranks(conn: Connection, pattern_name: str) -> int:
    """パターン内のスコア降順順位を振り直す。

    同点は物件IDの昇順で決めて、実行ごとに順位が揺れないようにする。
    """
    result = conn.execute(
        text(
            """
            UPDATE t_property_scores s SET rank_in_pattern = r.rn, updated_at = now()
            FROM (
                SELECT id, ROW_NUMBER() OVER (ORDER BY score DESC, property_id ASC) AS rn
                FROM t_property_scores
                WHERE pattern_name = :pattern_name AND must_result <> 'fail' AND score IS NOT NULL
            ) r
            WHERE s.id = r.id
            """
        ),
        {"pattern_name": pattern_name},
    )
    return result.rowcount or 0


def record_notification(
    conn: Connection,
    *,
    property_id: int,
    pattern_name: str,
    notification_type: str,
    price_at_notify: int | None,
    score_at_notify: float | None,
    status: str,
) -> None:
    """通知履歴を追記する。"""
    conn.execute(
        text(
            "INSERT INTO t_notifications ("
            " property_id, pattern_name, notification_type, price_at_notify,"
            " score_at_notify, status, notified_at, created_at) "
            "VALUES (:property_id, :pattern_name, :notification_type, :price_at_notify,"
            " :score_at_notify, :status, now(), now())"
        ),
        {
            "property_id": property_id,
            "pattern_name": pattern_name,
            "notification_type": notification_type,
            "price_at_notify": price_at_notify,
            "score_at_notify": score_at_notify,
            "status": status,
        },
    )


def already_notified(
    conn: Connection, *, property_id: int, pattern_name: str, notification_type: str
) -> bool:
    """同じ物件・同じ種別の通知を既に送っていないか。"""
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM t_notifications "
                "WHERE property_id = :property_id AND pattern_name = :pattern_name "
                "AND notification_type = :notification_type AND status = 'sent' LIMIT 1"
            ),
            {
                "property_id": property_id,
                "pattern_name": pattern_name,
                "notification_type": notification_type,
            },
        ).first()
    )


_PROPERTY_COLUMNS = """
    p.id, s.code AS site_code, p.url, p.title, p.price, p.price_prev,
    p.mgmt_fee_monthly, p.rent_total, p.repair_reserve_monthly,
    p.area_sqm, p.land_area_sqm, p.building_area_sqm, p.layout,
    p.floor_num, p.total_floors, p.age_years, p.walk_minutes,
    p.prefecture, p.address, p.image_url,
    (p.detail_fetched_at IS NOT NULL) AS detail_fetched
"""


def _to_view(row: Any, feature_codes: frozenset[str]) -> PropertyView:
    return PropertyView(
        property_id=row.id,
        site_code=row.site_code,
        url=row.url,
        title=row.title,
        price=row.price,
        mgmt_fee_monthly=row.mgmt_fee_monthly,
        rent_total=row.rent_total,
        repair_reserve_monthly=row.repair_reserve_monthly,
        area_sqm=float(row.area_sqm) if row.area_sqm is not None else None,
        land_area_sqm=float(row.land_area_sqm) if row.land_area_sqm is not None else None,
        building_area_sqm=(
            float(row.building_area_sqm) if row.building_area_sqm is not None else None
        ),
        layout=row.layout,
        floor_num=row.floor_num,
        total_floors=row.total_floors,
        age_years=row.age_years,
        walk_minutes=row.walk_minutes,
        prefecture=row.prefecture,
        address=row.address,
        detail_fetched=row.detail_fetched,
        feature_codes=feature_codes,
    )


def load_property_views(
    conn: Connection,
    *,
    property_ids: list[int] | None = None,
    property_type_code: str | None = None,
    site_codes: list[str] | None = None,
    active_only: bool = True,
) -> dict[int, PropertyView]:
    """採点に必要な物件ビューをまとめて読み出す。

    設備は1クエリでまとめて引いてから物件ごとに畳む（物件ごとに引くと
    数千件で往復が効いてくる）。
    """
    where = ["TRUE"]
    params: dict[str, Any] = {}
    if property_ids is not None:
        if not property_ids:
            return {}
        where.append("p.id = ANY(:property_ids)")
        params["property_ids"] = property_ids
    if property_type_code:
        where.append("pt.code = :property_type_code")
        params["property_type_code"] = property_type_code
    if site_codes:
        where.append("s.code = ANY(:site_codes)")
        params["site_codes"] = site_codes
    if active_only:
        where.append("p.status = 'active'")

    rows = conn.execute(
        text(
            f"SELECT {_PROPERTY_COLUMNS} FROM t_properties p "
            "JOIN m_sites s ON s.id = p.site_id "
            "JOIN m_property_types pt ON pt.id = p.property_type_id "
            f"WHERE {' AND '.join(where)}"
        ),
        params,
    ).all()
    if not rows:
        return {}

    ids = [row.id for row in rows]
    features: dict[int, set[str]] = {}
    for property_id, code in conn.execute(
        text(
            "SELECT f.property_id, c.code FROM t_property_features f "
            "JOIN m_conditions c ON c.id = f.condition_id "
            "WHERE f.property_id = ANY(:ids)"
        ),
        {"ids": ids},
    ):
        features.setdefault(property_id, set()).add(code)

    return {row.id: _to_view(row, frozenset(features.get(row.id, ()))) for row in rows}


def detail_queue(
    conn: Connection, *, site_id: int, limit: int, property_ids: list[int] | None = None
) -> list[tuple[int, str]]:
    """詳細ページ未取得の物件を取得キューとして引く。

    部分インデックス ``ix_t_properties_detail_pending`` がそのまま効く。
    """
    params: dict[str, Any] = {"site_id": site_id, "limit": limit}
    extra = ""
    if property_ids is not None:
        if not property_ids:
            return []
        extra = "AND id = ANY(:property_ids)"
        params["property_ids"] = property_ids
    rows = conn.execute(
        text(
            "SELECT id, url FROM t_properties "
            f"WHERE site_id = :site_id AND detail_fetched_at IS NULL AND status = 'active' {extra} "
            "ORDER BY first_seen_at DESC LIMIT :limit"
        ),
        params,
    ).all()
    return [(row.id, row.url) for row in rows]


def mark_status(conn: Connection, property_ids: list[int], status: str) -> None:
    """成約・掲載終了を記録する。"""
    if not property_ids:
        return
    conn.execute(
        text(
            "UPDATE t_properties SET status = :status, updated_at = now() "
            "WHERE id = ANY(:property_ids)"
        ),
        {"status": status, "property_ids": property_ids},
    )


def log(
    conn: Connection,
    *,
    run_id: Any,
    level: str,
    message: str,
    site_code: str | None = None,
    pattern_name: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """実行ログを1行書く。"""
    conn.execute(
        text(
            "INSERT INTO t_scrape_logs (run_id, level, site_code, pattern_name, message, "
            "detail, created_at) "
            "VALUES (:run_id, :level, :site_code, :pattern_name, :message, "
            "CAST(:detail AS jsonb), now())"
        ),
        {
            "run_id": run_id,
            "level": level,
            "site_code": site_code,
            "pattern_name": pattern_name,
            "message": message,
            "detail": json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
        },
    )


def start_run(
    conn: Connection, *, run_id: Any, mode: str, pattern_name: str | None, site_id: int | None
) -> int:
    """実行チェックポイントを開始する。"""
    return conn.execute(
        text(
            "INSERT INTO t_scrape_runs (run_id, mode, pattern_name, site_id, status, "
            "started_at, created_at, updated_at) "
            "VALUES (:run_id, :mode, :pattern_name, :site_id, 'running', now(), now(), now()) "
            "RETURNING id"
        ),
        {"run_id": run_id, "mode": mode, "pattern_name": pattern_name, "site_id": site_id},
    ).scalar_one()


def finish_run(
    conn: Connection,
    row_id: int,
    *,
    status: str,
    items_seen: int = 0,
    items_new: int = 0,
    items_failed: int = 0,
    phase: str | None = None,
    cursor: str | None = None,
) -> None:
    """実行チェックポイントを閉じる。"""
    conn.execute(
        text(
            "UPDATE t_scrape_runs SET status = :status, items_seen = :items_seen, "
            "items_new = :items_new, items_failed = :items_failed, phase = :phase, "
            "cursor = :cursor, finished_at = now(), updated_at = now() WHERE id = :row_id"
        ),
        {
            "row_id": row_id,
            "status": status,
            "items_seen": items_seen,
            "items_new": items_new,
            "items_failed": items_failed,
            "phase": phase,
            "cursor": cursor,
        },
    )


def record_digest(
    conn: Connection,
    *,
    pattern_name: str,
    digest_group: str | None,
    top_n: int,
    property_ids: list[int],
    status: str,
) -> None:
    """ダイジェスト送信履歴を追記する。"""
    conn.execute(
        text(
            "INSERT INTO t_ranking_digests (pattern_name, digest_group, top_n, property_ids, "
            "status, sent_at, created_at) "
            "VALUES (:pattern_name, :digest_group, :top_n, CAST(:property_ids AS jsonb), "
            ":status, now(), now())"
        ),
        {
            "pattern_name": pattern_name,
            "digest_group": digest_group,
            "top_n": top_n,
            "property_ids": json.dumps(property_ids),
            "status": status,
        },
    )


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
