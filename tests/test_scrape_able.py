"""エイブル アダプタのテスト（実HTMLフィクスチャ方式）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.able import PAGE_SIZE, AbleScraper
from house_search.scrape.area import AreaTarget

FIXTURES = Path(__file__).parent / "fixtures" / "able"

CHIYODA = AreaTarget(prefecture="東京都", city_name="千代田区", jis_code="13101", value="13101")


@pytest.fixture(scope="module")
def scraper() -> AbleScraper:
    return AbleScraper()


@pytest.fixture(scope="module")
def listings(scraper: AbleScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: AbleScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["ABLE"],
            "search": {"prefectures": ["東京都"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_一覧URLは都道府県スラグとJISコードで組む(scraper: AbleScraper) -> None:
    url = scraper.list_urls(_pattern(), [CHIYODA])[0]
    assert url.startswith("https://www.able.co.jp/tokyo/area/13101/list/")


def test_市区が必須(scraper: AbleScraper) -> None:
    # 都道府県だけでは一覧が0件になる（課題#1）
    assert scraper.requires_city is True
    with pytest.raises(ValueError, match="市区の指定が要ります"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="東京都")])


def test_価格上限は渡さず賃料昇順で代替する(scraper: AbleScraper) -> None:
    # ct= は実測で効かない。1ページ目に MUST を通る価格帯を集めるため
    # 賃料が安い順（o=1）で並べる
    url = scraper.list_urls(_pattern(price_max_hint=90000), [CHIYODA])[0]
    assert "ct=" not in url
    assert "o=1" in url


def test_ページ番号の付与と最終ページ判定(scraper: AbleScraper) -> None:
    assert scraper.page_url("https://x/list/?o=1", 3).endswith("&i=3")
    assert scraper.is_last_page(PAGE_SIZE - 1) is True
    assert scraper.is_last_page(PAGE_SIZE) is False


# --- 一覧パース ----------------------------------------------------------


def test_掲載が取れる(listings) -> None:
    assert len(listings) >= 20


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))


def test_詳細URLはonclickから組み立てる(listings) -> None:
    # 「詳細を見る」の href は javascript:void(0) なので使えない
    assert all(x.url.startswith("https://www.able.co.jp/detail/Detail.do?bk=") for x in listings)
    assert all("javascript" not in x.url for x in listings)


def test_詳細URLは住戸ごとの物件IDを含む(listings) -> None:
    for listing in listings:
        assert f"bk={listing.external_id}" in listing.url


def test_一覧から必須項目が取れる(listings) -> None:
    for field in ("price", "area_sqm", "layout", "walk_minutes", "address"):
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_面積の平米記号を読める(listings) -> None:
    # ABLE は ㎡（U+33A1）を使う。NFKC 正規化しないと取りこぼす
    assert all(x.area_sqm and x.area_sqm > 0 for x in listings)


def test_ダッシュだけの管理費欄は0円として読む(listings) -> None:
    # 「--」は「無し」の意味。None にすると rent_total が管理費不明になる
    assert all(x.mgmt_fee_monthly is not None for x in listings)
    assert any(x.mgmt_fee_monthly == 0 for x in listings)


def test_円を省いた敷金表記を読める(listings) -> None:
    # ABLE の敷金欄は「23.9万」と円を省く
    assert all(x.deposit_amount is not None for x in listings)
    assert any(x.deposit_amount and x.deposit_amount > 0 for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"ABLE"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    assert "浴室乾燥機" in detail.raw_features_text


def test_築年月は年月を持つ欄から採る(detail) -> None:
    # 「築年」は「築20年」で年月が無い。年月は「築年/築年月」にだけ入る
    assert detail.built_on == dt.date(2006, 2, 1)


def test_詳細から階数と金額が取れる(detail) -> None:
    assert detail.floor_num == 6
    assert detail.total_floors == 15
    assert detail.mgmt_fee_monthly == 10000
    assert detail.deposit_amount == 239000
    assert detail.key_money_amount == 0


def test_詳細から住所が取れる(detail) -> None:
    assert detail.address == "東京都千代田区二番町"


def test_未知表記の収集元から注記を外す(scraper: AbleScraper) -> None:
    """⚠ 設備欄に注記が同居している（→ 課題#15）。

    「※インターネット接続環境…について利用料金は、共益費に含まれるタイプや
    個別に契約するタイプなど物件により異なります」が ``span.attention`` に入っており、
    そのまま収集すると ``t_unknown_tokens`` が説明文の断片で埋まる
    （実測807種で全サイト最多）。

    ⚠ **照合には注記込みの原文を使うので ``raw_features_text`` は変えない。**
    部分一致なので注記があっても害はなく、外すと設備数が減る恐れがある
    （安全側に倒す → 課題#19 と同じ判断）。
    """
    html_text = (FIXTURES / "detail_sample.html").read_text(encoding="utf-8", errors="replace")
    detail = scraper.parse_detail(html_text)

    assert detail.raw_features_text is not None
    assert detail.unknown_token_text is not None
    # 原文には残る（照合に使う）
    assert "共益費に含まれる" in detail.raw_features_text
    # 収集元からは外れる
    assert "共益費に含まれる" not in detail.unknown_token_text
    assert "物件お取り扱い不動産会社" not in detail.unknown_token_text
    # タグ列そのものは残っている
    assert "クローゼット" in detail.unknown_token_text
    assert len(detail.unknown_token_text) < len(detail.raw_features_text)
