"""レート制御・リトライ・robots.txt 判定のテスト。"""

from __future__ import annotations

import httpx
import pytest

from house_search.scrape.fetch import (
    CONSECUTIVE_FAILURE_LIMIT,
    MAX_RETRIES,
    RateLimit,
    RobotsDisallowed,
    SiteAborted,
    SiteFetcher,
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


def test_404は再試行せず即座に例外になる() -> None:
    attempts: list[int] = []

    def factory(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        build_fetcher(robots_then(factory)).get("https://example.com/gone")
    assert len(attempts) == 1


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
