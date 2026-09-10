"""MetricRegistry のテスト。"""

from __future__ import annotations

import pytest

from house_search.config import metrics as m

# ⚠ 物件種別は**リテラル**で書く。`m.ALL_PROPERTY_TYPES` や `m.BUY_TYPES` を右辺に使うと、
# 定数の中身が変わったとき左右が同時に変わり**検出できない**（→ 課題#61）。
_R = "CHINTAI"
_SM = "SHINCHIKU_MANSION"
_CM = "CHUKO_MANSION"
_SK = "SHINCHIKU_KODATE"
_CK = "CHUKO_KODATE"
_T = "TOCHI"


def test_全metricが少なくとも1種別に紐づく() -> None:
    for spec in m.METRICS:
        assert spec.property_types, f"{spec.name} に適用種別が無い"
        assert spec.property_types <= m.ALL_PROPERTY_TYPES


def test_戸建てには専有面積metricを使わせない() -> None:
    """戸建ては土地面積・建物面積の2軸で、専有面積の概念が無い。"""
    for ptype in (m.SHINCHIKU_KODATE, m.CHUKO_KODATE):
        names = {spec.name for spec in m.metrics_for(ptype)}
        assert "area_sqm" not in names
        assert {"land_area_sqm", "building_area_sqm"} <= names


def test_マンションには土地建物面積metricを使わせない() -> None:
    for ptype in (m.SHINCHIKU_MANSION, m.CHUKO_MANSION):
        names = {spec.name for spec in m.metrics_for(ptype)}
        assert "area_sqm" in names
        assert "land_area_sqm" not in names
        assert "building_area_sqm" not in names


def test_新築には築年数metricを使わせない() -> None:
    for ptype in (m.SHINCHIKU_MANSION, m.SHINCHIKU_KODATE):
        assert "age_years" not in {spec.name for spec in m.metrics_for(ptype)}
    for ptype in (m.CHINTAI, m.CHUKO_MANSION, m.CHUKO_KODATE):
        assert "age_years" in {spec.name for spec in m.metrics_for(ptype)}


def test_賃料metricは賃貸のみ_価格metricは売買のみ() -> None:
    assert m.METRICS_BY_NAME["rent_total"].property_types == {_R}
    # 土地も売買なので価格を使う（→ 課題#61）
    assert m.METRICS_BY_NAME["price"].property_types == {_SM, _CM, _SK, _CK, _T}


def test_徒歩分数は全種別で使える() -> None:
    assert m.METRICS_BY_NAME["walk_minutes"].property_types == {_R, _SM, _CM, _SK, _CK, _T}


def test_metrics_forの並びは決定的() -> None:
    """スコア加算順を安定させるため、レジストリの並びは定義順で固定する。"""
    assert [s.name for s in m.metrics_for(m.CHINTAI)] == [
        "rent_total",
        "area_sqm",
        "age_years",
        "walk_minutes",
        "commute_minutes",
        "flood_rank_avg",
        "flood_area_ratio",
        "landslide_area_ratio",
        "liquefaction_rank_avg",
        "market_rate_ratio",
    ]


def test_派生metricの判定() -> None:
    assert m.METRICS_BY_NAME["monthly_cost"].is_derived
    assert not m.METRICS_BY_NAME["price"].is_derived


@pytest.mark.parametrize(
    ("value", "best", "worst", "expected"),
    [
        (50000, 50000, 70000, 1.0),  # 満点
        (70000, 50000, 70000, 0.0),  # 0点
        (60000, 50000, 70000, 0.5),  # 中間
        (40000, 50000, 70000, 1.0),  # best を超えてもクランプ
        (90000, 50000, 70000, 0.0),  # worst を下回ってもクランプ
        (45, 45, 30, 1.0),  # 高いほど良い方向（best > worst）
        (30, 45, 30, 0.0),
        (37.5, 45, 30, 0.5),
    ],
)
def test_正規化(value: float, best: float, worst: float, expected: float) -> None:
    assert m.normalize(value, best=best, worst=worst) == pytest.approx(expected)


def test_bestとworstが同値ならエラー() -> None:
    with pytest.raises(ValueError, match="0除算"):
        m.normalize(10, best=5, worst=5)


