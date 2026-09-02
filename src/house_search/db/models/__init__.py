"""全モデルの再エクスポート。

Alembic の ``target_metadata`` が全テーブルを認識できるよう、
このモジュールを import するだけで全モデルが登録されるようにしている。
"""

from house_search.db.base import Base
from house_search.db.models.masters import (
    City,
    CitySiteValue,
    Condition,
    ConditionCategory,
    ConditionPropertyType,
    ConditionSynonym,
    PropertyType,
    Site,
)
from house_search.db.models.transactions import (
    Listing,
    ListingFeature,
    ListingGroup,
    ListingScore,
    Notification,
    RankingDigest,
    ScrapeLog,
    ScrapeRun,
    UnknownToken,
)

__all__ = [
    "Base",
    "City",
    "CitySiteValue",
    "Condition",
    "ConditionCategory",
    "ConditionPropertyType",
    "ConditionSynonym",
    "Notification",
    "Listing",
    "ListingFeature",
    "ListingGroup",
    "ListingScore",
    "PropertyType",
    "RankingDigest",
    "ScrapeLog",
    "ScrapeRun",
    "Site",
    "UnknownToken",
]
