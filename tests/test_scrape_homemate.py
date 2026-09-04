"""ホームメイトアダプタの回帰テスト（実HTMLフィクスチャ）。

フィクスチャは 2026-09-04 に実サイトから取得したもの。実測の経緯と
数値の根拠は詳細設計書 §13 にある。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from house_search.commute.matcher import extract_station_names
from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.homemate import (
    HomemateScraper,
    rent_upper_value,
    walk_minutes_from_access,
)

FIXTURES = Path(__file__).parent / "fixtures" / "homemate"
# 住所の先頭に来る都道府県。⚠ **実装の定数を import しない**（実装が壊れても
# テストが道連れで collect エラーになり、回帰テストとして働かなくなる）。
_STARTS_WITH_PREFECTURE = re.compile(r"(?:東京都|北海道|(?:京都|大阪)府|[^\s]{2,3}県)")


@dataclass(frozen=True)
class _Search:
    prefectures: tuple[str, ...] = ("東京都",)
    cities: tuple[str, ...] = ()
    price_max_hint: int | None = None


@dataclass(frozen=True)
class _Pattern:
    search: _Search


@pytest.fixture(scope="module")
def scraper() -> HomemateScraper:
    return HomemateScraper()


@pytest.fixture(scope="module")
def list_html() -> str:
    return (FIXTURES / "list_page1.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def list_bus_html() -> str:
    """バス便を含む一覧（葛飾区・2026-09-05 取得）。

    ⚠ 足立区の ``list_page1.html`` は25棟すべてが「◯◯駅まで徒歩N分」型で、
    **バス便が1件も入っていない**。そのため本文走査の欠陥（→ §13.10）を
    検出できなかった。
    """
    return (FIXTURES / "list_bus.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def list_address_only_html() -> str:
    """交通欄が無く住所だけの棟を含む一覧（東久留米市・2026-09-05 取得）。

    ⚠ 既存の2本（足立区・葛飾区）は**全棟が交通欄と住所を1つずつ持つ**ため、
    ``li`` の位置で決め打ちする欠陥を検出できなかった。この一覧の
    「メゾン前沢」は交通欄が無く ``li`` が住所1つだけで、位置で決めると
    **住所を交通欄として渡してしまう**。あわせてバス停名に都県名を含む
    バス便（``西武バス 西団地入口（東京都）停まで…``）も3件入っている。
    """
    return (FIXTURES / "list_address_only.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIXTURES / "detail.html").read_text(encoding="utf-8", errors="replace")


def test_uses_jis_code_directly(scraper: HomemateScraper) -> None:
    """市区の検索値は JIS5桁そのもの（スラグ収集が要らない）。"""
    assert scraper.city_value_source == CITY_VALUE_JIS


def test_list_urls_adds_cheapest_sort_and_rent_cap(scraper: HomemateScraper) -> None:
    areas = [AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")]
    urls = scraper.list_urls(_Pattern(_Search(price_max_hint=130_000)), areas)
    assert urls == ["https://www.homemate.co.jp/pr-tokyo/13121/?so=11&ye=13"]


def test_rent_upper_value_rounds_up_to_half_man() -> None:
    """⚠ 選択肢は0.5万刻み。端数をそのまま渡すと0件になる（→ 課題#29）。"""
    assert rent_upper_value(130_000) == "13"
    assert rent_upper_value(96_000) == "10"
    assert rent_upper_value(92_000) == "9.5"
    assert rent_upper_value(None) is None


def test_page_url_is_one_based(scraper: HomemateScraper) -> None:
    """ページ送りは ``?pg=N`` の1始まり（→ §13.3）。"""
    base = "https://www.homemate.co.jp/pr-tokyo/13121/?so=11"
    assert scraper.page_url(base, 2) == f"{base}&pg=2"


def test_parse_list_reads_every_room(scraper: HomemateScraper, list_html: str) -> None:
    """⚠⚠ 階の表記が無い住戸と、全角数字IDの住戸を落とさないこと。

    どちらも**エラーにならず件数が減るだけ**で、実測26住戸のうち
    前者で8件・後者で8件を落としていた（→ §13）。
    """
    listings = scraper.parse_list(list_html)
    assert len(listings) == 26
    assert len({listing.external_id for listing in listings}) == 26
    # 階の表記が無い住戸（floor_num が None）も取り込まれている
    assert any(listing.floor_num is None for listing in listings)
    # 全角数字がURLエンコードされた external_id も含まれる
    assert any("%" in listing.external_id for listing in listings)