def test_一覧だけで判定できないMUST項目が明示されている() -> None:
    """2段判定の要。ここが誤っていると詳細取得を不当にスキップする。"""
    list_only = {i.name for i in m.MUST_ITEMS if not i.available_on_list}
    # commute_minutes_max は駅の同定と所要時間キャッシュの解決が要るため一覧では判定できない。
    # ハザードの2項目は住所の照合（address_normalized → m_hazard_levels）に依存し、
    # 住所は詳細ページで初めて埋まるサイトがあるため同じく一覧では判定できない。
    assert list_only == {
        "monthly_cost_max",
        "floor_min",
        "features",
        "commute_minutes_max",
        "market_rate_ratio_min",
        "flood_rank_max",
        "landslide_special_ratio_max",
    }


def test_全種別にファミリが割り当てられている() -> None:
    assert set(m.FAMILY_OF) == m.ALL_PROPERTY_TYPES
    assert set(m.FAMILY_OF.values()) == set(m.Family)


def test_相場比は賃貸と売買4種別だけ() -> None:
    """⚠ 相場のある種別にだけ許す（→ 課題#49 Step 6・2026-09-08 に売買へ拡張）。

    賃貸は SUUMO の家賃相場、売買は国交省「不動産情報ライブラリ」の㎡単価が
    `m_market_rates` に入っている。⚠ **`ALL_PROPERTY_TYPES` を使わない。**
    土地（TOCHI・Phase 9）を足した瞬間に意味が変わり、相場が無いのに YAML へ
    書けてしまう（書けても全件 missing になるだけで例外にならない → 課題#4）。
    """
    spec = m.METRICS_BY_NAME["market_rate_ratio"]
    assert spec.property_types == {_R, _SM, _CM, _SK, _CK}
    assert spec.direction is m.Direction.LOWER_IS_BETTER
    # ⚠ 土地には相場が無い（→ 課題#61）。旧版の
    # 「`!= ALL or len(ALL) == 5`」は TOCHI 追加の前後どちらでも通り、ガードになっていなかった
    assert "TOCHI" not in spec.property_types


# --- レジストリのスナップショット（→ 課題#61） --------------------------------
#
# 全 metric・全 MUST 項目の「使える種別の集合」を**リテラルで**丸ごと固定する。
# ⚠ 参照箇所をコメントで1つずつ判定するだけでは、`ALL_PROPERTY_TYPES` や
# `BUY_TYPES` の中身が変わったとき**どの項目が土地へ開いたか**がレビューに現れない。
# ここに固定しておけば、種別を足すと必ず落ち、差分がこの表の書き換えとして見える。

# ⚠ `_ALL` は土地を含む6種別、`_BUY` は**建物を伴う**売買4種別（土地を含まない）。
_ALL = {_R, _SM, _CM, _SK, _CK, _T}
_BUY = {_SM, _CM, _SK, _CK}
_MANSION = {_SM, _CM}
_KODATE = {_SK, _CK}

EXPECTED_METRIC_TYPES: dict[str, set[str]] = {
    "rent_total": {_R},
    "price": _BUY | {_T},
    "monthly_cost": _MANSION,
    "area_sqm": {_R} | _MANSION,
    "building_area_sqm": _KODATE,
    "land_area_sqm": _KODATE | {_T},
    "age_years": {_R, _CM, _CK},
    "walk_minutes": _ALL,
    "commute_minutes": _ALL,
    "flood_rank_avg": _ALL,
    "flood_area_ratio": _ALL,
    "landslide_area_ratio": _ALL,
    "liquefaction_rank_avg": _ALL,
    "market_rate_ratio": {_R} | _BUY,
}

EXPECTED_MUST_TYPES: dict[str, set[str]] = {
    "rent_total_max": {_R},
    "price_max": _BUY | {_T},
    "monthly_cost_max": _MANSION,
    "layouts": {_R} | _BUY,
    "area_min": {_R} | _MANSION,
    "area_max": {_R} | _MANSION,
    "land_area_min": _KODATE | {_T},
    "building_area_min": _KODATE,
    "age_max": {_R, _CM, _CK},
    "walk_minutes_max": _ALL,
    "commute_minutes_max": _ALL,
    "flood_rank_max": _ALL,
    "landslide_special_ratio_max": _ALL,
    "market_rate_ratio_min": {_R},
    "floor_min": {_R} | _MANSION,
    "features": _ALL,
}


def test_metricの適用種別はスナップショットどおり() -> None:
    actual = {spec.name: set(spec.property_types) for spec in m.METRICS}
    assert actual == EXPECTED_METRIC_TYPES


def test_MUST項目の適用種別はスナップショットどおり() -> None:
    actual = {spec.name: set(spec.property_types) for spec in m.MUST_ITEMS}
    assert actual == EXPECTED_MUST_TYPES
