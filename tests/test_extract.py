"""設備抽出（正規化・辞書照合・導出）のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.extract.dictionary import (
    DictionaryEntry,
    FeatureDictionary,
    load_dictionary,
)
from house_search.extract.extractor import (
    SOURCE_DERIVED,
    derive_features,
    extract_from_text,
    merge_features,
)
from house_search.extract.normalize import is_recordable_token, normalize_text, tokenize

DICTIONARY_PATH = Path(__file__).resolve().parents[1] / "data" / "feature_dictionary.yaml"


@pytest.fixture(scope="module")
def dictionary() -> FeatureDictionary:
    return load_dictionary(DICTIONARY_PATH)


def _dictionary(*entries: DictionaryEntry) -> FeatureDictionary:
    return FeatureDictionary(entries=entries)


# --- 正規化 --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ＴＶインターホン", "tvインターホン"),
        ("18㎡", "18m2"),
        ("バス・トイレ別", "バス・トイレ別"),
        ("  複数   空白  ", "複数 空白"),
        ("ｵｰﾄﾛｯｸ", "オートロック"),
        (None, ""),
    ],
)
def test_正規化は全角半角と単位表記を吸収する(raw: str | None, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_トークン化は読点で切るが中黒では切らない() -> None:
    # 「バス・トイレ別」を中黒で割ると条件を取りこぼすため、中黒は区切りにしない
    tokens = tokenize("バルコニー、バス・トイレ別、エアコン")
    assert tokens == ["バルコニー", "バス・トイレ別", "エアコン"]


def test_トークン化は重複を除いて出現順を保つ() -> None:
    assert tokenize("A、B、A、C") == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("token", "expected"),
    [("オートロック", True), ("あ", False), ("123", False), ("---", False), ("あ" * 41, False)],
)
def test_未知表記として記録する価値の判定(token: str, expected: bool) -> None:
    assert is_recordable_token(token) is expected


# --- 辞書照合 ------------------------------------------------------------


def test_否定パターンが条件を打ち消す() -> None:
    dic = _dictionary(
        DictionaryEntry(
            code="SEC_AUTOLOCK",
            family="CHINTAI",
            patterns=("オートロック",),
            negative_patterns=("オートロックなし",),
        )
    )
    assert extract_from_text("オートロック、宅配ボックス", dic, family="CHINTAI").codes == {
        "SEC_AUTOLOCK"
    }
    assert extract_from_text("オートロックなし", dic, family="CHINTAI").codes == frozenset()


def test_SRC物件をRCとして誤抽出しない(dictionary: FeatureDictionary) -> None:
    # 「src造」は「rc造」を部分文字列に含むため、否定パターンが無いと二重に当たる。
    # any_of で1項目にまとめていても、両方 hit すると内訳の説明が誤りになる
    src = extract_from_text("鉄骨鉄筋コンクリート", dictionary, family="CHINTAI").codes
    assert "STRUCT_SRC" in src
    assert "STRUCT_RC" not in src

    rc = extract_from_text("鉄筋コンクリート", dictionary, family="CHINTAI").codes
    assert "STRUCT_RC" in rc
    assert "STRUCT_SRC" not in rc


def test_中黒を含む条件を本文全体への部分一致で拾う(dictionary: FeatureDictionary) -> None:
    assert "BATH_SEPARATE" in extract_from_text(
        "エアコン、バス・トイレ別、都市ガス", dictionary, family="CHINTAI"
    ).codes


def test_サイト固有パターンは該当サイトでだけ効く() -> None:
    dic = _dictionary(
        DictionaryEntry(
            code="INT_LAUNDRY",
            family="CHINTAI",
            patterns=(),
            site_patterns=(("SUUMO", "洗濯機置場室内"),),
        )
    )
    text = "洗濯機置場室内"
    assert extract_from_text(text, dic, family="CHINTAI", site_code="SUUMO").codes == {
        "INT_LAUNDRY"
    }
    assert extract_from_text(text, dic, family="CHINTAI", site_code="HOMES").codes == frozenset()


def test_辞書に無い表記を未知として拾う(dictionary: FeatureDictionary) -> None:
    result = extract_from_text(
        "オートロック、全居室6畳以上、陽当り良好", dictionary, family="CHINTAI"
    )
    assert "SEC_AUTOLOCK" in result.codes
    assert "全居室6畳以上" in result.unknown_tokens
    assert "オートロック" not in result.unknown_tokens


def test_抽出結果は条件コード順で決定的(dictionary: FeatureDictionary) -> None:
    text = "オートロック、宅配ボックス、エアコン、都市ガス、フローリング"
    codes = [f.code for f in extract_from_text(text, dictionary, family="CHINTAI").features]
    assert codes == sorted(codes)


def test_空文字は何も抽出しない(dictionary: FeatureDictionary) -> None:
    result = extract_from_text(None, dictionary, family="CHINTAI")
    assert result.features == ()
    assert result.unknown_tokens == ()


def test_ファミリが違う辞書エントリは使わない() -> None:
    dic = _dictionary(
        DictionaryEntry(code="CERT_X", family="MANSION_BUY", patterns=("既存不適合",))
    )
    assert extract_from_text("既存不適合", dic, family="CHINTAI").codes == frozenset()
    assert extract_from_text("既存不適合", dic, family="MANSION_BUY").codes == {"CERT_X"}


# --- 型付き列からの導出 --------------------------------------------------


def test_所在階から2階以上と最上階を導出する() -> None:
    codes = {f.code for f in derive_features(floor_num=5, total_floors=5, age_years=None)}
    assert codes == {"LOC_FLOOR_2UP", "LOC_TOP_FLOOR"}


def test_1階は2階以上にならない() -> None:
    codes = {f.code for f in derive_features(floor_num=1, total_floors=3, age_years=None)}
    assert codes == {"LOC_FLOOR_1"}


def test_平屋は最上階とみなさない() -> None:
    # 1階建ての1階を「最上階」と言っても情報価値が無いので除く
    codes = {f.code for f in derive_features(floor_num=1, total_floors=1, age_years=None)}
    assert "LOC_TOP_FLOOR" not in codes


def test_築浅は新築扱いになる() -> None:
    assert "FEAT_NEW" in {
        f.code for f in derive_features(floor_num=None, total_floors=None, age_years=2)
    }
    assert "FEAT_NEW" not in {
        f.code for f in derive_features(floor_num=None, total_floors=None, age_years=10)
    }


def test_統合は先に渡したほうを優先する() -> None:
    derived = derive_features(floor_num=3, total_floors=9, age_years=None)
    merged = merge_features(derived, ())
    assert [f.source for f in merged] == [SOURCE_DERIVED]


def test_統合は条件コードで重複を排除する(dictionary: FeatureDictionary) -> None:
    text_features = extract_from_text("最上階", dictionary, family="CHINTAI").features
    derived = derive_features(floor_num=5, total_floors=5, age_years=None)
    merged = merge_features(derived, text_features)
    assert len({f.code for f in merged}) == len(merged)
    # 導出を先に渡したので、最上階は DERIVED 由来になる
    top = next(f for f in merged if f.code == "LOC_TOP_FLOOR")
    assert top.source == SOURCE_DERIVED


# --- 辞書ファイル本体 ----------------------------------------------------


def test_辞書YAMLが読め全エントリにパターンがある(dictionary: FeatureDictionary) -> None:
    assert dictionary.entries
    for entry in dictionary.for_family("CHINTAI"):
        assert entry.patterns or entry.site_patterns, f"{entry.code} にパターンがありません"


def test_辞書のパターンは正規化済みで格納される(dictionary: FeatureDictionary) -> None:
    for pattern in dictionary.all_patterns:
        assert pattern == normalize_text(pattern)


def test_辞書エントリに未知のキーがあればエラーになる(tmp_path: Path) -> None:
    path = tmp_path / "dict.yaml"
    path.write_text(
        "version: 1\nchintai:\n  SEC_AUTOLOCK:\n    pattern: [オートロック]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未知のキー"):
        load_dictionary(path)
