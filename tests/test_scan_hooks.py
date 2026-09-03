"""GET＋HTML の枠に収まらないサイトへの委譲（→ ADR 0019）。

UR賃貸は JSON API への POST で、団地と住戸の2段になっている。
``SiteScraper`` の Protocol は変えず、**任意フックを宣言したアダプタにだけ**
``pipeline.scan`` が委譲する。ここで固定するのは次の2点。

1. フックを宣言したアダプタでは既存のHTML経路が**1回も呼ばれない**
2. フックを宣言していない既存10アダプタの経路は**まったく変わらない**

DBもネットワークも要らない。
"""

from __future__ import annotations

import pytest

from house_search.pipeline.scan import SiteOutcome, _collect_listings

BASE = "https://example.test/a.html"


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Fetcher:
    """GET/POST の呼び出しを別々に記録するスタブ。"""

    def __init__(self) -> None:
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict]] = []

    def get(self, url: str) -> _Response:
        self.gets.append(url)
        return _Response("1")

    def post(self, url: str, data: dict) -> _Response:
        self.posts.append((url, data))
        return _Response("[]")


class _HtmlScraper:
    """既存10サイトと同じ形（フックを宣言しない）。"""

    site_code = "GOO"
    user_agent = None
    supports_site_filters = False

    def list_urls(self, pattern: object, areas: object) -> list[str]:
        return [BASE]

    def page_url(self, base_url: str, page: int) -> str:
        return base_url

    def parse_list(self, html_text: str) -> list[object]:
        return [object()] * int(html_text or 0)

    def is_last_page(self, count: int) -> bool:
        return True


class _HookScraper:
    """UR と同じ形（``collect_listings`` を宣言する）。"""

    site_code = "UR"
    user_agent = None
    supports_site_filters = False

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def collect_listings(self, fetcher, pattern, areas, *, max_pages, outcome):
        self.seen.append({"areas": list(areas), "max_pages": max_pages})
        fetcher.post("https://example.test/api/", {"tdfk": "13"})
        outcome.errors.append("フックから記録した")
        return ["listing-a", "listing-b"]

    def list_urls(self, pattern: object, areas: object) -> list[str]:
        raise AssertionError("フック宣言サイトで list_urls が呼ばれてはいけない")

    def parse_list(self, html_text: str) -> list[object]:
        raise AssertionError("フック宣言サイトで parse_list が呼ばれてはいけない")


class _Filters:
    enabled = False
    axes: list[str] = []
    exclude_sites: list[str] = []


class _Search:
    site_filters = _Filters()


class _Pattern:
    property_type = "CHINTAI"
    search = _Search()
    must = None


def _run(scraper, fetcher) -> tuple[list, SiteOutcome]:
    outcome = SiteOutcome(site_code=scraper.site_code)
    listings = _collect_listings(
        scraper,
        fetcher,
        _Pattern(),
        areas=[],
        max_pages=3,
        outcome=outcome,
        site_params=None,
    )
    return listings, outcome


def test_フックを宣言したサイトはHTML経路を通らない() -> None:
    scraper = _HookScraper()
    fetcher = _Fetcher()
    listings, outcome = _run(scraper, fetcher)

    assert listings == ["listing-a", "listing-b"]
    # ⚠ ここが本体。list_urls / parse_list に到達すると AssertionError になる
    assert fetcher.gets == []
    assert fetcher.posts == [("https://example.test/api/", {"tdfk": "13"})]
    # エラーの記録先は既存経路と共有する（実行サマリに出る）
    assert outcome.errors == ["フックから記録した"]


def test_フックへ対象エリアとページ上限を渡す() -> None:
    scraper = _HookScraper()
    _run(scraper, _Fetcher())
    assert scraper.seen == [{"areas": [], "max_pages": 3}]


def test_フックを宣言しないサイトは従来どおりGETで一覧を取る() -> None:
    fetcher = _Fetcher()
    listings, outcome = _run(_HtmlScraper(), fetcher)

    assert fetcher.gets == [BASE]
    assert fetcher.posts == []
    assert len(listings) == 1
    assert outcome.errors == []


@pytest.mark.parametrize("hook", ["collect_listings", "fetch_detail"])
def test_既存アダプタはフックを持たない(hook: str) -> None:
    """⚠ 既存10アダプタに実装義務を生まないことを固定する。

    Protocol にメソッドを足すとここが壊れる（→ ADR 0019 決定1）。
    """
    from house_search.scrape import SCRAPERS

    declared = {code for code, cls in SCRAPERS.items() if hasattr(cls, hook)}
    assert declared == {"UR"}
