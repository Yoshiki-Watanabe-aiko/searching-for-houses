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
    SiteOutcome,
    resolve_detail_limit,
)


class TestSiteSummaryLine:
    """実行サマリのサイト行（→ 課題#61・#62）。

    ⚠ 見送り・引き継ぎは**エラーではない**のでエラー欄に混ぜない（→ 課題#45）が、
    **黙って捨てない**。ファミリ違い・種別違い・引き継ぎは意味が違うので出し分ける。
    """

    def test_ファミリ違いと種別違いと引き継ぎを出し分ける(self) -> None:
        site = SiteOutcome(
            site_code="SUUMO",
            family_mismatch=("nc_1",),
            type_mismatch=("nc_2", "nc_3"),
            retyped=("nc_4",),
        )

        line = cli.format_site_line(site)

        assert "ファミリ違いで見送り 1件（nc_1）" in line
        assert "種別違いで見送り 2件（nc_2, nc_3）" in line
        assert "種別を引き継ぎ 1件（nc_4）" in line

    def test_何も無ければ足さない(self) -> None:
        line = cli.format_site_line(SiteOutcome(site_code="SUUMO", listings_seen=3))

        assert "見送り" not in line and "引き継ぎ" not in line
        assert line.lstrip().startswith("SUUMO")


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


class TestReExtractFamily:
    """``re-extract --family`` でファミリを絞る（→ 課題#61 9c）。

    土地の辞書を足したとき、土地の掲載だけを抽出し直せば既存3ファミリの設備が
    1行も動かないことを前後の件数で言える（全件を回すと scan 由来と re-extract 由来の
    差でぶれ、効果と切り分けられない）。
    """

    def test_既定は全ファミリ(self) -> None:
        assert build_parser().parse_args(["re-extract"]).family is None

    def test_ファミリを指定できる(self) -> None:
        assert build_parser().parse_args(["re-extract", "--family", "TOCHI_BUY"]).family == (
            "TOCHI_BUY"
        )

    def test_知らないファミリは弾く(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["re-extract", "--family", "TOCHI"])

    def test_指定したファミリを再抽出へ渡す(self, monkeypatch) -> None:
        """⚠ 引数を読むだけで再抽出へ渡し忘れると、黙って全ファミリを回す。"""
        from house_search.extract.dictionary import DictionaryEntry, FeatureDictionary
        from house_search.pipeline import runtime as runtime_module
        from house_search.pipeline import tasks
        from house_search.pipeline.tasks import ReExtractResult

        class _Runtime:
            dictionary = FeatureDictionary(
                entries=(DictionaryEntry(code="X", family="TOCHI_BUY", patterns=("x",)),)
            )

        calls: list[dict] = []

        def _fake_re_extract(runtime, **kwargs) -> ReExtractResult:
            calls.append(kwargs)
            return ReExtractResult(listings=0, features=0, unknown_tokens=0)

        monkeypatch.setattr(runtime_module, "build_runtime", lambda **_: _Runtime())
        monkeypatch.setattr(tasks, "re_extract", _fake_re_extract)
        assert cli.main(["re-extract", "--family", "TOCHI_BUY"]) == 0
        assert calls == [{"family": "TOCHI_BUY", "limit": None}]

        # ⚠ そのファミリの辞書が DB に無い（sync-dict の流し忘れ）なら回さずに止める。
        # 回すと抽出0件で黙って成功し、既存の設備があれば空で上書きする
        calls.clear()
        assert cli.main(["re-extract", "--family", "MANSION_BUY"]) == 1
        assert calls == []


class TestFamilyFilter:
    """``--family`` で種別ファミリを絞る（→ 課題#4・2026-09-07）。

    2時間ごとの定期スキャンは賃貸だけ、売買4パターンは1日1回の別タスクで回す。
    売買を4都県173市区へ広げたので、同居させると所要が上限 PT1H50M に迫るため。
    """

    def test_familyは複数指定できる(self) -> None:
        args = build_parser().parse_args(
            ["scan", "--family", "MANSION_BUY", "--family", "KODATE_BUY"]
        )
        assert args.family == ["MANSION_BUY", "KODATE_BUY"]

    def test_未指定ならNone(self) -> None:
        assert build_parser().parse_args(["scan"]).family is None
        assert build_parser().parse_args(["check-sold"]).family is None

    def test_知らないファミリは弾く(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["scan", "--family", "TOCHI"])

    def test_check_soldも同じ分け方(self) -> None:
        args = build_parser().parse_args(["check-sold", "--family", "CHINTAI"])
        assert args.family == ["CHINTAI"]

    def test_ファミリで絞れる(self) -> None:
        from house_search.config.pattern import parse_pattern

        chintai = parse_pattern(_pattern("賃貸", "CHINTAI"))
        mansion = parse_pattern(_pattern("中古M", "CHUKO_MANSION"))
        kodate = parse_pattern(_pattern("新築K", "SHINCHIKU_KODATE"))
        patterns = [chintai, mansion, kodate]

        assert cli.select_patterns(patterns) == patterns
        assert cli.select_patterns(patterns, families=["CHINTAI"]) == [chintai]
        assert cli.select_patterns(patterns, families=["MANSION_BUY", "KODATE_BUY"]) == [
            mansion,
            kodate,
        ]
        assert cli.select_patterns(patterns, name="中古M", families=["MANSION_BUY"]) == [mansion]

    def test_絞った結果が空なら例外(self) -> None:
        """黙って空を返すと、タスクが「対象0件で正常終了」を繰り返して気づけない。"""
        from house_search.config.pattern import parse_pattern

        patterns = [parse_pattern(_pattern("賃貸", "CHINTAI"))]
        with pytest.raises(ValueError):
            cli.select_patterns(patterns, families=["KODATE_BUY"])
        with pytest.raises(ValueError):
            cli.select_patterns(patterns, name="無い")


