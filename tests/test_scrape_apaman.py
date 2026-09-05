"""アパマンショップ アダプタのテスト（実HTMLフィクスチャ方式）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from lxml import html as lxml_html

from house_search.config.pattern import parse_pattern
from house_search.scrape.apaman import PAGE_SIZE, ApamanScraper, _detail_tables
from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget

FIXTURES = Path(__file__).parent / "fixtures" / "apaman"

ADACHI = AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")


@pytest.fixture(scope="module")
def scraper() -> ApamanScraper:
    return ApamanScraper()


@pytest.fixture(scope="module")
def listings(scraper: ApamanScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: ApamanScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["APAMAN"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_市区コードはJIS5桁の下3桁(scraper: ApamanScraper) -> None:
    # 足立区 13121 → /tokyo/121/
    assert scraper.city_value_source == CITY_VALUE_JIS
    url = scraper.list_urls(_pattern(), [ADACHI])[0]
    assert url == "https://www.apamanshop.com/tokyo/121/"


def test_市区が必須(scraper: ApamanScraper) -> None:
    assert scraper.requires_city is True
    with pytest.raises(ValueError, match="市区の指定が要ります"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])


def test_robotsを無視するのはこのサイトだけ(scraper: ApamanScraper) -> None:
    # robots.txt が `User-agent: * / Disallow: /` で全パスを禁じているが、
    # ユーザーの明示的な判断で取得する（→ ADR 0011）
    assert scraper.ignore_robots is True


def test_ページ番号の付与と最終ページ判定(scraper: ApamanScraper) -> None:
    base = "https://www.apamanshop.com/tokyo/121/"
    assert scraper.page_url(base, 1) == base
    assert scraper.page_url(base, 3) == base + "?page=3"
    assert scraper.is_last_page(PAGE_SIZE - 1) is True
    assert scraper.is_last_page(PAGE_SIZE) is False


def test_サイト側フィルタが付いていてもページ送りが壊れない(scraper: ApamanScraper) -> None:
    """⚠ ``?`` を重ねると page が黙って無視され、1ページ目を返し続ける。

    実測（2026-09-03）: ``...?senyu1=30&ekitoho=20?page=2`` は HTTP 200 で
    掲載26件を返し、**1ページ目と完全に同じ掲載**だった。例外にならないので
    ページ送りが死んでいることに気づけない（→ 課題#29）。
    """
    filtered = "https://www.apamanshop.com/tokyo/121/?senyu1=30&ekitoho=20"
    assert scraper.page_url(filtered, 2) == filtered + "&page=2"
    assert scraper.page_url(filtered, 1) == filtered


# --- 一覧パース ----------------------------------------------------------


def test_掲載が取れる(listings) -> None:
    assert len(listings) >= 20


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))


def test_詳細URLは住戸ページを指す(listings) -> None:
    # 「お問い合わせ」（/inquiry/bukkenentry/）は詳細ページではない
    for listing in listings:
        assert "/inquiry/" not in listing.url
        assert listing.url.endswith(f"/{listing.external_id}/")


def test_一覧から必須項目が取れる(listings) -> None:
    fields = ("price", "area_sqm", "layout", "walk_minutes", "address", "age_years", "floor_num")
    for field in fields:
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_ヘッダの列位置ではなく賃料セルを起点に読む(listings) -> None:
    # ヘッダは先頭に余分な空セルがあり、列位置で対応させると
    # 間取りの欄に「お気に入り」を読んでしまう
    assert all(x.layout and x.layout != "追加" for x in listings)


def test_月数表記の敷金礼金を円へ直す(listings) -> None:
    assert all(x.deposit_amount is not None for x in listings)
    assert any(x.key_money_amount == 0 for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"APAMAN"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    for token in ("バス・トイレ別", "宅配ボックス", "室内洗濯置場"):
        assert token in detail.raw_features_text


def test_見出しspanの系統から所在地と築年月を読む(detail) -> None:
    # 項目名が th ではなく span.heading になっている系統がある
    assert detail.built_on == dt.date(2025, 7, 1)
    assert detail.address and detail.address.startswith("東京都")


def test_詳細から階が取れる(detail) -> None:
    # 「3階建/2階」の後半が所在階
    assert detail.floor_num == 2
    assert detail.total_floors == 3


def test_詳細から金額が取れる(detail) -> None:
    assert detail.mgmt_fee_monthly == 3000
    assert detail.deposit_amount == 67000
    assert detail.key_money_amount == 0


def test_構造と方位を派生トークンへ寄せる(detail) -> None:
    # 「木造/アパート」の前半が構造
    assert "木造" in detail.raw_features_text
    assert "南向き" in detail.raw_features_text


def test_未知表記の収集元は設備欄だけ() -> None:
    """⚠ 「条件等」欄には初期費用の説明文が入る（→ 課題#15）。

    そのまま収集すると ``t_unknown_tokens`` が金額の断片で埋まる。実データでは
    ``000円``（464回）・``鍵セット費3``（394回）のように**カンマで切れた金額**として
    貯まっており、実測312種あった。

    ⚠ **実フィクスチャの「条件等」は空なので、ここだけ合成HTMLで固定する。**
    実HTMLに該当ケースが無い以上フィクスチャ方式では検出できない
    （課題#44 で踏んだ「既存フィクスチャでは検出できない」と同じ形）。

    ⚠ **照合には「条件等」込みの原文を使う**（ペット相談可などの入居条件が
    入りうるため。外すと設備数が減る恐れがある → 課題#19 と同じ判断）。
    """
    doc = lxml_html.fromstring(
        "<table>"
        "<tr><th>設備</th><td>エアコン･オートロック</td></tr>"
        "<tr><th>条件等</th><td>鍵交換費:ご契約時16,500円(税込)がかかります。"
        "契約時にクリーニング費60,000円がかかります。</td></tr>"
        "</table>"
    )
    _fields, features, tagged = _detail_tables(doc)

    # 照合用の原文には両方入る
    assert len(features) == 2
    assert any("鍵交換費" in f for f in features)
    # 収集元は設備欄だけ
    assert tagged == ["エアコン･オートロック"]
