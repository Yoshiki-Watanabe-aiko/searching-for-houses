"""検索パターンが参照する条件コードと設備抽出辞書の突き合わせ（→ ADR 0024 決定3）。

DBに触らない純関数。``validate-config`` とテストの両方から使う。

⚠⚠ **辞書にも導出（DERIVED）にも無い条件コードを書くと、例外にならず黙って壊れる。**

- WANT: 分母（Σw）に乗るのに分子には絶対に乗らず、永久に miss になる（→ 課題#15）
- MUST: 判定は「詳細取得済みで抽出結果に無ければ fail」なので、
  **詳細取得済みの掲載が全件 fail になる**。土地（TOCHI_BUY）は辞書がまだ空なので、
  1つでも書くとこれが起きる（→ 課題#61）

⚠ 照合先は**パターンのファミリの辞書**にする。賃貸の辞書と売買のパターンを
突き合わせると、抽出できている売買固有の条件が偽陽性で落ちる（→ 課題#4）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from house_search.config.metrics import FAMILY_OF
from house_search.extract.dictionary import FeatureDictionary
from house_search.extract.extractor import DERIVED_CODES

if TYPE_CHECKING:
    from house_search.config.pattern import SearchPattern


def referenced_condition_codes(pattern: SearchPattern) -> tuple[str, ...]:
    """WANT（``any_of`` も展開する）と MUST の ``features`` が参照する条件コード（昇順）。"""
    codes = {code for item in pattern.want.features for code in item.codes}
    codes.update(pattern.must.features)
    return tuple(sorted(codes))


def unknown_condition_codes(
    pattern: SearchPattern, dictionary: FeatureDictionary
) -> tuple[str, ...]:
    """そのファミリの辞書にも導出コードにも無い条件コード（昇順）。空なら問題なし。"""
    family = FAMILY_OF[pattern.property_type]
    known = {entry.code for entry in dictionary.for_family(family)} | DERIVED_CODES
    return tuple(code for code in referenced_condition_codes(pattern) if code not in known)
