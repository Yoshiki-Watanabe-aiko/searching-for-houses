"""個別通知の順位絞り込み（``ranking.notify_max_rank``）のテスト。

⚠ **純関数のテストだけでは機能の生存を保証できない。** 判定が正しくても
呼び出し側が通していなければ絞り込みは死んだままなので、
``_notify`` と ``_notify_cheaper_listings`` の**両方**が実際に
判定を通していることもテストする（通知経路は2つある）。
"""

from __future__ import annotations

import inspect

import pytest

from house_search.pipeline import scan as scan_module
from house_search.pipeline.scan import _within_notify_rank


@pytest.mark.parametrize(
    ("rank", "max_rank", "expected"),
    [
        (1, 200, True),
        (200, 200, True),  # 境界は含む
        (201, 200, False),
        (999, 200, False),
        (999, None, True),  # 上限なしなら従来どおり全部送る
        (1, None, True),
    ],
)
def test_順位で通知を絞る(rank: int, max_rank: int | None, expected: bool) -> None:
    assert _within_notify_rank(rank, max_rank) is expected


@pytest.mark.parametrize("max_rank", [None, 200, 1])
def test_順位が引けない掲載は通す(max_rank: int | None) -> None:
    """順位未確定（None）は上限にかかわらず通知する（2026-09-05 ユーザー判断）。

    ⚠ 落とす側に倒すと、順位付けが壊れたときに通知が**全滅しても
    エラーにならない**。鳴らないことと正常が見分けられなくなる。
    """
    assert _within_notify_rank(None, max_rank) is True


@pytest.mark.parametrize("func_name", ["_notify", "_notify_cheaper_listings"])
def test_両方の通知経路が絞り込みを通している(func_name: str) -> None:
    """通知経路を足したときに絞り込みを付け忘れるのを防ぐ。

    ⚠ 付け忘れても**例外にならず、圏外の掲載が黙って通知される**だけなので
    実データを見るまで気づけない。
    """
    source = inspect.getsource(getattr(scan_module, func_name))
    assert "_within_notify_rank" in source, (
        f"{func_name} が notify_max_rank の判定を通していない"
    )
    assert "notify_out_of_rank" in source, (
        f"{func_name} が圏外の件数を実行サマリに数えていない"
    )


def test_ダイジェストは専用の通知先を引いている() -> None:
    """``digest`` が ``webhook_ref`` を直接読んでいないこと。

    ⚠ 直接読んでいると ``digest_webhook_ref`` を書いても無視され、
    **送信は成功するので Discord を見るまで気づけない**。
    """
    from house_search.pipeline import tasks

    source = inspect.getsource(tasks.digest)
    assert "effective_digest_webhook_ref" in source
    assert "webhook_url(pattern.webhook_ref)" not in source


class Test通知先の事前解決:
    """取得を始める前に通知先を解決できることを確かめる。

    ⚠ ``_notify`` は ``scan_pattern`` の**最後**にあるので、ここで
    確かめないと「全16サイトを約50分かけて取得したあとに落ちる」ことになり、
    しかも後続の検索パターンは1件も走らない。
    """

    def test_解決できれば理由を返さない(self) -> None:
        from house_search.cli import _webhook_error

        class _Settings:
            def webhook_url(self, ref: str) -> str:
                return "https://discord.com/api/webhooks/x/y"

        assert _webhook_error(_Settings(), "DIGEST") == []

    def test_解決できなければ理由を返す(self) -> None:
        from house_search.cli import _webhook_error

        class _Settings:
            def webhook_url(self, ref: str) -> str:
                raise ValueError(f"{ref} が .env にありません")

        assert _webhook_error(_Settings(), "DIGEST") == ["DIGEST が .env にありません"]

    def test_scanが取得前に通知先を確かめている(self) -> None:
        """⚠ 付け忘れても取得は成功するので、実データを見るまで気づけない。"""
        from house_search import cli

        source = inspect.getsource(cli._run_scan)
        assert "_webhook_error" in source
        # ダイジェスト用の通知先も対象にしていること
        assert "effective_digest_webhook_ref" in source
        # ⚠ 取得（scan_pattern）より前に確かめていること
        assert source.index("_webhook_error") < source.index("scan_pattern(")


class Testエラー通知の配線:
    """``notify_error`` が実際に呼ばれていることを固定する。

    ⚠ **Phase 1 から定義だけがあり、呼び出し元が1箇所も無かった**
    （2026-09-05 発見）。`DISCORD_WEBHOOK_ERRORS` を設定しても何も起きず、
    **送信されないこと自体が例外にならない**ので実データを見るまで気づけない。
    要件定義書 §14 が求める「エラーチャンネルへ通知」が死んでいた。
    """

    def test_notify_errorに呼び出し元がある(self) -> None:
        """定義だけの状態へ戻ったら落ちる（本欠陥の回帰テスト）。"""
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "house_search"
        callers = [
            path.name
            for path in src.rglob("*.py")
            if "notify_error(" in path.read_text(encoding="utf-8")
            and path.name != "runtime.py"  # 定義そのものは除く
        ]
        assert callers, "notify_error の呼び出し元が1箇所も無い（定義だけの状態）"

    def test_scanがエラーをまとめて送る(self) -> None:
        from house_search.cli import _send_run_errors

        sent: list[tuple[str, str]] = []

        class _Runtime:
            def notify_error(self, title: str, detail: str) -> None:
                sent.append((title, detail))

        _send_run_errors(_Runtime(), ["[帯1] A", "[帯2] A", "[帯1] B"])
        assert len(sent) == 1, "1実行につき1通にまとめること"
        title, detail = sent[0]
        assert "3件" in title
        # ⚠ 同じ本文は件数でまとめる（既知の偽陽性で本物が埋もれないように）
        assert "・[帯1] A" in detail
        assert "・[帯2] A" in detail
        assert "・[帯1] B" in detail

    def test_同じ本文は件数でまとめる(self) -> None:
        from house_search.cli import _send_run_errors

        sent: list[tuple[str, str]] = []

        class _Runtime:
            def notify_error(self, title: str, detail: str) -> None:
                sent.append((title, detail))

        _send_run_errors(_Runtime(), ["同じ", "同じ", "同じ"])
        assert sent[0][1] == "・同じ（3件）"

    def test_エラーが無ければ送らない(self) -> None:
        """正常時に毎回1通届くと、通知そのものが読まれなくなる。"""
        from house_search.cli import _send_run_errors

        class _Runtime:
            def notify_error(self, title: str, detail: str) -> None:
                raise AssertionError("エラーが無いのに送信した")

        _send_run_errors(_Runtime(), [])

    def test_scanがエラー送信を呼んでいる(self) -> None:
        from house_search import cli

        source = inspect.getsource(cli._run_scan)
        assert "_send_run_errors" in source
