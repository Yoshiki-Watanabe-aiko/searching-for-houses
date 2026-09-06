"""SUUMO 中古マンション（売買）の解析テスト（→ 課題#4・Phase 6 手順4）。

フィクスチャは実HTML。一覧は**千代田区（都心）と八王子市（郊外）の2本**を置く。
⚠ **千代田区だけではバス便を検出できない**（バス便0件）。課題#41・#44 で
「バス便を含まないフィクスチャでは検出できない」を2度踏んでいるので、
最初から郊外の一覧を入れてある（八王子は20件中3件がバス便）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.commute.matcher import extract_station_names
from house_search.scrape.suumo_buy import SuumoBuyMansionScraper

FIXTURES = Path(__file__).parent / "fixtures" / "suumo_buy"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def scraper() -> SuumoBuyMansionScraper:
    return SuumoBuyMansionScraper()


@pytest.fixture
def listings(scraper: SuumoBuyMansionScraper):
    return scraper.parse_list(_read("list_chuko_m_page1.html"))


def test_一覧は1ページ20件(listings) -> None:
    """⚠ ``property_unit--osusume`` を「おすすめ枠」として除外してはいけない。

    20件のうち16件にこのクラスが付いているが中身は通常の掲載で、
    除外すると**80%が消える**（件数が減るだけでエラーにならない）。
    """
    assert len(listings) == 20


def test_一覧の1件目(listings) -> None:
    first = listings[0]
    assert first.external_id == "nc_21457575"
    assert first.url.endswith("/ms/chuko/tokyo/sc_chiyoda/nc_21457575/")
    assert first.price == 39_800_000
    assert first.address == "東京都千代田区神田多町２-８－２０"
    assert first.area_sqm == 25.98
    assert first.layout == "1K"
    assert first.walk_minutes == 2


def test_タイトルは物件名でキャッチコピーではない(listings) -> None:
    """⚠ ``h2.property_unit-title`` の中身は**広告のキャッチコピー**。

    実データの1件目は「◆現在空室◆最寄り駅から徒歩２分、８駅１０路線の
    マルチアクセス」で、物件名は ``dt=物件名`` の側にある。取り違えると
    **通知とダイジェストに広告文が並ぶ**（例外にならない）。
    """
    assert listings[0].title == "グランスイートＴＯＫＹＯマークス"
    # ⚠ 「◆を含まない」では検証にならない。SUUMO 側が**物件名欄そのものに
    # 広告文を入れる掲載がある**（``ダイアパレス水道橋◆水道橋駅3分◆…``）ので、
    # h2 の値と違うことで確かめる
    assert not any("マルチアクセス" in (row.title or "") for row in listings)


def test_必須項目が全件そろう(listings) -> None:
    """MUST 1段目が一覧だけで成立すること。"""
    for row in listings:
        assert row.price is not None
        assert row.area_sqm is not None
        assert row.layout
        assert row.address
        assert row.url


def test_面積は坪の数字を拾わない(scraper: SuumoBuyMansionScraper) -> None:
    """``23.08m2（6.98坪）（壁芯）`` から 6.98 を採らないこと。"""
    rows = scraper.parse_list(_read("list_chuko_m_bus.html"))
    assert rows[0].area_sqm == 23.08


def test_駅名を同定できる(listings) -> None:
    """⚠ 交通欄をそのまま渡すと駅が1件も取れない（D-room と同型 → 課題#41）。

    ``東京メトロ丸ノ内線「淡路町」徒歩2分`` は鉤括弧の**前に空白**と
    **後ろに「駅」**の両方が要る。片方だけだと路線名ごと駅名になり、
    マスタに当たらず**通勤時間が unknown になるだけで例外にならない**。
    """
    primary, _ = extract_station_names(listings[0].station_info or "")
    assert primary == ("淡路町",)
    for row in listings:
        names, _ = extract_station_names(row.station_info or "")
        assert not any("線" in name for name in names), row.station_info


def test_バス便は駅徒歩にしない(scraper: SuumoBuyMansionScraper) -> None:
    """⚠ ``京王高尾線「めじろ台」バス5分停歩5分`` の「5分」は**バス停からの徒歩**。

    駅徒歩として採ると ``walk_minutes_max`` を不当に通過する
    （UR・ホームメイト・D-room・レオパレスで踏んだのと同じ罠）。
    """
    rows = scraper.parse_list(_read("list_chuko_m_bus.html"))
    bus = [r for r in rows if "バス" in (r.station_info or "")]
    assert len(bus) == 3
    assert all(r.walk_minutes is None for r in bus)
    # バス便でない掲載の徒歩は従来どおり取れる
    assert rows[0].walk_minutes == 10


def test_詳細から管理費と修繕積立金を取る(scraper: SuumoBuyMansionScraper) -> None:
    """⚠ ``tr`` の中に ``th``/``td`` が**複数対**並ぶので ``th.getnext()`` で読む。

    ``tr`` から ``td`` を拾うと**値が1つずれる**（実測で管理費に販売価格が入った）。
    ⚠ ``1万3020円`` は課題#53 を直すまで 10000 と読めていた。
    """
    detail = scraper.parse_detail(_read("detail_chuko_m.html"))
    assert detail.mgmt_fee_monthly == 10_900
    assert detail.repair_reserve_monthly == 13_020
