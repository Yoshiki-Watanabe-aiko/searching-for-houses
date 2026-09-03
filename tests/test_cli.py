"""CLI の引数解釈と詳細取得上限の決定ロジック。

Phase 5 で ``scan --detail-limit`` を足した。初回全件スキャンが詳細キューを
掃くための逃げ道で、既定値（40 / --full 時 400）を壊していないことを固定する。
"""

from __future__ import annotations

import pytest

from house_search import cli
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


class TestForceUtf8Output:
    """標準出力を UTF-8 へ付け替える処理（→ 課題#34 の報告が消えた件）。"""

    def test_reconfigures_both_streams(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """stdout と stderr の**両方**を付け替える。片方だけだと報告が化ける。"""
        calls: list[tuple[str, str]] = []

        class Stream:
            def __init__(self, name: str) -> None:
                self.name = name

            def reconfigure(self, *, encoding: str) -> None:
                calls.append((self.name, encoding))

        monkeypatch.setattr(cli.sys, "stdout", Stream("stdout"))
        monkeypatch.setattr(cli.sys, "stderr", Stream("stderr"))
        cli._force_utf8_output()
        assert calls == [("stdout", "utf-8"), ("stderr", "utf-8")]

    def test_tolerates_streams_without_reconfigure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reconfigure を持たない差し替え先でも落ちない（テストの捕捉先など）。"""
        monkeypatch.setattr(cli.sys, "stdout", object())
        monkeypatch.setattr(cli.sys, "stderr", object())
        cli._force_utf8_output()


class TestSegmentIndexPrefectures:
    """乗車区間の駅索引の範囲（→ 課題#35）。"""

    @staticmethod
    def _region(name: str, pref_cds: set[int]):
        from house_search.commute.regions import RegionDestination

        return RegionDestination(
            name=name, pref_cds=frozenset(pref_cds), station="県庁前", prefecture="沖縄県"
        )

    def test_region指定時はその地方の都道府県に絞る(self) -> None:
        """⚠ 掲載都道府県で索引を作ると地方外の経路で1本も結び付かない。

        実測で沖縄18駅の区間72本すべてが捨てられた（「県庁前」が千葉県にもあるため、
        掲載都道府県＝1都3県の索引では一意に決まらない）。
        """
        region = self._region("沖縄", {47})
        assert cli._segment_index_prefectures(None, region) == (47,)

    def test_都道府県コードは並べて返す(self) -> None:
        """⚠ frozenset の反復順は実行ごとに揺れる。"""
        region = self._region("関東", {13, 8, 14, 11})
        assert cli._segment_index_prefectures(None, region) == (8, 11, 13, 14)


class TestReSegmentArguments:
    def test_地方と目的地を指定できる(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["re-segment", "--region", "沖縄"])
        assert (args.command, args.region, args.destination) == ("re-segment", "沖縄", None)

    def test_地方の指定は任意(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["re-segment", "--destination", "芝公園"])
        assert (args.region, args.destination) == (None, "芝公園")
