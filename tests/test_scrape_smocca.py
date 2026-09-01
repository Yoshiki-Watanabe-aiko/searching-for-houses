"""スモッカ アダプタのテスト（実HTMLフィクスチャ方式）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.smocca import SmoccaScraper

FIXTURES = Path(__file__).parent / "fixtures" / "smocca"

ADACHI = AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")


@pytest.fixture(scope="module")
def scraper() -> SmoccaScraper:
    return SmoccaScraper()


@pytest.fixture(scope="module")
def listings(scraper: SmoccaScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: SmoccaScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["SMOCCA"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_市区の検索値はJIS5桁(scraper: SmoccaScraper) -> None:
    # Phase 3 の実測で確定。m_city_site_values の東京23区ブロックが正しかった
    assert scraper.city_value_source == CITY_VALUE_JIS
    url = scraper.list_urls(_pattern(), [ADACHI])[0]
    assert url == "https://smocca.jp/search/tokyo/city/13121"


def test_市区が必須(scraper: SmoccaScraper) -> None:
    assert scraper.requires_city is True
    with pytest.raises(ValueError, match="市区の指定が要ります"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])


def test_賃料上限はサイトへ渡さない(scraper: SmoccaScraper) -> None:
    # /search/results が robots.txt で禁止のため条件検索を使えない
    url = scraper.list_urls(_pattern(price_max_hint=90000), [ADACHI])[0]
    assert "?" not in url


def test_ページ送りは禁止されているので1ページ目だけ扱う(scraper: SmoccaScraper) -> None:
    base = "https://smocca.jp/search/tokyo/city/13121"
    assert scraper.page_url(base, 1) == base
    with pytest.raises(ValueError, match="robots.txt"):
        scraper.page_url(base, 2)
    # 常に最終ページとして扱い、2ページ目を取りに行かせない
    assert scraper.is_last_page(90) is True
    assert scraper.is_last_page(0) is True


# --- 一覧パース ----------------------------------------------------------


def test_掲載が取れる(listings) -> None:
    assert len(listings) >= 80


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))


def test_詳細URLは物件詳細ページを指す(listings) -> None:
    for listing in listings:
        assert listing.url.startswith("https://smocca.jp/bukken/detail/")


def test_一覧から必須項目が取れる(listings) -> None:
    fields = ("price", "area_sqm", "layout", "walk_minutes", "address", "age_years", "total_floors")
    for field in fields:
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_入れ子のspanに割れた賃料を読める(listings) -> None:
    # <span><span>12.8</span>万円</span> を itertext で切ると金額にならない
    assert all(x.price and x.price > 0 for x in listings)


def test_グループ表示の掲載も読める(listings) -> None:
    # 賃料・管理費・敷金・礼金が1セルに同居するレイアウトが混ざる
    assert all(x.mgmt_fee_monthly is not None for x in listings)
    assert all(x.deposit_amount is not None for x in listings)


def test_築年月から築年数を数える(listings) -> None:
    # 建物欄は「地上3階建 / 2014年04月 / 賃貸アパート」で築年数が載らない
    assert any(x.age_years and x.age_years > 0 for x in listings)
    assert any(x.age_years == 0 for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"SMOCCA"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    for token in ("バス・トイレ別", "オートロック", "宅配ボックス"):
        assert token in detail.raw_features_text


def test_備考の生成文は設備原文に載せない(detail) -> None:
    assert "巡回管理" not in detail.raw_features_text


def test_詳細から築年月と階が取れる(detail) -> None:
    assert detail.built_on == dt.date(2022, 12, 1)
    # 「2階/地上3階建」
    assert detail.floor_num == 2
    assert detail.total_floors == 3


def test_賃料欄から管理費だけを取り出す(detail) -> None:
    # 「10.9万円（管理費等 3,000円）」が1つの td に入る
    assert detail.mgmt_fee_monthly == 3000


def test_詳細の住所から導線リンクを落とす(detail) -> None:
    # 「東京都足立区西新井３ 足立区の賃貸を探す」
    assert detail.address == "東京都足立区西新井３"


def test_構造と方位を派生トークンへ寄せる(detail) -> None:
    # 「アパート/木造」の後半が構造
    assert "木造" in detail.raw_features_text
    assert "南向き" in detail.raw_features_text
