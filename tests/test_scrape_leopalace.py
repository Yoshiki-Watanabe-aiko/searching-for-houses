"""レオパレス21アダプタの回帰テスト（実HTMLフィクスチャ）。

フィクスチャは 2026-09-04 に実サイトから取得したもの。実測の経緯と
数値の根拠は詳細設計書 §11 にある。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from house_search.scrape.area import AreaTarget
from house_search.scrape.leopalace import (
    LeopalaceScraper,
    rent_to_value,
    walk_minutes_from_access,
)

FIXTURES = Path(__file__).parent / "fixtures" / "leopalace"


@dataclass(frozen=True)
class _Search:
    prefectures: tuple[str, ...] = ("東京都",)
    cities: tuple[str, ...] = ()
    price_max_hint: int | None = None


@dataclass(frozen=True)
class _Pattern:
    search: _Search


@pytest.fixture(scope="module")
def scraper() -> LeopalaceScraper:
    return LeopalaceScraper()


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- 一覧 -----------------------------------------------------------------


def test_一覧から住戸を取り出せる(scraper: LeopalaceScraper) -> None:
    listings = scraper.parse_list(_fixture("list_page1.html"))
    assert len(listings) == 18

    first = listings[0]
    assert first.external_id == "44286_201"
    assert first.url.endswith("/properties/chintai/tokyo/adachi-ku-13121/act-mutsugi-44286/201")
    assert first.title == "レオパレスアクト六木 201号室"
    assert first.price == 64_000
    assert first.mgmt_fee_monthly == 6_500
    assert first.layout == "1K"
    assert first.area_sqm == pytest.approx(23.18)
    assert first.address == "東京都足立区六木４−６−９"
    assert first.station_info == "つくばエクスプレス「八潮駅」徒歩28分"
    assert first.walk_minutes == 28
    assert first.total_floors == 2


def test_敷金不要は0円で礼金1ヶ月は賃料から円へ直す(scraper: LeopalaceScraper) -> None:
    """⚠ 「不要」を None にすると MUST が unknown へ落ちる（SUUMO の「-」と同じ罠）。"""
    first = scraper.parse_list(_fixture("list_page1.html"))[0]
    assert first.deposit_amount == 0
    assert first.key_money_amount == 64_000  # 礼金 1ヶ月 × 賃料 64,000円


def test_築年数は建物の築年月から数える(scraper: LeopalaceScraper) -> None:
    """一覧の建物欄は「2階建てアパート / 2009年12月築」で築年数を直接は出さない。"""
    first = scraper.parse_list(_fixture("list_page1.html"))[0]
    assert first.age_years is not None
    assert first.age_years >= 16


def test_同じ建物の複数住戸がそれぞれ1掲載になる(scraper: LeopalaceScraper) -> None:
    listings = scraper.parse_list(_fixture("list_page1.html"))
    same_building = [x for x in listings if x.external_id.startswith("44286_")]
    assert {x.external_id for x in same_building} == {"44286_201", "44286_203"}
    # 建物の属性は住戸間で共有される
    assert len({x.address for x in same_building}) == 1


def test_最終ページを超えると0件で返る(scraper: LeopalaceScraper) -> None:
    """⚠ ``?page=12`` は HTTP 200・0件。総件数の表示はページ数と合わないので使わない。"""
    assert scraper.parse_list(_fixture("list_empty.html")) == []
    assert scraper.is_last_page(0) is True
    assert scraper.is_last_page(1) is False


# --- バス経由の徒歩を駅徒歩にしない ---------------------------------------


def test_バス経由の行から徒歩分を採らない(scraper: LeopalaceScraper) -> None:
    """⚠ バス停からの徒歩を駅徒歩にすると ``walk_minutes_max`` を不当に通過する。"""
    listings = scraper.parse_list(_fixture("list_bus.html"))
    bus_backed = [x for x in listings if x.station_info and "バス" in x.station_info]
    assert bus_backed, "フィクスチャにバス経由の掲載が無い"
    assert all(x.walk_minutes is None for x in bus_backed)
    # ⚠ 駅名は落とさない（通勤時間の算出に使う）
    assert all(x.station_info for x in bus_backed)


@pytest.mark.parametrize(
    ("access", "expected"),
    [
        ("つくばエクスプレス「八潮駅」徒歩28分", 28),
        ("京葉線「蘇我駅」バス6分 生実学校入口下車 徒歩7分", None),
        ("総武本線「稲毛駅」バス15分 五反田下車 徒歩9分", None),
        (None, None),
    ],
)
def test_駅徒歩の判定(access: str | None, expected: int | None) -> None:
    assert walk_minutes_from_access(access) == expected


# --- URL 組み立て ---------------------------------------------------------


def test_一覧URLは都道府県スラグを前置する(scraper: LeopalaceScraper) -> None:
    """⚠ 市区の検索値に都道府県は含まれない（``adachi-ku-13121``）。"""
    urls = scraper.list_urls(
        _Pattern(_Search()),
        [AreaTarget(prefecture="東京都", city_name="足立区", value="adachi-ku-13121")],
    )
    assert urls == [
        "https://www.leopalace21.com/properties/chintai/area/tokyo/adachi-ku-13121"
    ]


def test_市区の値が無ければ都道府県ページになる(scraper: LeopalaceScraper) -> None:
    urls = scraper.list_urls(
        _Pattern(_Search()), [AreaTarget(prefecture="千葉県", city_name=None, value=None)]
    )
    assert urls == ["https://www.leopalace21.com/properties/chintai/area/chiba"]


def test_ページ送りはクエリで足す(scraper: LeopalaceScraper) -> None:
    """⚠ ``?`` を重ねると page が黙って無視される（APAMAN で実測 → 課題#29）。"""
    base = "https://www.leopalace21.com/properties/chintai/area/tokyo/adachi-ku-13121"
    assert scraper.page_url(base, 2) == f"{base}?page=2"
    assert scraper.page_url(f"{base}?rentTo=70000", 2) == f"{base}?rentTo=70000&page=2"


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (None, None),
        (70_000, "70000"),  # 選択肢に一致
        (67_050, "70000"),  # ⚠ **切り上げ**（切り捨てると MUST を通る掲載を落とす）
        (100_001, "110000"),
        (900_000, None),  # 選択肢の上限を超えたらサイト側へ渡さない
    ],
)
def test_賃料上限は選択肢へ切り上げる(hint: int | None, expected: str | None) -> None:
    """⚠ 選択肢に無い値を送ると HTTP 200 のまま0件になる（→ 課題#29）。"""
    assert rent_to_value(hint) == expected


