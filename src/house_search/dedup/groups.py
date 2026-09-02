"""名寄せグループのDB同期。

差分管理をせず、**全体を冪等な集合演算として作り直す**方針にしてある。
掲載の消失・代表の交代・住所の後追い補正はすべて「次の同期で自然に直る」形になり、
イベント駆動の張り替えを書かずに済む。

``scan`` の採点前と ``check-sold`` の後に ``sync_groups`` を呼べば、
「代表が成約した → 次の実行で自動的に再選定」が集合演算の帰結として成立する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

from house_search.dedup.address import address_granularity, normalize_address
from house_search.dedup.key import compute_dedup_key


@dataclass(frozen=True, slots=True)
class GroupChange:
    """代表が交代したグループ。``cheaper_listing`` 通知の入力になる。"""

    group_id: int
    dedup_key: str
    previous_listing_id: int | None
    previous_cost: int | None
    current_listing_id: int | None
    current_cost: int | None
    member_count: int

    @property
    def is_cheaper(self) -> bool:
        """新しい代表のほうが安いか。

        比較は ``rent_total``（賃料＋管理費）で行う。管理費の計上方法が
        サイトによって違うため、賃料だけで比べると実支出と逆転する。
        同額では交代扱いにしない（狭義の不等号）。
        """
        if self.previous_cost is None or self.current_cost is None:
            return False
        return self.current_cost < self.previous_cost


@dataclass(frozen=True, slots=True)
class SiteDedupStats:
    """サイト1件ぶんの名寄せ実測（``dedup-stats`` の出力）。"""

    site_code: str
    listings: int
    with_key: int
    grouped: int
    shared_with_other_sites: int
    representative: int
    granularity: dict[str, int]

    @property
    def key_rate(self) -> float:
        return self.with_key / self.listings if self.listings else 0.0

    @property
    def unique_rate(self) -> float:
        """他サイトに同一物件の掲載が無い割合。賃貸EX の採否判断に使う。"""
        if not self.listings:
            return 0.0
        return 1.0 - self.shared_with_other_sites / self.listings


_SELECT_FOR_KEY = """
    SELECT p.id, pt.family, p.address, p.prefecture, p.layout, p.area_sqm, p.floor_num,
           p.land_area_sqm, p.building_area_sqm, p.address_normalized, p.dedup_key
    FROM t_listings p
    JOIN m_property_types pt ON pt.id = p.property_type_id
