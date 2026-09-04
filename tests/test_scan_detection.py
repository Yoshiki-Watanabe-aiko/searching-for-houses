"""サイト側フィルタの事故を検出する2層（→ 課題#29・ADR 0015）。

⚠ 無効なフィルタ値・ボット検知・DOM変更は**どれも HTTP 200 のまま0件を返し、
例外にならない**。「絞り込めた」のか「壊れた」のかを区別する仕組みを固定する。
DBもネットワークも要らない。
"""

from __future__ import annotations

import pytest

from house_search.config.settings import load_settings
from house_search.config.site_params import SITE_PARAMS_FILENAME, load_site_params
from house_search.pipeline.scan import SiteOutcome, _collect_listings

BASE = "https://example.test/a.html"
FILTERED = f"{BASE}?fl=30"


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Fetcher:
    """叩いたURLを記録するだけのスタブ。本文の数字がそのまま掲載件数になる。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str) -> _Response:
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"想定外のURLを叩いた: {url}")
        return _Response(self.pages[url])


class _Scraper:
    site_code = "GOO"
    user_agent = None
    supports_site_filters = True

    def list_urls(self, pattern: object, areas: object) -> list[str]:
        return [BASE]

    def page_url(self, base_url: str, page: int) -> str:
        return base_url if page <= 1 else f"{base_url}&p={page}"

    def parse_list(self, html_text: str) -> list[object]:
        return [object()] * int(html_text or 0)

    def is_last_page(self, count: int) -> bool:
        return count < 30


class _Must:
    area_min = 30.0
    walk_minutes_max = 12
    layouts = ["1LDK", "2DK"]


class _Filters:
    enabled = True
    axes = ["area_min"]
    exclude_sites: list[str] = []


class _Search:
    site_filters = _Filters()


class _Pattern:
    property_type = "CHINTAI"
    search = _Search()
    must = _Must()


@pytest.fixture
def table():
    return load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)


def _run(fetcher: _Fetcher, table, *, site_params=True, scraper=None) -> SiteOutcome:
    outcome = SiteOutcome(site_code="GOO")
    _collect_listings(
        scraper or _Scraper(),
        fetcher,
        _Pattern(),
        areas=[],
        max_pages=1,
        outcome=outcome,
        site_params=table if site_params else None,
    )
    return outcome


def test_フィルタ付きで0件なら外して取り直す(table) -> None:
    """⚠ 対照に掲載があれば、0件の原因はフィルタ側だと分かる。"""
    fetcher = _Fetcher({FILTERED: "0", BASE: "5"})
    outcome = _run(fetcher, table)
    assert fetcher.calls == [FILTERED, BASE]
    assert any("フィルタで0件になった疑い" in e for e in outcome.errors)
    assert any("外すと5件" in e for e in outcome.errors)


def test_対照も0件ならそのエリアに掲載が無いだけ(table) -> None:
    """掲載の無い市区は普通にある。ここで騒ぐと本物の異常が埋もれる。"""
    fetcher = _Fetcher({FILTERED: "0", BASE: "0"})
    outcome = _run(fetcher, table)
    assert fetcher.calls == [FILTERED, BASE]
    assert outcome.errors == []


def test_掲載が取れていれば対照は取らない(table) -> None:
    """⚠ 正常時にリクエストを増やさない。"""
    fetcher = _Fetcher({FILTERED: "7"})
    outcome = _run(fetcher, table)
    assert fetcher.calls == [FILTERED]
    assert outcome.errors == []


def test_フィルタを使っていないサイトでは対照を取らない(table) -> None:
    """フィルタ無しで0件なら比較する相手がいない（同じURLを2度叩くだけになる）。"""
    fetcher = _Fetcher({BASE: "0"})
    outcome = _run(fetcher, table, site_params=False)
    assert fetcher.calls == [BASE]
    assert outcome.errors == []


BASE2 = "https://example.test/b.html"
FILTERED2 = f"{BASE2}?fl=30"


class _TwoAreaScraper(_Scraper):
    """市区を2つ持つサイト（実運用は帯82市区）。"""

    def list_urls(self, pattern: object, areas: object) -> list[str]:
        return [BASE, BASE2]


def test_他の市区で取れていればフィルタ0件を申告しない(table) -> None:
    """⚠ 一部の市区が0件になるのは**正しい絞り込み**である（→ 課題#45）。

    千代田区・中央区に「30㎡以上・13万円以下」の住戸は実在しない。実測では
    SUUMO・GOO・HOMEMATE・CHINTAI_NET・DROOM から**2時間ごとに6件**の
    エラーが飛んでおり、⚠ **読まれない通知は本物のエラーを見逃す形で実害になる**
    （requirements.md §14.1 が1件ずつ送るのをやめたのと同じ理由）。
    """
    fetcher = _Fetcher({FILTERED: "0", BASE: "5", FILTERED2: "3"})
    outcome = _run(fetcher, table, scraper=_TwoAreaScraper())
    # 対照取得そのものは行う（原因の切り分けはしておく）
    assert fetcher.calls == [FILTERED, BASE, FILTERED2]
    assert outcome.errors == []


def test_サイト全体が0件ならフィルタ0件を申告する(table) -> None:
    """⚠ 検出力は落ちない。

    丸めは ``AXIS_BOUND`` が型で強制するので、フィルタ値が壊れるなら
    **そのサイトの全URLで壊れ**、1件も取れなくなる。
    """
    fetcher = _Fetcher({FILTERED: "0", BASE: "5", FILTERED2: "0"})
    outcome = _run(fetcher, table, scraper=_TwoAreaScraper())
    assert any("フィルタで0件になった疑い" in e for e in outcome.errors)
    assert any("外すと5件" in e for e in outcome.errors)


def test_対照取得の失敗は即エラーにする(table) -> None:
    """⚠ これは「絞り込めた結果の0件」ではなく取得そのものの失敗。

    サイト全体が0件かどうかに関わらず知りたいので、保留せず即記録する。
    """

    class _Broken(_Fetcher):
        def get(self, url: str) -> _Response:
            if url == BASE:
                raise ValueError("認証ページ")
            return super().get(url)

    fetcher = _Broken({FILTERED: "0", FILTERED2: "3"})
    outcome = _run(fetcher, table, scraper=_TwoAreaScraper())
    assert any("対照取得に失敗" in e for e in outcome.errors)
