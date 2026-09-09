"""標準出力の UTF-8 付け替えが、運用から走る経路すべてに入っていることを固定する。

⚠⚠ **日本語Windowsの標準出力は既定で cp932。** ``⚠`` を含む警告を print した時点で
``UnicodeEncodeError`` になり、**その1行だけでなく処理そのものが落ちる**。

2026-09-09 に実害が出た（→ 課題#49）。`build_market_rates.py` が
「相場表が無かった市区: 8件」を報告する行で落ち、**52分かけた取得は成功しているのに
CSV も DB も更新されないまま終了コード1**になった。
⚠ **手で叩くと通る**（Git Bash のパイプは UTF-8）ので、
**タスク・運用スクリプト経由でしか再現しない**のが厄介な点である。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from house_search import console
from house_search.console import force_utf8_output

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def _read_text(path: Path) -> str:
    """⚠ .ps1 は BOM 付き UTF-8、ログ由来の混在もあるので寛容に読む。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _tools_used_by_ps1() -> dict[str, list[str]]:
    """運用スクリプト（`scripts/*.ps1`）が名指しする `scripts/tools/*.py` を集める。

    ⚠ **リストをテストへ直接書かない。** 書くと、5本目のツールを運用に載せたときに
    リストへ足し忘れて**テストが緑のまま素通りする**。ps1 の側から引く。
    """
    pattern = re.compile(r"scripts[\\/]tools[\\/]([A-Za-z0-9_]+\.py)")
    used: dict[str, list[str]] = {}
    for ps1 in sorted(SCRIPTS.glob("*.ps1")):
        for name in pattern.findall(_read_text(ps1)):
            used.setdefault(name, []).append(ps1.name)
    return used


def _has_non_cp932_output(source: str) -> bool:
    """cp932 で書けない文字を print する行があるか。"""
    for line in source.splitlines():
        if "print(" not in line:
            continue
        for ch in line:
            try:
                ch.encode("cp932")
            except UnicodeEncodeError:
                return True
    return False


class Test付け替えそのもの:
    """⚠ 元は tests/test_cli.py にあった（→ 課題#34 の報告が消えた件）。
    実装を `house_search.console` へ移したのに合わせてここへ移設した。
    """

    def test_stdoutとstderrの両方を付け替える(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """⚠ 片方だけだと報告が化ける（stderr にも警告を出すため）。"""
        calls: list[tuple[str, str]] = []

        class Stream:
            def __init__(self, name: str) -> None:
                self.name = name

            def reconfigure(self, *, encoding: str) -> None:
                calls.append((self.name, encoding))

        monkeypatch.setattr(console.sys, "stdout", Stream("stdout"))
        monkeypatch.setattr(console.sys, "stderr", Stream("stderr"))
        force_utf8_output()
        assert calls == [("stdout", "utf-8"), ("stderr", "utf-8")]

    def test_reconfigureを持たないストリームでも落ちない(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⚠ pytest の捕捉先など、差し替えられたストリームは持たないことがある。"""
        monkeypatch.setattr(console.sys, "stdout", object())
        monkeypatch.setattr(console.sys, "stderr", object())
        force_utf8_output()  # 例外を出さないことが要件


class Test運用経路:
    def test_運用スクリプトが呼ぶツールは付け替えを行う(self) -> None:
        """⚠ ps1 から呼ばれる .py は、cp932 で落ちる print を持つなら必ず付け替える。

        ⚠ **「⚠ を含む print が無いから安全」ではない。** 相手サイトの表記や
        市区名がそのまま出力へ載る経路があるので、警告文言を1つ足しただけで
        条件を満たしてしまう。ここでは「いま落ちうるもの」だけを必須にし、
        足したときに気づけるようにしてある。
        """
        violations: list[str] = []
        for name, callers in sorted(_tools_used_by_ps1().items()):
            tool = SCRIPTS / "tools" / name
            if not tool.exists():
                violations.append(f"{name}: ps1（{', '.join(callers)}）が呼ぶが実体が無い")
                continue
            source = _read_text(tool)
            if _has_non_cp932_output(source) and "force_utf8_output()" not in source:
                violations.append(
                    f"{name}: cp932 で書けない文字を print するのに force_utf8_output() を"
                    f"呼んでいない（呼び出し元: {', '.join(callers)}）"
                )
        assert not violations, "運用から走るツールで UTF-8 付け替えが漏れている:\n" + "\n".join(
            violations
        )

    def test_落ちた実物が対象に入っている(self) -> None:
        """⚠ 検査が空振りしていないことの担保（→ 課題#49 で実際に落ちたツール）。"""
        used = _tools_used_by_ps1()
        assert "build_market_rates.py" in used, (
            "運用スクリプトからの呼び出しを検出できていない（正規表現が古い可能性）"
        )

    def test_CLIも入口で付け替える(self) -> None:
        """⚠ `house-search` はタスクの本体。ここが抜けると全コマンドが影響を受ける。"""
        source = (REPO / "src" / "house_search" / "cli.py").read_text(encoding="utf-8")
        assert "force_utf8_output()" in source
