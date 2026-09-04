"""ハウスコムアダプタの回帰テスト（実HTMLフィクスチャ）。

フィクスチャは 2026-09-04 に実サイトから取得したもの。実測の経緯と
数値の根拠は詳細設計書 §13 にある。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from house_search.commute.matcher import extract_station_names
from house_search.scrape.area import AreaTarget
from house_search.scrape.housecom import HousecomScraper, walk_minutes_from_access

FIXTURES = Path(__file__).parent / "fixtures" / "housecom"


@dataclass(frozen=True)
class _Search:
    prefectures: tuple[str, ...] = ("東京都",)
    cities: tuple[str, ...] = ()
    price_max_hint: int | None = None


@dataclass(frozen=True)
class _Pattern:
    search: _Search


@pytest.fixture(scope="module")
def scraper() -> HousecomScraper:
    return HousecomScraper()


@pytest.fixture(scope="module")
def list_html() -> str:
    return (FIXTURES / "list_page1.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIXTURES / "detail.html").read_text(encoding="utf-8", errors="replace")


def test_list_urls_uses_city_slug_and_cheapest_sort(scraper: HousecomScraper) -> None:
    """⚠ ``sort=0``（家賃が安い順）が必ず付く（→ §13.4）。"""
    areas = [
        AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="adachiku")
    ]
    urls = scraper.list_urls(_Pattern(_Search()), areas)
    assert urls == ["https://www.housecom.jp/tokyo/adachiku-city/?sort=0"]


def test_list_urls_skips_city_without_value(scraper: HousecomScraper) -> None:
    """検索値の無い市区は対象にできない（→ 課題#36）。"""
    areas = [AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value=None)]
    assert scraper.list_urls(_Pattern(_Search()), areas) == []


def test_page_url_is_one_based(scraper: HousecomScraper) -> None:
    """ページ送りは ``?page=N`` の1始まり（→ §13.3）。"""
    base = "https://www.housecom.jp/tokyo/adachiku-city/?sort=0"
    assert scraper.page_url(base, 2) == f"{base}&page=2"


def test_is_last_page_when_no_rooms(scraper: HousecomScraper) -> None:
    """件数表記は「棟」なので住戸0件を終端とする。"""
    assert scraper.is_last_page(0) is True
    assert scraper.is_last_page(1) is False


def test_parse_list_expands_building_into_rooms(
    scraper: HousecomScraper, list_html: str
) -> None:
    listings = scraper.parse_list(list_html)
    assert len(listings) == 11
    first = listings[0]
    assert first.external_id == "4775404"
    assert first.url == "https://www.housecom.jp/room_4775404/"
    assert first.title == "ソマール西竹ノ塚 405号室"
    assert first.price == 68_000
    assert first.mgmt_fee_monthly == 3_000
    assert first.area_sqm == pytest.approx(31.0)
    assert first.layout == "1LDK"
    assert first.floor_num == 4
    assert first.total_floors == 4
    assert first.address == "東京都足立区西竹の塚"
    assert first.walk_minutes == 12
    assert first.deposit_amount == 68_000
    assert first.key_money_amount == 68_000


def test_parse_list_has_no_duplicate_ids(scraper: HousecomScraper, list_html: str) -> None:
    """⚠ 棟の代表表示と住戸一覧に同じ住戸が載るので1件へ畳む（→ §13）。"""
    listings = scraper.parse_list(list_html)
    ids = [listing.external_id for listing in listings]
    assert len(ids) == len(set(ids))


def test_parse_list_keeps_area_and_floors(scraper: HousecomScraper, list_html: str) -> None:
    """⚠ 面積と総階数が取れていること。

    面積は ``parse_area_sqm`` が単位を要るので ``㎡`` ごと渡す必要があり、
    総階数は一覧が「地上4階」で ``parse_total_floors``（「4階建」を見る）では読めない。
    どちらも**落とすと None になるだけでエラーにならない**。
    """
    listings = scraper.parse_list(list_html)
    assert all(listing.area_sqm is not None for listing in listings)
    assert all(listing.total_floors is not None for listing in listings)