"""


def refresh_dedup_keys(conn: Connection, listing_ids: list[int] | None = None) -> int:
    """``address_normalized`` と ``dedup_key`` を計算し直す。

    値が変わった行だけ UPDATE する。詳細取得で階数・住所が埋まるとキーが
    後から作られるため、**一覧の upsert 直後と詳細の保存後の双方で呼ぶ**必要がある。

    戻り値は更新した行数。
    """
    params: dict[str, Any] = {}
    sql = _SELECT_FOR_KEY
    if listing_ids is not None:
        if not listing_ids:
            return 0
        sql += " WHERE p.id = ANY(:listing_ids)"
        params["listing_ids"] = listing_ids

    updates: list[dict[str, Any]] = []
    for row in conn.execute(text(sql), params):
        normalized = normalize_address(row.address, row.prefecture)
        key = compute_dedup_key(
            family=row.family,
            address_normalized=normalized,
            layout=row.layout,
            area_sqm=row.area_sqm,
            floor_num=row.floor_num,
            land_area_sqm=row.land_area_sqm,
            building_area_sqm=row.building_area_sqm,
        )
        if normalized == row.address_normalized and key == row.dedup_key:
            continue
        updates.append({"listing_id": row.id, "address_normalized": normalized, "dedup_key": key})

    if updates:
        conn.execute(
            text(
                "UPDATE t_listings SET address_normalized = :address_normalized, "
                "dedup_key = :dedup_key, updated_at = now() WHERE id = :listing_id"
            ),
            updates,
        )
    return len(updates)


# 代表選定: 月額が最安 → 設備抽出数が最多 → サイト優先順 → 物件ID。
# 最後に物件IDを置いているのは、同点で実行ごとに代表が揺れないようにするため。
_RANK_REPRESENTATIVES = text(
    """
    WITH feature_counts AS (
        SELECT listing_id, count(*) AS n FROM t_listing_features GROUP BY listing_id
    ), ranked AS (
        SELECT p.group_id,
               p.id AS listing_id,
               COALESCE(p.rent_total, p.price) AS cost,
               ROW_NUMBER() OVER (
                   PARTITION BY p.group_id
                   ORDER BY COALESCE(p.rent_total, p.price) ASC NULLS LAST,
                            COALESCE(fc.n, 0) DESC,
                            s.representative_priority ASC,
                            p.id ASC
               ) AS rn
        FROM t_listings p
        JOIN m_sites s ON s.id = p.site_id
        LEFT JOIN feature_counts fc ON fc.listing_id = p.id
        WHERE p.group_id IS NOT NULL AND p.status = 'active'
    )
    SELECT group_id, listing_id, cost FROM ranked WHERE rn = 1
    """
)

_INSERT_GROUPS = text(
    """
    INSERT INTO t_listing_groups
        (dedup_key, property_type_id, member_count, created_at, updated_at)
    SELECT DISTINCT ON (p.dedup_key) p.dedup_key, p.property_type_id, 1, now(), now()
    FROM t_listings p
    WHERE p.dedup_key IS NOT NULL
    ORDER BY p.dedup_key, p.property_type_id
    ON CONFLICT (dedup_key) DO NOTHING
    """
)

_SELECT_BEFORE = text(
    """
    SELECT g.id, g.dedup_key, g.member_count, g.representative_listing_id,
           COALESCE(p.rent_total, p.price) AS cost
    FROM t_listing_groups g
    LEFT JOIN t_listings p ON p.id = g.representative_listing_id
    """
)


def sync_groups(conn: Connection) -> list[GroupChange]:
    """``dedup_key`` からグループを作り直し、代表を選び直す。

    戻り値は代表が交代したグループの一覧。``is_cheaper`` が立っているものが
    ``cheaper_listing`` 通知の候補になる。
    """
    # 1. 未登録のキーをグループとして起こす。
    #    同じファミリでも property_type は複数あり得る（新築M/中古M）ので
    #    DISTINCT ON の並び順を固定して決定的に選ぶ。
    conn.execute(_INSERT_GROUPS)

    # 2. 所属を張り替える。キーが消えた物件（住所が粗くなった等）は未グループへ戻す。
    conn.execute(
        text(
            "UPDATE t_listings p SET group_id = g.id, updated_at = now() "
            "FROM t_listing_groups g "
            "WHERE g.dedup_key = p.dedup_key AND p.group_id IS DISTINCT FROM g.id"
        )
    )
    conn.execute(
        text(
            "UPDATE t_listings SET group_id = NULL, updated_at = now() "
            "WHERE dedup_key IS NULL AND group_id IS NOT NULL"
        )
    )

    # 3. メンバーが居なくなったグループを消す。
    #    t_notifications.group_id は ON DELETE SET NULL なので履歴は壊れない。
    conn.execute(
        text(
            "DELETE FROM t_listing_groups g "
            "WHERE NOT EXISTS (SELECT 1 FROM t_listings p WHERE p.group_id = g.id)"
        )
    )

    # 4. 掲載件数。通知に「同一条件の掲載n件」と出すための値。
    conn.execute(
        text(
            "UPDATE t_listing_groups g SET member_count = c.n, updated_at = now() "
            "FROM (SELECT group_id, count(*) AS n FROM t_listings "
            "      WHERE group_id IS NOT NULL GROUP BY group_id) c "
            "WHERE g.id = c.group_id AND g.member_count IS DISTINCT FROM c.n"
        )
    )

    # 5. 代表選定。交代の検出のため、更新前の状態を先に読む。
    before = {
        row.id: (row.representative_listing_id, row.dedup_key, row.member_count, row.cost)
        for row in conn.execute(_SELECT_BEFORE)
    }
    chosen = {
        row.group_id: (row.listing_id, row.cost) for row in conn.execute(_RANK_REPRESENTATIVES)
    }

    changes: list[GroupChange] = []
    updates: list[dict[str, Any]] = []
    for group_id, (previous_id, dedup_key, member_count, previous_cost) in before.items():
        # 掲載中のメンバーが1件も無いグループは代表を空にする
        # （成約済みの物件を代表に据えたままにしない）。
        current_id, current_cost = chosen.get(group_id, (None, None))
        if current_id == previous_id:
            continue
        updates.append({"group_id": group_id, "representative_listing_id": current_id})
        changes.append(
            GroupChange(
                group_id=group_id,
                dedup_key=dedup_key,
                previous_listing_id=previous_id,
                previous_cost=previous_cost,
                current_listing_id=current_id,
                current_cost=current_cost,
                member_count=member_count,
            )
        )

    if updates:
        conn.execute(
            text(
                "UPDATE t_listing_groups SET representative_listing_id = "
                ":representative_listing_id, updated_at = now() WHERE id = :group_id"
            ),
            updates,
        )
    return changes


@dataclass(frozen=True, slots=True)
class GroupMembership:
    """物件1件から見たグループの所属状況。通知とダイジェストの表示に使う。"""

    group_id: int | None
    member_count: int
    representative_listing_id: int | None
    other_site_codes: tuple[str, ...]

    @property
    def is_representative_of(self) -> bool:
        return self.group_id is not None and self.representative_listing_id is not None


_MEMBERSHIP = text(
    """
    SELECT target.id AS listing_id,
           target.group_id,
           g.member_count,
           g.representative_listing_id,
           s.code AS site_code
    FROM t_listings target
    JOIN t_listing_groups g ON g.id = target.group_id
    JOIN t_listings member ON member.group_id = target.group_id
    JOIN m_sites s ON s.id = member.site_id
    WHERE target.id = ANY(:ids) AND member.id <> target.id AND member.status = 'active'
    GROUP BY target.id, target.group_id, g.member_count, g.representative_listing_id, s.code
    ORDER BY target.id, s.code
    """
)

# 他サイト掲載が無い（＝単独掲載か1サイト内の重複だけ）グループも拾う必要がある。
# 上のクエリは他メンバーが居ないと1行も返さないため、所属だけを別に引く。
_MEMBERSHIP_BASE = text(
    """
    SELECT p.id AS listing_id, p.group_id, g.member_count, g.representative_listing_id
    FROM t_listings p
    JOIN t_listing_groups g ON g.id = p.group_id
    WHERE p.id = ANY(:ids)
    """
)

NO_GROUP = GroupMembership(
    group_id=None, member_count=1, representative_listing_id=None, other_site_codes=()
)


def group_membership(conn: Connection, listing_ids: list[int]) -> dict[int, GroupMembership]:
    """物件IDごとのグループ所属を返す。未グループの物件も既定値で埋める。

    通知の重複抑制（同一住戸の別サイト掲載を新着として二重に送らない）と、
    ダイジェストの「ほかNサイト」表示の双方がこれを使う。
    """
    if not listing_ids:
        return {}
    result: dict[int, GroupMembership] = dict.fromkeys(listing_ids, NO_GROUP)
    for row in conn.execute(_MEMBERSHIP_BASE, {"ids": listing_ids}):
        result[row.listing_id] = GroupMembership(
            group_id=row.group_id,
            member_count=row.member_count,
            representative_listing_id=row.representative_listing_id,
            other_site_codes=(),
        )
    for row in conn.execute(_MEMBERSHIP, {"ids": listing_ids}):
        current = result[row.listing_id]
        result[row.listing_id] = GroupMembership(
            group_id=row.group_id,
            member_count=row.member_count,
            representative_listing_id=row.representative_listing_id,
            other_site_codes=(*current.other_site_codes, row.site_code),
        )
    return result


_DEDUP_STATS = text(
    """
    SELECT s.code AS site_code,
           p.address_normalized,
           (p.dedup_key IS NOT NULL) AS has_key,
           (p.group_id IS NOT NULL) AS grouped,
           (g.representative_listing_id = p.id) AS is_representative,
           EXISTS (
               SELECT 1 FROM t_listings o
               WHERE o.group_id = p.group_id AND o.site_id <> p.site_id
           ) AS shared
    FROM t_listings p
    JOIN m_sites s ON s.id = p.site_id
    LEFT JOIN t_listing_groups g ON g.id = p.group_id
    ORDER BY s.code, p.id
    """
)


def dedup_stats(conn: Connection) -> list[SiteDedupStats]:
    """サイト別の名寄せ実測。

    「他サイトと同居している率」がクロスサイト重複率で、その裏返しが
    ユニーク物件率になる（賃貸EX の本採用判断 → 課題#5）。
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in conn.execute(_DEDUP_STATS):
        bucket = buckets.setdefault(
            row.site_code,
            {"listings": 0, "with_key": 0, "grouped": 0, "shared": 0, "rep": 0, "gran": {}},
        )
        bucket["listings"] += 1
        bucket["with_key"] += int(bool(row.has_key))
        bucket["grouped"] += int(bool(row.grouped))
        bucket["shared"] += int(bool(row.shared))
        bucket["rep"] += int(bool(row.is_representative))
        label = address_granularity(row.address_normalized)
        bucket["gran"][label] = bucket["gran"].get(label, 0) + 1

    return [
        SiteDedupStats(
            site_code=site_code,
            listings=bucket["listings"],
            with_key=bucket["with_key"],
            grouped=bucket["grouped"],
            shared_with_other_sites=bucket["shared"],
            representative=bucket["rep"],
            granularity=dict(sorted(bucket["gran"].items())),
        )
        for site_code, bucket in sorted(buckets.items())
    ]
