"""相場比の下限 MUST（``market_rate_ratio_min`` → 課題#50）のテスト。

⚠ この項目が守るのは「サイト側が賃料の単位を取り違えて登録した掲載」
（実勢 14.3万円が 14,000円 → 相場比 0.12）を**順位から外す**こと。
加点で覆うと実在する激安物件まで一緒に抑えてしまう（→ ADR 0022）ので、
足切りとして置く。⚠ 相場が引けない掲載は unknown で、``unknown_policy`` に委ねる
（0 にすると「相場ちょうど」と区別がつかない → ADR 0021 決定4 と同じ形）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from house_search.config.pattern import (
    ChintaiMust,
    KodateBuyMust,
    MansionBuyMust,
    load_pattern_file,
)
from house_search.scoring.listing_view import ListingView
from house_search.scoring.must import FAIL, PASS, UNKNOWN, evaluate_must

REPO_ROOT = Path(__file__).resolve().parents[1]


def _check(must: ChintaiMust, view: ListingView, **kwargs) -> str:
    result = evaluate_must(view, must, **kwargs)
    return next(c.result for c in result.checks if c.name == "market_rate_ratio_min")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.119, FAIL),  # リザーブ北綾瀬（実勢の約1/10）
        (0.072, FAIL),  # ルーチェ白金
        (0.20, PASS),  # 下限ちょうどは通す
        (0.54, PASS),  # 母集団の中央付近
        (None, UNKNOWN),  # 相場が引けない → 落とさず unknown_policy に委ねる
    ],
)
def test_相場比の下限は3値になる(value: float | None, expected: str) -> None:
    must = ChintaiMust(market_rate_ratio_min=0.20)
    assert _check(must, ListingView(market_rate_ratio=value)) == expected


def test_一覧だけの1段目ではunknownになる() -> None:
    """相場は市区×間取りの解決が要るので ``available_on_list=False``。"""
    must = ChintaiMust(market_rate_ratio_min=0.20)
    assert _check(must, ListingView(market_rate_ratio=0.1), list_stage_only=True) == UNKNOWN


def test_未設定なら判定に現れない() -> None:
    result = evaluate_must(ListingView(market_rate_ratio=0.1), ChintaiMust())
    assert all(c.name != "market_rate_ratio_min" for c in result.checks)


@pytest.mark.parametrize("cls", [MansionBuyMust, KodateBuyMust])
def test_売買のMUSTには書けない(cls: type) -> None:
    """売買の相場（国交省API）はまだ入っていない（→ 課題#49）。書けても全件 unknown に
    なるだけで例外にならないので、スキーマの段で弾く。"""
    with pytest.raises(ValidationError):
        cls(market_rate_ratio_min=0.20)


@pytest.mark.parametrize("filename", ["chintai_23ku.yaml", "chintai_suburb60.yaml"])
def test_実運用の賃貸2帯は下限0_20を持つ(filename: str) -> None:
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert pattern.must.market_rate_ratio_min == pytest.approx(0.20)  # type: ignore[attr-defined]
