"""D-room（大和リビング）アダプタの回帰テスト（実HTMLフィクスチャ）。

フィクスチャは 2026-09-04 に実サイトから取得したもの。実測の経緯と
数値の根拠は詳細設計書 §12 にある。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from house_search.commute.matcher import extract_station_names
from house_search.scrape.area import AreaTarget
from house_search.scrape.droom import (
    DroomScraper,
    mark_stations,
    rent_upper_value,
    total_floors_from_text,
    walk_minutes_from_access,
)

FIXTURES = Path(__file__).parent / "fixtures" / "droom"


@dataclass(frozen=True)
class _Search:
    prefectures: tuple[str, ...] = ("東京都",)
    cities: tuple[str, ...] = ()
    price_max_hint: int | None = None


@dataclass(frozen=True)
class _Pattern:
    search: _Search


@pytest.fixture(scope="module")
def scraper() -> DroomScraper:
    return DroomScraper()


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- 一覧 -----------------------------------------------------------------


def test_一覧から住戸を取り出せる(scraper: DroomScraper) -> None:
    listings = scraper.parse_list(_fixture("list_page1.html"))
    assert len(listings) == 31

    first = listings[0]
    assert first.external_id == "700021797-2-101"
    assert first.url == "https://www.droom-daiwaliving.net/700021797-2-101/"
    assert first.title == "Maison Verte(メゾンヴェール) B 101号室"
    assert first.price == 95_000
    assert first.mgmt_fee_monthly == 6_500
    assert first.layout == "1K"
    assert first.area_sqm == pytest.approx(25.91)
    assert first.total_floors == 2
    assert first.address == "東京都足立区綾瀬５丁目2-11"
    assert first.walk_minutes == 8


def test_住戸がPC用とSP用で二重に数えられない(scraper: DroomScraper) -> None:
    """⚠ レスポンシブで同じ住戸が2つの形で並ぶ（→ §12.8）。

    表示は「該当物件 334室（98棟）」で、この1ページ（10棟）には31住戸ある。
    両方を拾うと62件になり、**エラーにならないまま母集団が2倍になる**。
    """
    listings = scraper.parse_list(_fixture("list_page1.html"))
    assert len(listings) == 31
    assert len({listing.external_id for listing in listings}) == 31


def test_棟ではなく住戸の単位で取れている(scraper: DroomScraper) -> None:
    """⚠ ``room-list__card`` は「棟」（→ §12.3）。

    取り違えると棟あたり1件になり、10棟のページで10件しか取れない。
    """
    listings = scraper.parse_list(_fixture("list_page1.html"))
    assert len(listings) > 10
    # 同じ棟の別住戸（住所が同じで号室が違う）が実在すること
    by_address: dict[str, set[str]] = {}
    for listing in listings:
        by_address.setdefault(listing.address or "", set()).add(listing.external_id)
    assert max(len(ids) for ids in by_address.values()) >= 5


def test_管理費は欄が無ければ欠損にする(scraper: DroomScraper) -> None:
    """⚠ 欄そのものが無い住戸がある（SUUMO の「-」＝0円とは違う）。

    0円と決め打つと ``rent_total`` を過小評価するので、詳細で確定させる。
    """
    listings = scraper.parse_list(_fixture("list_page1.html"))
    assert all(listing.mgmt_fee_monthly is not None for listing in listings)


# --- 交通欄（バス経由と駅の同定） -------------------------------------------


def test_バス停からの徒歩を駅徒歩にしない(scraper: DroomScraper) -> None:
    """⚠ 1行に徒歩経路とバス経路が並ぶので、行ごと捨てると本物の駅徒歩を落とす。"""
    listings = scraper.parse_list(_fixture("list_bus.html"))
    assert listings
    first = listings[0]
    # 日暮里舎人「扇大橋」徒歩17分 常磐緩行線「北千住」バス15分「本木新道」停徒歩2分
    assert first.walk_minutes == 17  # バス停からの徒歩2分ではない


def test_駅徒歩の判定は経路ごとに行う() -> None:
    access = "伊勢崎線「西新井」徒歩6分 常磐緩行線「北千住」バス15分「西新井駅西口」停徒歩5分"
    assert walk_minutes_from_access(access) == 6
    # バス経由だけの行からは駅徒歩を採らない
    assert walk_minutes_from_access("常磐緩行線「北千住」バス15分「本木新道」停徒歩2分") is None
    assert walk_minutes_from_access(None) is None


def test_駅名に駅を補いバス停には補わない() -> None:
    """⚠ 一覧の交通欄には「駅」の字が無い（→ 課題#41・§12.3）。"""
    marked = mark_stations("常磐緩行線「綾瀬」徒歩8分")
    assert marked == "常磐緩行線 「綾瀬」駅 徒歩8分"

    marked = mark_stations("常磐緩行線「北千住」バス15分「本木新道」停徒歩2分")
    assert "「北千住」駅" in marked
    assert "「本木新道」駅" not in marked  # バス停に「駅」を付けない
    assert mark_stations(None) is None


