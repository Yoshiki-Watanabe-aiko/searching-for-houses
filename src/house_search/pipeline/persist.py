"""スクレイプ結果のDB永続化。

upsert はすべて冪等にしてある。中断・再開は「``detail_fetched_at IS NULL``
の物件を詳細取得キューとして引く」というSQLの自然な帰結になる。

``updated_at`` を明示的にセットしているのは、``ON CONFLICT DO UPDATE`` では
SQLAlchemy の ``onupdate`` が発火しないため（db/base.py の注記）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

from house_search.scoring.listing_view import ListingView
from house_search.scrape.base import ScrapedDetail, ScrapedListing

# 通知種別。
NEW = "new"
SOLD = "sold"
PRICE_UP = "price_up"
PRICE_DOWN = "price_down"
CHEAPER_LISTING = "cheaper_listing"


@dataclass(frozen=True, slots=True)
class UpsertOutcome:
    """1掲載ぶんの upsert 結果。通知の要否判定に使う。"""

    listing_id: int
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


@dataclass(frozen=True, slots=True)
class CityIndex:
    """住所から市区町村IDを引くための索引。``scan`` の開始時に1度だけ組む。

    ``rows`` は全国分。都道府県を前置した住所はこちらで照合する（検索対象外の
    県の掲載がサイトから返ってくることは普通にあり、落とす理由がない）。

    ``scoped_rows`` は検索パターンが対象にしている都道府県だけに絞ったもので、
    **都道府県を前置しない住所**（賃貸EX は「足立区竹の塚６」と書く）の
    フォールバック照合に使う。マスタを全国化すると全国で一意な市区名は
    1,861/1,918 まで減り、「府中市」（東京都/広島県）のように衝突が生まれる。
    パターンの検索範囲へ絞れば、その中では一意なので引き当てられる。
    """

    rows: tuple[tuple[str, str, int], ...]
    scoped_rows: tuple[tuple[str, str, int], ...]
    unique_names: frozenset[str]

    @classmethod
    def build(
        cls,
        rows: Sequence[tuple[str, str, int]],
        *,
        search_prefectures: Sequence[str] | None = None,
    ) -> CityIndex:
        """索引を組む。``rows`` は正規名の長い順に並んでいること。

        「横浜市西区」と「西区」のように短い名前が先に当たると誤判定するため、
        並び順は呼び出し側（``load_city_index``）の責務にしてある。
        """
        allowed = frozenset(search_prefectures) if search_prefectures else None
        scoped = tuple(row for row in rows if allowed is None or row[0] in allowed)
        return cls(
            rows=tuple(rows),
            scoped_rows=scoped,
            unique_names=_unique_city_names(scoped),
        )

    def scoped_to(self, prefectures: Sequence[str] | None) -> CityIndex:
        """検索範囲の都道府県へ絞り直した索引を返す。

        ``Runtime`` は実行ごとに1つだが検索パターンは複数あり、対象の
        都道府県はパターンごとに違う。全国分を読み直さずに絞れるようにする。
        """
        return CityIndex.build(self.rows, search_prefectures=prefectures)


def load_city_index(
    conn: Connection, *, search_prefectures: Sequence[str] | None = None
) -> CityIndex:
    """``m_cities`` を読んで索引を組む。

    ``(都道府県, 正規名, city_id)`` を正規名の長い順に並べる。
    """
    rows = conn.execute(
        text(
            "SELECT prefecture, canonical_name, id FROM m_cities "
            "ORDER BY length(canonical_name) DESC"
        )
    ).all()
    return CityIndex.build(
        [(pref, name, city_id) for pref, name, city_id in rows],
        search_prefectures=search_prefectures,
    )


def normalize_city_key(text: str) -> str:
    """市区名を照合するためのキー。小書き仮名の表記ゆれを吸収する。

    ``m_cities`` は「鎌ケ谷市」（大書き）だが、サイトの住所は「鎌ヶ谷市」
    （小書き）で来ることがある。NFKC 正規化ではこの2文字は区別されるため、
    そのまま照合すると **city_id が NULL のまま残る**。実測（2026-09-02）で
    SUUMO の新規485件中34件がこれで落ちていた。
    """
    return text.replace("ヶ", "ケ").replace("ヵ", "カ").replace("之", "ノ")


def resolve_city(address: str | None, index: CityIndex) -> tuple[str | None, int | None]:
    """住所から都道府県名と市区町村IDを解決する。

    都道府県から始まらない住所（賃貸EX は「足立区竹の塚６」と書く）にも効くよう、
    前置の都道府県が無いときは**検索対象の都道府県の中で一意な市区名**を
    引き当てる。「北区」「西区」のように範囲内に複数ある名前は取り違えるので
    解決しない。
    """
    if not address:
        return None, None
    key = normalize_city_key(address)
    for prefecture, canonical, city_id in index.rows:
        if key.startswith(prefecture) and normalize_city_key(canonical) in key:
            return prefecture, city_id
    for prefecture, _canonical, _city_id in index.rows:
        if key.startswith(prefecture):
            return prefecture, None

    for prefecture, canonical, city_id in index.scoped_rows:
        normalized = normalize_city_key(canonical)
        if normalized in index.unique_names and normalized in key:
            return prefecture, city_id
    return None, None


def _unique_city_names(rows: Sequence[tuple[str, str, int]]) -> frozenset[str]:
    """与えた範囲の中で1つしか存在しない市区町村名の集合（照合キーで数える）。"""
    counts: dict[str, int] = {}
    for _prefecture, canonical, _city_id in rows:
        key = normalize_city_key(canonical)
        counts[key] = counts.get(key, 0) + 1
    return frozenset(name for name, count in counts.items() if count == 1)


_SELECT_EXISTING = text(
    "SELECT external_id, id, price, status FROM t_listings "
    "WHERE site_id = :site_id AND external_id = ANY(:external_ids)"
)

_UPSERT = text(
    """
    INSERT INTO t_listings (
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
        title = COALESCE(EXCLUDED.title, t_listings.title),
        price = EXCLUDED.price,
        price_prev = EXCLUDED.price_prev,
        mgmt_fee_monthly = EXCLUDED.mgmt_fee_monthly,
        deposit_amount = EXCLUDED.deposit_amount,
        key_money_amount = EXCLUDED.key_money_amount,
        area_sqm = COALESCE(EXCLUDED.area_sqm, t_listings.area_sqm),
        layout = COALESCE(EXCLUDED.layout, t_listings.layout),
        floor_num = COALESCE(EXCLUDED.floor_num, t_listings.floor_num),
        total_floors = COALESCE(EXCLUDED.total_floors, t_listings.total_floors),
        age_years = COALESCE(EXCLUDED.age_years, t_listings.age_years),
        address = COALESCE(EXCLUDED.address, t_listings.address),
        prefecture = COALESCE(EXCLUDED.prefecture, t_listings.prefecture),
        city_id = COALESCE(EXCLUDED.city_id, t_listings.city_id),
        station_info = COALESCE(EXCLUDED.station_info, t_listings.station_info),
        walk_minutes = COALESCE(EXCLUDED.walk_minutes, t_listings.walk_minutes),
        image_url = COALESCE(EXCLUDED.image_url, t_listings.image_url),
        status = 'active',
        last_seen_at = now(),
        updated_at = now()
    RETURNING id
    """
)


def site_listing_count(conn: Connection, site_id: int) -> int:
    """そのサイトでこれまでに取り込んだ掲載数。

    「一覧0件」が異常かどうかの判断に使う。過去に1件も取れていないサイトなら
    0件は正常でありうるが、実績があるサイトの0件は取得が壊れた疑いが濃い。
    """
    return int(
        conn.execute(
            text("SELECT count(*) FROM t_listings WHERE site_id = :site_id"),
            {"site_id": site_id},
        ).scalar()
        or 0
    )


@dataclass(frozen=True, slots=True)
class RotationClaim:
    """市区ローテーションの権利（→ 課題#36・Phase 5E）。"""

    claimed: bool
    last_city_jis: str | None = None


def claim_city_rotation(
    conn: Connection, *, site_id: int, pattern_name: str, run_id: Any
) -> RotationClaim:
    """このパターンが今回の実行でそのサイトの取得枠を使ってよいかを決める。

    ⚠ **帯が2つあるので、素朴に実装すると予算が2倍消費される。** HOMES は
    両帯の ``sites:`` に載っており、1回の ``scan`` で 5+5=10 リクエストが飛ぶと
    後半の帯は全部 HTTP 202 になる。**1回の実行では1帯だけ**が枠を使う。

    どの帯が使うかは ``last_scanned_at`` の古い順（未実行が最優先）。
    同一実行での二重消費は ``last_run_id`` で防ぐ。
    """
    conn.execute(
        text(
            """
            INSERT INTO t_site_scan_cursors (site_id, pattern_name)
            VALUES (:site_id, :pattern_name)
            ON CONFLICT (site_id, pattern_name) DO NOTHING
            """
        ),
        {"site_id": site_id, "pattern_name": pattern_name},
    )
    # 同じ実行で既に別の（あるいは同じ）パターンが枠を使っていたら譲る
    already = conn.execute(
        text(
            "SELECT 1 FROM t_site_scan_cursors "
            "WHERE site_id = :site_id AND last_run_id = :run_id LIMIT 1"
        ),
        {"site_id": site_id, "run_id": run_id},
    ).first()
    if already is not None:
        return RotationClaim(claimed=False)

    row = conn.execute(
        text(
            """
            SELECT pattern_name, last_city_jis
            FROM t_site_scan_cursors
            WHERE site_id = :site_id
            -- 未実行（NULL）を最優先。同着はパターン名で決定的に決める
            ORDER BY last_scanned_at ASC NULLS FIRST, pattern_name ASC
            LIMIT 1
            """
        ),
        {"site_id": site_id},
    ).first()
    if row is None or row.pattern_name != pattern_name:
        return RotationClaim(claimed=False)

    conn.execute(
        text(
            "UPDATE t_site_scan_cursors "
            "SET last_scanned_at = now(), last_run_id = :run_id, updated_at = now() "
            "WHERE site_id = :site_id AND pattern_name = :pattern_name"
        ),
        {"site_id": site_id, "pattern_name": pattern_name, "run_id": run_id},
    )
    return RotationClaim(claimed=True, last_city_jis=row.last_city_jis)


def advance_city_rotation(
    conn: Connection, *, site_id: int, pattern_name: str, last_city_jis: str | None
) -> None:
    """次回の開始位置を進める。

    ⚠ **取得を試みる前に進める。** 取得が失敗（スロットリング・ボット検知）しても
    同じ市区を再試行し続けると、その市区から先へ永久に進めなくなる。
    """
    conn.execute(
        text(
            "UPDATE t_site_scan_cursors "
            "SET last_city_jis = :jis, updated_at = now() "
            "WHERE site_id = :site_id AND pattern_name = :pattern_name"
        ),
        {"site_id": site_id, "pattern_name": pattern_name, "jis": last_city_jis},
    )


def upsert_listings(
    conn: Connection,
    listings: list[ScrapedListing],
    *,
    site_id: int,
    property_type_id: int,
    city_index: CityIndex,
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
                listing_id=row_id,
                external_id=listing.external_id,
                is_new=is_new,
                is_reinstated=is_reinstated,
                price_event=price_event,
                price_prev=old_price,
            )
        )
    return outcomes


