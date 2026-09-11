"""土地（TOCHI）の通知表示（→ 課題#61 論点4・論点5）。

- 論点4（2026-09-11 決定）: **価格とローン返済額を出し、管理費欄は出さず
  「土地のみ・建物代別」と注記する。**
- 論点5（同）: 建築条件付き・借地権・再建築不可は**取り込んで通知に明示する**
  （除外 MUST は後で設計する）。

⚠⚠ 土地に売買の表示をそのまま当てると、条件欄に ``— / — / 築年不明`` が並んで
**土地面積が出ず**、金額欄には ``管理費等 不明`` が出る（土地に管理費は無い）。
例外にならないまま誤読の元になる（計画書 §10 のリスク表）。
"""

from __future__ import annotations

from house_search.notify.format import (
    LOAN_NOTE,
    DigestEntry,
    build_digest_message,
    build_listing_embed,
    monthly_loan_payment,
    notifiable_from,
    price_field,
    price_summary,
)
from house_search.scoring.listing_view import ListingView
from test_notify import make_prop, make_score


def _land(**overrides):
    """土地の掲載。⚠ `property_family` が無いと賃貸表示になる。"""
    defaults = {
        "property_family": "TOCHI_BUY",
        "title": "メルディア世田谷区北烏山",
        "price": 87_300_000,
        "mgmt_fee_monthly": None,
        "rent_total": 87_300_000,  # ⚠ 生成列なので売買でも値が入る（→ 課題#4）
        "layout": None,
        "area_sqm": None,
        "age_years": None,
        "land_area_sqm": 108.79,
        "walk_minutes": 20,
        "commute_minutes": 40,
        "address": "東京都世田谷区北烏山７",
    }
    return make_prop(**{**defaults, **overrides})


def _conditions(prop) -> str:
    embed = build_listing_embed(
        prop, make_score(), notification_type="new", pattern_name="土地"
    )
    return next(field["value"] for field in embed["fields"] if field["name"] == "条件")


class Test金額欄:
    def test_価格とローン返済額を出す(self) -> None:
        name, body = price_field(_land())
        assert name == "価格"
        assert body.startswith("8,730万円")
        assert f"ローン {monthly_loan_payment(87_300_000):,}円/月" in body
        assert LOAN_NOTE in body

    def test_管理費の欄を出さず土地のみと注記する(self) -> None:
        """⚠ 土地に管理費・修繕積立金は無い。「管理費等 不明」と出すと、
        **取れていないだけ**に読めてしまう。"""
        _name, body = price_field(_land())
        assert "管理費" not in body
        assert "土地のみ" in body
        assert "建物代別" in body

    def test_価格未定でも土地のみと注記する(self) -> None:
        _name, body = price_field(_land(price=None, rent_total=None))
        assert body.startswith("価格未定")
        assert "ローン" not in body  # ⚠ 価格が無ければ返済額は計算できない
        assert "土地のみ" in body

    def test_ダイジェストの金額は価格だけ(self) -> None:
        assert price_summary(_land()) == "8,730万円"

    def test_戸建ての金額欄は変えない(self) -> None:
        """⚠ 土地の分岐で戸建て・マンションの表示を巻き込まない。"""
        kodate = make_prop(
            property_family="KODATE_BUY",
            price=39_800_000,
            mgmt_fee_monthly=None,
            rent_total=39_800_000,
        )
        _name, body = price_field(kodate)
        assert "管理費等 不明" in body
        assert "土地のみ" not in body


class Test条件欄:
    def test_土地面積と徒歩と通勤を出す(self) -> None:
        """⚠ 間取り・専有面積・築年は土地に無い。「— / — / 築年不明」を並べない。"""
        assert _conditions(_land()) == "土地108.8㎡ / 徒歩20分 / 通勤40分"

    def test_土地面積が無ければ不明と明示する(self) -> None:
        assert _conditions(_land(land_area_sqm=None)).startswith("土地面積不明")

    def test_バス便は徒歩不明と明示する(self) -> None:
        assert "徒歩不明" in _conditions(_land(walk_minutes=None))

    def test_注意事項を条件欄に出す(self) -> None:
        """⚠ 論点5: 建築条件付き・借地権・再建築不可は**明示する**。"""
        text = _conditions(_land(caveats=("建築条件付き", "借地権")))
        assert text.endswith("⚠建築条件付き・借地権")

    def test_ダイジェストの1行にも注意事項を出す(self) -> None:
        entry = DigestEntry(rank=1, prop=_land(caveats=("再建築不可",)), score=make_score())
        description = build_digest_message([entry], pattern_name="土地")["embeds"][0][
            "description"
        ]
        assert "土地108.8㎡" in description
        assert "⚠再建築不可" in description

    def test_賃貸の条件欄は変えない(self) -> None:
        assert _conditions(make_prop()) == "2DK / 38.0㎡ / 築12年 / 徒歩8分"


def test_通知へ詰め替えるとき土地面積と注意事項を渡す() -> None:
    """⚠⚠ **純関数のテストは機能の生存を保証しない。** 表示の関数が正しくても、
    詰め替え（``notifiable_from``）が値を渡していなければ通知には出ない。"""
    view = ListingView(
        listing_id=7,
        property_family="TOCHI_BUY",
        price=10_000_000,
        land_area_sqm=150.5,
        caveats=("建築条件付き",),
    )
    prop = notifiable_from(view)
    assert prop.land_area_sqm == 150.5
    assert prop.caveats == ("建築条件付き",)
