"""賃貸EX アダプタのテスト（実HTMLフィクスチャ方式）。

robots.txt がクエリ付きURLを全面禁止しているサイトなので、
「URLにクエリを付けない」ことをテストで固定しておく。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.area import AreaTarget
from house_search.scrape.chintai_ex import PAGE_SIZE, ChintaiExScraper

FIXTURES = Path(__file__).parent / "fixtures" / "chintai_ex"

CHIYODA = AreaTarget(prefecture="東京都", city_name="千代田区", jis_code="13101", value="13101")


@pytest.fixture(scope="module")
def scraper() -> ChintaiExScraper:
    return ChintaiExScraper()


@pytest.fixture(scope="module")
def listings(scraper: ChintaiExScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def grouped(scraper: ChintaiExScraper):
    """建物ごとに行をまとめる別レイアウトの一覧（足立区）。"""
    return scraper.parse_list((FIXTURES / "list_grouped.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compact(scraper: ChintaiExScraper):
    """項目名を持たない圧縮行レイアウトの一覧。"""
    return scraper.parse_list((FIXTURES / "list_compact.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: ChintaiExScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["CHINTAI_EX"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_一覧URLはJISコードのパス形式(scraper: ChintaiExScraper) -> None:
    assert scraper.list_urls(_pattern(), [CHIYODA]) == [
        "https://chintai-ex.jp/search/city/13101"
    ]


def test_価格上限を渡してもクエリを付けない(scraper: ChintaiExScraper) -> None:
    # robots.txt が Disallow: *?* でクエリ付きURLを全面的に禁じている
    url = scraper.list_urls(_pattern(price_max_hint=90000), [CHIYODA])[0]
    assert "?" not in url


def test_ページ送りもパス形式(scraper: ChintaiExScraper) -> None:
    base = "https://chintai-ex.jp/search/city/13101"
    assert scraper.page_url(base, 1) == base
    assert scraper.page_url(base, 3) == f"{base}/page/3"
    assert "?" not in scraper.page_url(base, 3)


def test_市区が必須(scraper: ChintaiExScraper) -> None:
    assert scraper.requires_city is True
    with pytest.raises(ValueError, match="市区の指定が要ります"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])


def test_最終ページは0件で判定する(scraper: ChintaiExScraper) -> None:
    # 市区によって一覧レイアウトが2通りあり1ページあたりの件数が一定しない
    assert scraper.is_last_page(0) is True
    assert scraper.is_last_page(1) is False
    assert scraper.is_last_page(PAGE_SIZE) is False


# --- 一覧パース ----------------------------------------------------------


def test_PR枠を検索結果として拾わない(listings) -> None:
    # li.swiper-slide の中の table.bukken は PR 枠で js-bukken を持たない
    assert len(listings) == PAGE_SIZE


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))
    assert all(x.startswith("z_") for x in ids)


def test_一覧から必須項目が取れる(listings) -> None:
    for field in (
        "price",
        "mgmt_fee_monthly",
        "area_sqm",
        "layout",
        "floor_num",
        "total_floors",
        "age_years",
        "walk_minutes",
        "address",
    ):
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_築年数は築年月から計算する(listings) -> None:
    # 賃貸EX は「築N年」を出さず築年月しか載せない
    assert all(x.age_years is not None and x.age_years >= 0 for x in listings)


def test_詳細URLは絶対URLになる(listings) -> None:
    assert all(x.url.startswith("https://chintai-ex.jp/dwelling/show/") for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"CHINTAI_EX"}


def test_建物まとめレイアウトでも同じ項目が取れる(grouped) -> None:
    # 千代田区は 1掲載=1テーブル、足立区は建物ごとにまとめた tr。
    # タグではなくクラスで拾うことで両方に効く
    assert len(grouped) == PAGE_SIZE
    for field in ("price", "mgmt_fee_monthly", "area_sqm", "layout", "age_years", "address"):
        missing = [x for x in grouped if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"
    ids = [x.external_id for x in grouped]
    assert len(ids) == len(set(ids))


def test_圧縮レイアウトでも同じ項目が取れる(compact) -> None:
    # 項目名（th）が無く、値はセルのクラスで引く。住所・駅・階建・築年月は
    # 建物ヘッダ側にしか無いので親を辿って拾っている
    assert len(compact) > PAGE_SIZE
    for field in (
        "price",
        "mgmt_fee_monthly",
        "deposit_amount",
        "key_money_amount",
        "area_sqm",
        "layout",
        "floor_num",
        "total_floors",
        "age_years",
        "walk_minutes",
        "address",
    ):
        missing = [x for x in compact if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_圧縮レイアウトの間取りに面積を混ぜない(compact) -> None:
    # 間取りと面積は <br> 区切りの別ノード。連結して読むと「1R 12.66m²」になる
    assert all("m" not in (x.layout or "") for x in compact)
    assert {x.layout for x in compact} <= {
        "1R", "1K", "1DK", "1LDK", "2K", "2DK", "2LDK", "3K", "3DK", "3LDK", "4LDK",
    }


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    assert "エアコン" in detail.raw_features_text


def test_他社掲載ではなく対象の掲載を採る(detail) -> None:
    # 同一物件の他社掲載と近隣物件が続けて並ぶ。th/td は最初の出現だけを採る
    assert detail.built_on == dt.date(1971, 11, 1)
    assert detail.floor_num == 5
    assert detail.total_floors == 5
    assert detail.mgmt_fee_monthly == 15000


def test_詳細から住所が取れる(detail) -> None:
    assert detail.address == "東京都千代田区外神田２"
