"""Discord 通知に併記する「周辺相場」の表示（→ 課題#70）。

⚠ **ここで固定したいのは「静かに間違う」形の欠陥**である。相場の表示は
金額が出てさえいれば一見もっともらしく、例外にも件数の変化にもならない:

- 賃貸の分子に光熱費込みの月額を使う（比が SQL の値と食い違う → ADR 0025）
- 売買で専有と延床を取り違える（水準だけがずれる → 課題#70）
- 土地の「未収録」を「不明」と書く（相場の収集漏れに見える）
- `notifiable_from` の渡し忘れで全件が「相場不明」になる（落ちない）
"""

from __future__ import annotations

import pytest
from tests.test_notify import make_prop, make_score

from house_search.notify.format import (
    SEGMENT_AREA,
    STAT_BASIS_LABELS,
    NotifiableListing,
    build_listing_embed,
    market_rate_body,
    market_rate_summary,
    notifiable_from,
)
from house_search.scoring.listing_view import ListingView, MarketRateRef
from house_search.scoring.utility import UtilityEstimate

CHINTAI_RATE = MarketRateRef(
    rate_value=97_000.0, segment="2DK", stat_basis="rent_listed_mansion", period="2026-09"
)


def _market_field(prop: NotifiableListing) -> dict[str, object]:
    embed = build_listing_embed(
        prop, make_score(), notification_type="new", pattern_name="東京23区賃貸"
    )
    fields = [f for f in embed["fields"] if f["name"] == "周辺相場"]
    assert len(fields) == 1, "周辺相場の欄は常にちょうど1つ"
    return fields[0]


# --- 賃貸 --------------------------------------------------------------------


def test_賃貸は賃料と管理費の合計を相場と比べる() -> None:
    prop = make_prop(
        market_rate=CHINTAI_RATE, market_rate_ratio=60_000 / 97_000, city_name="足立区"
    )

    body = market_rate_body(prop)

    assert "相場比 0.62" in body
    assert "60,000円" in body
    assert "97,000円/月" in body
    assert "足立区・2DK" in body
    assert "スーモ掲載賃料・マンション" in body
    assert "2026-09" in body


def test_賃貸の分子に光熱費込みの月額を使わない() -> None:
    """⚠⚠ 相場比の分子は `rent_total` で、`living_cost` ではない（→ ADR 0025）。

    混ぜても例外にならず、光熱費込みの額 ÷ 家賃相場という無意味な比が出るだけ。
    """
    utility = UtilityEstimate(
        monthly_yen=10_201,
        gas="city",
        gas_basis="listed",
        stove="gas",
        stove_basis="listed",
        household_size=2,
        lpg_probability=None,
        city_gas_yen=None,
    )
    prop = make_prop(
        utility=utility,
        market_rate=CHINTAI_RATE,
        market_rate_ratio=60_000 / 97_000,
        city_name="足立区",
    )

    body = market_rate_body(prop)

    assert "60,000円" in body
    assert "70,201" not in body, "光熱費込みの月額を分子にしている"
    # 「光熱費は含まない」の注記としてだけ現れる
    assert "光熱費は含まない" in body


def test_賃貸で賃料が不明なら比較不可と出し相場は出す() -> None:
    """⚠ 「相場が無い」と「比べる材料が無い」を混ぜない。"""
    prop = make_prop(
        price=None, rent_total=None, market_rate=CHINTAI_RATE, city_name="足立区"
    )

    body = market_rate_body(prop)

    assert "97,000円/月" in body
    assert "比較不可" in body
    assert "相場不明" not in body


# --- 売買 --------------------------------------------------------------------


def _buy_prop(**overrides: object) -> NotifiableListing:
    base: dict[str, object] = {
        "listing_id": 9,
        "site_code": "SUUMO",
        "url": "https://suumo.jp/ms/chuko/tokyo/sc_setagaya/nc_1/",
        "title": "テストマンション",
        "price": 40_000_000,
        "mgmt_fee_monthly": 12_000,
        "rent_total": None,
        "layout": "3LDK",
        "area_sqm": 60.0,
        "age_years": 15,
        "walk_minutes": 7,
        "address": "東京都世田谷区",
        "property_family": "MANSION_BUY",
        "city_name": "世田谷区",
    }
    return NotifiableListing(**{**base, **overrides})  # type: ignore[arg-type]


def test_売買マンションは専有面積で換算する() -> None:
    rate = MarketRateRef(
        rate_value=596_000.0,
        segment="AREA_SQM",
        stat_basis="trade_unit_price_02",
        period="2025Q2-2026Q1",
    )
    ratio = (40_000_000 / 60.0) / 596_000.0
    prop = _buy_prop(market_rate=rate, market_rate_ratio=ratio)

    body = market_rate_body(prop)

    assert "専有㎡単価" in body
    assert "59.6万円/㎡" in body
    assert "同じ専有 60.0㎡なら相場 3,576万円" in body
    assert "国交省 成約価格" in body


def test_売買戸建ては延床で換算し専有面積を使わない() -> None:
    """⚠⚠ 面積の取り違えは例外にならず、水準だけが静かにずれる（→ 課題#70）。

    `area_sqm` にあり得ない値を入れてあるので、そちらを使えば必ず落ちる。
    """
    rate = MarketRateRef(
        rate_value=340_000.0,
        segment="FLOOR_SQM",
        stat_basis="trade_unit_price_01",
        period="2025Q2-2026Q1",
    )
    ratio = (30_000_000 / 95.0) / 340_000.0
    prop = _buy_prop(
        property_family="KODATE_BUY",
        price=30_000_000,
        area_sqm=999.0,
        building_area_sqm=95.0,
        land_area_sqm=120.0,
        city_name="さいたま市西区",
        market_rate=rate,
        market_rate_ratio=ratio,
    )

    body = market_rate_body(prop)

    assert "延床㎡単価" in body
    assert "同じ延床 95.0㎡なら相場 3,230万円" in body
    assert "999" not in body, "専有面積で換算している"
    assert "120.0" not in body, "土地面積で換算している"