def test_駅名が路線名ごと拾われない(scraper: DroomScraper) -> None:
    """⚠⚠ 鉤括弧の「前」の空白が要る（→ ``mark_stations``）。

    ``matcher`` は囲みを外して ``常磐緩行線綾瀬駅`` にしてから「駅」の左を遡るので、
    区切りが無いと ``常磐緩行線綾瀬`` という**実在しない駅名**になり、
    駅マスタに当たらず通勤時間が unknown になる。⚠ 例外にならないので気づけない。
    """
    listings = scraper.parse_list(_fixture("list_page1.html"))
    found: set[str] = set()
    for listing in listings:
        names, _ = extract_station_names(listing.station_info or "")
        assert names, f"駅を1件も同定できない: {listing.station_info!r}"
        found |= set(names)

    assert "綾瀬" in found
    assert "北千住" in found
    assert not [name for name in found if "線" in name], f"路線名が混ざっている: {found}"


# --- URL の組み立て --------------------------------------------------------


def test_ページ送りは0始まり(scraper: DroomScraper) -> None:
    """⚠⚠ ``page_num`` は 0 始まり（→ §12.6）。

    1始まりだと思って組むと**1ページ目を永久に取り逃す**（2ページ目が返るだけ）。
    """
    base = "https://www.droom-daiwaliving.net/tokyo/list/?city%5B%5D=13121"
    assert scraper.page_url(base, 1).endswith("&page_num=0")
    assert scraper.page_url(base, 2).endswith("&page_num=1")


def test_クエリの無いURLでも区切り文字を誤らない(scraper: DroomScraper) -> None:
    assert scraper.page_url("https://example.com/tokyo/list/", 1).endswith("?page_num=0")


def test_最終ページは住戸0件で判定する(scraper: DroomScraper) -> None:
    """⚠ ``amount`` は「棟」の数なので住戸数の閾値では判定できない。"""
    assert scraper.is_last_page(0) is True
    assert scraper.is_last_page(1) is False


def test_賃料上限は選択肢へ切り上げる() -> None:
    """⚠ 選択肢外の値は0件事故のもと（→ 課題#29）。"""
    assert rent_upper_value(100_000) == "100000"
    assert rent_upper_value(96_000) == "100000"  # 5,000円刻みへ切り上げ
    assert rent_upper_value(210_000) == "300000"  # 20万超は30万まで飛ぶ
    assert rent_upper_value(None) is None
    assert rent_upper_value(900_000) is None  # 選択肢を超える希望額は送らない


