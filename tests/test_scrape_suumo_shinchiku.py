"""SUUMO 新築マンション（売買）の解析テスト（→ 課題#4・Phase 6 手順6）。

フィクスチャは実HTML。**板橋区（郊外）と港区（都心）の2本**を置く。
⚠ 片方だけだと「価格未定0件」「レンジ0件」「個別住戸0件」のフィクスチャを
掴む恐れがある（課題#41・#44 で2度踏んだ形）。実測の内訳は次のとおり。

| | 掲載 | 棟 | 個別住戸 | 価格未定の棟 |
|---|---:|---:|---:|---:|
| 板橋区 | 11 | 10 | 1 | 6 |
| 港区 | 30 | 18 | 12 | 多数 |
"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.commute.matcher import extract_station_names
from house_search.scrape.suumo_shinchiku import (
    SuumoNewMansionScraper,
    parse_price_range,
    split_description,
)

FIXTURES = Path(__file__).parent / "fixtures" / "suumo_buy"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def scraper() -> SuumoNewMansionScraper:
    return SuumoNewMansionScraper()


@pytest.fixture
def itabashi(scraper) -> list:
    return scraper.parse_list(_read("list_shinchiku_m_itabashi.html"))


@pytest.fixture
def minato(scraper) -> list:
    return scraper.parse_list(_read("list_shinchiku_m_minato.html"))


def _by_id(listings: list, external_id: str):
    for listing in listings:
        if listing.external_id == external_id:
            return listing
    raise AssertionError(f"{external_id} が一覧に含まれていません")


# --- 価格レンジのパース --------------------------------------------------


def test_レンジは区切りで割ってから読む() -> None:
    """⚠⚠ ``parse_yen`` にレンジをそのまま渡すと**上限**が返る回帰テスト。

    ``9448万円～2億5498万円`` は 254,980,000（＝上限）になる（実測 2026-09-06）。
    「億」を含む側に先にマッチするためで、**例外にならず値だけが約2.7倍に狂う**。
    """
    assert parse_price_range("9448万円～2億5498万円") == (94_480_000, 254_980_000)
    assert parse_price_range("6558万円～9898万円") == (65_580_000, 98_980_000)


def test_注記と単位表記を落としてから読む() -> None:
    assert parse_price_range("6400万円台～9900万円台／予定") == (64_000_000, 99_000_000)
    assert parse_price_range("1億3000万円台～1億9000万円台※1000万円単位／予定") == (
        130_000_000,
        190_000_000,
    )


def test_中黒の列挙も下限と上限になる() -> None:
    # ``8590万円・8790万円`` はレンジではなく2つの価格の列挙
    assert parse_price_range("8590万円・8790万円") == (85_900_000, 87_900_000)


def test_単一価格は下限と上限が同じ() -> None:
    assert parse_price_range("8980万円") == (89_800_000, 89_800_000)


def test_価格未定は金額を返さない() -> None:
    assert parse_price_range("価格未定") == (None, None)
    assert parse_price_range(None) == (None, None)


def test_間取りと面積の分解() -> None:
    assert split_description("1LDK～3LDK / 37.01m2～152.01m2") == (
        "1LDK～3LDK",
        "37.01m2～152.01m2",
    )
    assert split_description("1LDK / 54.16m2（16.38坪）（壁芯）") == (
        "1LDK",
        "54.16m2（16.38坪）（壁芯）",
    )
    assert split_description(None) == (None, None)


# --- 一覧の解析 ----------------------------------------------------------


def test_棟と個別住戸の両方を取り込む(itabashi, minato) -> None:
    """⚠ 一覧には2種類が混在する（ユーザー判断 2026-09-06: 両方取り込む）。

    ⚠ **同じ建物が両方に出る**（港区のリビオタワー品川は棟1＋住戸6）ので、
    件数を建物名で数えると重複に見えるが ``nc_`` はすべて別。
    """
    assert len(itabashi) == 11
    assert len(minato) == 30
    # 棟は nc_67…、個別住戸は nc_20/21/78…（実測 2026-09-06）
    assert sum(x.external_id.startswith("nc_67") for x in itabashi) == 10
    assert sum(x.external_id.startswith("nc_67") for x in minato) == 18


def test_価格未定は金額をNULLのままフラグで表す(itabashi) -> None:
    """⚠ 0 やハイフンにすると「安い」と誤読され、順位だけが静かに狂う。"""
    proud = _by_id(itabashi, "nc_67734880")  # プラウドタワー板橋
    assert proud.title == "プラウドタワー板橋"
    assert proud.price is None
    assert proud.price_min is None
    assert proud.price_max is None
    assert proud.type_specific_attrs["price_undecided"] is True


def test_価格が付いた行があれば未定フラグは明示的にFalse(itabashi) -> None:
    """⚠⚠ 販売期ごとに行が複数あり、「価格未定」と実価格が同居する。

    保存は JSONB の ``||`` マージなので、**False を書かないとフラグが残り続ける**
    （価格があるのに「価格未定」と表示され、例外にならない）。
    """
    geo = _by_id(itabashi, "nc_67731070")  # ジオ板橋浮間舟渡（2行）
    assert geo.type_specific_attrs["price_undecided"] is False
    # ``6400万円台～9900万円台／予定`` と ``6558万円～9898万円`` のうち安いほう
    assert geo.price == 64_000_000
    assert geo.price_min == 64_000_000
    assert geo.price_max == 99_000_000


def test_棟の間取りと面積はレンジのまま採る(itabashi) -> None:
    """⚠ 面積は下限（``parse_area_sqm`` が先頭を読む）。間取りは潰さない。"""
    proud = _by_id(itabashi, "nc_67734880")
    assert proud.layout == "1LDK～3LDK"
    assert proud.area_sqm == pytest.approx(37.01)


def test_個別住戸は中古と同じ単一値になる(itabashi) -> None:
    crevista = _by_id(itabashi, "nc_21371763")  # CREVISTA成増Ⅱ
    assert crevista.price == 73_800_000
    assert crevista.price_min == 73_800_000
    assert crevista.price_max == 73_800_000
    assert crevista.layout == "1LDK"
    # ⚠ ``54.16m2（16.38坪）`` の**坪の数字を拾わない**
    assert crevista.area_sqm == pytest.approx(54.16)
    assert crevista.type_specific_attrs["price_undecided"] is False


def test_億を含むレンジでも下限が入る(minato) -> None:
    """⚠⚠ 実データでの回帰テスト（``9448万円～2億5498万円``）。"""
    livio = _by_id(minato, "nc_67726518")  # リビオタワー品川（棟）
    assert livio.price == 94_480_000
    assert livio.price_max == 254_980_000


def test_物件名は広告のキャッチコピーではない(itabashi, minato) -> None:
    """⚠ 広告文は別要素にあり、そこには価格が書いてある（板橋で4/11件）。

    中古は ``h2`` が広告文だったが**新築の h2 は物件名**である。取り違えると
    通知とダイジェストに広告文が並ぶ（例外にならない）。
    """
    for listing in itabashi + minato:
        assert listing.title
        assert "【" not in listing.title
        assert "万円" not in listing.title


def test_交通欄から駅が同定できる(itabashi, minato) -> None:
    """⚠ 新築の交通欄は ``ＪＲ埼京線/板橋 徒歩1分`` で**「駅」の字が無い**。

    そのまま渡すと第1パスが空振りし、**行すら残らないので気づけない**
    （→ 課題#41）。アダプタが ``ＪＲ埼京線 板橋駅 徒歩1分`` へ直してから渡す。
    """
    proud = _by_id(itabashi, "nc_67734880")
    assert proud.station_info == "ＪＲ埼京線 板橋駅 徒歩1分"
    matched, _ = extract_station_names(proud.station_info)
    assert matched == ("板橋",)

    for listing in itabashi + minato:
        assert listing.station_info
        names, _ = extract_station_names(listing.station_info)
        # ⚠ 路線名ごと駅名にしていないこと（D-room で踏んだ形）
        assert names, listing.station_info
        assert not any("線" in name for name in names), listing.station_info


def test_徒歩はレンジなら下限を採る(itabashi) -> None:
    geo = _by_id(itabashi, "nc_67731070")  # ＪＲ埼京線/浮間舟渡 徒歩5分～6分
    assert geo.walk_minutes == 5


def test_バス便は駅徒歩として採らない(scraper) -> None:
    """⚠ **板橋・港のフィクスチャにバス便は0件**なので合成入力で固定する。

    バス経由の「徒歩N分」は**バス停からの徒歩**で、駅徒歩にすると
    ``walk_minutes_max`` を不当に通過する。
    """
    from house_search.scrape.suumo_shinchiku import _walk_minutes

    assert _walk_minutes("ＪＲ中央線/八王子 バス12分 停歩5分") is None
    assert _walk_minutes("ＪＲ埼京線/板橋 徒歩1分") == 1


def test_住所は都道府県の有無が混在する(itabashi) -> None:
    """⚠ 棟は ``板橋区板橋１``（都道府県なし）、個別住戸は ``東京都板橋区赤塚３``。

    都道府県なしは賃貸EX と同じ形で ``resolve_city`` が一意な市区名なら引ける。
    """
    assert _by_id(itabashi, "nc_67734880").address == "板橋区板橋１"
    assert _by_id(itabashi, "nc_21371763").address == "東京都板橋区赤塚３"


def test_引渡時期を種別固有属性に残す(itabashi) -> None:
    proud = _by_id(itabashi, "nc_67734880")
    assert proud.type_specific_attrs["引渡時期"] == "2028年1月中旬予定"


# --- URL 組み立て --------------------------------------------------------


def test_一覧URLはSEOパスで市区スラグを使う(scraper) -> None:
    from house_search.scrape.area import AreaTarget

    area = AreaTarget(
        prefecture="東京都", city_name="板橋区", jis_code="13119", value="sc_itabashi"
    )
    urls = scraper.list_urls(None, [area])
    assert urls == ["https://suumo.jp/ms/shinchiku/tokyo/sc_itabashi/"]


def test_ページ送りは1始まりのpageクエリ(scraper) -> None:
    base = "https://suumo.jp/ms/shinchiku/tokyo/sc_itabashi/"
    assert scraper.page_url(base, 1) == base
    assert scraper.page_url(base, 2) == base + "?page=2"


def test_1ページ30件に満たなければ最終ページ(scraper) -> None:
    """⚠ **中古マンションは20件**で違う（実測 2026-09-06）。"""
    assert scraper.is_last_page(11) is True
    assert scraper.is_last_page(30) is False