def test_未知の区分は換算せず単価だけ出す() -> None:
    """⚠ 知らない区分でどれかの面積を推測で掛けない（例外にもしない）。"""
    rate = MarketRateRef(
        rate_value=500_000.0, segment="UNKNOWN_SQM", stat_basis="trade_unit_price_01",
        period="2025Q2-2026Q1",
    )
    prop = _buy_prop(market_rate=rate, market_rate_ratio=1.1)

    body = market_rate_body(prop)

    assert "㎡単価" in body
    assert "専有" not in body
    assert "延床" not in body


def test_未知の出典はコードのまま出す() -> None:
    rate = MarketRateRef(
        rate_value=500_000.0, segment="AREA_SQM", stat_basis="brand_new_source",
        period="2026Q1",
    )
    prop = _buy_prop(market_rate=rate, market_rate_ratio=1.1)

    assert "brand_new_source" in market_rate_body(prop)


# --- 相場が無いとき ----------------------------------------------------------


def test_土地は未収録であって不明ではない() -> None:
    """⚠ 土地の相場は構造的に無い（課題#61 の 9d）。「不明」だと収集漏れに見える。"""
    prop = _buy_prop(
        property_family="TOCHI_BUY", land_area_sqm=140.0, area_sqm=None, city_name="市川市"
    )

    body = market_rate_body(prop)

    assert body == "相場 未収録（土地の相場データは未整備）"
    assert "不明" not in body
    assert market_rate_summary(prop) == "相場未収録"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"city_name": None}, "市区を特定できず"),
        ({"city_name": "足立区", "layout": None}, "間取り不明"),
        ({"city_name": "足立区", "layout": "2LDK"}, "足立区・2LDK の相場は相場マスタに無い"),
    ],
)
def test_相場が引けないときは理由まで書く(overrides: dict, expected: str) -> None:
    body = market_rate_body(make_prop(**overrides))

    assert "相場不明" in body
    assert expected in body


def test_何も渡さない既定でも通知が落ちない() -> None:
    """⚠ `notifiable_from` の渡し忘れはこの形で現れる（全件が相場不明になるだけ）。"""
    field = _market_field(make_prop())

    assert field["value"].startswith("相場不明")


# --- 詰め替えの配線 ----------------------------------------------------------


def test_採点ビューから通知へ相場が渡る() -> None:
    """⚠⚠ 純関数が緑でも、詰め替えが無ければ本番は全件「相場不明」になる。"""
    view = ListingView(
        price=30_000_000,
        area_sqm=999.0,
        building_area_sqm=95.0,
        property_family="KODATE_BUY",
        market_rate_ratio=0.93,
        market_rate=MarketRateRef(
            rate_value=340_000.0,
            segment="FLOOR_SQM",
            stat_basis="trade_unit_price_01",
            period="2025Q2-2026Q1",
        ),
        city_name="さいたま市西区",
    )

    prop = notifiable_from(view)

    assert prop.market_rate_ratio == pytest.approx(0.93)
    assert prop.market_rate is not None
    assert prop.market_rate.segment == "FLOOR_SQM"
    assert prop.city_name == "さいたま市西区"
    # ⚠ 戸建ての換算に要る。渡し忘れると面積表記だけが静かに消える
    assert prop.building_area_sqm == pytest.approx(95.0)


def test_出典と区分の表がマスタ側の定数と食い違っていない() -> None:
    """⚠ `notify.format` は lxml を持ち込まないためコードを直書きしている。

    正典（`market.soba` / `market.rates`）が変わったらここで気づけるようにする。
    未知でも通知は落ちない（コードのまま出る）が、読みにくくなる。
    """
    from house_search.market.rates import BUY_STAT_BASIS
    from house_search.market.soba import STAT_BASIS_APART, STAT_BASIS_MANSION

    assert {STAT_BASIS_MANSION, STAT_BASIS_APART} <= set(STAT_BASIS_LABELS)
    assert set(STAT_BASIS_LABELS) >= BUY_STAT_BASIS
    # 売買の区分は m_market_rates の segment と対。土地を入れるときは両方触る
    assert set(SEGMENT_AREA) == {"AREA_SQM", "FLOOR_SQM", "LAND_SQM"}


# --- ダイジェスト ------------------------------------------------------------


def test_ダイジェストの相場は短い() -> None:
    """⚠ 上位15件を1つの description に詰めるので、1件あたりの追加は20字以内。"""
    prop = make_prop(
        market_rate=CHINTAI_RATE, market_rate_ratio=60_000 / 97_000, city_name="足立区"
    )

    summary = market_rate_summary(prop)

    assert summary == "相場比0.62（相場9.7万円/月）"
    assert len(summary) <= 20
    assert len(market_rate_summary(make_prop())) <= 20


def test_売買のダイジェストも20字に収まる() -> None:
    """⚠ 実データで一番長くなるのは億超えの売買（→ 課題#70 で実測）。"""
    rate = MarketRateRef(
        rate_value=1_047_000.0,
        segment="AREA_SQM",
        stat_basis="trade_unit_price_02",
        period="2025Q2-2026Q1",
    )
    ratio = (153_800_000 / 77.3) / 1_047_000.0
    prop = _buy_prop(price=153_800_000, area_sqm=77.3, market_rate=rate,
                     market_rate_ratio=ratio)

    summary = market_rate_summary(prop)

    assert summary.startswith("相場比")
    assert "8,093万円" in summary
    assert len(summary) <= 20
