"""ドキュメントの参照整合性を機械的に固定する（→ 課題#47）。

⚠ **参照切れは「たどっても目的の記述に行き当たらない」という形で静かに劣化する。**
実際に ADR 0004・0005・0007・0008 が起案されないまま参照だけが残り、
「ADR 0013 決定8」も本体には決定1〜6しか無い状態が続いていた。
設計判断そのものは実装に反映されているので**動作には影響せず、読む人だけが迷う**。

⚠ ここで見るのは**参照先が実在するか**だけで、内容の正しさは見ない。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
ISSUES = DOCS / "課題管理表.md"
ADR_DIR = DOCS / "adr"

#: 課題管理表の凡例で定義している語彙。増やすときは凡例も直す
VALID_STATES = {"未解決", "対応中", "解決済み", "クローズ"}

#: 参照をたどる対象。⚠ ここに無いファイルの参照切れは検出できない
REFERRING_FILES = [
    ISSUES,
    DOCS / "requirements.md",
    DOCS / "再設計計画.md",
    REPO / "CLAUDE.md",
    *sorted(ADR_DIR.glob("*.md")),
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _issue_numbers() -> list[int]:
    return [int(m) for m in re.findall(r"^## #(\d+)\s", _read(ISSUES), re.M)]


def _adr_numbers() -> set[int]:
    return {int(p.name[:4]) for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md")}


class Test課題管理表:
    def test_課題番号に欠番も重複も無い(self) -> None:
        nums = _issue_numbers()
        assert nums, "課題が1件も見つからない（見出しの形式が変わった可能性）"
        dup = [n for n, c in Counter(nums).items() if c > 1]
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        assert not dup, f"番号が重複している: {dup}"
        assert not missing, f"番号が飛んでいる: {missing}"

    def test_すべての課題に状態がある(self) -> None:
        """⚠ 状態が無いと「解決したのか放置なのか」が分からない。"""
        body = _read(ISSUES)
        blocks = re.split(r"^## #\d+\s", body, flags=re.M)[1:]
        missing = [
            b.split("\n", 1)[0][:36]
            for b in blocks
            if not re.search(r"^- 状態:\s*\*\*", b, re.M)
        ]
        assert not missing, f"状態が書かれていない課題: {missing}"

    def test_状態の語彙が凡例どおり(self) -> None:
        found = set(re.findall(r"^- 状態:\s*\*\*(.+?)\*\*", _read(ISSUES), re.M))
        unknown = {s for s in found if s not in VALID_STATES}
        assert not unknown, (
            f"凡例に無い状態: {unknown}（凡例は 未解決 / 対応中 / 解決済み / クローズ）"
        )


class Test参照先が実在する:
    @pytest.mark.parametrize("path", REFERRING_FILES, ids=lambda p: p.name)
    def test_課題番号の参照が実在する(self, path: Path) -> None:
        referenced = {int(n) for n in re.findall(r"課題#(\d+)", _read(path))}
        missing = sorted(referenced - set(_issue_numbers()))
        assert not missing, f"{path.name} が存在しない課題を参照している: {missing}"

    @pytest.mark.parametrize("path", REFERRING_FILES, ids=lambda p: p.name)
    def test_ADR番号の参照が実在する(self, path: Path) -> None:
        """⚠ 0004・0005・0007・0008 は**意図的な欠番**（→ 課題#47）。

        埋め直すと既存の参照がすべてずれるので埋めない。代わりに
        「存在しない番号を参照しない」ことをここで固定する。
        """
        referenced = {int(n) for n in re.findall(r"ADR[\s　]*(\d{4})", _read(path))}
        missing = sorted(referenced - _adr_numbers())
        assert not missing, (
            f"{path.name} が存在しない ADR を参照している: {missing}"
            "（欠番は 0004・0005・0007・0008。実在するものを指すか、"
            "詳細設計書など別の正典へ振り直す）"
        )


class TestADR一覧:
    def test_すべてのADRが一覧に載っている(self) -> None:
        """⚠ 一覧（`docs/adr/README.md`）が所在の正典（→ 課題#47）。"""
        listed = {int(n) for n in re.findall(r"\((?:\./)?(\d{4})-[^)]+\.md\)",
                                             _read(ADR_DIR / "README.md"))}
        missing = sorted(_adr_numbers() - listed)
        assert not missing, f"README に載っていない ADR: {missing}"
