"""SUUMO 土地のパーサ（→ 課題#61・Phase 9b）。

フィクスチャは 2026-09-11 の着手前実測（25リクエスト）で保存した実HTML。
**3市区の性質が違う**ことに意味がある（片方だけだと検出できない罠がある
→ 課題#41・#44 で2度踏んだ「そのフィクスチャでは検出できない」形）。

- 世田谷区（都心）: 物件名 20/20・価格のレンジ・中黒・``未定``・地番付きの住所
- 八王子市（郊外）: **物件名 0/20**・**バス便 5/20**・坪併記の面積
- 印西市（郡部に近い）: ``車1.9km``（徒歩が無い）・``徒歩19分～20分``・2ページ目が10件

⚠⚠ **戸建て（``suumo_kodate``）の派生にしてはいけない。** 戸建ては新築の索引に
混ざる ``/tochi/`` を**捨てる**ので、継承すると土地の一覧が**0件になるだけで
例外にならない**（計画書 §6.5-1）。下の ``Test戸建てとの取り違え`` が固定している。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.scrape import SCRAPERS, get_scraper
from house_search.scrape.area import AreaTarget
from house_search.scrape.suumo_kodate import (
    SuumoChukoKodateScraper,
    SuumoShinchikuKodateScraper,
)
from house_search.scrape.suumo_tochi import (
    SuumoTochiScraper,
    build_condition_flag,
    leasehold_flag,
    mentions_rebuild_prohibited,
)

FIXTURES = Path(__file__).parent / "fixtures" / "suumo_tochi"


def _html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def _by_id(listings, external_id: str):
    return next(x for x in listings if x.external_id == external_id)


@pytest.fixture(scope="module")
def scraper() -> SuumoTochiScraper:
    return SuumoTochiScraper()


@pytest.fixture(scope="module")
def setagaya(scraper):
    return scraper.parse_list(_html("list_setagaya"))


@pytest.fixture(scope="module")
def hachioji(scraper):
    return scraper.parse_list(_html("list_hachioji"))


@pytest.fixture(scope="module")
def inzai(scraper):
    return scraper.parse_list(_html("list_inzai"))


class Test一覧の件数:
    """⚠ 件数が減るだけの壊れ方は例外にならない。**1ページ20件**を下限として固定する。"""

    @pytest.mark.parametrize("name", ["list_setagaya", "list_hachioji", "list_inzai"])
    def test_1ページ目は20件取れる(self, scraper, name) -> None:
        assert len(scraper.parse_list(_html(name))) == 20

    def test_最終ページは20件に満たない(self, scraper) -> None:
        """印西市は総件数30件なので2ページ目が10件（実測）。"""
        page2 = scraper.parse_list(_html("list_inzai_page2"))
        assert len(page2) == 10
        assert scraper.is_last_page(len(page2)) is True
        assert scraper.is_last_page(20) is False

    def test_土地のリンクだけを採る(self, setagaya, hachioji, inzai) -> None:
        for listing in (*setagaya, *hachioji, *inzai):
            assert "/tochi/" in listing.url

    def test_物件IDと詳細URLが取れる(self, setagaya) -> None:
        first = setagaya[0]
        assert first.external_id == "nc_21674636"
        assert first.url == "https://suumo.jp/tochi/tokyo/sc_setagaya/nc_21674636/"
        assert first.site_code == "SUUMO"


class Test価格:
    def test_レンジは下限をpriceに入れて上限も持つ(self, setagaya) -> None:
        """``8599万円～1億1999万円``。⚠ ``parse_yen`` へそのまま渡すと
        **上限（1億1999万円）を読む**（→ ``parse_price_range``）。"""
        listing = _by_id(setagaya, "nc_21674636")
        assert listing.price == 85_990_000
        assert listing.price_min == 85_990_000
        assert listing.price_max == 119_990_000

    def test_中黒の列挙も最小と最大を持つ(self, setagaya) -> None:
        """``1億2800万円・1億4000万円``（2区画）。"""
        listing = _by_id(setagaya, "nc_21673624")
        assert (listing.price, listing.price_min, listing.price_max) == (
            128_000_000,
            128_000_000,
            140_000_000,
        )

    def test_単一価格(self, setagaya) -> None:
        listing = _by_id(setagaya, "nc_21660384")
        assert listing.price == 87_300_000

    def test_価格未定はフラグを立てNoneにする(self, setagaya) -> None:
        """⚠ 0円やハイフンにすると「安い」と誤読される（→ 要件定義書 §11.4）。"""
        listing = _by_id(setagaya, "nc_21646724")
        assert listing.price is None
        assert listing.type_specific_attrs.get("price_undecided") is True

    def test_価格があるときはFalseを明示する(self, setagaya, hachioji, inzai) -> None:
        """⚠⚠ JSONB は ``||`` でマージされるので、書かないと未定フラグが残り続け
        「価格があるのに価格未定」と表示される（例外にならない → PR #109）。"""
        priced = [x for x in (*setagaya, *hachioji, *inzai) if x.price is not None]
        assert len(priced) == 59
        assert all(x.type_specific_attrs.get("price_undecided") is False for x in priced)