def test_賃料上限がURLに載る(scraper: LeopalaceScraper) -> None:
    urls = scraper.list_urls(
        _Pattern(_Search(price_max_hint=67_050)),
        [AreaTarget(prefecture="東京都", city_name="足立区", value="adachi-ku-13121")],
    )
    assert urls[0].endswith("?rentTo=70000")


# --- 詳細 -----------------------------------------------------------------


def test_詳細から設備原文と築年月を取り出せる(scraper: LeopalaceScraper) -> None:
    detail = scraper.parse_detail(_fixture("detail.html"))
    assert detail.built_on is not None
    assert (detail.built_on.year, detail.built_on.month) == (2009, 12)
    assert detail.floor_num == 2
    assert detail.total_floors == 2
    assert detail.address == "東京都足立区六木４−６−９"

    features = (detail.raw_features_text or "").split("、")
    assert "バス・トイレ別" in features
    assert "室内洗濯機置き場" in features
    assert "角部屋" in features  # おすすめポイント側にしか出ない条件


def test_設備原文に諸費用や問い合わせ先を混ぜない(scraper: LeopalaceScraper) -> None:
    """⚠ ``TitleTextItem`` は「諸費用」「お問い合わせ先」でも使われている。

    セクションの見出しで絞らないと火災保険料・免許番号・電話番号まで載り、
    しかも金額のカンマで割れて ``500円（税込）`` のような断片が辞書を汚す。
    """
    raw = scraper.parse_detail(_fixture("detail.html")).raw_features_text or ""
    for leaked in ("火災保険", "免許番号", "コンタクトセンター", "円（税込）", "050-"):
        assert leaked not in raw, f"設備原文に {leaked} が混ざっている"
