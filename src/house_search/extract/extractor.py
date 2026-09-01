"""設備・特性のローカル抽出。

サイト側の検索フォームで絞り込む代わりに、詳細ページ本文から全サイト同一の
判定器で設備を取り出す（→ ADR 0003）。原文を ``t_properties.raw_features_text``
に残してあるので、辞書を改善したら再スクレイピングせず ``re-extract`` で
DB内の原文から全件やり直せる。

照合は「本文全体への部分一致」で行う。サイトによって区切り文字が
「、」「／」「・」とばらつくため、トークン単位に切ってから照合すると
「バス・トイレ別」のように語中に区切り文字を含む条件を取りこぼす。
トークン化は未知表記の収集にだけ使う。
"""

from __future__ import annotations

from dataclasses import dataclass

from house_search.extract.dictionary import DictionaryEntry, FeatureDictionary
from house_search.extract.normalize import is_recordable_token, normalize_text, tokenize

# t_property_features.source の値。
SOURCE_LIST = "LIST"
SOURCE_DETAIL = "DETAIL"
SOURCE_SITE_TAG = "SITE_TAG"
SOURCE_DERIVED = "DERIVED"

# FEAT_NEW（新築・築浅）とみなす築年数の上限。
NEW_BUILDING_MAX_AGE = 3


@dataclass(frozen=True, slots=True)
class ExtractedFeature:
    """抽出できた設備・特性1件。"""

    code: str
    source: str
    matched_text: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """1物件ぶんの抽出結果。"""

    features: tuple[ExtractedFeature, ...]
    unknown_tokens: tuple[str, ...]

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(feature.code for feature in self.features)


def _match_entry(entry: DictionaryEntry, text: str, site_code: str | None) -> str | None:
    """条件が本文に該当するなら、マッチした原文断片を返す。

    否定パターンが1つでも当たったら不成立にする（「オートロックなし」等）。
    """
    for negative in entry.negative_patterns:
        if negative in text:
            return None
    candidates = list(entry.patterns)
    if site_code:
        candidates.extend(pattern for code, pattern in entry.site_patterns if code == site_code)
    for pattern in candidates:
        if pattern in text:
            return pattern
    return None


def extract_from_text(
    raw_text: str | None,
    dictionary: FeatureDictionary,
    *,
    family: str,
    site_code: str | None = None,
    source: str = SOURCE_DETAIL,
) -> ExtractionResult:
    """設備ブロック原文から条件コードを抽出し、未知表記を拾う。"""
    normalized = normalize_text(raw_text)
    if not normalized:
        return ExtractionResult(features=(), unknown_tokens=())

    features = [
        ExtractedFeature(code=entry.code, source=source, matched_text=matched)
        for entry in dictionary.for_family(family)
        if (matched := _match_entry(entry, normalized, site_code)) is not None
    ]

    known_patterns = dictionary.all_patterns
    unknown = [
        token
        for token in tokenize(raw_text)
        if is_recordable_token(token)
        and not any(pattern in token or token in pattern for pattern in known_patterns)
    ]

    # 条件コード順に固定して、PYTHONHASHSEED が違っても同じ順序になるようにする。
    return ExtractionResult(
        features=tuple(sorted(features, key=lambda f: f.code)),
        unknown_tokens=tuple(unknown),
    )


def derive_features(
    *,
    floor_num: int | None,
    total_floors: int | None,
    age_years: int | None,
) -> tuple[ExtractedFeature, ...]:
    """型付き列から導出する条件。

    「2階以上」のような閾値条件は文字列照合では表現できないため、
    所在階・階建・築年数の列から直接判定する。辞書に頼るより取りこぼしが少ない。
    """
    derived: list[ExtractedFeature] = []
    if floor_num is not None:
        if floor_num == 1:
            derived.append(ExtractedFeature("LOC_FLOOR_1", SOURCE_DERIVED, f"{floor_num}階"))
        elif floor_num >= 2:
            derived.append(ExtractedFeature("LOC_FLOOR_2UP", SOURCE_DERIVED, f"{floor_num}階"))
        if total_floors is not None and total_floors > 1 and floor_num == total_floors:
            evidence = f"{floor_num}階/{total_floors}階建"
            derived.append(ExtractedFeature("LOC_TOP_FLOOR", SOURCE_DERIVED, evidence))
    if age_years is not None and age_years <= NEW_BUILDING_MAX_AGE:
        derived.append(ExtractedFeature("FEAT_NEW", SOURCE_DERIVED, f"築{age_years}年"))
    return tuple(sorted(derived, key=lambda f: f.code))


def merge_features(*groups: tuple[ExtractedFeature, ...]) -> tuple[ExtractedFeature, ...]:
    """複数の抽出結果を条件コードで重複排除して統合する。

    同じ条件が複数の経路で取れた場合は、先に渡されたほうを優先する
    （DERIVED を先に渡せば型付き列由来の判定が辞書照合に勝つ）。
    """
    merged: dict[str, ExtractedFeature] = {}
    for group in groups:
        for feature in group:
            merged.setdefault(feature.code, feature)
    return tuple(sorted(merged.values(), key=lambda f: f.code))
