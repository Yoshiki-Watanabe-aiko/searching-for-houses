"""全アダプタが ``SiteScraper`` の必須メソッドを持つことを検査する。

⚠⚠ **実装漏れは実地でしか出ない。** CHINTAI.net の初版は ``detail_url`` を
書き忘れており、フィクスチャテスト（``parse_list`` / ``parse_detail`` しか呼ばない）は
**18件すべて緑のまま**だった。本番で初めて
``'ChintaiNetScraper' object has no attribute 'detail_url'`` が出て、
**詳細取得が1,237回すべて失敗**した（相手サイトへの無駄な負荷にもなった）。

``SiteScraper`` は ``Protocol`` なので実装を強制しない（``isinstance`` も
実行時には属性の有無しか見ない）。**サイトを足すたびに黙って穴が空く**ので、
ここで機械的に突き合わせる。
"""

from __future__ import annotations

import inspect

import pytest

from house_search.scrape import SCRAPERS
from house_search.scrape.base import SiteScraper

# Protocol が定めるメソッド。``pipeline.scan`` はこれらを無条件に呼ぶ。
REQUIRED_METHODS: tuple[str, ...] = (
    "list_urls",
    "page_url",
    "is_last_page",
    "parse_list",
    "detail_url",
    "parse_detail",
    "is_sold",
)

# ⚠ **属性はクラスに書かなくてよい。** ``pipeline.scan`` は
# ``getattr(scraper, "city_rotation_limit", None)`` のように既定値つきで読むので、
# 宣言の有無ではなく**読み出した値**を検査する（宣言を強制すると既存9アダプタが落ちる）。
REQUIRED_ATTRIBUTES: tuple[str, ...] = ("site_code",)

# 既定値つきで読まれる属性と、その既定値。
OPTIONAL_ATTRIBUTES: dict[str, object] = {
    "requires_city": False,
    "city_value_source": None,
    "user_agent": None,
    "ignore_robots": False,
    "city_rotation_limit": None,
}


def test_必須メソッドの一覧がProtocolと一致する() -> None:
    """⚠ Protocol にメソッドが増えたらこのテストの一覧も直す。

    ここが古いままだと**検査の網から漏れる**ので、定義側と突き合わせて固定する。
    """
    declared = {
        name
        for name, value in vars(SiteScraper).items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert declared == set(REQUIRED_METHODS)


@pytest.mark.parametrize("site_code", sorted(SCRAPERS))
def test_アダプタが必須メソッドを実装している(site_code: str) -> None:
    scraper = SCRAPERS[site_code]()
    missing = [name for name in REQUIRED_METHODS if not callable(getattr(scraper, name, None))]
    assert not missing, f"{site_code}: {missing} が未実装"


@pytest.mark.parametrize("site_code", sorted(SCRAPERS))
def test_アダプタが必須属性を宣言している(site_code: str) -> None:
    scraper = SCRAPERS[site_code]()
    missing = [name for name in REQUIRED_ATTRIBUTES if not hasattr(scraper, name)]
    assert not missing, f"{site_code}: {missing} が未宣言"


@pytest.mark.parametrize("site_code", sorted(SCRAPERS))
def test_robotsを無視するのはAPAMANだけ(site_code: str) -> None:
    """⚠ ``ignore_robots`` はユーザーが明示的に決めたサイトだけ（→ ADR 0011）。

    ⚠ **宣言していないアダプタもある**ので、``scan`` と同じく既定値つきで読む。
    """
    scraper = SCRAPERS[site_code]()
    ignore = getattr(scraper, "ignore_robots", OPTIONAL_ATTRIBUTES["ignore_robots"])
    assert ignore is (site_code == "APAMAN")


@pytest.mark.parametrize("site_code", sorted(SCRAPERS))
def test_市区ローテーションを宣言するのは上限を実測したサイトだけ(site_code: str) -> None:
    """HOMES 5 / ATHOME 4。⚠ 運用値ではなくサイトの実測特性（→ 課題#36）。"""
    scraper = SCRAPERS[site_code]()
    limit = getattr(scraper, "city_rotation_limit", OPTIONAL_ATTRIBUTES["city_rotation_limit"])
    assert limit == {"HOMES": 5, "ATHOME": 4}.get(site_code)
