"""売買相場の窓（最新N四半期だけ読む）の回帰テスト（課題#49 Step 8）。

⚠ **定期実行に載せると保存済みの四半期が増え続ける。** `fetch_reinfolib_trades.py`
は最新4四半期を窓にして「既存は飛ばす」ので、四半期が進むたびに新しい1期が増え、
古いファイルはそのまま残る。集計側が保存済みを全部読むと窓が単調に広がり、
**古い相場が混ざったまま行数と sample_count だけが増える**。
⚠ 例外にならず「たくさんデータが取れた」ようにしか見えないので、水準のずれに
気づく手立てがない（→ ADR 0022 決定2 と同じ「目盛りが混ざる」形）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tools"))

import build_buy_market_rates as builder  # noqa: E402


def _write(raw: Path, period: str, area: str, rows: list[dict[str, Any]]) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    (raw / f"xit001_{period}_{area}.json").write_text(
        json.dumps({"data": rows}, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def raw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """5四半期ぶんの応答を置く（窓は既定4なので1期があふれる）。"""
    raw = tmp_path / "reinfolib"
    for period in ("2025q1", "2025q2", "2025q3", "2025q4", "2026q1"):
        # 都県を2つ置いて「同じ四半期の複数ファイル」も再現する
        for area in ("11", "13"):
            _write(raw, period, area, [{"どの四半期か": period}])
    monkeypatch.setattr(builder, "RAW", raw)
    return raw


def test_窓より古い四半期は読まない(raw_dir: Path) -> None:
    """⚠ これが本題。修正前は5期すべてを読んでいた。"""
    rows, periods = builder._iter_records(4)

    assert periods == ["2025q2", "2025q3", "2025q4", "2026q1"]
    assert "2025q1" not in {r["どの四半期か"] for r in rows}
    # 4期 × 2都県 = 8ファイル（1件ずつ）
    assert len(rows) == 8


def test_読み飛ばした四半期を報告する(raw_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """⚠ 黙って落とさない。窓の外に何期あるかは公開の遅れに気づく材料になる。"""
    builder._iter_records(4)

    assert "2025q1" in capsys.readouterr().out


def test_窓に足りていれば全部読む(raw_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """保存済みが窓以下なら従来どおり全部読み、読み飛ばしも報告しない。"""
    rows, periods = builder._iter_records(9)

    assert periods == ["2025q1", "2025q2", "2025q3", "2025q4", "2026q1"]
    assert len(rows) == 10
    assert "読み飛ばし" not in capsys.readouterr().out


def test_窓の外のファイルは消さない(raw_dir: Path) -> None:
    """⚠ 原典は残す（過去の窓で作り直せる状態を保つ → ハザード・家賃相場と同じ）。"""
    builder._iter_records(4)

    assert (raw_dir / "xit001_2025q1_13.json").exists()


def test_窓を広げれば古い四半期も読める(raw_dir: Path) -> None:
    """`--quarters` は fetch 側と同じ名前・同じ既定値にしてある。"""
    _, periods = builder._iter_records(5)

    assert periods[0] == "2025q1"


def test_期間ラベルは窓の並びから作る(raw_dir: Path) -> None:
    """⚠ 採点は period の降順で最新を1つ採る（→ persist.py）ので、
    次の窓が辞書順で大きくなること。"""
    _, periods = builder._iter_records(4)

    assert builder._period_label(periods) == "2025Q2-2026Q1"


def test_窓の既定はfetch側と揃っている() -> None:
    """⚠ 片方だけ変えると、取っていない四半期を待つか古い期を混ぜるかになる。"""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tools"))
    import fetch_reinfolib_trades as fetcher

    assert builder.WINDOW_QUARTERS == fetcher.WINDOW_QUARTERS
