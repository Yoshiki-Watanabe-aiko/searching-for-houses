"""CLI の引数解釈と詳細取得上限の決定ロジック。

Phase 5 で ``scan --detail-limit`` を足した。初回全件スキャンが詳細キューを
掃くための逃げ道で、既定値（40 / --full 時 400）を壊していないことを固定する。
"""

from __future__ import annotations

import pytest

from house_search.cli import build_parser
from house_search.pipeline.scan import (
    DEFAULT_DETAIL_LIMIT,
    FULL_DETAIL_LIMIT,
    resolve_detail_limit,
)


class TestScanArguments:
    """``scan`` サブコマンドの引数解釈。"""

    def test_detail_limit_defaults_to_none(self) -> None:
        """未指定なら None。ここが 0 や 40 になると既定値の分岐が死ぬ。"""
        args = build_parser().parse_args(["scan"])
        assert args.detail_limit is None

    def test_detail_limit_is_parsed_as_int(self) -> None:
        args = build_parser().parse_args(["scan", "--detail-limit", "2000"])
        assert args.detail_limit == 2000

    def test_detail_limit_rejects_non_integer(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["scan", "--detail-limit", "たくさん"])

    def test_combined_with_seed_and_full(self) -> None:
        """初回全件スキャンが実際に使う組み合わせ。"""
        args = build_parser().parse_args(["scan", "--seed", "--full", "--detail-limit", "2000"])
        assert (args.seed, args.full, args.detail_limit) == (True, True, 2000)


class TestResolveDetailLimit:
    """``resolve_detail_limit`` の決定表。"""

    def test_default_without_override(self) -> None:
        assert resolve_detail_limit(False, None) == DEFAULT_DETAIL_LIMIT

    def test_full_without_override(self) -> None:
        assert resolve_detail_limit(True, None) == FULL_DETAIL_LIMIT

    def test_override_wins_over_full(self) -> None:
        """--full と併用したときは上書きが勝つ（初回スキャンがこの経路を使う）。"""
        assert resolve_detail_limit(True, 2000) == 2000

    def test_override_wins_without_full(self) -> None:
        assert resolve_detail_limit(False, 5) == 5

    @pytest.mark.parametrize("value", [0, -1])
    def test_rejects_non_positive(self, value: int) -> None:
        """0 を「無制限」と読み違えられないよう明示的に弾く。"""
        with pytest.raises(ValueError, match="1以上"):
            resolve_detail_limit(True, value)