class Test土地面積:
    def test_レンジは下限を採る(self, setagaya) -> None:
        assert _by_id(setagaya, "nc_21674636").land_area_sqm == 80.37

    def test_坪の数字を面積として読まない(self, hachioji) -> None:
        """⚠ ``180m2（54.44坪）``。坪を読むと約3.3倍ずれ、**例外にならない**。"""
        assert _by_id(hachioji, "nc_21623710").land_area_sqm == 180.0

    def test_路地状部分の注記より前の面積を採る(self, hachioji) -> None:
        """``160.58㎡（48.57坪）（登記）、路地状部分：47.72...``。"""
        assert _by_id(hachioji, "nc_21649175").land_area_sqm == 160.58

    def test_全件に土地面積がある(self, setagaya, hachioji, inzai) -> None:
        assert all(x.land_area_sqm for x in (*setagaya, *hachioji, *inzai))

    def test_建物系の項目を持たない(self, setagaya, hachioji, inzai) -> None:
        """⚠ 土地に専有面積・建物面積・間取り・築年は無い（→ ADR 0024）。
        入れると名寄せや MUST が黙って建物前提で動く。"""
        for x in (*setagaya, *hachioji, *inzai):
            assert x.area_sqm is None
            assert x.building_area_sqm is None
            assert x.layout is None
            assert x.age_years is None
            assert x.floor_num is None


class Test物件名と住所:
    def test_物件名が無い掲載も落とさない(self, hachioji) -> None:
        """⚠ 八王子市は **0/20**。必須にすると一覧が丸ごと消える。"""
        assert len(hachioji) == 20
        assert all(x.title is None for x in hachioji)

    def test_物件名はdtの物件名から取る(self, setagaya) -> None:
        """⚠ ``h2`` は広告のキャッチコピーになりうる（中古マンションで実測）。"""
        assert _by_id(setagaya, "nc_21660485").title == "ADCAST 桜上水 SELECTION"

    def test_地番の注記を住所に残さない(self, setagaya) -> None:
        """⚠ ``瀬田２-856番17、76（地番）``。地番は住居表示ではないので注記を落とす
        （→ ADR 0020 の番地誤認と同型・新築マンションの棟と同じ扱い）。"""
        listing = _by_id(setagaya, "nc_21632081")
        assert listing.address == "東京都世田谷区瀬田２-856番17、76"