def save_detail(conn: Connection, listing_id: int, detail: ScrapedDetail) -> None:
    """詳細ページ由来の情報を書き戻し、詳細取得済みにする。"""
    conn.execute(
        text(
            """
            UPDATE t_listings SET
                raw_features_text = COALESCE(:raw_features_text, raw_features_text),
                built_on = COALESCE(:built_on, built_on),
                age_years = COALESCE(:age_years, age_years),
                floor_num = COALESCE(:floor_num, floor_num),
                total_floors = COALESCE(:total_floors, total_floors),
                mgmt_fee_monthly = COALESCE(:mgmt_fee_monthly, mgmt_fee_monthly),
                deposit_amount = COALESCE(:deposit_amount, deposit_amount),
                key_money_amount = COALESCE(:key_money_amount, key_money_amount),
                address = COALESCE(:address, address),
                walk_minutes = COALESCE(:walk_minutes, walk_minutes),
                type_specific_attrs = COALESCE(
                    t_listings.type_specific_attrs, '{}'::jsonb
                ) || CAST(:type_specific_attrs AS jsonb),
                detail_fetched_at = now(),
                updated_at = now()
            WHERE id = :listing_id
            """
        ),
        {
            "listing_id": listing_id,
            "raw_features_text": detail.raw_features_text,
            "built_on": detail.built_on,
            "age_years": detail.age_years,
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
    listing_id: int,
    features: tuple,
    condition_ids: dict[str, int],
) -> int:
    """抽出結果を保存する。

    再抽出できるよう、その物件の既存行をいったん消してから入れ直す
    （辞書から外れた条件が残らないようにする）。
    """
    conn.execute(
        text("DELETE FROM t_listing_features WHERE listing_id = :listing_id"),
        {"listing_id": listing_id},
    )
    rows = [
        {
            "listing_id": listing_id,
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
                "INSERT INTO t_listing_features "
                "(listing_id, condition_id, source, matched_text, extracted_at, "
                " created_at, updated_at) "
                "VALUES (:listing_id, :condition_id, :source, :matched_text, now(), now(), now())"
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
    listing_id: int,
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
            INSERT INTO t_listing_scores (
                listing_id, pattern_name, must_result, score, score_breakdown,
                config_hash, scored_at, created_at, updated_at
            ) VALUES (
                :listing_id, :pattern_name, :must_result, :score,
                CAST(:breakdown AS jsonb), :config_hash, now(), now(), now()
            )
            ON CONFLICT (listing_id, pattern_name) DO UPDATE SET
                must_result = EXCLUDED.must_result,
                score = EXCLUDED.score,
                score_breakdown = EXCLUDED.score_breakdown,
                config_hash = EXCLUDED.config_hash,
                scored_at = now(),
                updated_at = now()
            """
        ),
        {
            "listing_id": listing_id,
            "pattern_name": pattern_name,
            "must_result": must_result,
            "score": score,
            "breakdown": json.dumps(breakdown, ensure_ascii=False),
            "config_hash": config_hash,
        },
    )


def prune_scores(conn: Connection, pattern_name: str, keep_ids: list[int]) -> int:
    """そのパターンの採点対象から外れた掲載のスコア行を消す。

    ``save_score`` は upsert なので、対象外になった掲載の行は放っておくと
    残り続ける。エリア帯を絞ったり市区の解決が直ったりすると、
    **かつて採点した掲載が両方の帯に残って二重採点になる**
    （2026-09-02 実測で93件。両帯のランキング1位が同じ掲載になった）。
    """
    result = conn.execute(
        text(
            "DELETE FROM t_listing_scores "
            "WHERE pattern_name = :pattern_name AND NOT (listing_id = ANY(:keep_ids))"
        ),
        {"pattern_name": pattern_name, "keep_ids": keep_ids or [0]},
    )
    return result.rowcount or 0


def update_ranks(conn: Connection, pattern_name: str) -> int:
    """パターン内のスコア降順順位を振り直す。

    同点は物件IDの昇順で決めて、実行ごとに順位が揺れないようにする。

    **順位が付くのはグループ代表と未グループ物件だけ。** 非代表メンバーの
    ``rank_in_pattern`` は NULL に落とす。``digest`` は ``rank_in_pattern`` を
    起点に引くので、この一手だけでランキングがグループ単位になる。
    """
    # いったん全て外してから振り直す。代表が交代したときに古い順位が
    # 残らないようにするため（部分更新だと非代表に順位が残る）。
    conn.execute(
        text(
            "UPDATE t_listing_scores SET rank_in_pattern = NULL, updated_at = now() "
            "WHERE pattern_name = :pattern_name AND rank_in_pattern IS NOT NULL"
        ),
        {"pattern_name": pattern_name},
    )
    result = conn.execute(
        text(
            """
            UPDATE t_listing_scores s SET rank_in_pattern = r.rn, updated_at = now()
            FROM (
                SELECT sc.id,
                       ROW_NUMBER() OVER (ORDER BY sc.score DESC, sc.listing_id ASC) AS rn
                FROM t_listing_scores sc
                JOIN t_listings p ON p.id = sc.listing_id
                LEFT JOIN t_listing_groups g ON g.id = p.group_id
                WHERE sc.pattern_name = :pattern_name AND sc.must_result <> 'fail'
                  AND sc.score IS NOT NULL
                  AND (p.group_id IS NULL OR g.representative_listing_id = p.id)
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
    listing_id: int,
    pattern_name: str,
    notification_type: str,
    price_at_notify: int | None,
    score_at_notify: float | None,
    status: str,
    group_id: int | None = None,
) -> None:
    """通知履歴を追記する。"""
    conn.execute(
        text(
            "INSERT INTO t_notifications ("
            " listing_id, group_id, pattern_name, notification_type, price_at_notify,"
            " score_at_notify, status, notified_at, created_at) "
            "VALUES (:listing_id, :group_id, :pattern_name, :notification_type,"
            " :price_at_notify, :score_at_notify, :status, now(), now())"
        ),
        {
            "listing_id": listing_id,
            "group_id": group_id,
            "pattern_name": pattern_name,
            "notification_type": notification_type,
            "price_at_notify": price_at_notify,
            "score_at_notify": score_at_notify,
            "status": status,
        },
    )


# 通知済み判定はグループ単位で行う。履歴テーブルは追記専用のままにしたいので、
# 過去行の group_id を書き換えるのではなく **現在の所属を JOIN で見る**。
# こうするとグループ構成が後から変わっても、常に「いまの構成」で判定される。
_ALREADY_NOTIFIED = text(
    """
    SELECT 1 FROM t_notifications n
    JOIN t_listings p ON p.id = n.listing_id
    WHERE n.pattern_name = :pattern_name
      AND n.notification_type = :notification_type
      AND n.status = 'sent'
      AND (n.listing_id = :listing_id
           OR (CAST(:group_id AS bigint) IS NOT NULL AND p.group_id = :group_id))
    LIMIT 1
    """
)


def already_notified(
    conn: Connection,
    *,
    listing_id: int,
    pattern_name: str,
    notification_type: str,
    group_id: int | None = None,
) -> bool:
    """同じ物件（または同じグループ）・同じ種別の通知を既に送っていないか。

    ``group_id`` を渡すと、同一住戸の別サイト掲載が新着として二重通知されない。
    """
    return bool(
        conn.execute(
            _ALREADY_NOTIFIED,
            {
                "listing_id": listing_id,
                "group_id": group_id,
                "pattern_name": pattern_name,
                "notification_type": notification_type,
            },
        ).first()
    )


def cheaper_listing_notified_at(
    conn: Connection, *, group_id: int, pattern_name: str, price: int | None
) -> bool:
    """同じグループで同じ金額の「他サイト安値掲載」を既に送っていないか。

    金額まで見るのは、**さらに安い掲載が出たときは再通知したい**ため。
    同額の再検出（グループの作り直しなど）では送らない。
    """
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM t_notifications "
                "WHERE pattern_name = :pattern_name AND group_id = :group_id "
                "AND notification_type = 'cheaper_listing' AND status = 'sent' "
                "AND price_at_notify IS NOT DISTINCT FROM :price LIMIT 1"
            ),
            {"group_id": group_id, "pattern_name": pattern_name, "price": price},
        ).first()
    )


