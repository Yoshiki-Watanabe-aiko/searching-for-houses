"""アットホーム アダプタのテスト（実HTMLフィクスチャ方式）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.area import AreaTarget
from house_search.scrape.athome import PAGE_SIZE, AthomeScraper, _price_to_code

FIXTURES = Path(__file__).parent / "fixtures" / "athome"

ADACHI = AreaTarget(
    prefecture="東京都", city_name="足立区", jis_code="13121", value="tokyo/adachi-city"
)


@pytest.fixture(scope="module")
def scraper() -> AthomeScraper:
    return AthomeScraper()


@pytest.fixture(scope="module")
def listings(scraper: AthomeScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: AthomeScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["ATHOME"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_市区の値は都道府県を含むスラグをそのまま使う(scraper: AthomeScraper) -> None:
    url = scraper.list_urls(_pattern(), [ADACHI])[0]
    assert url.startswith("https://www.athome.co.jp/chintai/tokyo/adachi-city/list/")


def test_市区が空なら都道府県単位で引く(scraper: AthomeScraper) -> None:
    # 都道府県ページでも掲載が返ることを実測済み（requires_city ではない）
    assert scraper.requires_city is False
    url = scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])[0]
    assert url.startswith("https://www.athome.co.jp/chintai/tokyo/list/")


def test_未知の都道府県はエラーにする(scraper: AthomeScraper) -> None:
    with pytest.raises(ValueError, match="未知の都道府県"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京")])


def test_賃料上限はセレクトのコードへ変換する(scraper: AthomeScraper) -> None:
    # 3万円=kc101 から0.5万円刻み（kc109=7万円 を実測で確認）
    assert _price_to_code(70000) == "kc109"
    assert _price_to_code(90000) == "kc113"
    assert _price_to_code(None) is None
    url = scraper.list_urls(_pattern(price_max_hint=90000), [ADACHI])[0]
    assert "PRICETO=kc113" in url


def test_選択肢に無い賃料上限は切り上げる() -> None:
    # 取りこぼしを作らないため1つ上の帯へ寄せる（7.2万円 → 7.5万円）
    assert _price_to_code(72000) == "kc110"


def test_ページ送りはパス形式(scraper: AthomeScraper) -> None:
    paged = scraper.page_url("https://x/chintai/tokyo/list/?SORT=7", 3)
    assert paged == "https://x/chintai/tokyo/list/page3/?SORT=7"
    assert scraper.is_last_page(PAGE_SIZE - 1) is True
    assert scraper.is_last_page(PAGE_SIZE) is False


def test_robotsは尊重する(scraper: AthomeScraper) -> None:
    assert scraper.ignore_robots is False


def test_ボット検知の認証ページはエラーにする(scraper: AthomeScraper) -> None:
    # 200 のまま認証ページが返る。そのまま解析すると0件になり
    # 「取れているつもり」で気づけない（→ 課題#20）
    challenge = "<html><body>認証にご協力ください</body></html>"
    with pytest.raises(ValueError, match="ボット検知"):
        scraper.parse_list(challenge)
    with pytest.raises(ValueError, match="ボット検知"):
        scraper.parse_detail(challenge)


# --- 一覧パース ----------------------------------------------------------


def test_掲載が取れる(listings) -> None:
    # 1建物=複数住戸。建物30件から住戸が展開される
    assert len(listings) >= 60


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))


def test_詳細URLは物件IDから組み立てる(listings) -> None:
    for listing in listings:
        assert listing.url == f"https://www.athome.co.jp/chintai/{listing.external_id}/"


def test_一覧から必須項目が取れる(listings) -> None:
    for field in ("price", "area_sqm", "layout", "walk_minutes", "address", "age_years"):
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_管理費は賃料セルのspanから読む(listings) -> None:
    assert all(x.mgmt_fee_monthly is not None for x in listings)


def test_月数表記の敷金礼金を円へ直す(listings) -> None:
    # 「1ヶ月」表記。円へ直すには賃料が要る
    assert all(x.deposit_amount is not None for x in listings)
    assert any(x.key_money_amount and x.key_money_amount > 0 for x in listings)


def test_部屋番号の欄は階として読めるときだけ使う(listings) -> None:
    # 「２０５」のような号室表記では所在階を取らない（None のままにする）
    assert any(x.floor_num is not None for x in listings)
    assert any(x.floor_num is None for x in listings)


def test_建物ヘッダの住所と築年が住戸へ伝わる(listings) -> None:
    first = listings[0]
    assert first.address == "足立区綾瀬６丁目"
    assert first.total_floors == 3
    assert first.title


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"ATHOME"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    for token in ("バス・トイレ別", "システムキッチン", "モニター付インターホン"):
        assert token in detail.raw_features_text


def test_詳細から築年月と階が取れる(detail) -> None:
    assert detail.built_on == dt.date(1989, 12, 1)
    # 「3階建 / 2階」の後半が所在階
    assert detail.floor_num == 2
    assert detail.total_floors == 3


def test_詳細から金額が取れる(detail) -> None:
    assert detail.mgmt_fee_monthly == 10000
    assert detail.deposit_amount == 108000
    assert detail.key_money_amount == 108000


def test_詳細から住所が取れる(detail) -> None:
    assert detail.address == "東京都足立区綾瀬６丁目"


def test_有無の欄を派生トークンへ寄せる(detail) -> None:
    # 「駐輪場: 有」は語彙が無いので辞書が照合できる語に直す。
    # 「駐車場: 無」は載せない
    assert "駐輪場あり" in detail.raw_features_text
    assert "駐車場あり" not in detail.raw_features_text


def test_採光面を向きの語へ直す(detail) -> None:
    assert "南向き" in detail.raw_features_text
