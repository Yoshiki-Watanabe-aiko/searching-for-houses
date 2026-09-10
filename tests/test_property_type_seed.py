"""物件種別のシードSQLとレジストリの突き合わせ（DB不要 → 課題#61）。

⚠ 種別は3箇所に現れる。①``db/seed/01_property_types.sql``（DB の正典）
②``config/metrics.py`` の ``FAMILY_OF``（YAML の検証と採点の正典）
③``db/seed.py`` の ``EXPECTED_MIN_ROWS``（seed 後の件数検証）。

片方だけ足すと次のように壊れる。

- SQL だけ足す: パターンを読んだ瞬間に ``FAMILY_OF`` の KeyError（目に見える）
- ``FAMILY_OF`` だけ足す: ``property_type_ids`` に種別が無く scan で KeyError（目に見える）
- ``EXPECTED_MIN_ROWS`` を据え置く: **seed を当て忘れても ``>=`` 判定で黙って通る**

3つ目だけが黙るので、DB に繋がずにファイル同士で固定する。
"""

from __future__ import annotations

import re

from house_search.config.metrics import FAMILY_OF
from house_search.db.seed import EXPECTED_MIN_ROWS, SEED_DIR

SEED_SQL = SEED_DIR / "01_property_types.sql"

# ('CHINTAI', '賃貸', 'CHINTAI', 1) の形の行
_ROW = re.compile(
    r"\(\s*'(?P<code>[A-Z_]+)'\s*,\s*'[^']*'\s*,"
    r"\s*'(?P<family>[A-Z_]+)'\s*,\s*(?P<order>\d+)\s*\)"
)


def _seed_rows() -> list[tuple[str, str, int]]:
    text = SEED_SQL.read_text(encoding="utf-8")
    return [(m["code"], m["family"], int(m["order"])) for m in _ROW.finditer(text)]


def test_シードの行が読める() -> None:
    """正規表現が SQL の書式変更で黙って0件にならないことを先に確かめる。"""
    assert len(_seed_rows()) >= 5


def test_シードの種別とファミリはレジストリと一致する() -> None:
    seeded = {code: family for code, family, _order in _seed_rows()}
    registered = {code: family.value for code, family in FAMILY_OF.items()}
    assert seeded == registered


def test_シードの並び順は重複しない() -> None:
    orders = [order for _code, _family, order in _seed_rows()]
    assert len(orders) == len(set(orders))


def test_件数検証の期待値はシードの行数と一致する() -> None:
    """⚠ 据え置くと、seed を当て忘れても ``>=`` の判定で黙って通る。"""
    assert EXPECTED_MIN_ROWS["m_property_types"] == len(_seed_rows())
