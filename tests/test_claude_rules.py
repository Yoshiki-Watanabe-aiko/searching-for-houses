"""`.claude/rules/` の領域別ルールが読み込まれる形を保っていることを固定する（→ 課題#72）。

CLAUDE.md は毎セッション全文が読み込まれるため、「実装上の注意」を `paths:` 付きの
ルールへ移した。ここが崩れると次のどちらかが**例外にならないまま**起こる。

- ⚠ **`paths:` の綴り違い・移動でどのファイルにも当たらない** → 注意が二度と読み込まれない
- ⚠ **`paths:` を付け忘れる／CLAUDE.md へ注意を積み直す** → 毎セッションの読み込み量が元へ戻る
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
RULES_DIR = REPO / ".claude" / "rules"
CLAUDE_MD = REPO / "CLAUDE.md"

#: CLAUDE.md の上限。移した直後で約12.7KB。超えたら注意を `.claude/rules/` へ移す
CLAUDE_MD_MAX_BYTES = 20_000

RULE_FILES = sorted(RULES_DIR.glob("*.md"))


def _paths_of(rule: Path) -> list[str]:
    text = rule.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{rule.name}: 先頭に frontmatter が無い"
    front = text[len("---\n") : text.index("\n---\n", len("---\n"))]
    paths = (yaml.safe_load(front) or {}).get("paths")
    assert isinstance(paths, list) and paths, (
        f"{rule.name}: paths: が無い（毎セッション読み込まれる）"
    )
    return paths


def test_ルールファイルがある() -> None:
    assert RULE_FILES, ".claude/rules/*.md が1つも無い"


@pytest.mark.parametrize("rule", RULE_FILES, ids=lambda p: p.name)
class Test領域別ルール:
    def test_pathsが実在するファイルに当たる(self, rule: Path) -> None:
        dead = [p for p in _paths_of(rule) if not any(REPO.glob(p))]
        assert not dead, f"{rule.name}: どのファイルにも当たらない paths: {dead}"

    def test_CLAUDE_mdの表に載っている(self, rule: Path) -> None:
        assert f"`.claude/rules/{rule.name}`" in CLAUDE_MD.read_text(encoding="utf-8"), (
            f"{rule.name} が CLAUDE.md の「実装上の注意」の表に無い（設計段階で読まれない）"
        )


def test_CLAUDE_mdが上限を超えない() -> None:
    size = len(CLAUDE_MD.read_text(encoding="utf-8").encode("utf-8"))
    assert size <= CLAUDE_MD_MAX_BYTES, (
        f"CLAUDE.md が {size} バイト（上限 {CLAUDE_MD_MAX_BYTES}）。"
        "実際に踏んだ注意は .claude/rules/ の該当ファイルへ移す"
    )
