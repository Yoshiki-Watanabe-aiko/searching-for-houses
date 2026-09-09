"""コンソール出力の共通設定。

⚠⚠ **日本語Windowsの標準出力は既定で cp932** なので、``⚠`` のような文字を
``print`` した時点で ``UnicodeEncodeError`` になる。**例外は print の途中で飛ぶため、
その1行だけでなく後続の出力ごと失われる**（実際 ``fetch-commutes`` の
「意図と違う駅」28件の一覧が報告ごと消えた）。

⚠ **さらに悪いのは「処理そのものが落ちる」場合**である。2026-09-09 に
``build_market_rates.py`` が「相場表が無かった市区: 8件」を報告する行で落ち、
**52分かけた取得は成功しているのに CSV も DB も更新されないまま終了コード1**になった
（→ 課題#49）。⚠ **警告メッセージが本処理を巻き添えにする**という、
`alembic.ini` に日本語コメントを書くと `alembic upgrade` ごと落ちるのと同型の罠。

⚠ **コンソール判定には頼れない。** `scripts/*.ps1` は stdout をファイルへ向けるので
`isatty()` は偽になり、それでもエンコーディングは cp932 のままである。
**入口で明示的に付け替える**のが唯一確実な手当てになる。

⚠⚠ **「手で叩けば通る」でもない**（2026-09-10 実測）。Claude Code の Bash ツールから
`python` を起動したときの ``sys.stdout.encoding`` も **cp932** だった
（`scripts/tools/` の調査に使ったスクリプト自身が落ちて分かった）。
UTF-8 になるのは Git Bash の対話ターミナルなど限られた条件だけで、
**タスク・運用スクリプト・エージェントの調査実行はいずれも cp932** である。
→ `scripts/tools/*.py` は ps1 から呼ばれるものに限らず、
cp932 で書けない文字を print するなら全て付け替える（`tests/test_console_utf8.py`）。
"""

from __future__ import annotations

import sys

__all__ = ["force_utf8_output"]


def force_utf8_output() -> None:
    """標準出力・標準エラーを UTF-8 にする。

    ⚠ **コマンド／スクリプトの入口で最初に呼ぶ。** 途中で呼んでも、
    それより前の print が既に落ちていれば意味がない。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # テストの差し替え先は持たないことがある
            reconfigure(encoding="utf-8")
