"""ランキング一覧の絞り込み・並べ替え条件。

⚠ **利用者の文字列を SQL に入れない。** 並べ替えは許可表のキーだけを受け、
SQL 式への写像は ``queries`` 側の固定の表で行う。値はすべてバインド変数で渡す。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: 並べ替えのキー → 画面の表示名（並びは画面のプルダウンの順）
SORT_LABELS: dict[str, str] = {
    "rank": "順位（スコアの高い順）",
    "price_asc": "価格の安い順",
    "price_desc": "価格の高い順",
    "area_desc": "面積の広い順",
    "walk_asc": "駅徒歩の短い順",
    "commute_asc": "通勤時間の短い順",
    "newest": "新しく見つけた順",
}
SortKey = Literal[
    "rank", "price_asc", "price_desc", "area_desc", "walk_asc", "commute_asc", "newest"
]

MUST_LABELS: dict[str, str] = {
    "all": "すべて",
    "pass": "すべて満たす（pass）",
    "unknown": "判定できない項目あり（unknown）",
}

#: 1ページの件数
PAGE_SIZE = 50

_CHECKBOX_ON = frozenset({"1", "on", "true"})


class InvalidFilterError(ValueError):
    """クエリ文字列が検証を通らなかった（画面は 400 を返す）。"""


class ListingFilter(BaseModel):
    """ランキング一覧の絞り込み条件。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    #: 価格の下限・上限（万円）。賃貸は「賃料＋管理費」、売買は物件価格
    price_min: float | None = Field(default=None, ge=0, le=1_000_000)
    price_max: float | None = Field(default=None, ge=0, le=1_000_000)
    #: 面積の下限（㎡）。賃貸・マンションは専有、戸建ては延床、土地は土地面積
    area_min: float | None = Field(default=None, ge=0, le=100_000)
    #: 採点に使った駅徒歩・通勤時間の上限（分）
    walk_max: int | None = Field(default=None, ge=0, le=600)
    commute_max: int | None = Field(default=None, ge=0, le=600)
    must: Literal["all", "pass", "unknown"] = "all"
    site: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,40}$")
    city: int | None = Field(default=None, ge=1)
    favorites: bool = False
    include_excluded: bool = False
    sort: SortKey = "rank"
    page: int = Field(default=1, ge=1, le=100_000)

    @classmethod
    def from_query(cls, args: Mapping[str, str]) -> ListingFilter:
        """クエリ文字列から作る。空欄は未指定、チェックボックスは値の有無で読む。"""
        data: dict[str, Any] = {}
        for key, raw in args.items():
            value = raw.strip()
            if key in ("favorites", "include_excluded"):
                data[key] = value.lower() in _CHECKBOX_ON
            elif value:
                data[key] = value
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            fields = sorted({str(err["loc"][0]) for err in exc.errors() if err.get("loc")})
            message = f"絞り込み条件が正しくありません: {', '.join(fields)}"
            raise InvalidFilterError(message) from exc

    def to_query(self, **overrides: Any) -> dict[str, Any]:
        """ページ送り・並べ替えのリンクに載せる値（既定値は省く）。"""
        values = self.model_dump()
        values.update(overrides)
        defaults = ListingFilter().model_dump()
        query: dict[str, Any] = {}
        for key, value in values.items():
            if value == defaults[key] or value is None:
                continue
            query[key] = "1" if value is True else value
        return query

    @property
    def offset(self) -> int:
        return (self.page - 1) * PAGE_SIZE
