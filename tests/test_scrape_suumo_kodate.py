"""SUUMO 一戸建て（新築・中古）のパーサ（→ 課題#4 手順8）。

フィクスチャは**八王子市**（郊外）の実HTML。⚠ **都心の市区では検出できない罠がある**
——中古マンションを千代田区で測ったときバス便は 0/20 だったが、
戸建ての八王子市では**中古 11/20・新築 17/20 がバス便**だった
（→ 課題#41・#44 で2度踏んだ「そのフィクスチャでは検出できない」形）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.scrape.suumo_kodate import (
    SuumoChukoKodateScraper,
    SuumoShinchikuKodateScraper,
    station_info,
    walk_minutes_of,
)

FIXTURES = Path(__file__).parent / "fixtures" / "suumo_kodate"


def _html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chuko():
    return SuumoChukoKodateScraper().parse_list(_html("list_chuko_k"))


@pytest.fixture(scope="module")
def shinchiku():
    return SuumoShinchikuKodateScraper().parse_list(_html("list_shinchiku_k"))


class Test交通欄:
    """⚠⚠ 戸建てで最も危険なのはここ。**バス便が多数派**である。"""

    def test_バス便の徒歩は採らない(self) -> None:
        """「停歩5分」は**バス停からの徒歩**（→ 課題#58）。

        駅徒歩として採ると ``walk_minutes_max`` を不当に通過する。
        ⚠ 例外にも件数の変化にもならない。
        """
        assert walk_minutes_of("ＪＲ中央線「八王子」バス18分停歩5分") is None
        assert walk_minutes_of("ＪＲ中央線「西八王子」バス17分停歩3分") is None
        # 駅徒歩は従来どおり採る
        assert walk_minutes_of("ＪＲ中央線「高尾」徒歩34分") == 34

    def test_バス停名の鉤括弧を駅にしない(self) -> None:
        """⚠⚠ ``京王バス「館ヶ丘団地」徒歩1分`` を変換すると
        ``館ヶ丘団地駅`` という**実在しない駅名**になる。

        駅マスタに当たらず**通勤時間が unknown になるだけ**なので気づけない
        （→ 課題#41・D-room）。
        """
        assert station_info("京王バス「館ヶ丘団地」徒歩1分") is None

    def test_駅の鉤括弧は駅として整える(self) -> None:
        """⚠ **鉤括弧の「前に空白」と「後ろに駅」の両方が要る**。

        片方だけだと路線名ごと駅名になったり第1パスが空振りしたりする。
        """
        result = station_info("ＪＲ中央線「八王子」バス18分停歩5分")
        assert result is not None
        assert "「八王子」駅" in result
        assert " 「" in result

    def test_バス停と駅が同居しても駅だけ残る(self) -> None:
        value = "ＪＲ中央線「高尾」徒歩34分 京王バス「館ヶ丘団地」徒歩1分"
        result = station_info(value)
        assert result is not None
        assert "高尾" in result
        assert "館ヶ丘団地" not in result

    def test_駅が同定できる形になっている(self, chuko) -> None:
        """パーサを通した後に実際に駅名が取り出せること。

        ⚠ 純関数が正しくても、アダプタが渡す形が違えば同定は死ぬ。
        """
        from house_search.commute.matcher import extract_station_names

        infos = [x.station_info for x in chuko if x.station_info]
        assert infos, "交通欄が1件も取れていない"
        names, _ = extract_station_names(infos[0])
        assert names, f"駅名が取れない: {infos[0]}"
        assert not any("線" in n for n in names), f"路線名ごと拾っている: {names}"


class Test中古一戸建ての一覧:
    def test_掲載が取れる(self, chuko) -> None:
        assert len(chuko) == 20

    def test_土地面積と建物面積が取れる(self, chuko) -> None:
        """⚠ **戸建てに ``area_sqm`` を使わない**（→ 要件定義書 §5.3）。

        専有面積という概念が無く、土地・建物の2軸になる。
        """
        assert all(x.land_area_sqm for x in chuko)
        assert all(x.building_area_sqm for x in chuko)
        assert all(x.area_sqm is None for x in chuko)
        assert chuko[0].land_area_sqm == pytest.approx(100.9)
        assert chuko[0].building_area_sqm == pytest.approx(79.32)

    def test_価格と間取りと築年が取れる(self, chuko) -> None:
        assert chuko[0].price == 19_900_000
        assert chuko[0].layout == "3LDK"
        assert all(x.age_years is not None for x in chuko)

    def test_中古はレンジを持たない(self, chuko) -> None:
        """レンジは新築（分譲地）だけ（→ 要件定義書 §11.4）。"""
        assert all(x.price_min is None and x.price_max is None for x in chuko)

    def test_物件名が無い掲載があっても落とさない(self, chuko) -> None:
        """⚠ 実測で 16/20 しか物件名を持たない。必須にすると黙って落ちる。"""
        named = [x for x in chuko if x.title]
        assert 0 < len(named) < len(chuko)

    def test_バス便の掲載は徒歩がNoneになる(self, chuko) -> None:
        """⚠ 実測で 11/20 がバス便。**多数派**である。"""
        no_walk = [x for x in chuko if x.walk_minutes is None]
        assert len(no_walk) >= 10, f"バス便を弾けていない: {len(no_walk)}件"

    def test_物件IDと詳細URLが取れる(self, chuko) -> None:
        assert all(x.external_id.startswith("nc_") for x in chuko)
        assert all(x.url.startswith("https://suumo.jp/chukoikkodate/") for x in chuko)
        assert len({x.external_id for x in chuko}) == 20


class Test新築一戸建ての一覧:
    """⚠ **1掲載＝分譲地（全5区画）**。価格・面積・間取りがレンジになる。"""

    def test_掲載が取れる(self, shinchiku) -> None:
        assert len(shinchiku) == 20

    def test_価格レンジを持つ(self, shinchiku) -> None:
        """``4960万円～4980万円``。⚠ ``price`` には**下限**を入れる。"""
        ranged = [x for x in shinchiku if x.price_max and x.price_min != x.price_max]
        assert ranged, "レンジの掲載が1件も無い"
        assert all(x.price == x.price_min for x in shinchiku if x.price)

    def test_面積は下限を採る(self, shinchiku) -> None:
        """``120.17m2・120.18m2`` も ``91.5m2～94.4m2`` も先頭（下限）。

        ⚠ 坪数（``36.35坪``）を誤って拾わないこと。
        """
        assert shinchiku[0].land_area_sqm == pytest.approx(120.17)
        assert shinchiku[0].building_area_sqm == pytest.approx(91.5)

    def test_土地の掲載を取り込まない(self, shinchiku) -> None:
        """⚠ 新築の索引には ``/tochi/``（建築条件付き土地）が混ざる。

        種別が違うので取り込まない。
        """
        assert all("/tochi/" not in x.url for x in shinchiku)

    def test_バス便が多数派(self, shinchiku) -> None:
        """⚠ 実測 17/20。都心のフィクスチャでは検出できない。"""
        no_walk = [x for x in shinchiku if x.walk_minutes is None]
        assert len(no_walk) >= 15, f"バス便を弾けていない: {len(no_walk)}件"


class Test詳細ページ:
    def test_設備原文が取れる(self) -> None:
        """⚠ 見出しクラスは **``secTitleInnerR``**（新築マンションだけが ``…K``）。

        流用を間違えると**原文が空になるだけで例外にならない**（→ 課題#4）。
        """
        detail = SuumoChukoKodateScraper().parse_detail(_html("detail_chuko_k"))
        assert detail.raw_features_text
        assert "システムキッチン" in detail.raw_features_text
        assert "浴室乾燥機" in detail.raw_features_text

    def test_新築も同じ見出しクラスで取れる(self) -> None:
        detail = SuumoShinchikuKodateScraper().parse_detail(_html("detail_shinchiku_k"))
        assert detail.raw_features_text
        assert "システムキッチン" in detail.raw_features_text

    def test_住所と築年月が取れる(self) -> None:
        detail = SuumoChukoKodateScraper().parse_detail(_html("detail_chuko_k"))
        assert detail.address == "東京都八王子市中野上町４"
        assert detail.built_on is not None and detail.built_on.year == 1998

    def test_戸建て固有の属性を残す(self) -> None:
        """⚠ 接道・建ぺい率は表記揺れが激しいので JSONB へ入れる（→ §11.3）。"""
        detail = SuumoChukoKodateScraper().parse_detail(_html("detail_chuko_k"))
        attrs = detail.type_specific_attrs
        assert attrs.get("土地の権利形態") == "所有権"
        assert "建ぺい率・容積率" in attrs
        assert "私道負担・道路" in attrs

    def test_詳細のバス便も徒歩に採らない(self) -> None:
        """詳細の交通欄も ``バス18分…歩5分`` の形。"""
        detail = SuumoChukoKodateScraper().parse_detail(_html("detail_chuko_k"))
        assert detail.walk_minutes is None


def test_ページ送りは1始まりのpageクエリ() -> None:
    scraper = SuumoChukoKodateScraper()
    base = "https://suumo.jp/chukoikkodate/tokyo/sc_hachioji/"
    assert scraper.page_url(base, 1) == base
    assert scraper.page_url(base, 2) == base + "?page=2"


def test_一覧URLはSEOパスで組み立てる() -> None:
    """⚠ robots が ``/jj/bukken/ichiran/`` を禁じているのでこの経路しかない。"""
    from house_search.scrape.area import AreaTarget

    area = AreaTarget(prefecture="東京都", city_name="八王子市", value="sc_hachioji")
    assert SuumoChukoKodateScraper().list_urls(None, [area]) == [
        "https://suumo.jp/chukoikkodate/tokyo/sc_hachioji/"
    ]
    assert SuumoShinchikuKodateScraper().list_urls(None, [area]) == [
        "https://suumo.jp/ikkodate/tokyo/sc_hachioji/"
    ]


def test_サイト側フィルタは送らない() -> None:
    """⚠ 1軸も測っていないので送らない（→ ADR 0015・課題#29）。

    推測で書くと「0件になる／黙って無視される／向きが逆」のいずれかになり、
    **どれも例外にならない**。
    """
    assert SuumoChukoKodateScraper().supports_site_filters is False
    assert SuumoShinchikuKodateScraper().supports_site_filters is False
