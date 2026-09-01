"""クロスサイト名寄せ。

住所の正規化（``address``）→ キー合成（``key``）→ グループ同期（``groups``）の3層。
前2層はDBに触らない純関数で、DBなしでテストできる。
"""

from house_search.dedup.address import address_granularity, normalize_address
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
    "GroupChange",
    "SiteDedupStats",
    "address_granularity",
    "compute_dedup_key",
    "dedup_stats",
    "NO_GROUP",
    "GroupMembership",
    "group_membership",
    "normalize_address",
    "refresh_dedup_keys",
    "sync_groups",
]
