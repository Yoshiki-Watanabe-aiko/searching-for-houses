"""賃貸の月額光熱費（電気＋ガス）の推定（→ 課題#64・ADR 0025）。

採点の ``living_cost``（賃料＋管理費＋推定光熱費）の入力になる純関数。
設備（名寄せグループの和集合）からガス種別とコンロを判定し、
**世帯人数ぶんの需要 × 機器効率 × 料金**で月額を出す。DB にもネットワークにも触らない。

要点:
  - 光熱費の差は事実上**ガス種別**で決まる（IH とガスコンロの差は ±400円未満。
    電気で給湯する掲載は実測で約0.1%しかない）
  - ⚠⚠ **ガス種別が不明の掲載を「都市ガス」とみなさない。** 実測（2026-09-11）で、
    近郊帯ではガス種別を書いていない掲載の79%が実はプロパンだった（プロパンは伏せられる）。
    逆に23区帯では86%が都市ガスなので「不明＝プロパン」も誤り。帯ごとに実測した
    プロパン確率 ``p`` で期待値を取る（パターンの ``utility.unknown_lpg_probability``）
  - ⚠ **補助前の単価を使う。** 国の電気・ガス料金の負担軽減は都市ガスと電気だけが
    対象で、LPガスは対象外。補助後の単価で比べると、補助がある月だけ差額が広がり、
    補助が終わるたびに順位が揺れる
  - ⚠ 単価は **2026年10月検針分**（東京ガス・東京電力エナジーパートナー）と、
    石油情報センターの **2026年8月速報（関東）**。料金改定のときに更新する。
    定数の指紋（:func:`tariff_fingerprint`）が賃貸パターンの ``config_hash`` に入るので、
    更新すると次の採点で自動的に全件が採点し直される

出典（取得日はいずれも 2026-09-11）:
  - 東京ガス 一般料金（2026-08-28 発表の10月検針分）
    https://www.tokyo-gas.co.jp/news/press/20260828-02.html
  - 東京電力EP 従量電灯B・スマートライフS の単価、燃料費調整（10月検針分）
    https://enegent.jp/articles/tepco-juryou-b-tanka ほか（tepco.co.jp は取得不可だった）
  - 再エネ賦課金 4.18円/kWh（2026年5月〜2027年4月検針分）
    https://www.meti.go.jp/press/2025/03/20260319004/20260319004.html
  - LPガス 一般小売価格（関東局・2026年8月速報）
    https://oil-info.ieej.or.jp/price/price_ippan_lp_maitsuki.html
  - 需要の検算: 東京都環境局 平成26年度 実態調査（集合住宅の都市ガス 1人15m³／2人30m³・暖房込み）、
    家計調査 2025年（単身の電気代＋ガス代 10,226円）
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

# --- 条件コード（data/feature_dictionary.yaml） ------------------------------
CODE_CITY_GAS = "EQUIP_CITY_GAS"
CODE_LPG = "EQUIP_LPG"
CODE_ALL_ELECTRIC = "KITCHEN_ALL_ELEC"
CODE_IH = "KITCHEN_IH"
CODE_GAS_STOVE = "KITCHEN_GAS"

# --- 判定結果の語彙（内訳JSONB・表示で使う） ---------------------------------
GAS_CITY = "city"
GAS_LPG = "lpg"
GAS_ELECTRIC = "electric"
GAS_UNKNOWN = "unknown"
GAS_LABELS = {
    GAS_CITY: "都市ガス",
    GAS_LPG: "プロパンガス",
    GAS_ELECTRIC: "オール電化",
    GAS_UNKNOWN: "ガス種別不明",
}

BASIS_LISTING = "listing"
"""掲載（名寄せグループの和集合）の設備にそう書いてあった。"""
BASIS_PRIOR = "prior"
"""ガス種別が書かれていないので、帯ごとのプロパン確率で期待値を取った。"""
BASIS_CONFLICT = "conflict"
"""都市ガスとプロパンの両方が書かれていた（名寄せの誤結合か誤記）。期待値を取る。"""
BASIS_ASSUMED = "assumed"
"""コンロの記載が無いのでガスコンロとみなした。"""

STOVE_IH = "ih"
STOVE_GAS = "gas"
STOVE_LABELS = {STOVE_IH: "IH", STOVE_GAS: "ガスコンロ"}

# --- 需要（世帯人数ごと） ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Demand:
    """1世帯・1か月あたりのエネルギー需要。

    ``cooking_mj`` は鍋に入る**有効熱**で、機器の効率で割って入力に戻す。
    ``base_kwh`` は照明・家電・冷暖房（賃貸の冷暖房はエアコン）で、
    ガス種別に関係なく全掲載に同じだけ掛かる（順位には効かず、合計額の水準だけを決める）。
    """

    hot_water_liters_per_day: float
    cooking_mj: float
    base_kwh: float


#: ⚠ 1人・2人しか持たない。3人以上は出典のある値を確かめてから足す
#:   （推測で埋めると、合計額の水準が静かにずれる）。
DEMANDS: dict[int, Demand] = {
    1: Demand(hot_water_liters_per_day=130.0, cooking_mj=50.0, base_kwh=200.0),
    2: Demand(hot_water_liters_per_day=240.0, cooking_mj=85.0, base_kwh=300.0),
}
DAYS_PER_MONTH = 30
WATER_HEAT_KJ_PER_KG_K = 4.186
#: 40℃のお湯を年平均17℃の給水から沸かす
HOT_WATER_TEMP_RISE_K = 23.0

# --- 機器効率 ---------------------------------------------------------------
GAS_WATER_HEATER_EFFICIENCY = 0.80  # 従来型のガス給湯器（賃貸で多い）
GAS_STOVE_EFFICIENCY = 0.50
IH_EFFICIENCY = 0.90
#: ⚠ オール電化は電気温水器とみなす（エコキュートと断定できる掲載がほぼ無く、控えめな側）
ELECTRIC_WATER_HEATER_EFFICIENCY = 0.90

# --- 発熱量 -----------------------------------------------------------------
CITY_GAS_MJ_PER_M3 = 45.0  # 都市ガス13A
LPG_MJ_PER_M3 = 99.0  # LPガス（都市ガスの約2.2倍）
MJ_PER_KWH = 3.6

# --- 料金（補助前） ---------------------------------------------------------
#: 東京ガス 一般料金（東京地区等・2026年10月検針分・補助前）:
#: (使用量の上限 m³, 基本料金, 単位料金)。使用量でどの表を使うかが決まる
#: （20m³ の境目では A表と B表の料金が等しく連続している）。
CITY_GAS_TIERS: tuple[tuple[float, float, float], ...] = (
    (20.0, 759.0, 185.13),
    (80.0, 1056.0, 170.28),
    (200.0, 1232.0, 168.08),
)
#: LPガス 一般小売価格（関東局・2026年8月速報・税込）: (使用量 m³, 月額 円)。区分線形で補間する。
#: ⚠ 全契約の平均なので、設備費が上乗せされがちな賃貸では過小評価になりうる
#:   （→ 上乗せ係数 ``lpg_price_multiplier``。既定 1.0）。
LPG_PRICE_POINTS: tuple[tuple[float, float], ...] = (
    (5.0, 5960.0),
    (10.0, 9736.0),
    (20.0, 16744.0),
    (50.0, 35790.0),
)
#: 東京電力EP 従量電灯B（30A）: 基本料金と3段階の電力量料金 (段の上限 kWh, 単価)
ELECTRICITY_BASIC_30A = 935.25
ELECTRICITY_TIERS: tuple[tuple[float, float], ...] = (
    (120.0, 29.80),
    (300.0, 36.40),
    (math.inf, 40.49),
)
#: 燃料費調整（2026年10月検針分）。補助 3.50円/kWh を戻した補助前の値
FUEL_ADJUSTMENT_PER_KWH = -5.80
#: 再生可能エネルギー発電促進賦課金（2026年5月〜2027年4月検針分）
RENEWABLE_SURCHARGE_PER_KWH = 4.18
#: オール電化（スマートライフS・40A）: 基本料金・昼・夜（1〜6時）の単価
ALL_ELECTRIC_BASIC_40A = 1247.00
ALL_ELECTRIC_DAY_RATE = 35.76
ALL_ELECTRIC_NIGHT_RATE = 27.86
#: 基礎電力のうち夜間に使う割合（給湯は全量を夜間に沸かす）
ALL_ELECTRIC_BASE_NIGHT_SHARE = 0.20

#: 通知・内訳に出す単価の基準
TARIFF_LABEL = "2026年10月検針分・補助前"


class UtilityNotAttachedError(RuntimeError):
    """光熱費の見積もりが付いていないビューで ``living_cost`` を求めた。

    ⚠ **欠損（None）として黙って流さない。** 流すと ``living_cost`` の weight 40 が
    分母から消え、賃料が採点から抜けたまま順位が付く（例外にならない）。
    ``load_listing_views`` に ``utility_profile`` を渡し忘れた経路で起きる。
    """


@dataclass(frozen=True, slots=True)
class UtilityProfile:
    """光熱費の見積もり条件（検索パターンの ``utility`` セクションから作る）。"""

    household_size: int
    unknown_lpg_probability: float
    """ガス種別が不明な掲載がプロパンである確率（帯ごとの実測値）。"""
    lpg_price_multiplier: float = 1.0
    """LPガスの価格に掛ける係数（賃貸の割高分を見込むとき 1.0 より大きくする）。"""

    def __post_init__(self) -> None:
        if self.household_size not in DEMANDS:
            known = ", ".join(str(n) for n in sorted(DEMANDS))
            raise ValueError(
                f"世帯人数 {self.household_size} の需要は定義されていません（使えるのは: {known}）"
            )
        if not 0.0 <= self.unknown_lpg_probability <= 1.0:
            raise ValueError("unknown_lpg_probability は 0〜1 で指定してください")
        if self.lpg_price_multiplier <= 0:
            raise ValueError("lpg_price_multiplier は正の値で指定してください")


@dataclass(frozen=True, slots=True)
class UtilityEstimate:
    """1掲載の推定光熱費と、その根拠。

    ⚠ ``monthly_yen`` は**推定**であり、内訳と通知には必ず「推定」と根拠を添える。
    """

    monthly_yen: int
    gas: str
    gas_basis: str
    stove: str
    stove_basis: str
    household_size: int
    lpg_probability: float | None = None
    """期待値を取ったときのプロパン確率（書いてあったときは None）。"""
    city_gas_yen: int | None = None
    """同じコンロで都市ガスだった場合の月額（プロパン・不明の掲載との比較表示用）。"""
    lpg_yen: int | None = None
    """同じコンロでプロパンだった場合の月額。"""

    @property
    def is_estimated_gas(self) -> bool:
        """ガス種別を期待値で埋めたか。"""
        return self.gas_basis in (BASIS_PRIOR, BASIS_CONFLICT)

    def to_detail(self) -> dict[str, Any]:
        """``t_listing_scores.score_breakdown`` の ``living_cost`` 項目に入れる根拠。"""
        detail: dict[str, Any] = {
            "utility": self.monthly_yen,
            "gas": self.gas,
            "gas_basis": self.gas_basis,
            "stove": self.stove,
            "stove_basis": self.stove_basis,
            "household": self.household_size,
            "estimated": True,
            "tariff": TARIFF_LABEL,
        }
        if self.lpg_probability is not None:
            detail["lpg_probability"] = self.lpg_probability
        return detail


# --- 料金の計算 -------------------------------------------------------------


def city_gas_bill(volume_m3: float) -> float:
    """東京ガス一般料金（補助前）の月額。使用量で料金表を選ぶ。"""
    if volume_m3 <= 0:
        return 0.0
    for upper, basic, unit in CITY_GAS_TIERS:
        if volume_m3 <= upper:
            return basic + unit * volume_m3
    _, basic, unit = CITY_GAS_TIERS[-1]
    return basic + unit * volume_m3


def lpg_bill(volume_m3: float, *, multiplier: float = 1.0) -> float:
    """LPガスの月額。公表点の区分線形補間（両端は端の傾きで外挿）。"""
    if volume_m3 <= 0:
        return 0.0
    points = LPG_PRICE_POINTS
    if volume_m3 <= points[0][0]:
        (x0, y0), (x1, y1) = points[0], points[1]
    elif volume_m3 >= points[-1][0]:
        (x0, y0), (x1, y1) = points[-2], points[-1]
    else:
        index = next(i for i in range(1, len(points)) if volume_m3 <= points[i][0])
        (x0, y0), (x1, y1) = points[index - 1], points[index]
    price = y0 + (y1 - y0) * (volume_m3 - x0) / (x1 - x0)
    return price * multiplier


def electricity_bill(kwh: float) -> float:
    """従量電灯B（30A）の月額。段階料金を正確に積み上げる。"""
    bill = ELECTRICITY_BASIC_30A
    lower = 0.0
    for upper, rate in ELECTRICITY_TIERS:
        if kwh <= lower:
            break
        bill += rate * (min(kwh, upper) - lower)
        lower = upper
    return bill + (FUEL_ADJUSTMENT_PER_KWH + RENEWABLE_SURCHARGE_PER_KWH) * kwh


def all_electric_bill(day_kwh: float, night_kwh: float) -> float:
    """スマートライフS（40A）の月額。"""
    total = day_kwh + night_kwh
    return (
        ALL_ELECTRIC_BASIC_40A
        + ALL_ELECTRIC_DAY_RATE * day_kwh
        + ALL_ELECTRIC_NIGHT_RATE * night_kwh
        + (FUEL_ADJUSTMENT_PER_KWH + RENEWABLE_SURCHARGE_PER_KWH) * total
    )


def hot_water_mj(demand: Demand) -> float:
    """給湯の有効熱（MJ/月）。"""
    liters = demand.hot_water_liters_per_day * DAYS_PER_MONTH
    return liters * WATER_HEAT_KJ_PER_KG_K * HOT_WATER_TEMP_RISE_K / 1000.0


@dataclass(frozen=True, slots=True)
class Bill:
    """ガス代と電気代の内訳（円/月・丸める前）。"""

    gas: float
    electricity: float

    @property
    def total(self) -> float:
        return self.gas + self.electricity


def monthly_bill(
    gas: str, stove: str, household_size: int, *, lpg_price_multiplier: float = 1.0
) -> Bill:
    """ガス種別とコンロが決まっているときの月額光熱費。

    ⚠ ``gas`` に ``unknown`` は渡せない（期待値は :func:`estimate_utility` が取る）。
    """
    demand = DEMANDS[household_size]
    hot_water = hot_water_mj(demand)
    cooking_kwh_ih = demand.cooking_mj / IH_EFFICIENCY / MJ_PER_KWH

    if gas == GAS_ELECTRIC:
        # 給湯は電気温水器で全量を夜間に、調理は IH、基礎電力の2割を夜間に使う
        night = hot_water / ELECTRIC_WATER_HEATER_EFFICIENCY / MJ_PER_KWH
        night += demand.base_kwh * ALL_ELECTRIC_BASE_NIGHT_SHARE
        day = cooking_kwh_ih + demand.base_kwh * (1.0 - ALL_ELECTRIC_BASE_NIGHT_SHARE)
        return Bill(gas=0.0, electricity=all_electric_bill(day, night))

    if gas not in (GAS_CITY, GAS_LPG):
        raise ValueError(f"ガス種別が決まっていません: {gas!r}")

    gas_input_mj = hot_water / GAS_WATER_HEATER_EFFICIENCY
    kwh = demand.base_kwh
    if stove == STOVE_IH:
        kwh += cooking_kwh_ih
    else:
        gas_input_mj += demand.cooking_mj / GAS_STOVE_EFFICIENCY

    if gas == GAS_CITY:
        gas_yen = city_gas_bill(gas_input_mj / CITY_GAS_MJ_PER_M3)
    else:
        gas_yen = lpg_bill(gas_input_mj / LPG_MJ_PER_M3, multiplier=lpg_price_multiplier)
    return Bill(gas=gas_yen, electricity=electricity_bill(kwh))


def _gas_type(codes: Collection[str]) -> tuple[str, str]:
    """設備からガス種別と根拠を決める。

    ⚠ 都市ガスとプロパンの両方があれば**矛盾**として期待値へ回す
    （名寄せの誤結合か誤記。どちらかに寄せると根拠のない断定になる）。
    ⚠ オール電化はガス種別の記載が無いときだけ採る。
    """
    has_city = CODE_CITY_GAS in codes
    has_lpg = CODE_LPG in codes
    if has_city and has_lpg:
        return GAS_UNKNOWN, BASIS_CONFLICT
    if has_city:
        return GAS_CITY, BASIS_LISTING
    if has_lpg:
        return GAS_LPG, BASIS_LISTING
    if CODE_ALL_ELECTRIC in codes:
        return GAS_ELECTRIC, BASIS_LISTING
    return GAS_UNKNOWN, BASIS_PRIOR


def _stove(codes: Collection[str], gas: str) -> tuple[str, str]:
    """コンロと根拠を決める。IH の記載が無ければガスコンロとみなす。"""
    if CODE_IH in codes:
        return STOVE_IH, BASIS_LISTING
    if gas == GAS_ELECTRIC:
        return STOVE_IH, BASIS_ASSUMED
    if CODE_GAS_STOVE in codes:
        return STOVE_GAS, BASIS_LISTING
    return STOVE_GAS, BASIS_ASSUMED


def estimate_utility(codes: Collection[str], profile: UtilityProfile) -> UtilityEstimate:
    """設備（名寄せグループの和集合）から月額光熱費を推定する。

    ガス種別が不明・矛盾なら ``p × プロパン + (1 − p) × 都市ガス`` の期待値を取る
    （``p`` は ``profile.unknown_lpg_probability``）。
    """
    gas, gas_basis = _gas_type(codes)
    stove, stove_basis = _stove(codes, gas)
    size = profile.household_size
    multiplier = profile.lpg_price_multiplier

    if gas == GAS_ELECTRIC:
        total = monthly_bill(GAS_ELECTRIC, stove, size).total
        return UtilityEstimate(
            monthly_yen=round(total),
            gas=gas,
            gas_basis=gas_basis,
            stove=stove,
            stove_basis=stove_basis,
            household_size=size,
        )

    city = monthly_bill(GAS_CITY, stove, size).total
    lpg = monthly_bill(GAS_LPG, stove, size, lpg_price_multiplier=multiplier).total
    probability: float | None = None
    if gas == GAS_CITY:
        total = city
    elif gas == GAS_LPG:
        total = lpg
    else:
        probability = profile.unknown_lpg_probability
        total = probability * lpg + (1.0 - probability) * city
    return UtilityEstimate(
        monthly_yen=round(total),
        gas=gas,
        gas_basis=gas_basis,
        stove=stove,
        stove_basis=stove_basis,
        household_size=size,
        lpg_probability=probability,
        city_gas_yen=round(city),
        lpg_yen=round(lpg),
    )


def utility_profile_for(pattern: Any) -> UtilityProfile | None:
    """検索パターンの ``utility`` セクションから見積もり条件を作る（無ければ None）。

    賃貸以外のパターンは ``utility`` を持たないので None になる。
    """
    spec = getattr(pattern, "utility", None)
    if spec is None:
        return None
    return UtilityProfile(
        household_size=spec.household_size,
        unknown_lpg_probability=spec.unknown_lpg_probability,
        lpg_price_multiplier=spec.lpg_price_multiplier,
    )


def tariff_fingerprint() -> str:
    """料金・需要・効率の定数の指紋（SHA256）。

    賃貸パターンの ``config_hash`` に入れる。⚠ 入れないと、定数を更新しても
    保存済みの採点との食い違いに気づけない（→ 課題#59 の ``detect_config_drift`` と同じ関心事）。
    """
    payload = {
        "demands": {str(n): [d.hot_water_liters_per_day, d.cooking_mj, d.base_kwh]
                    for n, d in sorted(DEMANDS.items())},
        "days": DAYS_PER_MONTH,
        "water_heat": WATER_HEAT_KJ_PER_KG_K,
        "temp_rise": HOT_WATER_TEMP_RISE_K,
        "efficiency": [
            GAS_WATER_HEATER_EFFICIENCY,
            GAS_STOVE_EFFICIENCY,
            IH_EFFICIENCY,
            ELECTRIC_WATER_HEATER_EFFICIENCY,
        ],
        "heat_value": [CITY_GAS_MJ_PER_M3, LPG_MJ_PER_M3, MJ_PER_KWH],
        "city_gas": [list(t) for t in CITY_GAS_TIERS],
        "lpg": [list(p) for p in LPG_PRICE_POINTS],
        "electricity": [
            ELECTRICITY_BASIC_30A,
            [[u if math.isfinite(u) else "inf", r] for u, r in ELECTRICITY_TIERS],
            FUEL_ADJUSTMENT_PER_KWH,
            RENEWABLE_SURCHARGE_PER_KWH,
        ],
        "all_electric": [
            ALL_ELECTRIC_BASIC_40A,
            ALL_ELECTRIC_DAY_RATE,
            ALL_ELECTRIC_NIGHT_RATE,
            ALL_ELECTRIC_BASE_NIGHT_SHARE,
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
