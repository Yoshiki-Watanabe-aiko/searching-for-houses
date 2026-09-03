"""UR賃貸住宅アダプタの回帰テスト。

フィクスチャは**実APIの応答をそのまま保存したもの**（2026-09-03 実測）。
UR は空室が出ると早く埋まるため、取れたときの応答を固定しておかないと
同じ状態を作り直せない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from house_search.scrape.prefectures import PREFECTURE_JIS
from house_search.scrape.ur import (
    NO_GUARANTOR_TOKEN,
    SITE_CODE,
    UrScraper,
    build_external_id,
    parse_access,
    parse_room_page_url,
    room_page_url,
    room_rent,
    split_danchi_id,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ur"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def scraper() -> UrScraper:
    return UrScraper()


@pytest.fixture
def danchi() -> dict:
    """空室13件の団地（館ヶ丘・八王子市）。"""
    return next(row for row in _load("search_danchi.json") if row["id"] == "20_2600")


def test_都道府県コードはJIS上2桁になる() -> None:
    # PREFECTURE_ROMAJI の並び順に依存して導出しているので代表値を固定する
    assert PREFECTURE_JIS["北海道"] == "01"
    assert PREFECTURE_JIS["埼玉県"] == "11"
    assert PREFECTURE_JIS["千葉県"] == "12"
    assert PREFECTURE_JIS["東京都"] == "13"
    assert PREFECTURE_JIS["神奈川県"] == "14"
    assert PREFECTURE_JIS["沖縄県"] == "47"
    assert len(PREFECTURE_JIS) == 47


def test_団地IDを住戸APIのパラメータへ分解する() -> None:
    assert split_danchi_id("20_2600") == {
        "shisya": "20",
        "danchi": "260",
        "shikibetu": "0",
    }


def test_住戸ページURLと復元が往復する() -> None:
    url = room_page_url("/chintai/kanto/tokyo/20_2600.html", "001120513")
    assert url == (
        "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_2600_room.html?JKSS=001120513"
    )
    # 詳細取得と成約確認はキューのURLしか手掛かりが無いので、URLから戻せること
    assert parse_room_page_url(url) == {
        "shisya": "20",
        "danchi": "260",
        "shikibetu": "0",
        "id": "001120513",
    }


def test_住戸IDは団地IDを前に置いて一意にする() -> None:
    # 住戸IDは「棟＋部屋番号」の符号化で団地の中でしか一意でない
    assert build_external_id("20_2600", "001120513") == "20_2600_001120513"


class TestAccess:
    """交通欄の解析。⚠ ここを誤ると MUST の徒歩判定が壊れる。"""

    def test_バス経由の徒歩分を駅徒歩として採らない(self) -> None:
        access = (
            "<li>JR中央線｢高尾｣駅バス7分 徒歩1～11分</li>"
            "<li>京王高尾線｢高尾｣駅バス7分 徒歩1～11分</li>"
        )
        stations, walk = parse_access(access)
        # バス停からの徒歩なので walk_minutes にしてはいけない
        assert walk is None
        # 駅名は通勤時間の算出に使うので拾う
        assert stations == "高尾駅"

    def test_徒歩レンジは下限を採る(self) -> None:
        _, walk = parse_access("<li>JR八高線｢北八王子｣駅 徒歩9～12分</li>")
        assert walk == 9

    def test_徒歩単一もレンジと同じ経路で読む(self) -> None:
        _, walk = parse_access("<li>京王相模原線｢南大沢｣駅 徒歩7分</li>")
        assert walk == 7

    def test_バス経由と徒歩の行が混ざったら徒歩の行だけを使う(self) -> None:
        access = (
            "<li>JR中央線｢高尾｣駅バス7分 徒歩1～11分</li>"
            "<li>JR中央本線｢高尾｣駅 徒歩29～38分</li>"
        )
        stations, walk = parse_access(access)
        assert walk == 29
        assert stations == "高尾駅"

    def test_複数駅は重複を除いて並べる(self) -> None:
        access = (
            "<li>都営新宿線｢小川町｣駅 徒歩2分</li>"
            "<li>東京メトロ千代田線｢新御茶ノ水｣駅 徒歩2分</li>"
            "<li>東京メトロ丸ノ内線｢淡路町｣駅 徒歩3分</li>"
        )
        stations, walk = parse_access(access)
        assert stations == "小川町駅 / 新御茶ノ水駅 / 淡路町駅"
        assert walk == 2

    def test_空欄は両方Noneになる(self) -> None:
        assert parse_access(None) == (None, None)
        assert parse_access("") == (None, None)


class TestParseDanchiRooms:
    """①団地＋②住戸から掲載を組み立てる。"""

    def test_住戸ごとに1掲載になる(self, scraper: UrScraper, danchi: dict) -> None:
        rooms = _load("rooms_page0.json")
        listings = scraper.parse_danchi_rooms(danchi, rooms, prefecture="東京都")
        assert len(listings) == 5
        assert {listing.site_code for listing in listings} == {SITE_CODE}

    def test_一覧項目がMUST1段目に足りる(self, scraper: UrScraper, danchi: dict) -> None:
        rooms = _load("rooms_page0.json")
        first = scraper.parse_danchi_rooms(danchi, rooms, prefecture="東京都")[0]
        assert first.external_id == "20_2600_001120513"
        assert first.price == 47_200
        assert first.mgmt_fee_monthly == 4_500
        assert first.layout == "2DK"
        # ⚠ floorspace は "42&#13217;" とHTML実体参照で㎡が入る
        assert first.area_sqm == pytest.approx(42.0)
        assert first.floor_num == 5
        assert first.total_floors == 5
        assert first.station_info == "高尾駅"
        # 館ヶ丘の交通欄は「バス7分＋徒歩1～11分」が2本と「徒歩29～38分」が1本。
        # ⚠ バス経由の11分ではなく、徒歩のみの行の下限29分を採るのが正しい
        # （バス停からの徒歩を駅徒歩にすると MUST を不当に通過する）。
        # 実際この掲載は MUST の walk_minutes_max=20 で正しく落ちる
        assert first.walk_minutes == 29

    def test_礼金は制度上ゼロで敷金は賃料から円へ直す(
        self, scraper: UrScraper, danchi: dict
    ) -> None:
        rooms = _load("rooms_page0.json")
        first = scraper.parse_danchi_rooms(danchi, rooms, prefecture="東京都")[0]
        assert first.key_money_amount == 0
        # shikikin は「2か月」表記。⚠ 敷金は UR も取られるのでゼロにしない
        assert first.deposit_amount == 47_200 * 2

    def test_住所は市区までで組み立てる(self, scraper: UrScraper, danchi: dict) -> None:
        rooms = _load("rooms_page0.json")
        first = scraper.parse_danchi_rooms(danchi, rooms, prefecture="東京都")[0]
        # ①にも③にも住所欄が無いので skcs から作る。市区が分かれば
        # 帯の絞り込みと採点は成立する（→ 詳細設計書 §9.3）
        assert first.address == "東京都八王子市"

    def test_タイトルは団地名と部屋名を繋ぐ(self, scraper: UrScraper, danchi: dict) -> None:
        rooms = _load("rooms_page0.json")
        first = scraper.parse_danchi_rooms(danchi, rooms, prefecture="東京都")[0]
        assert first.title == "館ヶ丘 1-12号棟513号室"

    def test_住戸ページURLを掲載URLにする(self, scraper: UrScraper, danchi: dict) -> None:
        rooms = _load("rooms_page0.json")
        first = scraper.parse_danchi_rooms(danchi, rooms, prefecture="東京都")[0]
        assert first.url.startswith("https://www.ur-net.go.jp/chintai/kanto/tokyo/")
        assert first.url.endswith("20_2600_room.html?JKSS=001120513")

    def test_住戸が空なら掲載も空になる(self, scraper: UrScraper, danchi: dict) -> None:
        assert scraper.parse_danchi_rooms(danchi, [], prefecture="東京都") == []


class TestParseRoomDetail:
    """③住戸詳細。設備原文と初期費用の合成トークン。"""

    def test_設備原文に辞書が拾う語彙が入る(self, scraper: UrScraper) -> None:
        detail = scraper.parse_room_detail(_load("room_detail.json")[0])
        assert detail.raw_features_text is not None
        for token in ("バス・トイレ別", "追い焚き", "洗濯機置場（室内）", "洗面化粧台"):
            assert token in detail.raw_features_text

    def test_初期費用の有利さを合成トークンで載せる(self, scraper: UrScraper) -> None:
        detail = scraper.parse_room_detail(_load("room_detail.json")[0])
        # rent_total だけで比べると現れないので、既存の条件へ載せる（辞書は無変更）
        assert "礼金なし" in detail.raw_features_text
        assert "更新料なし" in detail.raw_features_text
        assert "仲介手数料なし" in detail.raw_features_text
        # requirement が「ナシ」＝保証人不要
        assert NO_GUARANTOR_TOKEN in detail.raw_features_text

    def test_敷金なしは合成しない(self, scraper: UrScraper) -> None:
        # ⚠ UR も敷金は2か月取られる。ここを足すと誤って加点される
        detail = scraper.parse_room_detail(_load("room_detail.json")[0])
        assert "敷金なし" not in detail.raw_features_text

    def test_保証人が要る住戸には保証人不要を足さない(self, scraper: UrScraper) -> None:
        room = dict(_load("room_detail.json")[0])
        room["requirement"] = "アリ"
        detail = scraper.parse_room_detail(room)
        assert NO_GUARANTOR_TOKEN not in detail.raw_features_text

    def test_築年数は日付にできないので属性へ残す(self, scraper: UrScraper) -> None:
        detail = scraper.parse_room_detail(_load("room_detail.json")[0])
        # ⚠ year は築年月ではなく築年数（51 ＝ 築51年）
        assert detail.built_on is None
        assert detail.type_specific_attrs["ur_age_years"] == 51

    def test_所在階と総階数を1つの欄から読む(self, scraper: UrScraper) -> None:
        # ⚠ ③の floor は "5階 /5階"（所在階/総階数）で②の形と違う
        detail = scraper.parse_room_detail(_load("room_detail.json")[0])
        assert detail.floor_num == 5
        assert detail.total_floors == 5

    def test_共益費は_sp側の欄から読む(self, scraper: UrScraper) -> None:
        # ⚠ ③では commonfee が None で commonfee_sp に入る（②と逆）
        detail = scraper.parse_room_detail(_load("room_detail.json")[0])
        assert detail.mgmt_fee_monthly == 4_500


class TestHooks:
    """任意フックの宣言。scan 側は getattr で見つけて委譲する。"""

    def test_一覧と詳細のフックを宣言している(self, scraper: UrScraper) -> None:
        assert callable(scraper.collect_listings)
        assert callable(scraper.fetch_detail)

    def test_HTML経路は使わないので明示的に失敗する(self, scraper: UrScraper) -> None:
        # 黙って空を返すと「取れているつもり」になるので NotImplementedError にする
        with pytest.raises(NotImplementedError):
            scraper.parse_list("<html></html>")
        with pytest.raises(NotImplementedError):
            scraper.parse_detail("<html></html>")

    def test_robotsは無視しない(self, scraper: UrScraper) -> None:
        # APIホストの robots.txt は 403 だが、フラグは立てず
        # SiteFetcher 側で「記録されたうえでの全許可」にする（→ ADR 0019）
        assert scraper.ignore_robots is False

class TestDiscountedRent:
    """割引適用の住戸（``rent`` が空で ``rent_normal`` に賃料が入る）。

    ⚠⚠ **見落とすと ``price`` が NULL のまま黙って通る。** ``rent_total`` が
    NULL になり MUST が ``unknown`` へ落ちるので、``unknown_policy: keep`` の下では
    **賃料不明の掲載がランキングに並ぶ**。例外にならないので気づけない。
    実際に さいたま市南区の3件がこの形で入り込んだ（2026-09-03）。
    """

    def test_rentが空ならrent_normalを使う(self) -> None:
        room = _load("rooms_discounted.json")[0]
        assert room["rent"] == ""  # フィクスチャがこの形であることを固定する
        assert room_rent(room) == 103_600

    def test_rentがあればそちらを優先する(self) -> None:
        assert room_rent({"rent": "47,200円", "rent_normal": "99,000円"}) == 47_200

    def test_どちらも無ければNone(self) -> None:
        assert room_rent({"rent": "", "rent_normal": None}) is None

    def test_掲載の賃料と敷金が埋まる(self, scraper: UrScraper) -> None:
        danchi = _load("danchi_discounted.json")[0]
        rooms = _load("rooms_discounted.json")
        listings = scraper.parse_danchi_rooms(danchi, rooms, prefecture="埼玉県")
        first = listings[0]
        assert first.price == 103_600
        assert first.mgmt_fee_monthly == 4_000
        # 敷金は賃料の2か月ぶん。賃料が None だとここも NULL になっていた
        assert first.deposit_amount == 103_600 * 2
        assert first.address == "埼玉県さいたま市南区"

    def test_徒歩とバスが並ぶ行から駅徒歩を拾う(self, scraper: UrScraper) -> None:
        """⚠ 行ごとに弾くと本物の駅徒歩16分を捨ててしまう。"""
        danchi = _load("danchi_discounted.json")[0]
        rooms = _load("rooms_discounted.json")
        first = scraper.parse_danchi_rooms(danchi, rooms, prefecture="埼玉県")[0]
        # 「徒歩16～19分 または バス3分 徒歩2～5分」→ バスでない選択肢の下限
        assert first.walk_minutes == 16
        assert first.station_info == "東浦和駅 / 浦和駅"


def test_選択肢が並ぶ行ではバス経由だけを落とす() -> None:
    access = "<li>JR武蔵野線「東浦和」駅 徒歩16～19分 または バス3分 徒歩2～5分</li>"
    stations, walk = parse_access(access)
    assert stations == "東浦和駅"
    # バス側の2分を採ってはいけない（バス停からの徒歩なので駅徒歩ではない）
    assert walk == 16
