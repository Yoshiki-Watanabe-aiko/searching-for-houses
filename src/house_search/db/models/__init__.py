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
    Notification,
    Property,
    PropertyFeature,
    PropertyGroup,
    PropertyScore,
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
    "Property",
    "PropertyFeature",
    "PropertyGroup",
    "PropertyScore",
    "PropertyType",
    "RankingDigest",
    "ScrapeLog",
    "ScrapeRun",
    "Site",
    "UnknownToken",
]