def test_station_info_keeps_station_suffix_and_space(
    scraper: HousecomScraper, list_html: str
) -> None:
    """⚠ 「駅」と路線名との空白を残す（→ 課題#41・§12.3）。

    落とすと ``commute/matcher`` が路線名ごと駅名にしてしまい、
    実在しない駅名になって**通勤時間が unknown になるだけでエラーにならない**。
    """
    listings = scraper.parse_list(list_html)
    first = listings[0]
    assert first.station_info is not None
    assert "竹ノ塚駅" in first.station_info
    names = [name for group in extract_station_names(first.station_info) for name in group]
    assert "竹ノ塚" in names
    # ⚠ 路線名ごと拾っていないこと（空白を落とすと `東武…線竹ノ塚` になる）
    assert not [name for name in names if "線" in name]
    # ⚠ 徒歩をカッコで囲むと第2パスが「徒」を拾うので外してある
    assert "徒" not in names


def test_walk_minutes_ignores_bus_route() -> None:
    """⚠ バス経由の「徒歩N分」はバス停からの徒歩なので使わない。"""
    assert walk_minutes_from_access("東武伊勢崎線 竹ノ塚駅 （徒歩12分）") == 12
    assert walk_minutes_from_access("東武伊勢崎線 竹ノ塚駅 バス15分 停徒歩2分") is None
    assert walk_minutes_from_access(None) is None


def test_parse_detail(scraper: HousecomScraper, detail_html: str) -> None:
    detail = scraper.parse_detail(detail_html)
    assert detail.address == "東京都足立区西竹の塚"
    assert detail.floor_num == 4
    assert detail.total_floors == 4
    assert detail.mgmt_fee_monthly == 3_000
    assert detail.walk_minutes == 12
    assert detail.built_on is not None
    assert detail.built_on.year == 1992


def test_detail_features_exclude_costs(scraper: HousecomScraper, detail_html: str) -> None:
    """⚠⚠ 費用の項目を設備原文に載せない（→ §13.7）。

    同じ表に「初期費用」「更新料」「鍵交換費用」「損保・火災保険」「保証会社」が
    並んでおり、載せると辞書が費用の文言を設備として拾う。
    """
    text = scraper.parse_detail(detail_html).raw_features_text or ""
    assert "エアコン" in text
    assert "室内洗濯機置場" in text
    assert "オンライン相談可" in text
    for ng in ("鍵交換費用", "保証会社", "損保", "火災保険", "初期費用", "更新料"):
        assert ng not in text
    # ⚠ 設備でないカテゴリ（ライフステージ・こだわり・趣味）も落とす
    for ng in ("一人暮らしに人気の設備", "服やおしゃれが好き", "外で子どもと遊びたい"):
        assert ng not in text
    # ⚠ 非該当の「－」を載せない（HOMES の sr-only・goo の "-" と同型）
    assert "、－" not in text


def test_masked_room_number_is_not_sold(scraper: HousecomScraper) -> None:
    """⚠⚠ 号室の伏字を掲載終了と判定しない（→ §13.6）。

    調査段階では ``/room_1/`` の ``***号室`` を「募集を終えた住戸」と解釈したが、
    実地の取り込みで覆った。一覧（家賃が安い順）の1ページ目にも伏字が並び、
    本番413掲載のうち **100件（24%）** が伏字だった。伏字で ``sold`` にすると
    **募集中の掲載の4分の1をランキングから消す**。
    """

    class _Response:
        status_code = 200
        text = (FIXTURES / "detail_sold.html").read_text(encoding="utf-8", errors="replace")

    class _Fetcher:
        def get(self, url: str) -> _Response:  # noqa: ARG002
            return _Response()

    assert scraper.is_sold(_Fetcher(), "https://www.housecom.jp/room_1/") is False


def test_masked_rooms_are_still_parsed(scraper: HousecomScraper, list_html: str) -> None:
    """伏字の住戸も一覧から取り込む（募集中のため）。"""
    listings = scraper.parse_list(list_html)
    masked = [x for x in listings if "＊" in (x.title or "")]
    assert len(masked) == 3
    assert all(x.price and x.area_sqm for x in masked)


def test_is_sold_on_404(scraper: HousecomScraper) -> None:
    """存在しないIDは 404（実測 ``/room_99999999/``）。"""

    class _Response:
        status_code = 404
        text = ""

    class _Fetcher:
        def get(self, url: str) -> _Response:  # noqa: ARG002
            return _Response()

    assert scraper.is_sold(_Fetcher(), "https://www.housecom.jp/room_99999999/") is True


def test_declares_no_city_rotation(scraper: HousecomScraper) -> None:
    """連続取得の上限は実測で見つからなかった（20市区すべて正常 → §13.3）。"""
    assert scraper.city_rotation_limit is None
    assert scraper.ignore_robots is False
    assert scraper.requires_city is True
