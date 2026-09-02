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
    SiteSearchParam,
    Station,
)
from house_search.db.models.transactions import (
    Listing,
    ListingFeature,
    ListingGroup,
    ListingScore,
    ListingStation,
    Notification,
    RankingDigest,
    ScrapeLog,
    ScrapeRun,
    StationCommute,
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
    "ListingStation",
    "PropertyType",
    "RankingDigest",
    "ScrapeLog",
    "ScrapeRun",
    "Site",
    "SiteSearchParam",
    "Station",
    "StationCommute",
    "UnknownToken",
]
