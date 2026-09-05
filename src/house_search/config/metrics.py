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
    MetricSpec(
        name="commute_minutes",
        label="通勤時間",
        direction=Direction.LOWER_IS_BETTER,
        unit="分",
        property_types=ALL_PROPERTY_TYPES,
        # ⚠ t_listings の物理列ではない。t_listing_stations（掲載→駅）と
        # t_station_commutes（駅ペアの所要時間キャッシュ）から導出する
        # 最寄り駅ごとの最短値。列名としてはここにしか現れない。
        source_columns=("commute_minutes",),
    ),
    # --- ハザード評価（→ 課題#46） -------------------------------------
    # ⚠ いずれも t_listings の物理列ではない。address_normalized から
    # m_hazard_levels を JOIN して引く（丁目、無ければ町の値）。
    # ⚠⚠ **0.0 は「区域外だと確認した」で、未解決は None**。混ぜると
    # 「危険なのに情報が無いから減点されない」掲載が満点になる。
    MetricSpec(
        name="flood_rank_avg",
        label="洪水浸水深（丁目の面積加重平均ランク）",
        direction=Direction.LOWER_IS_BETTER,
        unit="ランク",
        property_types=ALL_PROPERTY_TYPES,
        # ⚠ 最大ランクではなく加重平均を主力にする。最大は外れ値に引っ張られ、
        # 水路際のランク6がごく一部でも丁目全体が最悪扱いになる（→ 課題#46 の実測）。
        source_columns=("flood_rank_avg",),
    ),
    MetricSpec(
        name="flood_area_ratio",
        label="洪水浸水域の面積比",
        direction=Direction.LOWER_IS_BETTER,
        unit="割合",
        property_types=ALL_PROPERTY_TYPES,
        source_columns=("flood_area_ratio",),
    ),
    MetricSpec(
        name="landslide_area_ratio",
        label="土砂災害警戒区域の面積比",
        direction=Direction.LOWER_IS_BETTER,
        unit="割合",
        property_types=ALL_PROPERTY_TYPES,
        # ⚠ 土砂は該当が18.9%と少なく、その大半が「丁目の端に5%未満」の形。
        # 洪水（該当72.9%）とは分布が正反対なので、同じ best/worst を流用しない。
        source_columns=("landslide_area_ratio",),
    ),
    # --- 相場との比較（→ 課題#49） ---------------------------------------
    # ⚠ t_listings の物理列ではない。city_id × layout で m_market_rates を
    # 引き、`rent_total ÷ 相場` を出す（同じ市区・同じ間取りの相場と比べる）。
    MetricSpec(
        name="market_rate_ratio",
        label="相場に対する賃料の比",
        direction=Direction.LOWER_IS_BETTER,
        unit="倍",
        # ⚠ 賃貸のみ。売買の相場（国交省API）はまだ入っていないので、
        # 広げると売買パターンで全件 missing になるだけ（測っていないものは書かない）
        property_types=frozenset({CHINTAI}),
        source_columns=("market_rate_ratio",),
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
    MustSpec(
        "layouts",
        "間取り",
        # ⚠ ALL_PROPERTY_TYPES を使わない。土地（Phase 9）を足すとその瞬間に
        # 間取りが土地へも適用可能になり、土地パターンに間取りMUSTが書けてしまう
        # （validate は通り、実行時に全件 unknown になるだけで例外にならない）。
        # 現時点の値は ALL_PROPERTY_TYPES と同一集合（→ 課題#4）。
        frozenset({CHINTAI}) | BUY_TYPES,
        ("layout",),
        True,
    ),
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
        "commute_minutes_max",
        "通勤時間の上限",
        ALL_PROPERTY_TYPES,
        ("commute_minutes",),
        # 駅の同定と所要時間キャッシュの解決が要るため一覧だけでは判定できない。
        # 未解決は unknown になり、unknown_policy に従う（既定 keep）。
        False,
    ),
    MustSpec(
        "flood_rank_max",
        "洪水浸水深ランクの上限",
        ALL_PROPERTY_TYPES,
        ("flood_rank_max",),
        # 住所の解決（address_normalized → m_hazard_levels）に依存するため
        # 一覧だけでは判定できない。住所が詳細で初めて埋まるサイトもある。
        # ⚠ 未解決は unknown。unknown_policy の既定 keep を drop へ倒さないこと
        # （情報が無いだけの掲載が黙って消える。町名までしか出さないサイトが全滅する）。
        False,
    ),
    MustSpec(
        "landslide_special_ratio_max",
        "土砂災害特別警戒区域の面積比の上限",
        ALL_PROPERTY_TYPES,
        ("landslide_special_ratio",),
        False,
    ),
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
