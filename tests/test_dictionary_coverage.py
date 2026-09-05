"""WANT で配点している条件が実際に抽出できることを固定する（→ 課題#15）。

⚠⚠ **辞書に表記が無い条件を WANT に書くと、永久に miss になる。**
分母（Σw）には乗るのに分子には絶対に乗らないので、
**該当する掲載が加点されず順位に差が出ない**。

⚠ **例外にも件数の減少にもならない。** スコアは全掲載で等しく下がるだけなので、
数字を見ても異常と分からない（要件定義書 §6 が ``any_of`` を導入したのと同じ問題）。
実測（2026-09-05）で `EQUIP_TRASH_24H`（weight 4）がこの状態にあり、
設備原文に該当表記を持つ58件が加点されていなかった。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.config.pattern import load_patterns
from house_search.extract.dictionary import FeatureDictionary, load_dictionary
from house_search.extract.extractor import extract_from_text

REPO = Path(__file__).resolve().parents[1]
DICTIONARY_PATH = REPO / "data" / "feature_dictionary.yaml"
CONFIGS_DIR = REPO / "configs"

#: 型付き列から導出するので辞書に表記が無くてよい条件
#: （``extract/extractor.py`` の ``derive_features`` が作る）
DERIVED_CODES = frozenset({"LOC_FLOOR_1", "LOC_FLOOR_2UP", "LOC_TOP_FLOOR", "FEAT_NEW"})


@pytest.fixture(scope="module")
def dictionary() -> FeatureDictionary:
    return load_dictionary(DICTIONARY_PATH)


def _want_feature_codes(pattern) -> list[str]:
    """検索パターンの WANT が参照している条件コード（``any_of`` も展開する）。"""
    codes: list[str] = []
    for item in pattern.want.features:
        if getattr(item, "any_of", None):
            codes.extend(item.any_of)
        elif getattr(item, "code", None):
            codes.append(item.code)
    return codes


def _patterns():
    return load_patterns(CONFIGS_DIR)


@pytest.mark.parametrize("pattern", _patterns(), ids=lambda p: p.name)
def test_WANTの条件はすべて抽出できる(pattern, dictionary: FeatureDictionary) -> None:
    """⚠ **これが本ファイルの主目的**（実際に1件見つかった → 課題#15）。

    辞書にも DERIVED にも無い条件を WANT に書くと、weight が死ぬだけでなく
    **他の項目の満点も相対的に下がる**（分母に乗り続けるため）。
    """
    known = {e.code for e in dictionary.entries if e.family == "CHINTAI"} | DERIVED_CODES
    missing = sorted(set(_want_feature_codes(pattern)) - known)
    assert not missing, (
        f"WANT に書いてあるのに抽出できない条件: {missing}\n"
        "  辞書（data/feature_dictionary.yaml）に表記を足すか、"
        "型付き列からの導出（DERIVED）にする。\n"
        "  ⚠ 放置すると永久に miss になり、分母だけを押し上げる。"
    )


class Test取りこぼしていた表記:
    """⚠ 実データで見つかった表記を回帰として固定する。"""

    @pytest.mark.parametrize(
        "text",
        [
            "24時間ゴミ出し可",
            "24時間ごみ出し可",
            "オートロック、24時間ゴミ出し可、宅配ボックス",
        ],
    )
    def test_24時間ゴミ出しが拾える(self, text: str, dictionary: FeatureDictionary) -> None:
        result = extract_from_text(text, dictionary, family="CHINTAI")
        assert "EQUIP_TRASH_24H" in result.codes

    @pytest.mark.parametrize(
        "text",
        [
            "敷地内ごみ置き場",  # ⚠ 送り仮名あり。実測1,879回で最多
            "敷地内ゴミ置き場",
            "専用ゴミ置き場",
            "敷地内ゴミ捨て場",
            "敷地内ゴミ置場",  # 送り仮名なし（従来から拾えていた形）
        ],
    )
    def test_ゴミ置き場の表記ゆれを拾える(
        self, text: str, dictionary: FeatureDictionary
    ) -> None:
        result = extract_from_text(text, dictionary, family="CHINTAI")
        assert "EQUIP_TRASH" in result.codes

    def test_ゴミ置き場と24時間ゴミ出しは別の条件(
        self, dictionary: FeatureDictionary
    ) -> None:
        """⚠ 部分一致なので、片方の表記がもう片方に当たらないことを確かめる。"""
        only_trash = extract_from_text("敷地内ごみ置き場", dictionary, family="CHINTAI")
        assert "EQUIP_TRASH_24H" not in only_trash.codes

        only_24h = extract_from_text("24時間ゴミ出し可", dictionary, family="CHINTAI")
        assert "EQUIP_TRASH" not in only_24h.codes


class Test2面採光とクローゼットの表記:
    """課題#15 で追加した2条件（各 weight 3）。

    ⚠ 追加は実データの分布を測ってから決めた（2面採光 9.8% / クローゼット 40.9%）。
    洗面化粧台・脱衣所を見送ったのは、``INT_WASHROOM``（weight 7）と
    90.8% / 84.3% 重なり、同じことに二重の重みが掛かるため。
    """

    @pytest.mark.parametrize(
        "text",
        [
            "2面採光",
            "２面採光",  # ⚠ 全角。NFKC 正規化が効かないと黙って抽出0件になる（→ 課題#51 と同型）
            "二面採光",
            "南向き、2面採光、角部屋",
        ],
    )
    def test_2面採光の表記ゆれを拾える(self, text: str, dictionary: FeatureDictionary) -> None:
        result = extract_from_text(text, dictionary, family="CHINTAI")
        assert "LOC_TWO_SIDE_LIGHT" in result.codes

    @pytest.mark.parametrize(
        "text",
        [
            "クローゼット",
            "クロゼット",  # ⚠ SUUMO の表記は長音符なし
        ],
    )
    def test_クローゼットの表記ゆれを拾える(
        self, text: str, dictionary: FeatureDictionary
    ) -> None:
        result = extract_from_text(text, dictionary, family="CHINTAI")
        assert "STORAGE_CLOSET" in result.codes

    def test_ウォークインクローゼットは収納2条件の両方に当たる(
        self, dictionary: FeatureDictionary
    ) -> None:
        """⚠ 部分一致の既知の性質。WIC はクローゼットの一種なので意図どおり。

        ⚠ ``STORAGE_WIC`` は WANT に配点していないので二重加点にはならない。
        配点済みの ``STORAGE_SHOE``（weight 3）と重なる
        「シューズインクローゼット」は実測4件で、いずれも他のクローゼット表記も
        持っていた（＝下駄箱だけでクローゼット扱いされる掲載は0件）。
        """
        codes = extract_from_text("ウォークインクローゼット", dictionary, family="CHINTAI").codes
        assert {"STORAGE_CLOSET", "STORAGE_WIC"} <= set(codes)

    def test_2面採光と南向きは別の条件(self, dictionary: FeatureDictionary) -> None:
        """⚠ どちらも採光の話だが、南向きでない2面採光は実在する。"""
        only_light = extract_from_text("2面採光", dictionary, family="CHINTAI")
        assert "LOC_SOUTH_FACING" not in only_light.codes

        only_south = extract_from_text("南向き", dictionary, family="CHINTAI")
        assert "LOC_TWO_SIDE_LIGHT" not in only_south.codes
