"""いい部屋ネット アダプタのテスト（実HTMLフィクスチャ方式）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.eheya import PAGE_SIZE, EheyaScraper

FIXTURES = Path(__file__).parent / "fixtures" / "eheya"

ADACHI = AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")


@pytest.fixture(scope="module")
def scraper() -> EheyaScraper:
    return EheyaScraper()


@pytest.fixture(scope="module")
def listings(scraper: EheyaScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: EheyaScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["EHEYA"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_市区の検索値はJIS5桁(scraper: EheyaScraper) -> None:
    # Phase 3 の実測で確定。m_city_site_values の東京23区ブロックが正しかった
    assert scraper.city_value_source == CITY_VALUE_JIS
    url = scraper.list_urls(_pattern(), [ADACHI])[0]
    assert url == "https://www.eheya.net/tokyo/area/13121/search/"


def test_市区が空なら都道府県単位で引く(scraper: EheyaScraper) -> None:
    assert scraper.requires_city is False
    url = scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])[0]
    assert url == "https://www.eheya.net/tokyo/search/"


def test_賃料上限はサイトへ渡さない(scraper: EheyaScraper) -> None:
    # クエリで条件を受け取らないことを実測済み。上限判定はローカルで行う
    url = scraper.list_urls(_pattern(price_max_hint=90000), [ADACHI])[0]
    assert "?" not in url


def test_ページ番号の付与と最終ページ判定(scraper: EheyaScraper) -> None:
    assert scraper.page_url("https://x/tokyo/search/", 2).endswith("?page=2")
    assert scraper.is_last_page(PAGE_SIZE - 1) is True
    assert scraper.is_last_page(PAGE_SIZE) is False
    # ⚠ サイト側フィルタを配線したときに ? を重ねないこと（APAMAN で実際に起きた
    # 「page が黙って無視され1ページ目を返し続ける」事故の予防 → 課題#29）
    assert scraper.page_url("https://x/tokyo/search/?a=1", 2).endswith("?a=1&page=2")


# --- 一覧パース ----------------------------------------------------------


def test_掲載が取れる(listings) -> None:
    assert len(listings) >= 40


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))


def test_詳細URLは物件IDから組み立てる(listings) -> None:
    for listing in listings:
        assert listing.url == f"https://www.eheya.net/detail/{listing.external_id}/"


def test_一覧から必須項目が取れる(listings) -> None:
    fields = ("price", "area_sqm", "layout", "walk_minutes", "address", "age_years", "floor_num")
    for field in fields:
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_金額はJSONの数値をそのまま使う(listings) -> None:
    # 「無料」の文字列解釈が要らないので 0 が素直に入る
    assert all(x.mgmt_fee_monthly is not None for x in listings)
    assert all(x.deposit_amount is not None for x in listings)
    assert any(x.deposit_amount == 0 for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"EHEYA"}


def test_NEXT_DATAが無ければエラーにする(scraper: EheyaScraper) -> None:
    # 黙って0件を返すと「取れているつもり」で気づけない
    with pytest.raises(ValueError, match="__NEXT_DATA__"):
        scraper.parse_list("<html><body>変更後のページ</body></html>")


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    for token in ("システムキッチン", "エアコン", "ディンプルキー"):
        assert token in detail.raw_features_text


def test_生成文は設備原文に載せない(detail) -> None:
    # remarks / salesPoint は宣伝の生成文。載せると未知表記が文断片で埋まる
    assert "いかがでしょうか" not in detail.raw_features_text
    assert "ご依頼ください" not in detail.raw_features_text


def test_詳細から築年月と階が取れる(detail) -> None:
    assert detail.built_on == dt.date(2017, 1, 1)
    assert detail.floor_num == 3
    assert detail.total_floors == 3


def test_詳細から金額と住所が取れる(detail) -> None:
    assert detail.mgmt_fee_monthly == 15000
    assert detail.deposit_amount == 0
    assert detail.key_money_amount == 0
    assert detail.address == "東京都足立区千住中居町"


def test_徒歩分数は複数路線の最短を採る(detail) -> None:
    assert detail.walk_minutes == 10