def test_一覧URLに市区のJISコードと管理費込みの指定が載る(scraper: DroomScraper) -> None:
    """⚠ ``cff=Y`` を ``rcu`` と対で送らないと ``rcu`` が賃料だけに掛かる。"""
    pattern = _Pattern(search=_Search(price_max_hint=100_000))
    areas = [AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")]
    urls = scraper.list_urls(pattern, areas)
    assert len(urls) == 1
    assert urls[0].startswith("https://www.droom-daiwaliving.net/tokyo/list/?")
    assert "rcu=100000" in urls[0]
    assert "cff=Y" in urls[0]
    assert "city%5B%5D=13121" in urls[0]
    assert "amount=100" in urls[0]


def test_価格上限が無ければ管理費込みの指定も送らない(scraper: DroomScraper) -> None:
    pattern = _Pattern(search=_Search())
    areas = [AreaTarget(prefecture="埼玉県", city_name="越谷市", jis_code="11222", value="11222")]
    urls = scraper.list_urls(pattern, areas)
    assert "rcu" not in urls[0]
    assert "cff" not in urls[0]
    assert urls[0].startswith("https://www.droom-daiwaliving.net/saitama/list/?")


def test_未知の都道府県は例外にする(scraper: DroomScraper) -> None:
    pattern = _Pattern(search=_Search())
    areas = [AreaTarget(prefecture="架空県", city_name="架空市", jis_code="99999", value="99999")]
    with pytest.raises(ValueError, match="未知の都道府県"):
        scraper.list_urls(pattern, areas)


# --- 詳細 -----------------------------------------------------------------


def test_詳細から設備と補足項目を取れる(scraper: DroomScraper) -> None:
    detail = scraper.parse_detail(_fixture("detail.html"))
    assert detail.built_on is not None
    assert detail.built_on.year == 2013
    assert detail.floor_num == 1
    assert detail.total_floors == 2
    assert detail.mgmt_fee_monthly == 6_500
    assert detail.deposit_amount == 0  # 「なし」
    assert detail.key_money_amount == 95_000  # 「1ヶ月」× 賃料95,000円
    assert detail.address == "東京都足立区綾瀬５丁目2-11"
    assert detail.walk_minutes == 8

    features = (detail.raw_features_text or "").split("、")
    assert "バス・トイレ別" in features  # ⚠ 中黒で割ると取りこぼす
    assert "保証人不要" in features
    assert "ウォークインクローゼット" in features


def test_設備原文に他物件や費用の記述を混ぜない(scraper: DroomScraper) -> None:
    """⚠⚠ 同じページに「同じエリアの似た物件」20件が並ぶ（→ §12.8）。

    混ぜると**他物件の設備が自分の設備として載る**。「注意事項等」の保証料と
    「その他」の室内清掃費用も設備ではない（レオパレスの「諸費用」と同型）。
    """
    text = scraper.parse_detail(_fixture("detail.html")).raw_features_text or ""
    assert "ロイヤルパークス西新井" not in text  # 似た物件の建物名
    assert "保証料" not in text
    assert "室内清掃費用" not in text
    assert "めやす賃料" not in text


def test_総階数は地上N階から取る() -> None:
    """⚠ 共通の ``parse_total_floors`` は「◯階建」を前提にしていて当たらない。"""
    assert total_floors_from_text("地上2階") == 2
    assert total_floors_from_text("1階部分（地上14階）") == 14
    assert total_floors_from_text(None) is None


# --- 掲載終了 --------------------------------------------------------------


class _StubResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _StubFetcher:
    def __init__(self, response: _StubResponse) -> None:
        self._response = response

    def get(self, url: str) -> _StubResponse:
        return self._response


def test_掲載終了は404ではなくタイトルで判別する(scraper: DroomScraper) -> None:
    """⚠ 存在しない住戸URLも HTTP 200 で返る（→ §12.9）。"""
    gone = _StubFetcher(_StubResponse(200, _fixture("detail_gone.html")))
    assert scraper.is_sold(gone, "https://www.droom-daiwaliving.net/700021797-2-999/") is True

    alive = _StubFetcher(_StubResponse(200, _fixture("detail.html")))
    assert scraper.is_sold(alive, "https://www.droom-daiwaliving.net/700021797-2-101/") is False

    missing = _StubFetcher(_StubResponse(404, ""))
    assert scraper.is_sold(missing, "https://www.droom-daiwaliving.net/x/") is True
