"""ニフティ不動産 アダプタのテスト（実HTMLフィクスチャ方式）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.area import CITY_VALUE_MAPPING, AreaTarget
from house_search.scrape.nifty import NiftyScraper

FIXTURES = Path(__file__).parent / "fixtures" / "nifty"

ADACHI = AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="adachiku")


@pytest.fixture(scope="module")
def scraper() -> NiftyScraper:
    return NiftyScraper()


@pytest.fixture(scope="module")
def listings(scraper: NiftyScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: NiftyScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["NIFTY"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_一覧URLは市区スラグに_ctを付ける(scraper: NiftyScraper) -> None:
    assert scraper.city_value_source == CITY_VALUE_MAPPING
    url = scraper.list_urls(_pattern(), [ADACHI])[0]
    assert url == "https://myhome.nifty.com/rent/tokyo/adachiku_ct/"


def test_市区が必須(scraper: NiftyScraper) -> None:
    # 都道府県ページはエリア索引で掲載が載らない（実測で詳細リンク6本のみ）
    assert scraper.requires_city is True
    with pytest.raises(ValueError, match="市区の指定が要ります"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])


def test_賃料上限はサイトへ渡さない(scraper: NiftyScraper) -> None:
    # robots.txt がクエリ付きURLを広く禁じており、上限のパラメータも未検証
    url = scraper.list_urls(_pattern(price_max_hint=90000), [ADACHI])[0]
    assert "?" not in url


def test_ページ送りはパス形式(scraper: NiftyScraper) -> None:
    base = "https://myhome.nifty.com/rent/tokyo/adachiku_ct/"
    assert scraper.page_url(base, 1) == base
    assert scraper.page_url(base, 2) == base.rstrip("/") + "/2/"


def test_件数では最終ページを判定しない(scraper: NiftyScraper) -> None:
    # 外部ドメインの掲載を落とすので掲載数が建物数を下回りうる
    assert scraper.is_last_page(1) is False
    assert scraper.is_last_page(0) is True


# --- 一覧パース ----------------------------------------------------------


def test_掲載が取れる(listings) -> None:
    assert len(listings) >= 25


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))


def test_自社ドメインの詳細だけを取り込む(listings) -> None:
    # 他社サイト（sumaisagashi-madoguchi.com 等）へ飛ぶ掲載は対象外
    for listing in listings:
        assert listing.url.startswith("https://myhome.nifty.com/rent/")
        assert f"detail_{listing.external_id}/" in listing.url


def test_一覧から必須項目が取れる(listings) -> None:
    fields = ("price", "area_sqm", "layout", "walk_minutes", "address", "age_years", "floor_num")
    for field in fields:
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_築年数に築が付かない表記を読める(listings) -> None:
    # 「1年7ヶ月」形式。共通の parse_age_years は「築」を要求するので専用に読む
    assert listings[0].age_years == 1


def test_不要表記の敷金礼金を0円として読む(listings) -> None:
    assert all(x.deposit_amount is not None for x in listings)
    assert any(x.deposit_amount == 0 for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"NIFTY"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    for token in ("バス・トイレ別", "オートロック", "宅配ボックス"):
        assert token in detail.raw_features_text


def test_備考の生成文は設備原文に載せない(detail) -> None:
    assert "その他の情報" not in detail.raw_features_text
    assert "東証グロース" not in detail.raw_features_text


def test_詳細から築年月と階が取れる(detail) -> None:
    assert detail.built_on == dt.date(2021, 3, 1)
    assert detail.floor_num == 9
    assert detail.total_floors == 12


def test_賃料欄から管理費だけを取り出す(detail) -> None:
    # 「10.1万円＋ 管理費等8,000円」が1つの dd に入る
    assert detail.mgmt_fee_monthly == 8000


def test_詳細から敷金礼金と住所が取れる(detail) -> None:
    assert detail.deposit_amount == 0
    assert detail.key_money_amount == 202000
    assert detail.address == "東京都足立区千住緑町3丁目28-1"


def test_案内文言を値から落とす(detail) -> None:
    # 「無 質問 駐車場について詳しく教えてほしい」の後半は値ではない
    assert "質問" not in (detail.raw_features_text or "")