class Test交通:
    def test_バス便は徒歩を採らない(self, hachioji) -> None:
        """⚠ ``バス11分停歩4分`` の4分は**バス停からの徒歩**（→ 課題#58）。
        八王子市は実測 5/20 がバス便。"""
        bus = _by_id(hachioji, "nc_21651296")
        assert bus.walk_minutes is None
        assert bus.station_info is not None and "「高尾」駅" in bus.station_info
        assert sum(1 for x in hachioji if x.walk_minutes is None) == 5

    def test_車の距離しかない掲載は徒歩不明にする(self, inzai) -> None:
        """``北総線「印西牧の原」車1.9km～2km``。駅は同定できるが徒歩は無い。"""
        listing = _by_id(inzai, "nc_79196826")
        assert listing.walk_minutes is None
        assert listing.station_info is not None and "「印西牧の原」駅" in listing.station_info

    def test_徒歩のレンジは下限を採る(self, inzai) -> None:
        """``徒歩19分～20分``（区画ごとに違う）。"""
        assert _by_id(inzai, "nc_20156998").walk_minutes == 19

    def test_駅名は同定できる形に整える(self, setagaya) -> None:
        """⚠ 鉤括弧の前に空白・後ろに「駅」が要る（→ 課題#41）。"""
        listing = _by_id(setagaya, "nc_21660485")
        assert listing.station_info == "京王線 「桜上水」駅 徒歩5分"
        assert listing.walk_minutes == 5


class Test詳細ページ:
    def test_設備原文は特徴ピックアップから取る(self, scraper) -> None:
        detail = scraper.parse_detail(_html("detail_plain"))
        assert detail.raw_features_text is not None
        assert "更地渡し" in detail.raw_features_text
        assert "建築条件なし" in detail.raw_features_text

    def test_広告の見出しを設備原文に入れない(self, scraper) -> None:
        """⚠ ``h3`` の広告文（``◇京王線「桜上水」駅徒歩5分！第一種低層の閑静な…``）を
        入れると、その物件に無い設備が拾われて**設備数が黙って水増しされる**。"""
        detail = scraper.parse_detail(_html("detail_plain"))
        for ng in ("閑静な住環境♪限定", "会員限定物件", "住宅ローンが不安"):
            assert ng not in (detail.raw_features_text or "")

    def test_住所は所在地から取る(self, scraper) -> None:
        """⚠ 「住所」欄は ``[ ■周辺環境 ]`` の導線が同居する。"""
        detail = scraper.parse_detail(_html("detail_plain"))
        assert detail.address == "東京都世田谷区桜上水５"

    def test_駅徒歩が取れる(self, scraper) -> None:
        assert scraper.parse_detail(_html("detail_plain")).walk_minutes == 5

    def test_詳細のバス便も徒歩に採らない(self, scraper) -> None:
        """``「高尾」バス11分高尾台中央歩4分`` の4分はバス停からの徒歩。"""
        assert scraper.parse_detail(_html("detail_bus_noname")).walk_minutes is None

    def test_土地固有の属性を残す(self, scraper) -> None:
        """⚠ 表記揺れが激しく正規化が未確立なので JSONB へ入れる（→ §11.3）。"""
        attrs = scraper.parse_detail(_html("detail_plain")).type_specific_attrs
        assert attrs["土地の権利形態"] == "所有権"
        assert attrs["土地状況"] == "古家有り更地渡し"
        assert attrs["引き渡し時期"] == "相談"
        assert attrs["地目"] == "宅地"
        assert attrs["用途地域"] == "１種低層"
        assert attrs["私道負担・道路"] == "道路幅：4ｍ、アスファルト舗装"
        assert attrs["建ぺい率・容積率"] == "建ペい率：50％、容積率：100％"
        assert attrs["その他制限事項"].startswith("高度地区")

    def test_古家有りの土地状況を残す(self, scraper) -> None:
        attrs = scraper.parse_detail(_html("detail_bus_noname")).type_specific_attrs
        assert attrs["土地状況"] == "古家有り"

    def test_空欄の項目は残さない(self, scraper) -> None:
        """``-`` は「記載なし」。値として残すと JSONB が雑音で埋まる。"""
        attrs = scraper.parse_detail(_html("detail_undecided_range")).type_specific_attrs
        assert "引き渡し時期" not in attrs
        assert all(value != "-" for value in attrs.values())

    def test_建ぺい率は半角の中黒のラベルでも取れる(self, scraper) -> None:
        """⚠ 同じページに ``建ぺい率･容積率``（半角）と ``建ぺい率・容積率``（全角）の
        2つのラベルがある。テンプレートによって片方しか無ければ黙って欠ける。"""
        html = (
            "<html><body><table><tr><th>建ぺい率･容積率</th><td>60％・200％</td></tr>"
            "</table></body></html>"
        )
        attrs = scraper.parse_detail(html).type_specific_attrs
        assert attrs["建ぺい率・容積率"] == "60％・200％"

    def test_建築プラン例を建物として読まない(self, scraper) -> None:
        """⚠ ``プラン間取り`` ``プラン建物面積`` は**建築プラン例**で、その土地の建物ではない。"""
        detail = scraper.parse_detail(_html("detail_plain"))
        assert not any("プラン" in key for key in detail.type_specific_attrs)
        assert detail.floor_num is None
        assert detail.total_floors is None
        assert detail.built_on is None
        assert detail.mgmt_fee_monthly is None


