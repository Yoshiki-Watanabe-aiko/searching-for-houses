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
    RobotsRules,
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
    """⚠ **統合もワイルドカード展開もしない標準の判定**（比較対象）。

    本体の判定は ``RobotsRules`` なので、テストの「直った側」は必ずそちらを通す。
    検証が実装の経路を通らないと、正しい実装を壊れていると誤診する（→ 課題#46）。
    """
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(lines)
    return parser


def test_2つ目のUserAgentグループのDisallowが効く() -> None:
    """CHINTAI.net の実 robots.txt。統合前は /api/ を許可と誤判定していた。"""
    text = CHINTAI_NET_ROBOTS.read_text(encoding="utf-8")

    naive = _parse(text.splitlines())
    assert naive.can_fetch("house-search/2.0", "https://www.chintai.net/api/") is True

    merged = RobotsRules.parse(text)
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
    merged = RobotsRules.parse(text)
    assert merged.can_fetch("MauiBot", "https://www.chintai.net/") is False
    # ⚠ Sitemap はどのグループにも属さない行。統合で落とさないこと
    lines = merge_robots_groups(text)
    assert sum(1 for line in lines if line.lower().startswith("sitemap:")) == 6


def test_グループが1つのrobotsは判定が変わらない() -> None:
    """既存15サイトはすべてこの形。統合が既存の挙動を変えないことを固定する。"""
    text = "User-agent: *\nDisallow: /admin/\nAllow: /admin/public/\nCrawl-delay: 10\n"
    paths = ["/admin/x", "/admin/public/y", "/list/"]
    naive = _parse(text.splitlines())
    merged = RobotsRules.parse(text)
    for path in paths:
        url = "https://example.com" + path
        assert merged.can_fetch("house-search/2.0", url) == naive.can_fetch(
            "house-search/2.0", url
        )
    # ⚠ Crawl-delay は判定に使わない（レート制御は m_sites が正典）。
    # ここでは統合が行を落とさないことだけを見る。
    assert "crawl-delay: 10" in merge_robots_groups(text)


def test_1つのグループに複数のUserAgentがあれば両方に規則が配られる() -> None:
    text = "User-agent: alpha\nUser-agent: beta\nDisallow: /x/\n"
    merged = RobotsRules.parse(text)
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
    merged = RobotsRules.parse(text)
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


# ---------------------------------------------------------------------------
# robots.txt のワイルドカード（→ 課題#52・RFC 9309 §2.2.3）
#
# ⚠ 標準の RobotFileParser は Disallow の値の `*` を展開せず**単純な前方一致**で
# 見るため、`/*?*sort=` は「そういう名前のパス」として扱われどのURLにも当たらない。
# ⚠⚠ 課題#43 と同じく**許可側に倒れる**ので、取得は成功したまま禁止パスを叩く。
# ---------------------------------------------------------------------------

SUUMO_ROBOTS = Path(__file__).parent / "fixtures" / "robots" / "suumo.txt"
UA = "house-search/2.0"


def _suumo() -> RobotsRules:
    return RobotsRules.parse(SUUMO_ROBOTS.read_text(encoding="utf-8"))


def test_sortつきのURLは標準では許可と誤判定される() -> None:
    """⚠ 実際にこれで賃貸の一覧が2時間ごとに禁止パスを叩いていた。"""
    text = SUUMO_ROBOTS.read_text(encoding="utf-8")
    url = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&sort=2"

    assert _parse(merge_robots_groups(text)).can_fetch(UA, url) is True
    assert _suumo().can_fetch(UA, url) is False


def test_効いた規則が分かる() -> None:
    """診断のため、どの規則で禁止になったかを引けること。"""
    url = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&sort=2"
    assert _suumo().matched_rule(UA, url) == "/*?*sort="


def test_sortを外せば取得できる() -> None:
    """⚠ この1本が「sort=2 を外す」という運用判断の根拠になっている。"""
    url = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13&sc=13121"
    assert _suumo().can_fetch(UA, url) is True


def test_売買のSEOパスは許可で検索フォームのパスは禁止() -> None:
    """Phase 6 の実測（2026-09-06）を固定する。⚠ 賃貸からの連想で
    ``/jj/bukken/ichiran/`` を使うと**明示的な禁止パス**を叩く（→ 課題#4）。
    """
    rules = _suumo()
    assert rules.can_fetch(UA, "https://suumo.jp/ms/chuko/tokyo/sc_chiyoda/") is True
    assert rules.can_fetch(UA, "https://suumo.jp/ms/chuko/tokyo/sc_chiyoda/?kt=5000") is True
    assert rules.can_fetch(UA, "https://suumo.jp/jj/bukken/ichiran/JJ010FJ001/") is False


def test_ワイルドカードを含まない規則は従来どおり() -> None:
    """⚠ 標準との差は ``*`` / ``$`` の展開だけに閉じていること。"""
    text = """User-agent: *
Disallow: /info/
Allow: /info/ok/
"""
    rules = RobotsRules.parse(text)
    standard = _parse(merge_robots_groups(text))
    for path in ("/info/", "/info/x", "/other/", "/"):
        url = f"https://example.com{path}"
        assert rules.can_fetch(UA, url) is standard.can_fetch(UA, url)


def test_末尾一致のドル記号() -> None:
    rules = RobotsRules.parse("""User-agent: *
Disallow: /*.pdf$
""")
    assert rules.can_fetch(UA, "https://example.com/a/b.pdf") is False
    assert rules.can_fetch(UA, "https://example.com/a/b.pdf?x=1") is True


def test_名指しのグループが優先される() -> None:
    """標準と同じUA選択規則であること（名指しが当たれば ``*`` は見ない）。"""
    text = """User-agent: *
Disallow: /

User-agent: house-search
Disallow: /ng/
"""
    rules = RobotsRules.parse(text)
    assert rules.can_fetch(UA, "https://example.com/ok/") is True
    assert rules.can_fetch(UA, "https://example.com/ng/") is False
    assert rules.can_fetch("other-bot/1.0", "https://example.com/ok/") is False


def test_値が空のDisallowは全許可() -> None:
    """``Disallow:``（値なし）は「禁止なし」を意味する（標準と同じ扱い）。"""
    rules = RobotsRules.parse("""User-agent: *
Disallow:
""")
    assert rules.can_fetch(UA, "https://example.com/anything") is True


def test_規則が無ければ全許可() -> None:
    """robots.txt を取得できなかったときの経路（→ ADR 0019）。"""
    assert RobotsRules([]).can_fetch(UA, "https://example.com/x") is True