_PROPERTY_COLUMNS = """
    p.id, s.code AS site_code, p.url, p.title, p.price, p.price_prev,
    p.mgmt_fee_monthly, p.rent_total, p.repair_reserve_monthly,
    p.area_sqm, p.land_area_sqm, p.building_area_sqm, p.layout,
    p.floor_num, p.total_floors, p.age_years, p.walk_minutes,
    p.prefecture, p.address, p.image_url, pt.family AS property_family,
    (
        p.detail_fetched_at IS NOT NULL
        OR EXISTS (
            SELECT 1 FROM t_listings m
            WHERE p.group_id IS NOT NULL AND m.group_id = p.group_id
              AND m.detail_fetched_at IS NOT NULL
        )
    ) AS detail_fetched,
    (
        SELECT min(sc.commute_minutes)
        FROM t_listings member
        JOIN t_listing_stations ls ON ls.listing_id = member.id
        JOIN t_station_commutes sc
          ON sc.origin_station_g_cd = ls.station_g_cd
         AND sc.destination_station_g_cd = :commute_destination
        WHERE (
            (p.group_id IS NULL AND member.id = p.id)
            OR (p.group_id IS NOT NULL AND member.group_id = p.group_id)
        )
          AND sc.status = 'ok'
    ) AS commute_minutes,
    hz.flood_rank_avg, hz.flood_rank_max, hz.flood_area_ratio,
    hz.landslide_area_ratio, hz.landslide_special_ratio,
    (
        -- 相場との比較（→ 課題#49）。同じ市区・同じ間取りの相場と比べる。
        -- ⚠ **最新の period を1つだけ採る。** m_market_rates は履歴を残す設計
        -- （period が違えば別の行）なので、絞らないと古い相場と混ざる。
        -- ⚠ 相場が無いセルは NULL＝未解決。metric は欠損として再正規化される
        -- （0 にすると「相場ちょうど」と区別がつかなくなる）。
        SELECT p.rent_total::numeric / mr.rate_value
        FROM m_market_rates mr
        WHERE mr.family = pt.family
          AND mr.level = 'city'
          AND mr.city_id = p.city_id
          AND mr.segment = p.layout
        ORDER BY mr.period DESC
        LIMIT 1
    ) AS market_rate_ratio
"""


