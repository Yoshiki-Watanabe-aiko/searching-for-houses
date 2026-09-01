"""MetricRegistry のテスト。"""

from __future__ import annotations

import pytest

from house_search.config import metrics as m


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
    assert m.METRICS_BY_NAME["rent_total"].property_types == frozenset({m.CHINTAI})
    assert m.METRICS_BY_NAME["price"].property_types == m.BUY_TYPES


def test_徒歩分数は全種別で使える() -> None:
    assert m.METRICS_BY_NAME["walk_minutes"].property_types == m.ALL_PROPERTY_TYPES


def test_metrics_forの並びは決定的() -> None:
    """スコア加算順を安定させるため、レジストリの並びは定義順で固定する。"""
    assert [s.name for s in m.metrics_for(m.CHINTAI)] == [
        "rent_total",
        "area_sqm",
        "age_years",
        "walk_minutes",
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
    assert list_only == {"monthly_cost_max", "floor_min", "features"}


def test_全種別にファミリが割り当てられている() -> None:
    assert set(m.FAMILY_OF) == m.ALL_PROPERTY_TYPES
    assert set(m.FAMILY_OF.values()) == set(m.Family)