def test_parse_list_first_row(scraper: HomemateScraper, list_html: str) -> None:
    first = scraper.parse_list(list_html)[0]
    assert first.external_id == "C604046634201"
    assert first.url == "https://www.homemate.co.jp/dtl-C604046634201/"
    assert first.title == "山﨑コーポ"
    assert first.price == 55_000
    # ⚠ 「共益費：－」は 0円の意味。None にすると rent_total が不明になり
    #    MUST が unknown へ落ちる（SUUMO の「-」と同型）
    assert first.mgmt_fee_monthly == 0
    assert first.area_sqm == pytest.approx(33.0)
    assert first.layout == "2K"
    assert first.floor_num == 2
    assert first.total_floors == 2
    assert first.age_years == 49
    assert first.address == "東京都足立区本木南町"
    assert first.walk_minutes == 15
    # ⚠ 「敷無 礼無」は 0円。原文のまま parse_months_fee へ渡すこと
    assert first.deposit_amount == 0
    assert first.key_money_amount == 0


def test_station_info_keeps_station_suffix_and_space(
    scraper: HomemateScraper, list_html: str
) -> None:
    """⚠ 「駅」と路線名との空白を残す（→ 課題#41・§12.3）。"""
    first = scraper.parse_list(list_html)[0]
    assert first.station_info is not None
    assert "扇大橋駅" in first.station_info
    # ⚠ 建物名が交通欄に混じらないこと
    assert "山﨑コーポ" not in first.station_info
    names = [name for group in extract_station_names(first.station_info) for name in group]
    assert "扇大橋" in names
    assert not [name for name in names if "線" in name or "ライナー" in name]


def test_access_is_read_from_dom_for_every_room(
    scraper: HomemateScraper, list_html: str, list_bus_html: str
) -> None:
    """⚠⚠ 交通欄は**DOMの li から**取る。本文走査だと**バス便が丸ごと落ちる**。

    本文から「◯◯駅まで徒歩N分」を探して切り出していたため、その形を持たない
    バス便は交通欄が None になり、**駅が同定できず通勤時間が unknown**になっていた
    （実測で236掲載中45件＝19%。→ §13.10・課題#37）。
    ⚠ **例外にならず、交通欄が取れた掲載の同定率は98.4%と健全に見える**ので、
    掲載全体を分母にしないと気づけない。
    """
    for html in (list_html, list_bus_html):
        listings = scraper.parse_list(html)
        assert listings
        assert [listing for listing in listings if not listing.station_info] == []


def test_bus_route_keeps_station_but_not_walk_minutes(
    scraper: HomemateScraper, list_bus_html: str
) -> None:
    """バス便は**駅名を残しつつ駅徒歩は付けない**。

    ⚠ 「東金町五丁目停まで徒歩3分」はバス停からの徒歩なので
    ``walk_minutes`` にしてはいけない（UR・D-room・レオパレスで踏んだ罠）。
    ⚠ 一方で駅名を捨ててもいけない（捨てると通勤時間が unknown になる）。
    """
    bus = [
        listing
        for listing in scraper.parse_list(list_bus_html)
        if listing.station_info and "バス乗車" in listing.station_info
    ]
    assert bus, "フィクスチャにバス便の住戸が無い"
    for listing in bus:
        assert listing.walk_minutes is None
        names = extract_station_names(listing.station_info)[0]
        assert names, f"駅名を拾えていない: {listing.station_info}"
        # ⚠ バス停名を駅として拾わないこと（「東金町五丁目停」「テクノプラザかつしか停」）
        assert not [name for name in names if name.endswith("停")]


def test_walk_minutes_ignores_bus_route() -> None:
    """⚠ バス経由の「徒歩N分」はバス停からの徒歩なので使わない。"""
    assert walk_minutes_from_access("都営日暮里・舎人ライナー 扇大橋駅まで徒歩15分") == 15
    assert walk_minutes_from_access("東武伊勢崎線 竹ノ塚駅までバス15分 停まで徒歩2分") is None
    assert walk_minutes_from_access(None) is None


def test_parse_detail_strips_postal_code(scraper: HomemateScraper, detail_html: str) -> None:
    """⚠ 所在地は「〒123-0855東京都…地図」。落とさないと dedup_key が一致しない。"""
    detail = scraper.parse_detail(detail_html)
    assert detail.address == "東京都足立区本木南町"


