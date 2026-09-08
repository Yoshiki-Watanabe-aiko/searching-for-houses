"""順位相関（相場比が価格 metric の言い換えでないかを測る道具）のテスト。

⚠ この値がゲート（|r| < 0.6）の根拠になるので、**入力の順番で結果が動かない**ことと、
**測れなかったときに 0 を返さない**ことを固定する。
"""

from __future__ import annotations

import random

import pytest

from house_search.market.stats import spearman


def test_完全に同じ順位なら1() -> None:
    pairs = [(1.0, 10.0), (2.0, 20.0), (3.0, 30.0), (4.0, 40.0)]
    assert spearman(pairs) == pytest.approx(1.0)


def test_完全に逆の順位ならマイナス1() -> None:
    pairs = [(1.0, 40.0), (2.0, 30.0), (3.0, 20.0), (4.0, 10.0)]
    assert spearman(pairs) == pytest.approx(-1.0)


def test_同順位は平均順位で扱う() -> None:
    """⚠ 同じ値が固まる帯（価格の「2,980万円」など）で結果が歪まないこと。"""
    pairs = [(1.0, 1.0), (1.0, 2.0), (2.0, 1.0), (2.0, 2.0)]
    assert spearman(pairs) == pytest.approx(0.0)


def test_入力の順番で結果が変わらない() -> None:
    """⚠⚠ 並び順で番号を振ると、同順位のある入力で**順番だけで相関が動く**。

    非決定的な指標は判断に使えない（→ 検証の独立性）。
    """
    pairs = [(1.0, 5.0), (1.0, 3.0), (2.0, 3.0), (2.0, 9.0), (3.0, 1.0), (3.0, 9.0)]
    base = spearman(pairs)
    rng = random.Random(1234)
    for _ in range(20):
        shuffled = list(pairs)
        rng.shuffle(shuffled)
        assert spearman(shuffled) == pytest.approx(base)


def test_測れないときはNoneを返す() -> None:
    """⚠ 「相関が無い（0）」と「測れなかった」を混ぜない。

    0 を返すと、測れていないのにゲート（|r| < 0.6）を通ったことになる。
    """
    assert spearman([]) is None
    assert spearman([(1.0, 2.0)]) is None
    # 片方が定数（分散0）なら相関は定義できない
    assert spearman([(1.0, 5.0), (2.0, 5.0), (3.0, 5.0)]) is None


def test_実測に近い並びで符号が正になる() -> None:
    """相場比と価格は弱い正の相関を持つ（実測 r=+0.23〜+0.43）。"""
    pairs = [(float(i), float(i) + (i % 3)) for i in range(30)]
    corr = spearman(pairs)
    assert corr is not None
    assert 0.8 < corr <= 1.0
