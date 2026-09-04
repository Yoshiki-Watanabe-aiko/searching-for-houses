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
    AddressIndex,
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


# ---------------------------------------------------------------------------
# 丁目の実在判定（→ ADR 0020・課題#48）
# ---------------------------------------------------------------------------


@pytest.fixture
def address_index() -> AddressIndex:
    """住所マスタの索引。値は位置参照情報（令和7年版）の実データから採った。

    - 深谷市中瀬・横浜市緑区長津田町・茂原市早野 … 丁目が存在しない大字（区分1）
    - 川越市砂新田 … 1〜6丁目が実在する（7丁目以降は無い）
    - 足立区千住 … 1〜5丁目が実在する
    """
    return AddressIndex.build(
        [
            ("埼玉県深谷市中瀬", None),
            ("神奈川県横浜市緑区長津田町", None),
            ("千葉県茂原市早野", None),
            *(("埼玉県川越市砂新田", n) for n in range(1, 7)),
            *(("東京都足立区千住", n) for n in range(1, 6)),
        ]
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 丁目が存在しない町。数字は番地なので町名まで下げる
        # （修正前は「埼玉県深谷市中瀬1480丁目」という実在しない住所になっていた）
        ("埼玉県深谷市中瀬1480-1", "埼玉県深谷市中瀬"),
        ("埼玉県深谷市中瀬1480", "埼玉県深谷市中瀬"),
        # APAMAN だけが番地を持つため 18掲載が分断されていた実例
        ("神奈川県横浜市緑区長津田町2436-4", "神奈川県横浜市緑区長津田町"),
        # 丁目はあるが番号が範囲外（砂新田は7丁目まで）
        ("埼玉県川越市砂新田1763", "埼玉県川越市砂新田"),
        # 実在する丁目はこれまでどおり丁目として採る
        ("埼玉県川越市砂新田3-5", "埼玉県川越市砂新田3丁目"),
        ("東京都足立区千住3-1-1", "東京都足立区千住3丁目"),
        # 小字つき（APAMAN・goo が実際に返す形）。マスタは小字を持たないので
        # 「字」の手前を町名として引き直す
        ("千葉県茂原市早野字下夕田1079", "千葉県茂原市早野"),
        ("千葉県茂原市早野1079", "千葉県茂原市早野"),
        # 町がマスタに無ければ判定材料が無いので従来どおり（安全側のフォールバック）。
        # 神奈川県横浜市中区花咲町は実在するが、この索引には入れていない
        ("神奈川県横浜市中区花咲町1234", "神奈川県横浜市中区花咲町1234丁目"),
    ],
)
def test_丁目が実在するときだけ数字塊を丁目として採る(
    raw: str, expected: str, address_index: AddressIndex
) -> None:
    assert normalize_address(raw, index=address_index) == expected


def test_明示的な丁目表記はマスタを見ずに信じる(address_index: AddressIndex) -> None:
    # マスタに9丁目は無いが、サイトが「丁目」と書いている以上そのまま採る
    # （推測ではないので実在判定に掛けない）。
    result = normalize_address("東京都足立区千住9丁目1-2", index=address_index)
    assert result == "東京都足立区千住9丁目"


def test_索引を渡さなければ従来どおりの挙動になる() -> None:
    # マスタが未同期の環境で結果がぶれないようにするための後方互換。
    assert normalize_address("埼玉県深谷市中瀬1480-1") == "埼玉県深谷市中瀬1480丁目"
    nagatsuta = normalize_address("神奈川県横浜市緑区長津田町2436-4")
    assert nagatsuta == "神奈川県横浜市緑区長津田町2436丁目"


def test_空の索引は全件フォールバックと同義なので検出できる() -> None:
    empty = AddressIndex.build([])
    assert empty.is_empty
    assert normalize_address("埼玉県深谷市中瀬1480-1", index=empty) == "埼玉県深谷市中瀬1480丁目"


def test_索引は町と丁目の実在をそれぞれ答える(address_index: AddressIndex) -> None:
    assert not address_index.is_empty
    assert address_index.has_town("埼玉県深谷市中瀬")
    assert not address_index.has_chome("埼玉県深谷市中瀬", 1480)
    assert address_index.has_chome("埼玉県川越市砂新田", 6)
    assert not address_index.has_chome("埼玉県川越市砂新田", 7)
    assert not address_index.has_town("神奈川県横浜市中区花咲町")


def test_町名の一部に字を含む地名は壊さない(address_index: AddressIndex) -> None:
    # 小田原市「十字四丁目」は「字」が町名の一部。明示的な丁目表記なのでそもそも
    # 実在判定に掛からないが、「字」を無条件に落とす実装にすると壊れる形として固定する。
    assert normalize_address("神奈川県小田原市十字四丁目1-2", index=address_index) == (
        "神奈川県小田原市十字4丁目"
    )
