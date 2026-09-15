"""閲覧画面の起動時に1回だけ組む文脈。

``build_runtime()`` は使わない（住所索引・辞書・サイトパラメータの読み込みが重く、
閲覧には要らない）。ここに置くのは検索パターン・通勤の目的地・条件名だけ。

⚠ **``Settings`` をここに載せない。** テンプレートへ渡す値の出どころを限り、
Webhook URL や接続文字列が画面に出る経路を作らない（→ ADR 0026 セキュリティ）。
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from house_search.commute.resolve import resolve_destination_group
from house_search.config.pattern import load_pattern_file
from house_search.pipeline import persist


@dataclass(frozen=True, slots=True)
class PatternEntry:
    """閲覧画面で扱う検索パターン1本。"""

    #: URL に使う識別子。YAML のファイル名（拡張子なし）
    slug: str
    pattern: Any
    #: 通勤時間の目的地の駅グループコード。未設定・解決できなければ None
    destination_g_cd: int | None


@dataclass(frozen=True, slots=True)
class WebContext:
    """アプリ全体で共有する読み取り専用の文脈。"""

    engine: Engine
    patterns: tuple[PatternEntry, ...]
    condition_names: Mapping[str, str]
    site_names: Mapping[str, str]
    port: int
    #: 書き込みフォームの CSRF トークン。プロセスごとに作り直す
    csrf_token: str

    def pattern_by_slug(self, slug: str) -> PatternEntry | None:
        return next((entry for entry in self.patterns if entry.slug == slug), None)


def build_context(
    engine: Engine, *, configs_dir: Path, port: int, csrf_token: str | None = None
) -> WebContext:
    """検索パターンを読み、目的地・条件名・サイト名を DB から引いて文脈を作る。

    ⚠ パターンの読み込みは ``load_patterns`` と同じく ``configs_dir`` 直下の ``*.yaml``
    だけ（非再帰）。雛形の ``examples/`` は読まない。
    """
    paths = sorted(configs_dir.glob("*.yaml"))
    with engine.connect() as conn:
        entries = tuple(
            PatternEntry(
                slug=path.stem,
                pattern=(pattern := load_pattern_file(path)),
                destination_g_cd=resolve_destination_group(conn, pattern.commute),
            )
            for path in paths
        )
        condition_names = persist.load_condition_names(conn)
        site_names = {
            code: name for code, name in conn.execute(text("SELECT code, name FROM m_sites"))
        }
    return WebContext(
        engine=engine,
        patterns=entries,
        condition_names=condition_names,
        site_names=site_names,
        port=port,
        csrf_token=csrf_token or secrets.token_urlsafe(32),
    )
