"""SUUMO 家賃相場ページのパーサのテスト（実HTMLフィクスチャ）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.market.soba import (
    STAT_BASIS_APART,
    STAT_BASIS_MANSION,
    SobaParseError,
    SobaRate,
    merge_rates,
    parse_soba,
)
from house_search.scoring.listing_view import normalize_layout

FIXTURES = Path(__file__).parent / "fixtures" / "suumo_soba"
ADACHI = (FIXTURES / "13121_adachi.html").read_text(encoding="utf-8", errors="replace")


def test_足立区の相場を間取りごとに取り出せる() -> None:
    rates = {r.layout: r.rent_yen for r in parse_soba(ADACHI)}
    # 実ページの表示（2026-09-05 取得）と一致すること
    assert rates["1LDK"] == 129_000
    assert rates["2DK"] == 130_000
    assert rates["2LDK"] == 159_000
    assert rates["3LDK"] == 199_000
    assert rates["1R"] == 77_000


def test_MUSTで使う間取りがすべて揃う() -> None:
    """⚠ 相場が引けない間取りがあると、その掲載だけ採点軸が1本減る。

    欠けても例外にならず missing になるだけなので、実運用の間取りが
    揃っていることを固定する。
    """
    rates = {r.layout for r in parse_soba(ADACHI)}
    must_layouts = {"1LDK", "2K", "2DK", "2LDK", "3DK", "3LDK"}
    assert must_layouts <= rates


def test_ワンルームは1Rへ正規化される() -> None:
    """⚠ ページは「ワンルーム」、DB は `1R`。

    集計側と採点側で別の正規化を当てると、突き合わせが0件になったとき
    「相場が無い」のか「表記がずれている」のかを区別できない。
    どちらも `normalize_layout` を通すことで揃える。
    """
    assert normalize_layout("ワンルーム") == "1R"
    assert any(r.layout == "1R" for r in parse_soba(ADACHI))


def test_万円以外の数値を相場として拾わない() -> None:
    """⚠ 単位を確かめずに数値だけ拾うと、面積や件数が相場になる。"""
    rates = {r.layout: r.rent_yen for r in parse_soba(ADACHI)}
    # 家賃相場が数万〜数十万円の範囲に収まっていること
    assert all(30_000 <= yen <= 1_000_000 for yen in rates.values()), rates


def test_相場表が無ければ例外にする() -> None:
    """⚠ 0件を黙って返すと、相場が無いまま正常終了して気づけない。"""
    with pytest.raises(SobaParseError):
        parse_soba("<html><body><table><tr><td>なにもない</td></tr></table></body></html>")


def test_マンションに無い間取りだけアパートで補完される() -> None:
    """⚠ 2DK はマンションの掲載が少なく 26市区で相場が出ない（2026-09-05 実測）。

    アパート相場で埋めると 56 → 約80市区になる。⚠ ただし建物種別で相場は
    0.75〜0.92 倍と系統的に違うので、**どちらの相場かを行に残す**。
    """
    mansion = [SobaRate(layout="1LDK", rent_yen=124_000)]
    apart = [SobaRate(layout="1LDK", rent_yen=111_000), SobaRate(layout="2DK", rent_yen=127_000)]
    merged = {r.layout: r for r in merge_rates(mansion, apart)}

    assert merged["1LDK"].rent_yen == 124_000, "マンションにある間取りは上書きしない"
    assert merged["1LDK"].stat_basis == STAT_BASIS_MANSION
    assert merged["2DK"].rent_yen == 127_000, "マンションに無い間取りはアパートで埋める"
    assert merged["2DK"].stat_basis == STAT_BASIS_APART


def test_アパート相場が無ければマンションだけを返す() -> None:
    mansion = [SobaRate(layout="2DK", rent_yen=130_000)]
    merged = merge_rates(mansion, [])
    assert [(r.layout, r.rent_yen, r.stat_basis) for r in merged] == [
        ("2DK", 130_000, STAT_BASIS_MANSION)
    ]


def test_補完の結果は間取り順で決まる() -> None:
    """⚠ 出力順が揺れるとCSVの差分が読めなくなる（決定性）。"""
    mansion = [SobaRate(layout="2LDK", rent_yen=159_000)]
    apart = [SobaRate(layout="1K", rent_yen=71_000), SobaRate(layout="2DK", rent_yen=127_000)]
    assert [r.layout for r in merge_rates(mansion, apart)] == ["1K", "2DK", "2LDK"]
