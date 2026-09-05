"""幾何演算ライブラリが実行時コードへ流入していないことを固定する。

⚠ ``shapely`` / ``pyshp`` は **ハザード評価の生成スクリプト専用**（→ 課題#46）。
``src/house_search/`` から import すると、``scan`` / ``rescore`` がこれらに依存する。

⚠ **これは「動くかどうか」ではなく「壊れ方」の問題である。**
``rescore`` は「DB保存済みの属性からの純関数」であることを設計の柱にしており
（→ requirements.md §6.1）、ネットワークも重い依存も要らないから
いつでも流し直せる。幾何ライブラリが入り込むと、その保証が静かに失われる。

⚠ **後から気づく手段がない。** 開発機では両方入っているのでテストも通り、
壊れるのは依存を入れ直した環境だけ。だから import の時点で止める。
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "house_search"

# import shapely / from shapely import ... / import shapefile（pyshp のモジュール名）
_FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+(shapely|shapefile|fiona|geopandas|osgeo)\b",
    re.MULTILINE,
)


def test_src_has_no_geometry_imports() -> None:
    """``src/house_search/`` の全 .py が幾何ライブラリを import していないこと。"""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _FORBIDDEN.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(
                f"{path.relative_to(SRC.parent.parent)}:{line} {match.group(0).strip()}"
            )
    assert not offenders, (
        "幾何ライブラリは scripts/tools/ の生成スクリプト専用です。"
        "実行時コードから import すると rescore がネットワーク不要・軽量依存という"
        "前提を失います（→ 課題#46）。\n  " + "\n  ".join(offenders)
    )


def test_guard_actually_detects() -> None:
    """ガード自体が働くこと（正規表現が空振りしていないかの確認）。"""
    assert _FORBIDDEN.search("import shapely")
    assert _FORBIDDEN.search("from shapely.geometry import Point")
    assert _FORBIDDEN.search("    import shapefile")
    # 紛らわしいが別物のものは拾わない
    assert not _FORBIDDEN.search("import shapely_helper_docs")
    assert not _FORBIDDEN.search("# import shapely")


def test_src_directory_exists() -> None:
    """走査対象が空でないこと（パスを間違えると全件 green になる）。"""
    files = list(SRC.rglob("*.py"))
    assert len(files) > 20, f"src 配下の .py が {len(files)} 件しかない。パスが誤っている疑い"
