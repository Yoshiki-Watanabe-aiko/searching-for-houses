"""goo不動産 アダプタのテスト（実HTMLフィクスチャ方式）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.area import AreaTarget
from house_search.scrape.goo import PAGE_SIZE, GooScraper

FIXTURES = Path(__file__).parent / "fixtures" / "goo"

CHIYODA = AreaTarget(prefecture="東京都", city_name="千代田区", jis_code="13101", value="13101")


@pytest.fixture(scope="module")
def scraper() -> GooScraper:
    return GooScraper()


@pytest.fixture(scope="module")
def listings(scraper: GooScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: GooScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["GOO"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_一覧URLは地方区分と都道府県スラグとJISコードで組む(scraper: GooScraper) -> None:
    url = scraper.list_urls(_pattern(), [CHIYODA])[0]
    assert url == "https://house.goo.ne.jp/rent/shuto_ap/area_tokyo/13101.html"


def test_価格上限は万円の整数で切り上げる(scraper: GooScraper) -> None:
    # 90,000円 → ru=9。端数があるときは切り上げて取りこぼさない
    assert "ru=9" in scraper.list_urls(_pattern(price_max_hint=90000), [CHIYODA])[0]
    assert "ru=10" in scraper.list_urls(_pattern(price_max_hint=95000), [CHIYODA])[0]


def test_設備条件のパラメータを含めない(scraper: GooScraper) -> None:
    url = scraper.list_urls(_pattern(price_max_hint=90000), [CHIYODA])[0]
    for parameter in ("ut%5B%5D", "si%5B%5D", "lo%5B%5D", "ut[]", "si[]"):
        assert parameter not in url


def test_市区が無ければエラーになる(scraper: GooScraper) -> None:
    with pytest.raises(ValueError, match="市区の指定が要ります"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])


def test_地方区分が未登録の都道府県はエラーになる(scraper: GooScraper) -> None:
    # 綴りを推測して 404 を踏むより、足りていないことを明示的に落とす
    area = AreaTarget(prefecture="大阪府", city_name="北区", value="27127")
    with pytest.raises(ValueError, match="地方区分が未登録"):
        scraper.list_urls(_pattern(), [area])


def test_ページ番号の付与と最終ページ判定(scraper: GooScraper) -> None:
    assert scraper.page_url("https://x/a.html", 3).endswith("?p=3")
    assert scraper.page_url("https://x/a.html?ru=9", 3).endswith("&p=3")
    assert scraper.is_last_page(PAGE_SIZE - 1) is True
    assert scraper.is_last_page(PAGE_SIZE) is False


# --- 一覧パース ----------------------------------------------------------


def test_建物ごとに複数住戸を展開する(listings) -> None:
    assert len(listings) > 30


def test_物件IDは掲載ごとに一意(listings) -> None:
    # goo の ID は数字だけのものと英字混じり（1030H1005...）が混在する
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))
    assert all(x.isalnum() for x in ids)


def test_一覧から必須項目が取れる(listings) -> None:
    for field in ("price", "area_sqm", "layout", "age_years", "walk_minutes", "address"):
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_管理費と敷金礼金は0円として取れる(listings) -> None:
    for field in ("mgmt_fee_monthly", "deposit_amount", "key_money_amount"):
        assert all(getattr(x, field) is not None for x in listings), field
    assert any(x.deposit_amount == 0 for x in listings)


def test_所在階にNEWバッジを拾わない(listings) -> None:
    # td.property-floor の ul には「NEW」「閲覧済」も並ぶ
    assert all(x.floor_num is not None for x in listings)
    assert all(-10 < x.floor_num < 80 for x in listings)


def test_詳細URLは絶対URLになる(listings) -> None:
    assert all(x.url.startswith("https://house.goo.ne.jp/") for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"GOO"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    assert "オートロック" in detail.raw_features_text


def test_該当しない条件を原文に載せない(detail) -> None:
    # 条件行は td が ○ なら該当・- なら非該当。- まで載せると辞書が拾う
    assert "最上階" not in detail.raw_features_text
    assert "角部屋" not in detail.raw_features_text


def test_市区の統計情報を原文に載せない(detail) -> None:
    # 同じページに「ごみ収集」「病院総数」などが大量に並ぶ。
    # ラベルの白名簿で絞らないと未知表記が汚染される
    for noise in ("ごみ", "待機児童", "刑法犯", "水道料金"):
        assert noise not in detail.raw_features_text


def test_住所から導線リンクの文言を落とす(detail) -> None:
    assert detail.address == "東京都千代田区神田神保町2丁目23-2"


def test_詳細から築年月と階数が取れる(detail) -> None:
    assert detail.built_on == dt.date(2004, 3, 1)
    assert detail.floor_num == 8
    assert detail.total_floors == 13
