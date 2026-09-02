"""metric・MUST項目のレジストリ。

物件種別ごとに「どの metric / MUST 項目が使えるか」「値をどの列から取るか」
「単位と方向は何か」を一元管理する。検索パターンYAMLの検証はこのレジストリを
引いて行うため、種別追加はレジストリへ1行足すだけで済む。

戸建てに ``area_sqm`` を流用しないのは意図的。専有面積が存在せず土地面積・
建物面積の2軸になるため、混線と名寄せ事故を避けて別metricにしている。
坪単価・㎡単価を metric にしないのも意図的で、price と area に既に weight を
配れる以上、二重に重みが掛かって解釈が濁るため（``price_per_sqm`` は表示用の派生値）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# --- 物件種別コード -------------------------------------------------------
CHINTAI = "CHINTAI"
SHINCHIKU_MANSION = "SHINCHIKU_MANSION"
CHUKO_MANSION = "CHUKO_MANSION"
SHINCHIKU_KODATE = "SHINCHIKU_KODATE"
CHUKO_KODATE = "CHUKO_KODATE"

ALL_PROPERTY_TYPES = frozenset(
    {CHINTAI, SHINCHIKU_MANSION, CHUKO_MANSION, SHINCHIKU_KODATE, CHUKO_KODATE}
)
BUY_TYPES = frozenset({SHINCHIKU_MANSION, CHUKO_MANSION, SHINCHIKU_KODATE, CHUKO_KODATE})
MANSION_TYPES = frozenset({SHINCHIKU_MANSION, CHUKO_MANSION})
KODATE_TYPES = frozenset({SHINCHIKU_KODATE, CHUKO_KODATE})


class Family(StrEnum):
    """種別ファミリ。metric体系・dedup_key・YAMLスキーマの分岐単位。"""

    CHINTAI = "CHINTAI"
    MANSION_BUY = "MANSION_BUY"
    KODATE_BUY = "KODATE_BUY"


FAMILY_OF: dict[str, Family] = {
    CHINTAI: Family.CHINTAI,
    SHINCHIKU_MANSION: Family.MANSION_BUY,
    CHUKO_MANSION: Family.MANSION_BUY,
    SHINCHIKU_KODATE: Family.KODATE_BUY,
    CHUKO_KODATE: Family.KODATE_BUY,
}


class Direction(StrEnum):
    """metric の良し悪しの向き。"""

    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """WANT の numeric 項目として使える metric の定義。"""

    name: str
    label: str
    direction: Direction
    unit: str
    property_types: frozenset[str]
    source_columns: tuple[str, ...]
    """値の取得元となる ``t_listings`` の列。複数なら合算した派生値になる。"""

    @property
    def is_derived(self) -> bool:
        """複数列から算出する派生metricか。"""
        return len(self.source_columns) > 1

    def applies_to(self, property_type: str) -> bool:
        return property_type in self.property_types


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="rent_total",
        label="賃料＋管理費",
        direction=Direction.LOWER_IS_BETTER,
        unit="円/月",
        property_types=frozenset({CHINTAI}),
        source_columns=("rent_total",),
    ),
    MetricSpec(
        name="price",
        label="物件価格",
        direction=Direction.LOWER_IS_BETTER,
        unit="円",
        property_types=BUY_TYPES,
        source_columns=("price",),
    ),
    MetricSpec(
        name="monthly_cost",
        label="管理費＋修繕積立金",
        direction=Direction.LOWER_IS_BETTER,
        unit="円/月",
        # 新築マンションでは詳細に出ないことがあるが、欠損は再正規化で吸収する。
        property_types=MANSION_TYPES,
        source_columns=("mgmt_fee_monthly", "repair_reserve_monthly"),
    ),
    MetricSpec(
        name="area_sqm",
        label="専有面積",
        direction=Direction.HIGHER_IS_BETTER,
        unit="㎡",
        property_types=frozenset({CHINTAI}) | MANSION_TYPES,
        source_columns=("area_sqm",),
    ),
    MetricSpec(
        name="building_area_sqm",
        label="建物面積",
        direction=Direction.HIGHER_IS_BETTER,
        unit="㎡",
        property_types=KODATE_TYPES,
        source_columns=("building_area_sqm",),
    ),
    MetricSpec(
        name="land_area_sqm",
        label="土地面積",
        direction=Direction.HIGHER_IS_BETTER,
        unit="㎡",
        property_types=KODATE_TYPES,
        source_columns=("land_area_sqm",),
    ),
    MetricSpec(
        name="age_years",
        label="築年数",
        direction=Direction.LOWER_IS_BETTER,
        unit="年",
        # 新築（新築M・新築K）には築年数の概念がないため対象外。
        property_types=frozenset({CHINTAI, CHUKO_MANSION, CHUKO_KODATE}),
        source_columns=("age_years",),
    ),
    MetricSpec(
        name="walk_minutes",
        label="駅徒歩",
        direction=Direction.LOWER_IS_BETTER,
        unit="分",
        property_types=ALL_PROPERTY_TYPES,
        source_columns=("walk_minutes",),
    ),
)

METRICS_BY_NAME: dict[str, MetricSpec] = {m.name: m for m in METRICS}


@dataclass(frozen=True, slots=True)
class MustSpec:
    """MUST（未充足なら除外）として指定できる項目の定義。"""

    name: str
    label: str
    property_types: frozenset[str]
    source_columns: tuple[str, ...]
    available_on_list: bool
    """一覧ページだけで判定できるか。

    True の項目は詳細ページを取得する前に判定でき、``fail`` なら詳細取得を
    スキップできる（2段判定によるコスト緩和の要）。
    """


MUST_ITEMS: tuple[MustSpec, ...] = (
    MustSpec("rent_total_max", "賃料＋管理費の上限", frozenset({CHINTAI}), ("rent_total",), True),
    MustSpec("price_max", "物件価格の上限", BUY_TYPES, ("price",), True),
    MustSpec(
        "monthly_cost_max",
        "管理費＋修繕積立金の上限",
        MANSION_TYPES,
        ("mgmt_fee_monthly", "repair_reserve_monthly"),
        # 売買の管理費・修繕積立金は一覧に出ないため詳細を見ないと判定できない。
        False,
    ),
    MustSpec("layouts", "間取り", ALL_PROPERTY_TYPES, ("layout",), True),
    MustSpec(
        "area_min", "専有面積の下限", frozenset({CHINTAI}) | MANSION_TYPES, ("area_sqm",), True
    ),
    MustSpec(
        "area_max", "専有面積の上限", frozenset({CHINTAI}) | MANSION_TYPES, ("area_sqm",), True
    ),
    MustSpec("land_area_min", "土地面積の下限", KODATE_TYPES, ("land_area_sqm",), True),
    MustSpec("building_area_min", "建物面積の下限", KODATE_TYPES, ("building_area_sqm",), True),
    MustSpec(
        "age_max",
        "築年数の上限",
        frozenset({CHINTAI, CHUKO_MANSION, CHUKO_KODATE}),
        ("age_years",),
        True,
    ),
    MustSpec("walk_minutes_max", "駅徒歩の上限", ALL_PROPERTY_TYPES, ("walk_minutes",), True),
    MustSpec(
        "floor_min",
        "所在階の下限",
        frozenset({CHINTAI}) | MANSION_TYPES,
        ("floor_num",),
        False,
    ),
    MustSpec(
        "features",
        "必須の設備・条件コード",
        ALL_PROPERTY_TYPES,
        ("raw_features_text",),
        # 設備は詳細ページの本文からしか判定できない。
        False,
    ),
)

MUST_ITEMS_BY_NAME: dict[str, MustSpec] = {m.name: m for m in MUST_ITEMS}


def metrics_for(property_type: str) -> tuple[MetricSpec, ...]:
    """指定した物件種別で使える metric を返す（定義順＝決定的）。"""
    return tuple(m for m in METRICS if m.applies_to(property_type))


def must_items_for(property_type: str) -> tuple[MustSpec, ...]:
    """指定した物件種別で使える MUST 項目を返す（定義順＝決定的）。"""
    return tuple(m for m in MUST_ITEMS if property_type in m.property_types)


def normalize(value: float, *, best: float, worst: float) -> float:
    """metric の値を 0.0〜1.0 へ線形正規化する。

    ``s = clamp((worst - x) / (worst - best), 0, 1)``。
    best と worst の大小関係が方向を表すため、Direction を別途参照する必要はない
    （「低いほど良い」なら best < worst、「高いほど良い」なら best > worst）。
    """
    if best == worst:
        raise ValueError("best と worst に同じ値は指定できません（0除算になります）")
    s = (worst - value) / (worst - best)
    return max(0.0, min(1.0, s))