def test_detail_features_exclude_guides(scraper: HomemateScraper, detail_html: str) -> None:
    """⚠⚠ 案内文と他物件へのリンクを設備原文に載せない（→ §13.7）。"""
    text = scraper.parse_detail(detail_html).raw_features_text or ""
    assert "バス・トイレ別" in text
    assert "独立洗面所" in text
    assert "クローゼット" in text
    for ng in ("よくある質問", "個人情報", "鍵交換代", "害虫駆除", "保証会社", "こだわり条件から"):
        assert ng not in text
    # ⚠ 非該当の「-」を載せない
    assert "、-、" not in text


def test_is_sold_on_404(scraper: HomemateScraper) -> None:
    """掲載終了は素直に 404（→ §13.3）。"""

    class _Response:
        def __init__(self, status: int) -> None:
            self.status_code = status

    class _Fetcher:
        def __init__(self, status: int) -> None:
            self._status = status

        def get(self, url: str) -> _Response:  # noqa: ARG002
            return _Response(self._status)

    assert scraper.is_sold(_Fetcher(404), "https://www.homemate.co.jp/dtl-X/") is True
    assert scraper.is_sold(_Fetcher(200), "https://www.homemate.co.jp/dtl-X/") is False


def test_declares_no_city_rotation(scraper: HomemateScraper) -> None:
    """連続取得の上限は実測で見つからなかった（20市区すべて正常 → §13.3）。"""
    assert scraper.city_rotation_limit is None
    assert scraper.ignore_robots is False


def test_address_is_read_from_dom_not_body_text(
    scraper: HomemateScraper, list_address_only_html: str
) -> None:
    """住所を**本文への正規表現**で探してはいけない（2026-09-05 の実測）。

    ⚠ バス停名に都県名が入る（``西武バス 西団地入口（東京都）停まで徒歩2分、…``）ので、
    本文を ``(?:東京都|…県)`` で ``search`` すると**バス停名を住所と誤認する**。
    実測では ``address`` が ``（東京都）停まで徒歩2分、バス乗車して東武東上線`` になり、
    ⚠ **例外にならず `city_id` が NULL になって名寄せが黙って失敗していた**（active 6件）。
    """
    listings = scraper.parse_list(list_address_only_html)
    assert listings
    broken = [
        listing.address
        for listing in listings
        if listing.address and re.search(r"停|徒歩|バス|乗車", listing.address)
    ]
    assert broken == []
    assert all(listing.address for listing in listings)


def test_access_and_address_are_told_apart_by_content_not_position(
    scraper: HomemateScraper, list_address_only_html: str
) -> None:
    """``li`` の位置ではなく**中身**で交通欄と住所を見分ける。

    ⚠ **交通欄が無く住所だけの棟が実在する**（東久留米市「メゾン前沢」）。
    「1つ目＝交通欄」と決め打ちすると**住所を交通欄として渡す**ことになり、
    駅が同定できないまま ``address`` も空になる。⚠ 例外にならない。
    """
    listings = scraper.parse_list(list_address_only_html)
    # 交通欄に住所が紛れ込んでいない（住所は必ず都道府県で始まる）
    assert [
        listing.station_info
        for listing in listings
        if listing.station_info and _STARTS_WITH_PREFECTURE.match(listing.station_info)
    ] == []
    # 交通欄が無い棟でも住所は取れている
    address_only = [
        listing
        for listing in listings
        if listing.address == "東京都東久留米市前沢５丁目" and listing.station_info is None
    ]
    assert address_only, "交通欄が無く住所だけの棟が拾えていない"


def test_detail_address_strips_leaked_heading(scraper: HomemateScraper) -> None:
    """見出しの語が値に混入しても住所は都道府県から始まる。

    ⚠ ``_detail_rows`` は ``th``/``dt`` を見出しにするが、その語が ``td``/``dd``
    側にも現れる組があり ``所在地東京都葛飾区細田１丁目`` になっていた（active 3件）。
    ⚠ **`dedup_key` が他サイトと一致せず名寄せが黙って失敗する**だけで例外にならない。
    """
    assert scraper._detail_address("所在地東京都葛飾区細田１丁目") == "東京都葛飾区細田１丁目"
    assert scraper._detail_address("〒124-0022 東京都葛飾区細田１丁目 地図") == (
        "東京都葛飾区細田１丁目"
    )
    assert scraper._detail_address(None) is None
