"""最終ページで止める任意フック ``last_page``（→ 課題#69）。

SUUMO 賃貸の ``is_last_page`` は**住戸数（40〜72）を建物数の上限（30）と比べる**ので
発火しない。``max_pages_per_run`` を 5 から 40 へ上げた以上、止める役が要る。
⚠ 最終ページの先が何を返すか（0件／1ページ目へ戻る／エラーページ）は測っていないので、
**1ページ目へ戻って重複が静かに増える**可能性を手前で潰す。

ここで固定するのは3点。

1. フックを宣言したアダプタは**最終ページで止まる**（``max_pages`` まで叩かない）
2. 宣言していないアダプタの経路は**まったく変わらない**
3. フックが例外を投げても取得は止まらない（従来の判定に委ねる）

DBもネットワークも要らない。
"""

from __future__ import annotations

from house_search.pipeline.scan import SiteOutcome, _collect_listings

BASE = "https://example.test/list"


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Fetcher:
    def __init__(self) -> None:
        self.gets: list[str] = []

    def get(self, url: str) -> _Response:
        self.gets.append(url)
        return _Response(url)


class _PagedScraper:
    """SUUMO 賃貸と同じ形。``is_last_page`` は**わざと発火させない**。"""

    site_code = "SUUMO"
    user_agent = None
    supports_site_filters = False

    def __init__(self, last: int | None, *, raises: bool = False) -> None:
        self._last = last
        self._raises = raises

    def list_urls(self, pattern: object, areas: object) -> list[str]:
        return [BASE]

    def page_url(self, base_url: str, page: int) -> str:
        return f"{base_url}?pn={page}"

    def parse_list(self, html_text: str) -> list[object]:
        # 住戸は毎ページ返る（＝「1ページに満たない」では止まらない）
        return [object()] * 44

    def is_last_page(self, count: int) -> bool:
        return False

    def last_page(self, html_text: str) -> int | None:
        if self._raises:
            raise ValueError("ページ送りが読めない")
        return self._last


class _NoHookScraper(_PagedScraper):
    """``last_page`` を持たない既存アダプタの形。"""

    site_code = "GOO"
    last_page = None  # type: ignore[assignment]


class _Pattern:
    """サイト側フィルタを使わない検索パターンの最小形。"""

    class search:  # noqa: N801 - 属性として読まれるだけの入れ物
        class site_filters:  # noqa: N801
            enabled = False


def _collect(scraper, *, max_pages: int) -> _Fetcher:
    fetcher = _Fetcher()
    _collect_listings(
        scraper,
        fetcher,
        _Pattern(),
        areas=[],
        max_pages=max_pages,
        outcome=SiteOutcome(site_code=scraper.site_code),
    )
    return fetcher


def test_最終ページで止まる() -> None:
    fetcher = _collect(_PagedScraper(3), max_pages=40)

    assert fetcher.gets == [f"{BASE}?pn=1", f"{BASE}?pn=2", f"{BASE}?pn=3"]


def test_最終ページが上限より大きければ上限で止まる() -> None:
    """⚠ 上限（``max_pages_per_run``）は残る。網羅しきれない市区は次回に続く。"""
    fetcher = _collect(_PagedScraper(32), max_pages=5)

    assert len(fetcher.gets) == 5


def test_フックを持たないアダプタは上限まで辿る() -> None:
    fetcher = _collect(_NoHookScraper(None), max_pages=4)

    assert len(fetcher.gets) == 4


def test_ページ送りが読めなくても取得は止まらない() -> None:
    """⚠ ページ送りの読み損ないで市区の取得を落とすのは本末転倒。"""
    fetcher = _collect(_PagedScraper(None, raises=True), max_pages=3)

    assert len(fetcher.gets) == 3
