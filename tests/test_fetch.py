"""レート制御・リトライ・robots.txt 判定のテスト。"""

from __future__ import annotations

import urllib.robotparser
from pathlib import Path

import httpx
import pytest

from house_search.scrape.fetch import (
    CONSECUTIVE_FAILURE_LIMIT,
    MAX_RETRIES,
    RateLimit,
    RobotsDisallowed,
    SiteAborted,
    SiteFetcher,
    merge_robots_groups,
)

ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow: /admin/\n"


def build_fetcher(handler, *, rate_limit: RateLimit | None = None) -> SiteFetcher:
    """待たずに動くテスト用フェッチャ。"""
    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": "house-search-test"}
    )
    return SiteFetcher(
        site_code="TEST",
        client=client,
        rate_limit=rate_limit or RateLimit(min_interval_sec=0.0),
        sleep=lambda _: None,
    )


def robots_then(response_factory):
    """robots.txt は全許可を返し、それ以外は指定のレスポンスを返すハンドラ。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW_ALL)
        return response_factory(request)

    return handler


def test_正常に取得できる() -> None:
    fetcher = build_fetcher(robots_then(lambda r: httpx.Response(200, text="ok")))
    assert fetcher.get("https://example.com/list").text == "ok"
    assert fetcher.stats.requests >= 1


def test_robotsで禁止されたパスは取りに行かない() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret/\n")
        return httpx.Response(200, text="ok")

    fetcher = build_fetcher(handler)
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.com/secret/page")
    assert fetcher.get("https://example.com/public").text == "ok"


def test_robotsは同一オリジンで1回しか取りに行かない() -> None:
    robots_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            robots_calls.append(str(request.url))
        return httpx.Response(200, text=ROBOTS_ALLOW_ALL)

    fetcher = build_fetcher(handler)
    for page in range(3):
        fetcher.get(f"https://example.com/list?pn={page}")
    assert len(robots_calls) == 1


def test_robotsが取れなくても取得は続く() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("robots unreachable")
        return httpx.Response(200, text="ok")

    assert build_fetcher(handler).get("https://example.com/list").text == "ok"


def test_5xxはバックオフして再試行する() -> None:
    attempts: list[int] = []

    def factory(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, text="ok") if len(attempts) > 2 else httpx.Response(503)

    fetcher = build_fetcher(robots_then(factory))
    assert fetcher.get("https://example.com/list").text == "ok"
    assert len(attempts) == 3
    assert fetcher.stats.retries == 2


def test_429も再試行対象() -> None:
    attempts: list[int] = []

    def factory(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, text="ok") if len(attempts) > 1 else httpx.Response(429)

    assert build_fetcher(robots_then(factory)).get("https://example.com/list").text == "ok"


def test_404は再試行せず_例外にもせずresponseを返す() -> None:
    """404 は「その掲載が無い」という正常な状態変化。

    例外にすると ``is_sold`` の 404 判定に到達できず、掲載終了を
    永久に検知できなくなる（実際にデッドコードになっていた）。
    """
    attempts: list[int] = []

    def factory(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(404)

    fetcher = build_fetcher(robots_then(factory))
    response = fetcher.get("https://example.com/gone")
    assert response.status_code == 404
    assert len(attempts) == 1
    assert fetcher.stats.failures == 0


def test_拒否系の4xxは再試行せず失敗として数える() -> None:
    """403・405 は相手の拒否。再試行しないが失敗には数える。

    数えないと ``consecutive_failures`` が増えず、連続失敗による
    打ち切りが永久に発火しない（NIFTY の 405 を271回叩き続けた原因）。
    """
    attempts: list[int] = []

    def factory(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(405)

    fetcher = build_fetcher(robots_then(factory))
    with pytest.raises(RuntimeError):
        fetcher.get("https://example.com/detail")
    assert len(attempts) == 1  # 再試行しない
    assert fetcher.stats.failures == 1
    assert fetcher.stats.consecutive_failures == 1


def test_失敗のメッセージにステータスが載る() -> None:
    """⚠ 理由が本文に無いと、ログから 405 とタイムアウトを区別できない。

    ``raise ... from`` の連鎖は ``t_scrape_logs`` へ ``str(exc)`` で書かれる
    時点で落ちる。実際に NIFTY の 405 を調べるのに実サイトを叩き直した。
    """
    fetcher = build_fetcher(robots_then(lambda r: httpx.Response(405)))
    with pytest.raises(RuntimeError, match="405"):
        fetcher.get("https://example.com/detail")


def test_拒否系が連続したらサイトを打ち切る() -> None:
    fetcher = build_fetcher(robots_then(lambda r: httpx.Response(403)))
    for _ in range(CONSECUTIVE_FAILURE_LIMIT - 1):
        with pytest.raises(RuntimeError):
            fetcher.get("https://example.com/detail")
    with pytest.raises(SiteAborted, match="403"):
        fetcher.get("https://example.com/detail")


def test_404は連続失敗の数え上げをリセットする() -> None:
    """404 は失敗ではないので、拒否系のカウントを持ち越さない。"""
    responses = [httpx.Response(403), httpx.Response(404)]

    def factory(request: httpx.Request) -> httpx.Response:
        return responses.pop(0) if responses else httpx.Response(200, text="ok")

    fetcher = build_fetcher(robots_then(factory))
    with pytest.raises(RuntimeError):
        fetcher.get("https://example.com/a")
    assert fetcher.stats.consecutive_failures == 1
    fetcher.get("https://example.com/b")
    assert fetcher.stats.consecutive_failures == 0


def test_リトライを使い切ったら失敗として数える() -> None:
    fetcher = build_fetcher(robots_then(lambda r: httpx.Response(503)))
    with pytest.raises(RuntimeError):
        fetcher.get("https://example.com/list")
    assert fetcher.stats.failures == 1
    assert fetcher.stats.retries == MAX_RETRIES


def test_連続失敗が続いたらサイトを打ち切る() -> None:
    fetcher = build_fetcher(robots_then(lambda r: httpx.Response(503)))
    for _ in range(CONSECUTIVE_FAILURE_LIMIT - 1):
        with pytest.raises(RuntimeError):
            fetcher.get("https://example.com/list")
    with pytest.raises(SiteAborted):
        fetcher.get("https://example.com/list")


def test_成功したら連続失敗カウントが戻る() -> None:
    responses = [503] * (MAX_RETRIES + 1) + [200]

    def factory(request: httpx.Request) -> httpx.Response:
        return httpx.Response(responses.pop(0))

    fetcher = build_fetcher(robots_then(factory))
    with pytest.raises(RuntimeError):
        fetcher.get("https://example.com/list")
    assert fetcher.stats.consecutive_failures == 1
    fetcher.get("https://example.com/list")
    assert fetcher.stats.consecutive_failures == 0


def test_日次リクエスト上限に達したら打ち切る() -> None:
    fetcher = build_fetcher(
        robots_then(lambda r: httpx.Response(200, text="ok")),
        rate_limit=RateLimit(min_interval_sec=0.0, daily_request_cap=2),
    )
    fetcher.get("https://example.com/a")
    fetcher.get("https://example.com/b")
    with pytest.raises(SiteAborted, match="日次リクエスト上限"):
        fetcher.get("https://example.com/c")


def test_リクエスト間隔をジッタ込みで待つ() -> None:
    waits: list[float] = []
    client = httpx.Client(
        transport=httpx.MockTransport(robots_then(lambda r: httpx.Response(200, text="ok"))),
        headers={"User-Agent": "house-search-test"},
    )
    fetcher = SiteFetcher(
        site_code="TEST",
        client=client,
        rate_limit=RateLimit(min_interval_sec=2.5),
        sleep=waits.append,
    )
    fetcher.get("https://example.com/a")
    fetcher.get("https://example.com/b")
    # ±30% のジッタが載るので幅で確認する
    assert waits
    assert all(0.0 <= wait <= 2.5 * 1.3 for wait in waits)


# ---------------------------------------------------------------------------
# robots.txt のグループ統合（→ 課題#43・RFC 9309 §2.2.1）
#
# ⚠ 標準の RobotFileParser は同じ User-agent のグループが2つ以上あると
# 2つ目以降を丸ごと落とすため、禁止パスを**黙って許可と誤判定する**。
# ---------------------------------------------------------------------------

CHINTAI_NET_ROBOTS = Path(__file__).parent / "fixtures" / "robots" / "chintai_net.txt"


def _parse(lines: list[str]) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(lines)
    return parser


def test_2つ目のUserAgentグループのDisallowが効く() -> None:
    """CHINTAI.net の実 robots.txt。統合前は /api/ を許可と誤判定していた。"""
    text = CHINTAI_NET_ROBOTS.read_text(encoding="utf-8")

    naive = _parse(text.splitlines())
    assert naive.can_fetch("house-search/2.0", "https://www.chintai.net/api/") is True

    merged = _parse(merge_robots_groups(text))
    # 1つ目のグループ
    assert merged.can_fetch("house-search/2.0", "https://www.chintai.net/info/") is False
    # 2つ目のグループ（統合しないと落ちる）
    assert merged.can_fetch("house-search/2.0", "https://www.chintai.net/api/") is False
    assert merged.can_fetch("house-search/2.0", "https://www.chintai.net/list/?b=1") is False
    # 実装で使う市区一覧とページ送りは許可されたまま
    assert merged.can_fetch("house-search/2.0", "https://www.chintai.net/tokyo/area/13121/list/")
    assert merged.can_fetch(
        "house-search/2.0", "https://www.chintai.net/tokyo/area/13121/list/page2/"
    )


def test_統合しても他のUserAgentのグループとSitemapは保たれる() -> None:
    text = CHINTAI_NET_ROBOTS.read_text(encoding="utf-8")
    merged = _parse(merge_robots_groups(text))
    assert merged.can_fetch("MauiBot", "https://www.chintai.net/") is False
    assert len(merged.site_maps() or []) == 6


def test_グループが1つのrobotsは判定が変わらない() -> None:
    """既存15サイトはすべてこの形。統合が既存の挙動を変えないことを固定する。"""
    text = "User-agent: *\nDisallow: /admin/\nAllow: /admin/public/\nCrawl-delay: 10\n"
    paths = ["/admin/x", "/admin/public/y", "/list/"]
    naive = _parse(text.splitlines())
    merged = _parse(merge_robots_groups(text))
    for path in paths:
        url = "https://example.com" + path
        assert merged.can_fetch("house-search/2.0", url) == naive.can_fetch(
            "house-search/2.0", url
        )
    assert merged.crawl_delay("house-search/2.0") == naive.crawl_delay("house-search/2.0")


def test_1つのグループに複数のUserAgentがあれば両方に規則が配られる() -> None:
    text = "User-agent: alpha\nUser-agent: beta\nDisallow: /x/\n"
    merged = _parse(merge_robots_groups(text))
    assert merged.can_fetch("alpha", "https://example.com/x/") is False
    assert merged.can_fetch("beta", "https://example.com/x/") is False
    assert merged.can_fetch("gamma", "https://example.com/x/") is True


def test_UserAgent行より前の規則は捨てる() -> None:
    """標準の挙動に合わせる（どのグループにも属さない規則は無効）。"""
    merged = merge_robots_groups("Disallow: /orphan/\nUser-agent: *\nDisallow: /x/\n")
    assert "disallow: /orphan/" not in merged
    assert _parse(merged).can_fetch("house-search/2.0", "https://example.com/orphan/") is True


def test_コメントと空行は統合の妨げにならない() -> None:
    text = (
        "# 1つ目\nUser-agent: *\nDisallow: /a/  # 末尾コメント\n\n"
        "# 2つ目\nUser-agent: *\n\nDisallow: /b/\n"
    )
    merged = _parse(merge_robots_groups(text))
    assert merged.can_fetch("house-search/2.0", "https://example.com/a/") is False
    assert merged.can_fetch("house-search/2.0", "https://example.com/b/") is False


def test_取得したrobotsがグループ統合を通る() -> None:
    """SiteFetcher 経由でも統合が効くこと（配線の確認）。"""
    two_groups = "User-agent: *\nDisallow: /a/\n\nUser-agent: *\nDisallow: /b/\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=two_groups)
        return httpx.Response(200, text="ok")

    fetcher = build_fetcher(handler)
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.com/b/page")
