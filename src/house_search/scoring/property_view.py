"""採点・MUST判定の入力となる物件ビュー。

ORM の行そのものではなくこの不変オブジェクトを介すことで、スコアリングを
「DB保存済みの属性からの純関数」に保てる。``rescore`` がネットワーク不要の
DBバッチで完結するのはこの性質による。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 「1SLDK」のサービスルーム表記。間取り比較では S を無視する
# （1SLDK は 1LDK に納戸が付いた形で、1LDK を許容するなら除外する理由がない）。
_SERVICE_ROOM = re.compile(r"(?<=\d)s(?=[ldk])", re.IGNORECASE)


def normalize_layout(layout: str | None) -> str | None:
    """間取り表記を比較用に正規化する。

    全角・空白・サービスルーム表記の揺れを吸収する。
    """
    if not layout:
        return None
    value = layout.strip().upper().replace("　", "").replace(" ", "")
    value = value.replace("ＬＤＫ", "LDK").replace("ワンルーム", "1R")
    return _SERVICE_ROOM.sub("", value.lower()).upper() or None


@dataclass(frozen=True, slots=True)
class PropertyView:
    """1物件の採点に必要な属性一式。

    ``detail_fetched`` が False のときは設備が「未確認」であることを意味し、
    WANT の設備項目は miss ではなく unknown として扱う（0点なのは同じだが、
    通知に「未確認N項目」と出して判断材料にする）。
    """

    property_id: int | None = None
    site_code: str | None = None
    url: str | None = None
    title: str | None = None

    price: int | None = None
    mgmt_fee_monthly: int | None = None
    rent_total: int | None = None
    repair_reserve_monthly: int | None = None

    area_sqm: float | None = None
    land_area_sqm: float | None = None
    building_area_sqm: float | None = None
    layout: str | None = None
    floor_num: int | None = None
    total_floors: int | None = None
    age_years: int | None = None
    walk_minutes: int | None = None

    prefecture: str | None = None
    address: str | None = None
    detail_fetched: bool = False
    feature_codes: frozenset[str] = field(default_factory=frozenset)

    @property
    def normalized_layout(self) -> str | None:
        return normalize_layout(self.layout)

    @property
    def monthly_cost(self) -> int | None:
        """管理費＋修繕積立金。双方 None のときだけ None を返す。"""
        if self.mgmt_fee_monthly is None and self.repair_reserve_monthly is None:
            return None
        return (self.mgmt_fee_monthly or 0) + (self.repair_reserve_monthly or 0)

    def metric_value(self, metric: str) -> float | None:
        """metric名から値を取り出す。未定義・欠損は None。"""
        if metric == "rent_total":
            # 生成列が無いビュー（テスト・スクレイプ直後）でも計算できるようにする。
            if self.rent_total is not None:
                return float(self.rent_total)
            if self.price is None:
                return None
            return float(self.price + (self.mgmt_fee_monthly or 0))
        if metric == "monthly_cost":
            cost = self.monthly_cost
            return None if cost is None else float(cost)
        value = getattr(self, metric, None)
        return None if value is None else float(value)
