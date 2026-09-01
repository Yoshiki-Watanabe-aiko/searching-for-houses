"""SUUMO アダプタのテスト（実HTMLフィクスチャ方式）。

``tests/fixtures/suumo/`` に保存した実ページを使い、ネットワーク無しで
一覧・詳細のパースを回帰テストする。DOM構造が変わったらここが落ちる。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.base import (
    parse_age_years,
    parse_area_sqm,
    parse_built_on,
    parse_floor,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
    prefecture_targets,
)
from house_search.scrape.suumo import PAGE_SIZE, SuumoScraper

FIXTURES = Path(__file__).parent / "fixtures" / "suumo"


@pytest.fixture(scope="module")
def scraper() -> SuumoScraper:
    return SuumoScraper()


@pytest.fixture(scope="module")
def listings(scraper: SuumoScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: SuumoScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


# --- パース補助 ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3万円", 30000), ("3.5万円", 35000), ("2000円", 2000), ("-", None), (None, None)],
)
def test_金額のパース(raw, expected) -> None:
    assert parse_yen(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("18m2", 18.0), ("42.5m²", 42.5), ("1,020m2", 1020.0), ("-", None)]
)
def test_面積のパース(raw, expected) -> None:
    assert parse_area_sqm(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("築40年", 40), ("新築", 0), ("築0年", 0), ("-", None)]
)
def test_築年数のパース(raw, expected) -> None:
    assert parse_age_years(raw) == expected


def test_徒歩分数は複数路線の最短を採る() -> None:
    assert parse_walk_minutes("ＪＲ/A駅 歩50分 / 京王線/B駅 歩7分") == 7


@pytest.mark.parametrize(
    ("raw", "expected"), [("3階", 3), ("地下1階", -1), ("B1階", 1), ("-", None)]
)
def test_所在階のパース(raw, expected) -> None:
    assert parse_floor(raw) == expected


def test_階建のパース() -> None:
    assert parse_total_floors("1階/2階建") == 2
    assert parse_total_floors("14階建") == 14


def test_築年月は日を1日に固定する() -> None:
    assert parse_built_on("1987年1月") == dt.date(1987, 1, 1)
    assert parse_built_on("2024年12月") == dt.date(2024, 12, 1)
    assert parse_built_on("-") is None


# --- URL構築 -------------------------------------------------------------


def _areas(pattern):
    """パターンの都道府県から、市区指定なしのエリア対象を作る。"""
    return prefecture_targets(pattern.search.prefectures)


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["SUUMO"],
            "search": {"prefectures": ["東京都", "千葉県"], **search_overrides},
        }
    )


def test_一覧URLは都道府県ごとに1本作られる(scraper: SuumoScraper) -> None:
    urls = scraper.list_urls(p := _pattern(price_max_hint=90000), _areas(p))
    assert len(urls) == 2
    assert "ta=13" in urls[0] and "ar=030" in urls[0]
    assert "ta=12" in urls[1]


def test_一覧URLに設備条件のパラメータを含めない(scraper: SuumoScraper) -> None:
    # v2 でサイトへ渡すのはエリア・種別・価格上限だけ（→ ADR 0003）。
    # tc= を送ると WANT の物件がサイト側で除外され、ランキングと両立しない
    url = scraper.list_urls(p := _pattern(price_max_hint=90000), _areas(p))[0]
    assert "tc=" not in url
    assert "md=" not in url
    assert "mb=" not in url
    assert "et=" not in url


def test_賃貸コードと価格上限が万円で渡る(scraper: SuumoScraper) -> None:
    url = scraper.list_urls(p := _pattern(price_max_hint=90000), _areas(p))[0]
    assert "bs=040" in url
    assert "ct=9.0" in url


def test_価格上限が未指定ならctを付けない(scraper: SuumoScraper) -> None:
    assert "ct=" not in scraper.list_urls(p := _pattern(), _areas(p))[0]


def test_未知の都道府県はエラーになる(scraper: SuumoScraper) -> None:
    pattern = parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["SUUMO"],
            "search": {"prefectures": ["架空県"]},
        }
    )
    with pytest.raises(ValueError, match="未知の都道府県"):
        scraper.list_urls(pattern, _areas(pattern))


def test_ページ番号の付与と最終ページ判定(scraper: SuumoScraper) -> None:
    assert scraper.page_url("https://x/?a=1", 3).endswith(f"&pc={PAGE_SIZE}&pn=3")
    assert scraper.is_last_page(PAGE_SIZE - 1) is True
    assert scraper.is_last_page(PAGE_SIZE) is False


# --- 一覧パース ----------------------------------------------------------


def test_建物ごとに複数住戸を展開する(listings) -> None:
    # SUUMO は「1建物=複数住戸」の入れ子構造。建物数(30)より掲載数が多くなる
    assert len(listings) > 30


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))
    assert all(x.isdigit() for x in ids)


def test_一覧から必須項目が取れる(listings) -> None:
    # 一覧だけで MUST の1段目判定ができることがこのパーサの要件
    for field in ("price", "area_sqm", "layout", "age_years", "walk_minutes", "address"):
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_管理費と敷金礼金は0円として取れる(listings) -> None:
    # 「-」を None にすると rent_total が「管理費不明」になり、
    # 実際には管理費0円の物件が MUST 判定で unknown に落ちてしまう
    for field in ("mgmt_fee_monthly", "deposit_amount", "key_money_amount"):
        assert all(getattr(x, field) is not None for x in listings), field
    assert any(x.mgmt_fee_monthly == 0 for x in listings)


def test_詳細URLは絶対URLになる(listings) -> None:
    assert all(x.url.startswith("https://suumo.jp/") for x in listings)


def test_サムネイルにデータURIを拾わない(listings) -> None:
    assert not any((x.image_url or "").startswith("data:") for x in listings)


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"SUUMO"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    assert "エアコン" in detail.raw_features_text


def test_構造化フィールドを辞書が照合できる形に正規化する(detail) -> None:
    # 「駐車場: 近隣205m16500円」のような欄は語彙が無いので、
    # サイトアダプタが「駐車場あり」という派生トークンへ寄せる
    assert "駐車場あり" in detail.raw_features_text
    assert "木造" in detail.raw_features_text


def test_建物種別は設備原文に混ぜない(detail) -> None:
    # アパート/マンションは対応する条件が無く、未知表記を汚すだけ
    assert "アパート" not in (detail.raw_features_text or "")
    assert detail.type_specific_attrs.get("building_type") == "アパート"


def test_詳細から所在階と築年月が取れる(detail) -> None:
    assert detail.floor_num == 1
    assert detail.total_floors == 2
    assert detail.built_on == dt.date(1987, 1, 1)


def test_詳細から住所と徒歩分数が取れる(detail) -> None:
    assert detail.address == "東京都八王子市中野町"
    assert detail.walk_minutes == 42


def test_値なしの欄は種別固有属性に入れない(detail) -> None:
    # 「向き: -」を属性として持つと、後段が「-」という値を意味ありと誤解する
    assert "facing" not in detail.type_specific_attrs