# ハザード評価は住所（address_normalized）から m_hazard_levels を引く（→ 課題#46）。
# ⚠ **2段の LATERAL にしてある。** 値ごとにスカラーサブクエリを5本並べると、
# 同じ「どのキーで引くか」の解決を5回繰り返すことになる。
#
# 1段目 hzk: グループ内で**最も細かい住所**を持つ掲載のキーを1つ選ぶ。
#   設備の和集合・通勤時間の最短と同じく、名寄せしたグループ全体から
#   情報が最大になるものを採る（サイトによって住所の粒度が違うため）。
#   ⚠ 丁目で引けなければ m_address_points.town_key 経由で町の評価へ落とす。
#   SUUMO・ハウスコムは住所が町名までしか無いので、これが無いと
#   そのサイトの掲載が丸ごと未解決になる。
#   ⚠ **町へ落とすのに SQL の文字列操作で丁目を削らない。** 住所マスタが持つ
#   物理列 town_key を引く（「N丁目」を正規表現で剥がす推測は、丁目の無い町で
#   番地を削るなどして黙って別の町を指す → ADR 0020 と同じ失敗）。
#   ⚠ ORDER BY で chome を優先し、同着は member.id で決める（順位の決定性）。
#
# 2段目 hz: 決まった (key, level) の9行（3種別×3方式）を横に畳む。
#   ⚠ 集約なので該当行が無くても1行（全 NULL）を返す。NULL は「未解決」の意味で、
#   「区域外」は value=0 の明示行として入っている（両者を混ぜない）。
_HAZARD_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT h.normalized_key AS key, h.level AS level
        FROM t_listings member
        LEFT JOIN m_address_points ap ON ap.normalized_key = member.address_normalized
        JOIN m_hazard_levels h
          ON h.normalized_key IN (member.address_normalized, ap.town_key)
        WHERE (
            (p.group_id IS NULL AND member.id = p.id)
            OR (p.group_id IS NOT NULL AND member.group_id = p.group_id)
        )
        ORDER BY (h.level = 'chome') DESC, member.id
        LIMIT 1
    ) hzk ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            max(h.value) FILTER (
                WHERE h.hazard_type = 'flood' AND h.aggregation = 'rank_avg'
            ) AS flood_rank_avg,
            max(h.value) FILTER (
                WHERE h.hazard_type = 'flood' AND h.aggregation = 'rank_max'
            ) AS flood_rank_max,
            max(h.value) FILTER (
                WHERE h.hazard_type = 'flood' AND h.aggregation = 'area_ratio'
            ) AS flood_area_ratio,
            max(h.value) FILTER (
                WHERE h.hazard_type = 'landslide' AND h.aggregation = 'area_ratio'
            ) AS landslide_area_ratio,
            max(h.value) FILTER (
                WHERE h.hazard_type = 'landslide_special' AND h.aggregation = 'area_ratio'
            ) AS landslide_special_ratio
        FROM m_hazard_levels h
        WHERE h.normalized_key = hzk.key AND h.level = hzk.level
    ) hz ON TRUE