class TestPatternArgument:
    """``--pattern`` の複数指定（→ 2026-09-10 の実測）。

    ⚠ argparse は同じオプションを繰り返すと **最後の値で上書き** する。
    ``--family`` は複数指定できるのに ``--pattern`` は単一だったため、
    ``rescore --pattern A --pattern B --pattern C`` が C だけを採点して
    **エラーにも警告にもならなかった**（3本のつもりで1本しか処理されない）。
    """

    def test_scanで複数指定できる(self) -> None:
        args = build_parser().parse_args(["scan", "--pattern", "A", "--pattern", "B"])
        assert args.pattern == ["A", "B"]

    def test_rescoreで複数指定できる(self) -> None:
        args = build_parser().parse_args(["rescore", "--pattern", "A", "--pattern", "B"])
        assert args.pattern == ["A", "B"]

    def test_check_soldとdigestも同じ(self) -> None:
        for command in ("check-sold", "digest"):
            args = build_parser().parse_args([command, "--pattern", "A", "--pattern", "B"])
            assert args.pattern == ["A", "B"], command

    def test_未指定はNone(self) -> None:
        assert build_parser().parse_args(["rescore"]).pattern is None

    def test_1つに絞るコマンドは単一のまま(self) -> None:
        """⚠ fetch-commutes 等は「対象を1つに絞る」仕様なので複数指定にしない。"""
        args = build_parser().parse_args(["fetch-commutes", "--pattern", "A", "--pattern", "B"])
        assert args.pattern == "B"

    def test_複数の名前で絞れる(self) -> None:
        from house_search.config.pattern import parse_pattern

        chintai = parse_pattern(_pattern("賃貸", "CHINTAI"))
        mansion = parse_pattern(_pattern("中古M", "CHUKO_MANSION"))
        kodate = parse_pattern(_pattern("新築K", "SHINCHIKU_KODATE"))
        patterns = [chintai, mansion, kodate]

        assert cli.select_patterns(patterns, name=["中古M", "新築K"]) == [mansion, kodate]
        # 単一の文字列も従来どおり通す（既存の呼び出しを壊さない）
        assert cli.select_patterns(patterns, name="中古M") == [mansion]

    def test_1つでも見つからなければ例外(self) -> None:
        """⚠ 綴り違いを黙って捨てると「指定したのに採点されない」に戻る。"""
        from house_search.config.pattern import parse_pattern

        patterns = [parse_pattern(_pattern("賃貸", "CHINTAI"))]
        with pytest.raises(ValueError):
            cli.select_patterns(patterns, name=["賃貸", "無い"])


class TestConfigDrift:
    """scan の実行中に採点設定が変わったことを検出する（→ 2026-09-10・課題#59）。

    ⚠⚠ **切り離し起動した長時間の scan は、起動時に読んだ YAML を保持したまま走る。**
    その最中に YAML を編集して ``rescore`` を流しても、scan が後から
    **起動時の（古い）設定で採点を上書きする**。
    ⚠ **例外にならず件数も減らない**ので、``config_hash`` の不一致だけが手がかりになる。
    実測（2026-09-10）では、3時間54分の掃き出しの最中に液状化の配点を入れたため、
    掃き出しが後から採点した売買3本で **配点がまるごと失われていた**。
    """

    def test_変化が無ければ黙る(self) -> None:
        before = {"A": "h1", "B": "h2"}
        assert cli.detect_config_drift(before, dict(before)) == []

    def test_ハッシュが変われば警告する(self) -> None:
        warnings = cli.detect_config_drift({"A": "h1"}, {"A": "h9"})
        assert len(warnings) == 1
        assert "A" in warnings[0]
        # ⚠ 対処（rescore）まで書かないと、警告を見ても次の一手が分からない
        assert "rescore" in warnings[0]

    def test_共通するパターンだけを比べる(self) -> None:
        """⚠ 実行中に増えた／消えたパターンは、この scan の採点対象ではない。"""
        assert cli.detect_config_drift({"A": "h1"}, {"A": "h1", "B": "h2"}) == []
        assert cli.detect_config_drift({"A": "h1", "B": "h2"}, {"A": "h1"}) == []

    def test_複数変われば順序を固定してすべて出す(self) -> None:
        warnings = cli.detect_config_drift({"B": "h2", "A": "h1"}, {"B": "h8", "A": "h9"})
        assert len(warnings) == 2
        assert "A" in warnings[0] and "B" in warnings[1]


def _pattern(name: str, property_type: str) -> dict:
    """最小の検索パターン（ファミリの絞り込みだけを試すためのもの）。"""
    return {
        "name": name,
        "property_type": property_type,
        "webhook_ref": "X",
        "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"], "cities": []},
        "must": {"unknown_policy": "keep"},
        "want": {"features": [], "numeric": []},
    }
