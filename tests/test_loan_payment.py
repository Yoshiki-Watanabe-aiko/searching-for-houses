"""売買の通知に出す月々の負担のテスト（→ 課題#57）。

⚠⚠ **「月々」という語が2つの違うものを指していた。**
こちらの通知は `monthly_cost`（管理費＋修繕積立金）を「月々」と書いていたが、
SUUMO の物件ページの「月々の支払額」は**住宅ローンの返済額**である。
1億5,480万円の物件で前者は 40,340円・後者は約40万円になるため、
**ちょうど10倍に見えて「40万円が4万円と表示されている」と誤読された**
（2026-09-07 ユーザー報告）。両方を名前付きで出して区別する。

⚠ **ローン返済額は metric にしない。** `price` の完全な関数なので、
metric にすると価格軸に二重の重みが掛かる
（要件定義書 §5.3 の「坪単価・㎡単価を metric にしない」と同じ理由）。表示専用。
"""

from __future__ import annotations

from house_search.notify.format import (
    LOAN_ANNUAL_RATE,
    LOAN_YEARS,
    monthly_loan_payment,
    price_field,
)
from test_notify import make_prop


def _buy_prop(**overrides):
    """マンション売買の掲載。⚠ `property_family` が無いと賃貸表示になる。"""
    defaults = {
        "property_family": "MANSION_BUY",
        "price": 154_800_000,
        "mgmt_fee_monthly": 28_820,
        "repair_reserve_monthly": 11_520,
        "rent_total": 154_828_820,  # ⚠ 生成列なので売買でも値が入る（→ 課題#4）
    }
    return make_prop(**{**defaults, **overrides})


# --- 返済額の計算（純関数） ----------------------------------------------


def test_元利均等返済の月額を計算する() -> None:
    """1億5,480万円・年0.5%・35年・頭金0円 → 約40.2万円。

    ⚠ **この値が「40万円」の正体**。管理費等の 40,340円 と桁が1つ違う。
    """
    assert monthly_loan_payment(154_800_000) == 401_838


def test_既定の前提は変動0_5パーセント35年() -> None:
    """⚠ 前提を変えれば数字も変わる。既定値を固定しておく。"""
    assert LOAN_ANNUAL_RATE == 0.005
    assert LOAN_YEARS == 35


def test_金利と年数を上書きできる() -> None:
    assert monthly_loan_payment(50_000_000, annual_rate=0.019, years=35) == 163_077


def test_金利0なら単純割り算になる() -> None:
    """⚠ ゼロ除算を避ける。金利0%は借入額を回数で割るだけ。"""
    assert monthly_loan_payment(4_200_000, annual_rate=0.0, years=35) == 10_000


def test_価格が無ければ返済額も出さない() -> None:
    """⚠ 新築は価格未定が実在する。0円にすると「安い」と誤読される。"""
    assert monthly_loan_payment(None) is None
    assert monthly_loan_payment(0) is None


# --- 通知の金額欄 --------------------------------------------------------


def test_売買の金額欄にローンと管理費等と総額が並ぶ() -> None:
    heading, body = price_field(_buy_prop())

    assert heading == "価格"
    assert "1億5,480万円" in body
    assert "ローン 401,838円" in body
    assert "管理費等 40,340円" in body
    assert "月々 442,178円" in body


def test_金額はカンマ区切りの円で出す() -> None:
    """⚠ 万円表記だと「4.0万円」が 40,340円 の意味だと読み取りにくい。

    誤読の再発を防ぐため、桁がそのまま見える円表記を固定する。
    """
    _, body = price_field(_buy_prop())

    assert "4.0万円" not in body
    assert "40.2万円" not in body


def test_計算の前提を必ず併記する() -> None:
    """⚠ 前提を書かないと数字だけが一人歩きする。"""
    _, body = price_field(_buy_prop())

    assert "35年" in body
    assert "0.5%" in body
    assert "頭金0円" in body


def test_管理費が未取得なら総額を出さない() -> None:
    """⚠ 0円として合計すると総額が小さく見える（新築の棟は管理費が無い）。"""
    _, body = price_field(_buy_prop(mgmt_fee_monthly=None, repair_reserve_monthly=None))

    assert "ローン 401,838円" in body
    assert "管理費等 不明" in body
    assert "月々" not in body


def test_価格未定ならローンも出さない() -> None:
    """⚠ 「価格未定」と明示する（0円やハイフンだと「安い」と誤読される）。"""
    _, body = price_field(_buy_prop(price=None))

    assert "価格未定" in body
    assert "ローン" not in body
    assert "管理費等 40,340円" in body


def test_賃貸の金額欄は変わらない() -> None:
    """回帰。⚠ 賃貸にローン返済額を出さない（`property_family` は None）。"""
    heading, body = price_field(make_prop())

    assert heading == "月額"
    assert "ローン" not in body
    assert "60,000円" in body
