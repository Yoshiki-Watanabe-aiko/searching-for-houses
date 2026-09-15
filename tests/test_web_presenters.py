"""閲覧画面の絞り込み条件と表示の詰め替え（DB不要）。"""

from __future__ import annotations

import datetime as dt

import pytest

from house_search.scoring.listing_view import ListingView
from house_search.web import presenters
from house_search.web.filters import InvalidFilterError, ListingFilter

# ============================================================
# 絞り込み条件
# ============================================================


def test_空欄は未指定として読む() -> None:
    flt = ListingFilter.from_query({"price_max": "", "walk_max": " ", "sort": ""})
    assert flt == ListingFilter()


def test_チェックボックスは値の有無で読む() -> None:
    flt = ListingFilter.from_query({"favorites": "1", "include_excluded": ""})
    assert flt.favorites is True
    assert flt.include_excluded is False


@pytest.mark.parametrize(
    "query",
    [
        {"sort": "price; DROP TABLE t_listings"},
        {"site": "suumo' OR 1=1"},
        {"price_max": "-1"},
        {"page": "0"},
        {"must": "fail"},
        {"city": "abc"},
    ],
)
def test_許可していない値は弾く(query: dict[str, str]) -> None:
    with pytest.raises(InvalidFilterError):
        ListingFilter.from_query(query)


def test_知らないキーは無視する() -> None:
    assert ListingFilter.from_query({"utm_source": "x"}) == ListingFilter()


def test_ページ送りのリンクには既定値を載せない() -> None:
    flt = ListingFilter.from_query({"price_max": "12.5", "favorites": "1", "page": "2"})
    assert flt.to_query(page=3) == {"price_max": 12.5, "favorites": "1", "page": 3}
    assert ListingFilter().to_query() == {}


# ============================================================
# 表示
# ============================================================


def test_ハザードの未解決と区域外を書き分ける() -> None:
    """⚠⚠ None（照合できず）を 0（区域外）と同じに見せない（→ ADR 0021）。"""
    view = ListingView(flood_rank_max=0.0, flood_rank_avg=None, liquefaction_rank_avg=1.0)
    items = {item.label: item for item in presenters.hazard_items(view)}

    flood_max = next(v for k, v in items.items() if "最大浸水深" in k)
    flood_avg = next(v for k, v in items.items() if "面積加重平均（0〜6）" in k)
    liquefaction = next(v for k, v in items.items() if "液状化" in k)

    assert flood_max.resolved and "区域外" in flood_max.value_text
    assert not flood_avg.resolved and "未解決" in flood_avg.value_text
    assert "区域外" not in liquefaction.value_text


def test_採点内訳の欠損は分母から外したと明示する() -> None:
    items = presenters.breakdown_items(
        [
            {"code": "walk_minutes", "name": "駅徒歩", "kind": "numeric", "weight": 15,
             "s": 0.0, "points": 0.0, "status": "unknown", "missing": True},
            {"code": "living_cost", "name": "月額", "kind": "numeric", "weight": 40,
             "s": 0.5, "points": 20.0, "status": "hit", "value": 98765.4},
            {"code": "flood_area_ratio", "name": "浸水", "kind": "numeric", "weight": 5,
             "s": 1.0, "points": 5.0, "status": "hit", "value": 0.125},
        ]
    )
    assert items[0].status_label == "欠損（分母から除外）"
    assert items[1].value_text == "98,765円/月"
    assert items[2].value_text == "12.5%"


def test_必須条件の値を単位つきで書く() -> None:
    assert presenters.format_must_value("rent_total_max", 150000) == "150,000円"
    assert presenters.format_must_value("walk_minutes_max", 20.0) == "20分"
    assert presenters.format_must_value("layouts", ["2LDK", "1LDK"]) == "1LDK、2LDK"
    assert presenters.format_must_value("freehold_only", True) == "はい"
    assert presenters.format_must_value("area_min", None) == "不明"


def test_日時は日本時間で出す() -> None:
    value = dt.datetime(2026, 9, 15, 3, 0, tzinfo=dt.UTC)
    assert presenters.format_datetime(value) == "2026-09-15 12:00"
    assert presenters.format_datetime(None) == "—"


def test_駅は通知と違って打ち切らずバス便の徒歩は不明と書く() -> None:
    from house_search.scoring.listing_view import StationAccess

    stations = tuple(StationAccess(name=f"駅{i}", walk_minutes=None) for i in range(6))
    items = presenters.station_items(ListingView(stations=stations))
    assert len(items) == 6
    assert items[0].walk_text == "徒歩不明"


def test_原文項目の真偽値と入れ子を読める形にする() -> None:
    items = presenters.attr_items({"b": True, "a": {"x": 1}, "c": None})
    assert [(item.key, item.value) for item in items] == [
        ("a", '{"x": 1}'),
        ("b", "はい"),
        ("c", "—"),
    ]