class Test取引上の注意事項:
    """⚠ 論点5（2026-09-11）: 建築条件付き・借地権・再建築不可は**取り込んで明示する**
    （除外 MUST は後で設計する）。判定は仕様表の欄で行う。"""

    def test_建築条件付きは仕様表の欄で判定する(self, scraper) -> None:
        """⚠⚠ 一覧の本文を「建築条件」で探すと ``建築条件なし`` のタグや
        ``建築条件付土地購入サポート`` のバッジに当たる（実測で73件）。"""
        attrs = scraper.parse_detail(_html("detail_jouken_tsuki")).type_specific_attrs
        assert attrs["build_condition"] is True
        assert attrs["建築条件"] == "付"

    def test_建築条件なしはFalseを明示する(self, scraper) -> None:
        for name in ("detail_plain", "detail_undecided_range", "detail_bus_noname"):
            assert scraper.parse_detail(_html(name)).type_specific_attrs["build_condition"] is False

    def test_所有権は借地ではない(self, scraper) -> None:
        attrs = scraper.parse_detail(_html("detail_plain")).type_specific_attrs
        assert attrs["leasehold"] is False

    def test_再建築不可の記載が無ければFalse(self, scraper) -> None:
        for name in (
            "detail_plain",
            "detail_undecided_range",
            "detail_jouken_tsuki",
            "detail_bus_noname",
            "detail_multi",
        ):
            attrs = scraper.parse_detail(_html(name)).type_specific_attrs
            assert attrs["rebuild_prohibited"] is False

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("付", True), ("-", False), ("無", False), ("なし", False), (None, None), ("相談", None)],
    )
    def test_建築条件の読み方(self, value, expected) -> None:
        """⚠ 実測したのは ``付`` と ``-`` だけ。**知らない表記は None**（推測で True/False に
        倒さない → ADR 0015）。原文は ``建築条件`` のキーに残る。"""
        assert build_condition_flag(value) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("所有権", False),
            ("旧法借地権", True),
            ("定期借地権（一般）", True),
            ("所有権・借地権", True),
            (None, None),
            ("-", None),
        ],
    )
    def test_権利形態の読み方(self, value, expected) -> None:
        """⚠ 借地が混ざるなら借地として明示する（所有権と並記でも相場より安く見える）。"""
        assert leasehold_flag(value) is expected

    def test_再建築不可の記載を見つける(self) -> None:
        assert mentions_rebuild_prohibited(["高度地区", "再建築不可、接道なし"]) is True
        assert mentions_rebuild_prohibited(["高度地区", None, "再建築可"]) is False


