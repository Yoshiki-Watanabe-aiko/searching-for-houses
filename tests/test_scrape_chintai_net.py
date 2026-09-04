"""CHINTAI.net アダプタの回帰テスト（実HTMLフィクスチャ）。

フィクスチャは 2026-09-04 に実サイトから取得したもの。実測の経緯と
数値の根拠は詳細設計書 §14 にある。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from house_search.commute.matcher import extract_station_names
from house_search.scrape.area import AreaTarget
from house_search.scrape.chintai_net import (
    ChintaiNetScraper,
    rent_max_code,
    walk_minutes_from_access,
)

FIXTURES = Path(__file__).parent / "fixtures" / "chintai_net"


@dataclass(frozen=True)
class _Search:
    prefectures: tuple[str, ...] = ("東京都",)
    cities: tuple[str, ...] = ()
    price_max_hint: int | None = None


@dataclass(frozen=True)
class _Pattern:
    search: _Search


@pytest.fixture(scope="module")
def scraper() -> ChintaiNetScraper:
    return ChintaiNetScraper()


@pytest.fixture(scope="module")
def list_html() -> str:
    return (FIXTURES / "list_page1.html").read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIXTURES / "detail.html").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------


def test_住戸単位で取り出す(scraper: ChintaiNetScraper, list_html: str) -> None:
    """⚠ **棟（`.cassette_item` 23件）ではなく住戸（`tbody[data-bkkey]` 49件）**。

    棟だけ数えると住戸49件のうち26件を黙って落とす（D-room §12.3 と同型）。
    """
    listings = scraper.parse_list(list_html)
    assert len(listings) == 49
    assert len({x.external_id for x in listings}) == 49


def test_一覧の項目がすべて埋まる(scraper: ChintaiNetScraper, list_html: str) -> None:
    listings = scraper.parse_list(list_html)
    for field in (
        "price",
        "mgmt_fee_monthly",
        "deposit_amount",
        "key_money_amount",
        "area_sqm",
        "layout",
        "floor_num",
        "total_floors",
        "address",
        "station_info",
        "walk_minutes",
        "title",
    ):
        filled = sum(1 for x in listings if getattr(x, field) is not None)
        assert filled == len(listings), f"{field} が {filled}/{len(listings)} 件しか埋まらない"


def test_賃料はhidden_inputの生値から取る(scraper: ChintaiNetScraper, list_html: str) -> None:
    """⚠⚠ ``chinRyo`` は「118000」の**円の生値**で ``parse_yen`` は読めない。

    素朴に ``parse_yen(values["chinRyo"])`` と書くと **49件すべて price が None** になり、
    ``rent_total`` が NULL → MUST が ``unknown`` へ落ちて
    ``unknown_policy: keep`` の下で**賃料不明の掲載がランキングに並ぶ**。
    ⚠ **例外にならないので、実データで動かすまで気づけない。**
    """
    listings = scraper.parse_list(list_html)
    assert all(x.price is not None for x in listings)
    assert min(x.price for x in listings) == 57000
    assert max(x.price for x in listings) == 160000


def test_PR枠は取り込まない(scraper: ChintaiNetScraper, list_html: str) -> None:
    """⚠ PR枠は ``tbody[data-bkkey]`` を持たず、``data-detailurl`` が
    **robots.txt が禁じている ``?vm=`` を含む**（→ §14.5）。
    """
    listings = scraper.parse_list(list_html)
    assert all("vm=" not in x.url for x in listings)
    assert all("?" not in x.url for x in listings)


def test_住所に地図への導線が混ざらない(scraper: ChintaiNetScraper, list_html: str) -> None:
    """⚠ 住所セルには ``周辺地図`` が子要素として同居する。

    混ざると ``dedup_key`` が他サイトと一致せず**名寄せが黙って失敗する**。
    """
    listings = scraper.parse_list(list_html)
    assert listings[0].address == "東京都足立区東保木間２丁目"
    for listing in listings:
        assert "地図" not in (listing.address or "")
        assert "チェック" not in (listing.address or "")


def test_管理費の表記ゆれ(scraper: ChintaiNetScraper, list_html: str) -> None:
    """「11.8万円10,000円」の後半を読む。⚠ 「32.5万円--」は0円扱い。"""
    listings = scraper.parse_list(list_html)
    assert listings[0].mgmt_fee_monthly == 10000
    assert 0 in {x.mgmt_fee_monthly for x in listings}


def test_敷礼は月数表記も実額も読める(scraper: ChintaiNetScraper, list_html: str) -> None:
    """⚠ 実測では「1ヶ月1ヶ月」が最多で「なし」「135,000円」も混じる。"""
    listings = scraper.parse_list(list_html)
    first = listings[0]
    assert first.deposit_amount == 0  # 「なし」
    assert first.key_money_amount == first.price  # 「1ヶ月」


def test_階に惹句が続いても読める(scraper: ChintaiNetScraper, list_html: str) -> None:
    """⚠ 「2階即入居可」のように惹句が続くことがある。"""
    listings = scraper.parse_list(list_html)
    assert listings[0].floor_num == 7
    assert all(x.floor_num is not None for x in listings)


def test_駅名が路線名ごと拾われない(scraper: ChintaiNetScraper, list_html: str) -> None:
    """⚠ 交通欄の空白を落とすと ``matcher`` が路線名ごと駅名にする（D-room §12.3）。

    実在しない駅名になるとマスタに当たらず**通勤時間が unknown になるだけで
    例外にならない**ので、駅名に「線」が入らないことで固定する。
    """
    listings = scraper.parse_list(list_html)
    found = False
    for listing in listings:
        names, _ = extract_station_names(listing.station_info or "")
        for name in names:
            assert "線" not in name, f"路線名が駅名に混ざった: {name}"
            found = True
    assert found, "駅名が1件も取れていない"


def test_交通欄の複数駅から最短の徒歩を採る() -> None:
    access = "東武伊勢崎線・スカイツリーライン/竹ノ塚駅 徒歩23分 つくばエクスプレス/六町駅 徒歩24分"
    assert walk_minutes_from_access(access) == 23


def test_バス経由の徒歩を駅徒歩にしない() -> None:
    """⚠ バス経由の「徒歩N分」は**バス停からの徒歩**（UR・D-room で踏んだ罠）。"""
    assert walk_minutes_from_access("○○線/△△駅 バス10分 徒歩3分") is None
    assert walk_minutes_from_access("○○線/△△駅 徒歩12分 バス5分 徒歩2分") == 12


# ---------------------------------------------------------------------------
# URL の組み立て
# ---------------------------------------------------------------------------


def test_一覧URLはJIS5桁のパス(scraper: ChintaiNetScraper) -> None:
    pattern = _Pattern(_Search())
    areas = [AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")]
    assert scraper.list_urls(pattern, areas) == [
        "https://www.chintai.net/tokyo/area/13121/list/"
    ]


def test_賃料上限は選択肢へ切り上げる(scraper: ChintaiNetScraper) -> None:
    """⚠ **上限なので切り上げる。** 切り下げると MUST を通る掲載を落とす。"""
    # ⚠⚠ 選択肢の単位は**千円**（13万円 = ct=130）。万円と取り違えると
    #    ct=13 を送ることになり、選択肢外なので**黙って無視される**
    assert rent_max_code(130000) == "130"
    assert rent_max_code(90000) == "90"
    assert rent_max_code(96000) == "100"  # 9.6万円 → 選択肢の10万円へ切り上げ
    assert rent_max_code(101000) == "110"
    assert rent_max_code(None) is None
    assert rent_max_code(20_000_000) is None  # 選択肢の最大を超えたら送らない


def test_賃料上限がURLに載る(scraper: ChintaiNetScraper) -> None:
    pattern = _Pattern(_Search(price_max_hint=130000))
    areas = [AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")]
    assert scraper.list_urls(pattern, areas) == [
        "https://www.chintai.net/tokyo/area/13121/list/?ct=130"
    ]


def test_ページ送りはパス形式でクエリの前に入る(scraper: ChintaiNetScraper) -> None:
    """⚠ 実測で ``/list/page2/?sf=30`` が2ページ目を返すことを確認済み（→ §14.1）。"""
    plain = "https://www.chintai.net/tokyo/area/13121/list/"
    assert scraper.page_url(plain, 1) == plain
    assert scraper.page_url(plain, 2) == plain + "page2/"
    with_query = plain + "?sf=30&ct=100"
    assert scraper.page_url(with_query, 1) == with_query
    assert (
        scraper.page_url(with_query, 2)
        == "https://www.chintai.net/tokyo/area/13121/list/page2/?sf=30&ct=100"
    )


def test_住戸0件で最終ページ(scraper: ChintaiNetScraper) -> None:
    assert scraper.is_last_page(0) is True
    assert scraper.is_last_page(49) is False


# ---------------------------------------------------------------------------
# 詳細
# ---------------------------------------------------------------------------


def test_設備は用語集の解説文を含まない(
    scraper: ChintaiNetScraper, detail_html: str
) -> None:
    """⚠⚠ ``.detail_specTable`` には用語のツールチップが展開されており、
    **その住戸に無い設備名**が本文に出てくる（→ §14.6）。

    「システムキッチン…レンジフード…」「IHクッキングヒーターよりも火力が強い」
    のような解説から辞書が設備を拾うと、**設備数が黙って水増しされる**
    （HOMES の ``sr-only`` と同型）。``.mod_equipmentBox`` のタグ列だけを使う。
    """
    detail = scraper.parse_detail(detail_html)
    raw = detail.raw_features_text or ""
    assert "バス・トイレ別" in raw
    assert "オートロック" in raw
    for noise in ("レンジフード", "IHクッキングヒーター", "閉じる", "画像提供"):
        assert noise not in raw, f"用語集の解説文が混ざった: {noise}"


def test_詳細から築年月と階と住所を取る(
    scraper: ChintaiNetScraper, detail_html: str
) -> None:
    """⚠ 住所セルには「地図で物件の周辺環境をチェック！」が同居する。"""
    detail = scraper.parse_detail(detail_html)
    assert detail.built_on is not None
    assert (detail.built_on.year, detail.built_on.month) == (1993, 8)
    assert detail.floor_num == 7
    assert detail.address == "東京都足立区東保木間２丁目"
