"""クロスサイト名寄せ。

住所の正規化（``address``）→ キー合成（``key``）→ グループ同期（``groups``）の3層。
前2層はDBに触らない純関数で、DBなしでテストできる。

住所マスタの読み込みとDB同期（``address_master``）だけは別扱いで、
純関数の ``address`` へ ``AddressIndex`` を渡す形にしてある
（``commute`` が ``StationIndex`` を外から渡すのと同じ構成 → ADR 0020）。
"""

from house_search.dedup.address import AddressIndex, address_granularity, normalize_address
from house_search.dedup.address_master import (
    load_address_index,
    load_address_rows,
    sync_address_points,
)
from house_search.dedup.groups import (
    NO_GROUP,
    GroupChange,
    GroupMembership,
    SiteDedupStats,
    dedup_stats,
    group_membership,
    refresh_dedup_keys,
    sync_groups,
)
from house_search.dedup.key import DEDUP_KEY_VERSION, compute_dedup_key

__all__ = [
    "DEDUP_KEY_VERSION",
    "AddressIndex",
    "GroupChange",
    "SiteDedupStats",
    "address_granularity",
    "compute_dedup_key",
    "dedup_stats",
    "NO_GROUP",
    "GroupMembership",
    "group_membership",
    "load_address_index",
    "load_address_rows",
    "normalize_address",
    "sync_address_points",
    "refresh_dedup_keys",
    "sync_groups",
]
