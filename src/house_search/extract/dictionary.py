"""設備抽出辞書のロードとDB同期。

``data/feature_dictionary.yaml`` が正典で、``sync-dict`` が
``m_condition_synonyms`` へ反映する。実行時はDBを参照して JOIN する。

同期は「YAMLに無い行は削除する」完全同期にしている。差分追加だけにすると
辞書から外したパターンがDBに残り続け、YAMLのdiffレビューが意味を失うため。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, text

from house_search.extract.normalize import normalize_text

# YAML のトップレベルキー → m_condition_synonyms.property_family（複数可）
#
# ⚠ **`buy` は MANSION_BUY と KODATE_BUY の両方へ展開する。** 片方しか作らないと
# そのファミリの掲載は照合先の辞書が空集合になり、詳細から原文を保存しても
# **抽出0件のまま正常終了する**（例外にも件数の減少にもならない → 課題#4）。
# 証明書・性能評価系の語彙はマンションと戸建てで概ね共通なので、
# 実測で語彙が分かれたときにセクションを分割する。
#
# ⚠ **`common` は設備の語彙が賃貸と売買で共通だから置いている。** どの条件を
# ここへ置くかは人が選ばず、**マスタ（m_condition_property_types）が
# マンション売買にも紐づけているか**で機械的に決まる。`buy` へ表記をコピーすると
# 同じ語を2箇所で保守することになり、賃貸側を直したとき売買側が黙って古くなる。
FAMILY_SECTIONS: dict[str, tuple[str, ...]] = {
    "chintai": ("CHINTAI",),
    "common": ("CHINTAI", "MANSION_BUY", "KODATE_BUY"),
    "buy": ("MANSION_BUY", "KODATE_BUY"),
}


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    """1条件ぶんの照合パターン。"""

    code: str
    family: str
    patterns: tuple[str, ...]
    negative_patterns: tuple[str, ...] = ()
    site_patterns: tuple[tuple[str, str], ...] = ()
    """サイト固有パターン。``(サイトコード, パターン)`` の組。"""


@dataclass(frozen=True, slots=True)
class FeatureDictionary:
    """照合に使う辞書全体。"""

    entries: tuple[DictionaryEntry, ...] = field(default_factory=tuple)

    def for_family(self, family: str) -> tuple[DictionaryEntry, ...]:
        """指定ファミリのエントリを条件コード順に返す（決定的）。"""
        return tuple(sorted((e for e in self.entries if e.family == family), key=lambda e: e.code))

    @property
    def all_patterns(self) -> frozenset[str]:
        """全エントリの肯定パターン（未知表記の判定に使う）。"""
        result: set[str] = set()
        for entry in self.entries:
            result.update(entry.patterns)
            result.update(pattern for _, pattern in entry.site_patterns)
        return frozenset(result)


def _as_patterns(values: Any) -> tuple[str, ...]:
    """YAML の値を正規化済みパターンの組へ変換する（重複と空を除く）。"""
    if not values:
        return ()
    seen: dict[str, None] = {}
    for value in values:
        normalized = normalize_text(str(value))
        if normalized:
            seen.setdefault(normalized, None)
    return tuple(seen)


def load_dictionary(path: Path) -> FeatureDictionary:
    """辞書YAMLを読み込む。パターンは照合と同じ正規化を通す。"""
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    entries: list[DictionaryEntry] = []
    for section, families in FAMILY_SECTIONS.items():
        for code, spec in (raw.get(section) or {}).items():
            if not isinstance(spec, dict):
                raise ValueError(f"辞書エントリ '{code}' の内容がマッピングではありません")
            unknown = set(spec) - {"patterns", "negative_patterns", "site_overrides"}
            if unknown:
                raise ValueError(f"辞書エントリ '{code}' に未知のキー: {sorted(unknown)}")
            site_patterns = tuple(
                (site_code, pattern)
                for site_code, values in sorted((spec.get("site_overrides") or {}).items())
                for pattern in _as_patterns(values)
            )
            patterns = _as_patterns(spec.get("patterns"))
            negative = _as_patterns(spec.get("negative_patterns"))
            for family in families:
                entries.append(
                    DictionaryEntry(
                        code=code,
                        family=family,
                        patterns=patterns,
                        negative_patterns=negative,
                        site_patterns=site_patterns,
                    )
                )
    return FeatureDictionary(entries=tuple(entries))


@dataclass(frozen=True, slots=True)
class SyncResult:
    """``sync-dict`` の結果。"""

    inserted: int
    deleted: int
    unknown_condition_codes: tuple[str, ...]
    unknown_site_codes: tuple[str, ...]

    @property
    def has_unknown_refs(self) -> bool:
        return bool(self.unknown_condition_codes or self.unknown_site_codes)


def sync_to_db(engine: Engine, dictionary: FeatureDictionary) -> SyncResult:
    """辞書を ``m_condition_synonyms`` へ完全同期する。

    条件コード・サイトコードがマスタに存在しない場合はその行を捨て、
    呼び出し側へ報告する（黙って落とすと辞書の綴り間違いに気付けない）。
    """
    rows: list[dict[str, Any]] = []
    missing_conditions: set[str] = set()
    missing_sites: set[str] = set()

    with engine.begin() as conn:
        condition_ids = {
            code: cid for code, cid in conn.execute(text("SELECT code, id FROM m_conditions"))
        }
        site_ids = {code: sid for code, sid in conn.execute(text("SELECT code, id FROM m_sites"))}

        for entry in dictionary.entries:
            condition_id = condition_ids.get(entry.code)
            if condition_id is None:
                missing_conditions.add(entry.code)
                continue
            for pattern in entry.patterns:
                rows.append(
                    {
                        "condition_id": condition_id,
                        "site_id": None,
                        "property_family": entry.family,
                        "pattern": pattern,
                        "is_negative": False,
                    }
                )
            for pattern in entry.negative_patterns:
                rows.append(
                    {
                        "condition_id": condition_id,
                        "site_id": None,
                        "property_family": entry.family,
                        "pattern": pattern,
                        "is_negative": True,
                    }
                )
            for site_code, pattern in entry.site_patterns:
                site_id = site_ids.get(site_code)
                if site_id is None:
                    missing_sites.add(site_code)
                    continue
                rows.append(
                    {
                        "condition_id": condition_id,
                        "site_id": site_id,
                        "property_family": entry.family,
                        "pattern": pattern,
                        "is_negative": False,
                    }
                )

        deleted = conn.execute(text("DELETE FROM m_condition_synonyms")).rowcount or 0
        if rows:
            conn.execute(
                text(
                    "INSERT INTO m_condition_synonyms "
                    "(condition_id, site_id, property_family, pattern, is_negative) "
                    "VALUES (:condition_id, :site_id, :property_family, :pattern, :is_negative)"
                ),
                rows,
            )

    return SyncResult(
        inserted=len(rows),
        deleted=deleted,
        unknown_condition_codes=tuple(sorted(missing_conditions)),
        unknown_site_codes=tuple(sorted(missing_sites)),
    )


def load_from_db(engine: Engine) -> FeatureDictionary:
    """DBに同期済みの辞書を読み戻す（``re-extract`` などの実行時用）。"""
    sql = text(
        "SELECT c.code, s.property_family, s.pattern, s.is_negative, si.code AS site_code "
        "FROM m_condition_synonyms s "
        "JOIN m_conditions c ON c.id = s.condition_id "
        "LEFT JOIN m_sites si ON si.id = s.site_id "
        "ORDER BY c.code, s.is_negative, s.pattern"
    )
    grouped: dict[tuple[str, str], dict[str, list]] = {}
    with engine.connect() as conn:
        for code, family, pattern, is_negative, site_code in conn.execute(sql):
            bucket = grouped.setdefault(
                (code, family), {"patterns": [], "negative": [], "site": []}
            )
            if site_code:
                bucket["site"].append((site_code, pattern))
            elif is_negative:
                bucket["negative"].append(pattern)
            else:
                bucket["patterns"].append(pattern)

    return FeatureDictionary(
        entries=tuple(
            DictionaryEntry(
                code=code,
                family=family,
                patterns=tuple(bucket["patterns"]),
                negative_patterns=tuple(bucket["negative"]),
                site_patterns=tuple(bucket["site"]),
            )
            for (code, family), bucket in sorted(grouped.items())
        )
    )