class Test戸建てとの取り違え:
    """⚠⚠ 戸建ては ``/tochi/`` を捨て、土地は ``/tochi/`` だけを採る。逆にすると
    **0件になるか別の種別を取り込むだけで例外にならない**（計画書 §6.5-1）。"""

    @pytest.mark.parametrize("kodate", [SuumoChukoKodateScraper, SuumoShinchikuKodateScraper])
    def test_戸建てのアダプタは土地の一覧から1件も採らない(self, kodate) -> None:
        """⚠ 戸建てのフィクスチャには ``/tochi/`` が0件なので、既存の
        ``test_土地の掲載を取り込まない`` は空振りしていた。土地の実物で固定する。"""
        assert kodate().parse_list(_html("list_setagaya")) == []

    def test_土地のアダプタは戸建ての一覧から1件も採らない(self, scraper) -> None:
        kodate_html = (
            Path(__file__).parent / "fixtures" / "suumo_kodate" / "list_chuko_k.html"
        ).read_text(encoding="utf-8")
        assert scraper.parse_list(kodate_html) == []


class TestURLと登録:
    def test_一覧URLはSEOパスで新着順(self, scraper) -> None:
        """⚠ ``po=1&pj=2`` は土地でも新着順として効く（実測: 世田谷区の1ページ目の
        「新着」が既定 0/20 → 20/20。対照の ``zzz=1`` は既定と並びが同一）。"""
        area = AreaTarget(prefecture="東京都", city_name="世田谷区", value="sc_setagaya")
        assert scraper.list_urls(None, [area]) == [
            "https://suumo.jp/tochi/tokyo/sc_setagaya/?po=1&pj=2"
        ]

    def test_スラグの無い市区は組み立てない(self, scraper) -> None:
        area = AreaTarget(prefecture="東京都", city_name="檜原村", value=None)
        assert scraper.list_urls(None, [area]) == []

    def test_ページ送りは1始まりのpageクエリ(self, scraper) -> None:
        """⚠ ``?`` を重ねると page が黙って無視される（→ 課題#29）。"""
        base = "https://suumo.jp/tochi/tokyo/sc_setagaya/?po=1&pj=2"
        assert scraper.page_url(base, 1) == base
        assert scraper.page_url(base, 2) == base + "&page=2"

    def test_一覧URLがrobotsで許可される(self, scraper) -> None:
        """組み立てたURL（1ページ目・2ページ目）を実 robots.txt に当てる（→ 課題#52）。"""
        from house_search.scrape.fetch import RobotsRules

        robots = (Path(__file__).parent / "fixtures" / "robots" / "suumo.txt").read_text(
            encoding="utf-8"
        )
        rules = RobotsRules.parse(robots)
        area = AreaTarget(prefecture="東京都", city_name="八王子市", value="sc_hachioji")
        for url in scraper.list_urls(None, [area]):
            assert rules.can_fetch("house-search/2.0", url) is True
            assert rules.can_fetch("house-search/2.0", scraper.page_url(url, 2)) is True
        detail = "https://suumo.jp/tochi/tokyo/sc_setagaya/nc_21674636/"
        assert rules.can_fetch("house-search/2.0", detail) is True

    def test_掲載終了はHTTP404(self, scraper) -> None:
        """実測: 存在しない ``nc_`` は 404（戸建てと同じ）。"""

        class _Response:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        class _Fetcher:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

            def get(self, url: str) -> _Response:
                return _Response(self.status_code)

        url = "https://suumo.jp/tochi/tokyo/sc_setagaya/nc_00000001/"
        assert scraper.is_sold(_Fetcher(404), url) is True
        assert scraper.is_sold(_Fetcher(200), url) is False

    def test_サイトと種別の組で登録される(self) -> None:
        assert SCRAPERS[("SUUMO", "TOCHI")] is SuumoTochiScraper
        assert isinstance(get_scraper("SUUMO", "TOCHI"), SuumoTochiScraper)

    def test_サイト側フィルタは送らない(self, scraper) -> None:
        """⚠ 土地の面積の選択肢は 60〜150㎡ の10刻みで、現行の MUST（60㎡未満も
        ありうる）を表現しきれない。推測で書くと「0件になる／黙って無視される／
        向きが逆」のいずれかになり**どれも例外にならない**（→ ADR 0015）。"""
        assert scraper.supports_site_filters is False
        assert scraper.requires_city is True
