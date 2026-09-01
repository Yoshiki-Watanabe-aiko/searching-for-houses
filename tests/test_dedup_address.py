"""住所正規化のテスト。

期待値はすべて 2026-09-02 に実DB（301掲載）から採った実表記。
「作った仕様」ではなく「サイトが実際に返す形」を固定している。
"""

from __future__ import annotations

import pytest

from house_search.dedup.address import (
    GRANULARITY_CHOME,
    GRANULARITY_NONE,
    GRANULARITY_TOWN,
    address_granularity,
    normalize_address,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # ABLE は住所欄の末尾に「周辺地図」が入る
        ("東京都足立区梅田７丁目周辺地図", "東京都足立区梅田7丁目"),
        # APAMAN は全角スペース区切りで番地まで書く
        ("東京都 足立区 南花畑 ４丁目 35-15", "東京都足立区南花畑4丁目"),
        # EHEYA は丁目が漢数字
        ("東京都足立区伊興四丁目", "東京都足立区伊興4丁目"),
        ("東京都足立区東和５丁目", "東京都足立区東和5丁目"),
        # GOO・SUUMO・スモッカは「丁目」を省いて数字だけのことがある
        ("東京都足立区古千谷本町１", "東京都足立区古千谷本町1丁目"),
        ("東京都足立区東和５", "東京都足立区東和5丁目"),
        ("東京都八王子市大和田町１", "東京都八王子市大和田町1丁目"),
        # HOME'S は番地まで書く。丁目で打ち切らないと他サイトと一致しない
        ("東京都足立区谷中1丁目28-1", "東京都足立区谷中1丁目"),
        ("東京都足立区西保木間１丁目17-11", "東京都足立区西保木間1丁目"),
        # 丁目表記が無く番-号だけの都市部表記も丁目まで寄せる
        ("東京都新宿区西早稲田3-1-1", "東京都新宿区西早稲田3丁目"),
        # SUUMO は丁目そのものが無いことがある（町名までで確定）
        ("千葉県長生郡白子町剃金", "千葉県長生郡白子町剃金"),
        ("神奈川県相模原市緑区中野", "神奈川県相模原市緑区中野"),
        # 「大字」はサイトによって書いたり書かなかったりする
        ("埼玉県比企郡川島町大字上伊草", "埼玉県比企郡川島町上伊草"),
        ("埼玉県比企郡川島町上伊草", "埼玉県比企郡川島町上伊草"),
    ],
)
def test_実サイトの住所表記が同じ正規形になる(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(
    ("word", "expected"),
    [("十丁目", 10), ("十二丁目", 12), ("二十丁目", 20), ("二十三丁目", 23)],
)
def test_漢数字の丁目を算用数字にする(word: str, expected: int) -> None:
    assert normalize_address(f"東京都足立区千住{word}") == f"東京都足立区千住{expected}丁目"


def test_ヶとケを平仮名のがへ寄せる() -> None:
    normalized = {
        normalize_address(f"東京都千代田区霞{mark}関1丁目") for mark in ("ヶ", "ケ", "が")
    }
    assert normalized == {"東京都千代田区霞が関1丁目"}


def test_都道府県が無い住所には解決済みの都道府県を前置する() -> None:
    # 賃貸EX は「足立区竹の塚６」のように都道府県を書かない
    assert normalize_address("足立区竹の塚６", prefecture="東京都") == "東京都足立区竹の塚6丁目"
    # 既に都道府県から始まっていれば二重に付けない
    assert (
        normalize_address("東京都足立区竹の塚６", prefecture="東京都") == "東京都足立区竹の塚6丁目"
    )


@pytest.mark.parametrize("raw", [None, "", "　", "周辺地図"])
def test_住所として使えない入力はNoneを返す(raw: str | None) -> None:
    assert normalize_address(raw) is None


def test_粒度を分類できる() -> None:
    assert address_granularity("東京都足立区梅田7丁目") == GRANULARITY_CHOME
    assert address_granularity("千葉県長生郡白子町剃金") == GRANULARITY_TOWN
    assert address_granularity(None) == GRANULARITY_NONE
