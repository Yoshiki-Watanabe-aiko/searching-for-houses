"""条件マスタのシードSQLと設備抽出辞書の突き合わせ（DB不要 → 課題#61 9c）。

⚠ 条件は4箇所に現れる。①``db/seed/04_conditions.sql``（条件の正典）
②``db/seed/05_condition_property_types.sql``（どの物件種別に適用するか）
③``data/feature_dictionary.yaml``（抽出の表記）④``db/seed.py`` の ``EXPECTED_MIN_ROWS``。

どれかを片方だけ直すと、次の形で**黙って**壊れる。

- 05 の条件コードを綴り間違える: 05 は ``m_conditions`` と JOIN して入れるので、
  **その行が落ちるだけで例外にならない**（紐づけが1件足りないまま seed が終わる）
- 辞書にだけ条件を足す: ``sync-dict`` はマスタに無い条件の表記を捨てる
- ``EXPECTED_MIN_ROWS`` を据え置く: **seed を当て忘れても ``>=`` の判定で通る**
  （9c の着手時点で条件 148 / 紐づけ 487 のまま、実数 151 / 501 に追随していなかった）

DB に繋がずにファイル同士で固定する（``tests/test_property_type_seed.py`` と同じ考え方）。
"""

from __future__ import annotations

import re
from pathlib import Path

from house_search.config.metrics import FAMILY_OF
from house_search.db.seed import EXPECTED_MIN_ROWS, SEED_DIR
from house_search.extract.dictionary import FAMILY_SECTIONS, load_dictionary

REPO = Path(__file__).resolve().parents[1]
DICTIONARY_PATH = REPO / "data" / "feature_dictionary.yaml"

# ('LAND', 'LAND_SETBACK', 'セットバック要', '…', 'boolean', TRUE, 8) の形の行
_CONDITION_ROW = re.compile(
    r"\(\s*'(?P<category>[A-Z_]+)'\s*,\s*'(?P<code>[A-Z0-9_]+)'\s*,"
    r"\s*'(?:[^']|'')*'\s*,\s*(?:NULL|'(?:[^']|'')*')\s*,"
    r"\s*'(?P<data_type>[a-z]+)'\s*,\s*(?P<extractable>TRUE|FALSE)\s*,\s*(?P<order>\d+)\s*\)"
)
# ('LOC_CORNER_LOT', 'TOCHI') の形の行
_LINK_ROW = re.compile(r"\(\s*'(?P<code>[A-Z0-9_]+)'\s*,\s*'(?P<property_type>[A-Z_]+)'\s*\)")
# ('LAND', '土地・法規制', 19) の形の行
_CATEGORY_ROW = re.compile(r"\(\s*'(?P<code>[A-Z_]+)'\s*,\s*'[^']*'\s*,\s*(?P<order>\d+)\s*\)")
# ('SUUMO', 'SUUMO', 'https://suumo.jp', ... の形の行（行頭から始まるもの）
_SITE_ROW = re.compile(r"^\s*\('(?P<code>[A-Z_]+)',", re.MULTILINE)


def _read(name: str) -> str:
    return (SEED_DIR / name).read_text(encoding="utf-8")


def _conditions() -> dict[str, re.Match[str]]:
    return {m["code"]: m for m in _CONDITION_ROW.finditer(_read("04_conditions.sql"))}


def _condition_rows() -> list[re.Match[str]]:
    return list(_CONDITION_ROW.finditer(_read("04_conditions.sql")))


def _links() -> list[tuple[str, str]]:
    text = _read("05_condition_property_types.sql")
    return [(m["code"], m["property_type"]) for m in _LINK_ROW.finditer(text)]


def _categories() -> list[str]:
    return [m["code"] for m in _CATEGORY_ROW.finditer(_read("03_condition_categories.sql"))]


def _sites() -> list[str]:
    return [m["code"] for m in _SITE_ROW.finditer(_read("02_sites.sql"))]


def test_シードの行が読める() -> None:
    """正規表現が SQL の書式変更で黙って0件にならないことを先に確かめる。"""
    assert len(_condition_rows()) >= 151
    assert len(_links()) >= 501
    assert len(_categories()) >= 19
    assert len(_sites()) >= 18


def test_条件コードは重複しない() -> None:
    codes = [m["code"] for m in _condition_rows()]
    assert len(codes) == len(set(codes))


def test_紐づけは重複しない() -> None:
    links = _links()
    assert len(links) == len(set(links))


def test_紐づけの条件コードは条件マスタにある() -> None:
    """⚠ 05 は JOIN で入れるので、綴り違いの行は例外にならず黙って落ちる。"""
    missing = sorted({code for code, _type in _links()} - _conditions().keys())
    assert not missing, f"05 にあって 04 に無い条件コード: {missing}"


def test_紐づけの物件種別は種別マスタにある() -> None:
    missing = sorted({ptype for _code, ptype in _links()} - FAMILY_OF.keys())
    assert not missing, f"05 にあってレジストリに無い物件種別: {missing}"


def test_条件のカテゴリはカテゴリマスタにある() -> None:
    missing = sorted({m["category"] for m in _condition_rows()} - set(_categories()))
    assert not missing, f"04 にあって 03 に無いカテゴリ: {missing}"


def test_件数検証の期待値はシードの行数と一致する() -> None:
    """⚠ 据え置くと、seed を当て忘れても ``>=`` の判定で黙って通る。"""
    assert EXPECTED_MIN_ROWS["m_conditions"] == len(_condition_rows())
    assert EXPECTED_MIN_ROWS["m_condition_property_types"] == len(_links())
    assert EXPECTED_MIN_ROWS["m_condition_categories"] == len(_categories())
    assert EXPECTED_MIN_ROWS["m_sites"] == len(_sites())


def test_辞書の条件はすべて条件マスタにあり抽出対象になっている() -> None:
    """⚠ マスタに無い条件の表記は ``sync-dict`` が捨てる（辞書にだけ書いても効かない）。"""
    conditions = _conditions()
    codes = {entry.code for entry in load_dictionary(DICTIONARY_PATH).entries}
    missing = sorted(codes - conditions.keys())
    assert not missing, f"辞書にあって 04 に無い条件コード: {missing}"
    not_extractable = sorted(c for c in codes if conditions[c]["extractable"] != "TRUE")
    assert not not_extractable, f"辞書にあるのに is_extractable が FALSE: {not_extractable}"


def test_土地の辞書と土地の紐づけは一致する() -> None:
    """どの条件を土地に効かせるかは、辞書と 05 の両方で同じにする（→ 課題#61 9c）。

    ⚠ 片方向だけでは足りない。辞書にだけあるとマスタの線引きから外れた条件を
    土地で抽出し、05 にだけあると「土地に適用する」と言いながら抽出0件のままになる。
    ⚠ ユーザー判断（2026-09-11）で、土地の紐づけは**辞書で使う条件だけ**にしている。
    """
    families = {family for fams in FAMILY_SECTIONS.values() for family in fams}
    assert "TOCHI_BUY" in families
    dictionary = load_dictionary(DICTIONARY_PATH)
    in_dictionary = {e.code for e in dictionary.entries if e.family == "TOCHI_BUY"}
    in_master = {code for code, ptype in _links() if ptype == "TOCHI"}
    assert in_dictionary, "土地の辞書が空です"
    assert in_dictionary == in_master, (
        f"辞書にだけある: {sorted(in_dictionary - in_master)} / "
        f"05 にだけある: {sorted(in_master - in_dictionary)}"
    )