"""


# 設備はグループ内の和集合で引く。サイトAでしか判らない設備とサイトBでしか
# 判らない設備をマージしないと、名寄せで代表を1件に絞った時点で情報が減る
# （再設計計画 §6「スコアはグループ内の抽出情報の和集合で計算」）。
_GROUP_FEATURES = text(
    """
    SELECT target.id AS listing_id, c.code
    FROM t_listings target
    JOIN t_listings member
      ON (target.group_id IS NULL AND member.id = target.id)
      OR (target.group_id IS NOT NULL AND member.group_id = target.group_id)
    JOIN t_listing_features f ON f.listing_id = member.id
    JOIN m_conditions c ON c.id = f.condition_id
    WHERE target.id = ANY(:ids)
    GROUP BY target.id, c.code
    """
)


def _opt_float(value: Any) -> float | None:
    """NUMERIC 列（Decimal）を float へ。⚠ None はそのまま None を返す。

    ハザード評価では **0.0（区域外だと確認した）と None（未解決）の区別**が
    そのまま採点の意味になるので、``float(value or 0)`` のような書き方をしない。
    """
    return None if value is None else float(value)


def _to_view(row: Any, feature_codes: frozenset[str]) -> ListingView:
    return ListingView(
        listing_id=row.id,
        site_code=row.site_code,
        url=row.url,
        title=row.title,
        price=row.price,
        mgmt_fee_monthly=row.mgmt_fee_monthly,
        rent_total=row.rent_total,
        repair_reserve_monthly=row.repair_reserve_monthly,
        area_sqm=_opt_float(row.area_sqm),
        land_area_sqm=_opt_float(row.land_area_sqm),
        building_area_sqm=_opt_float(row.building_area_sqm),
        layout=row.layout,
        floor_num=row.floor_num,
        total_floors=row.total_floors,
        age_years=row.age_years,
        walk_minutes=row.walk_minutes,
        commute_minutes=row.commute_minutes,
        market_rate_ratio=_opt_float(row.market_rate_ratio),
        flood_rank_avg=_opt_float(row.flood_rank_avg),
        flood_rank_max=_opt_float(row.flood_rank_max),
        flood_area_ratio=_opt_float(row.flood_area_ratio),
        landslide_area_ratio=_opt_float(row.landslide_area_ratio),
        landslide_special_ratio=_opt_float(row.landslide_special_ratio),
        property_family=row.property_family,
        prefecture=row.prefecture,
        address=row.address,
        detail_fetched=row.detail_fetched,
        feature_codes=feature_codes,
    )


def load_listing_views(
    conn: Connection,
    *,
    listing_ids: list[int] | None = None,
    property_type_code: str | None = None,
    site_codes: list[str] | None = None,
    city_names: list[str] | None = None,
    commute_destination_g_cd: int | None = None,
    active_only: bool = True,
) -> dict[int, ListingView]:
    """採点に必要な物件ビューをまとめて読み出す。

    設備は1クエリでまとめて引いてから物件ごとに畳む（物件ごとに引くと
    数千件で往復が効いてくる）。

    ``commute_destination_g_cd`` は勤務先の最寄り駅（駅グループコード）。
    通勤時間は**グループ内の最短**を採る。設備と同じく、サイトによって挙げる駅が
    違うため、名寄せしたグループ全体から拾わないと情報が減る。

    ``city_names`` は検索パターンのエリア帯。**採点範囲を帯に閉じるために要る。**
    エリア帯は取得URLを絞るだけなので、これが無いとDBに残っている帯外の掲載
    （帯を変える前に取ったもの）にも帯のスコアが付き、23区のランキングが
    群馬県境の掲載で埋まる。
    """
    where = ["TRUE"]
    # 目的地が未設定なら通勤時間のサブクエリは常に NULL を返す（照合が成立しない）。
    # 分岐でSQLを組み替えるより、値だけ差し替えるほうが経路が1本で済む。
    params: dict[str, Any] = {"commute_destination": commute_destination_g_cd}
    if listing_ids is not None:
        if not listing_ids:
            return {}
        where.append("p.id = ANY(:listing_ids)")
        params["listing_ids"] = listing_ids
    if property_type_code:
        where.append("pt.code = :property_type_code")
        params["property_type_code"] = property_type_code
    if site_codes:
        where.append("s.code = ANY(:site_codes)")
        params["site_codes"] = site_codes
    if city_names:
        # 市区を解決できなかった掲載は**どの帯にも属さない**ので採点しない。
        # 通したことがあるが、帯1と帯2の双方に入って93件が二重採点され、
        # 両帯のランキング1位が同じ掲載になった（2026-09-02 実測）。
        # 取りこぼしは resolve_city の表記ゆれ吸収で潰す方が筋がよい
        where.append(
            "EXISTS ("
            "  SELECT 1 FROM m_cities c"
            "  WHERE c.id = p.city_id AND c.canonical_name = ANY(:city_names)"
            ")"
        )
        params["city_names"] = city_names
    if active_only:
        where.append("p.status = 'active'")

    rows = conn.execute(
        text(
            f"SELECT {_PROPERTY_COLUMNS} FROM t_listings p "
            "JOIN m_sites s ON s.id = p.site_id "
            "JOIN m_property_types pt ON pt.id = p.property_type_id "
            f"{_HAZARD_LATERAL} "
            f"WHERE {' AND '.join(where)}"
        ),
        params,
    ).all()
    if not rows:
        return {}

    ids = [row.id for row in rows]
    features: dict[int, set[str]] = {}
    for listing_id, code in conn.execute(_GROUP_FEATURES, {"ids": ids}):
        features.setdefault(listing_id, set()).add(code)

    return {row.id: _to_view(row, frozenset(features.get(row.id, ()))) for row in rows}


def detail_queue(
    conn: Connection,
    *,
    site_id: int,
    limit: int,
    oldest_limit: int = 0,
    listing_ids: list[int] | None = None,
) -> list[tuple[int, str]]:
    """詳細ページ未取得の物件を取得キューとして引く。

    2つの母集団の**和集合**を ``limit`` 件まで返す（→ 課題#54）。

    1. ``first_seen_at`` が**古い順**に ``oldest_limit`` 件（滞留を必ず削る）
    2. 残りの枠を**新しい順**で埋める（新着の詳細を遅らせない）

    ⚠ **1が無いと古い掲載に枠が永久に回らない。** 新規流入が上限（既定40件）に
    近いと、新しい順だけでは古い掲載まで届かない。実測（2026-09-06）で SUUMO の
    詳細未取得 1,342件が 09-03・09-04 のまま滞留し、**設備0件**（取得済みは
    平均18.9件）のまま採点されていた。設備の weight は263点中118点なので、
    これらは構造的に45%ぶん沈む。
    ⚠ **例外にならず件数も減らない**（順位が付いてしまうので気づけない）。
    近郊60分圏帯では詳細未取得の掲載が**4位**＝ダイジェストに載っていた。

    ⚠ ``oldest_limit=0`` で1を無効にできる（従来の挙動へ戻す逃げ道）。

    部分インデックス ``ix_t_listings_detail_pending`` は ``pending`` にそのまま効く。
    """
    params: dict[str, Any] = {
        "site_id": site_id,
        "limit": limit,
        "oldest_limit": oldest_limit,
    }
    extra = ""
    if listing_ids is not None:
        if not listing_ids:
            return []
        extra = "AND id = ANY(:listing_ids)"
        params["listing_ids"] = listing_ids
    rows = conn.execute(
        text(
            "WITH pending AS ("
            "  SELECT id, url, first_seen_at FROM t_listings "
            "   WHERE site_id = :site_id AND detail_fetched_at IS NULL"
            f"     AND status = 'active' {extra}"
            "), oldest AS ("
            # LIMIT 0 なら空集合になるので、無効化のための WHERE は要らない
            "  SELECT id FROM pending ORDER BY first_seen_at ASC, id ASC LIMIT :oldest_limit"
            ") "
            "SELECT p.id, p.url FROM pending p "
            # 古い枠を先頭へ寄せてから limit で切る。順に並べてから切らないと
            # 「和集合を作ったのに古い側が落ちる」ことになる
            " ORDER BY (p.id IN (SELECT id FROM oldest)) DESC, p.first_seen_at DESC, p.id DESC"
            " LIMIT :limit"
        ),
        params,
    ).all()
    return [(row.id, row.url) for row in rows]


def mark_status(conn: Connection, listing_ids: list[int], status: str) -> None:
    """成約・掲載終了を記録する。"""
    if not listing_ids:
        return
    conn.execute(
        text(
            "UPDATE t_listings SET status = :status, updated_at = now() "
            "WHERE id = ANY(:listing_ids)"
        ),
        {"status": status, "listing_ids": listing_ids},
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
    listing_ids: list[int],
    status: str,
) -> None:
    """ダイジェスト送信履歴を追記する。"""
    conn.execute(
        text(
            "INSERT INTO t_ranking_digests (pattern_name, digest_group, top_n, listing_ids, "
            "status, sent_at, created_at) "
            "VALUES (:pattern_name, :digest_group, :top_n, CAST(:listing_ids AS jsonb), "
            ":status, now(), now())"
        ),
        {
            "pattern_name": pattern_name,
            "digest_group": digest_group,
            "top_n": top_n,
            "listing_ids": json.dumps(listing_ids),
            "status": status,
        },
    )


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
