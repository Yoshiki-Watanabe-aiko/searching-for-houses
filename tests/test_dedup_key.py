"""名寄せキーのテスト。

回帰データはすべて 2026-09-02 の実DB（301掲載）から採った実物件。
クロスサイト一致5件・課題#13 の同一サイト内重複をそのまま焼き込んでいる。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from house_search.dedup.address import normalize_address
from house_search.dedup.key import (
    FAMILY_CHINTAI,
    FAMILY_KODATE_BUY,
    FAMILY_MANSION_BUY,
    compute_dedup_key,
    dedup_components,
)


def key(address: str, layout: str, area: object, floor: int, prefecture: str | None = None):
    return compute_dedup_key(
        family=FAMILY_CHINTAI,
        address_normalized=normalize_address(address, prefecture),
        layout=layout,
        area_sqm=area,
        floor_num=floor,
    )


# --- クロスサイト一致（実測5件） -----------------------------------------


@pytest.mark.parametrize(
    ("name", "left", "right"),
    [
        (
            "ルミエールＳ＆Ｓ（SUUMO / goo）",
            ("東京都八王子市東中野", "1LDK", "44.99", 1),
            ("東京都八王子市東中野", "1LDK", "44.99", 1),
        ),
        (
            # 建物名が「レオパレスパークソフィア 桜」と「パークソフィア桜」で違う。
            # 建物名をキーに含めていたら一致しない
            "パークソフィア桜（いい部屋ネット / ニフティ）",
            ("東京都足立区新田３丁目", "1K", "19.87", 2),
            ("東京都足立区新田3丁目", "1K", "19.87", 2),
        ),
        (
            "モント竹ノ塚（賃貸EX / スモッカ）",
            ("東京都足立区西保木間１", "1LDK", "23.25", 4),
            ("東京都足立区西保木間１", "1LDK", "23.25", 4),
        ),
        (
            # スモッカは丁目を省く。丁目を補わないと一致しない
            "アルファコート北綾瀬2（いい部屋ネット / スモッカ）",
            ("東京都足立区東和５丁目", "1K", "25.64", 4),
            ("東京都足立区東和５", "1K", "25.64", 4),
        ),
        (
            "エトワール大和田（SUUMO / goo）",
            ("東京都八王子市大和田町１", "1LDK", "36.00", 1),
            ("東京都八王子市大和田町１丁目", "1LDK", "36.00", 1),
        ),
    ],
)
def test_クロスサイトの同一住戸が同じキーになる(name, left, right) -> None:
    assert key(*left) == key(*right), name


def test_匿名掲載でも建物名付き掲載と一致する() -> None:
    # SUUMO は建物名の代わりに「ＪＲ相模線 上溝駅 2階建 築41年」と出すことがある。
    # キーに建物名を含めない設計なので、掲載タイトルが何であれ一致する
    named = key("神奈川県愛甲郡愛川町中津", "2K", "33.08", 1)
    anonymous = key("神奈川県愛甲郡愛川町中津", "2K", "33.08", 1)
    assert named == anonymous


# --- 課題#13: 同一サイト内の重複 -----------------------------------------


def test_取り扱い店舗違いの同一住戸が同じキーになる() -> None:
    # フレンズハイツ: jnc_ が異なる別ページだが住所・間取り・面積・階数が一致
    a = key("埼玉県比企郡川島町大字上伊草", "2DK", "43.00", 1)
    b = key("埼玉県比企郡川島町大字上伊草", "2DK", "43.00", 1)
    assert a == b


def test_棟違いは面積で分かれる() -> None:
    # フレンズハイツ（43.00㎡）とフレンズハイツＡ（40.92㎡）は別棟
    assert key("埼玉県比企郡川島町大字上伊草", "2DK", "43.00", 1) != key(
        "埼玉県比企郡川島町大字上伊草", "2DK", "40.92", 1
    )


def test_同一仕様の別住戸は同じキーに潰れる() -> None:
    """ベル・グラースの事例。**これは仕様**（2026-09-02 ユーザー判断）。

    住所・間取り・面積・階数が同じ別住戸は区別できず1グループに潰れる。
    ユーザーから見て区別のつかない選択肢がランキング枠を食うのを防ぐ狙いで、
    グループには全掲載を残し通知に件数を明示する。
    """
    assert key("埼玉県熊谷市妻沼", "1LDK", "45.09", 1) == key(
        "埼玉県熊谷市妻沼", "1LDK", "45.09", 1
    )


def test_階が違えば別グループになる() -> None:
    # アムールヒルズ: 同住所・同間取り・同面積で1階と2階に分かれる
    assert key("東京都八王子市石川町", "1LDK", "33.17", 1) != key(
        "東京都八王子市石川町", "1LDK", "33.17", 2
    )


# --- 丸め・欠損・ファミリ ------------------------------------------------


def test_面積は丸めない() -> None:
    """実測でクロスサイト一致5件は全て小数第2位まで一致していた。

    丸めても一致は1件も増えず、同一建物の隣接住戸を余分に潰すだけだった
    （ロワールの 38.96㎡ と 38.97㎡ は別住戸）。
    """
    assert key("東京都八王子市七国１", "1LDK", "38.96", 3) != key(
        "東京都八王子市七国１", "1LDK", "38.97", 3
    )
    assert key("東京都八王子市東中野", "1LDK", "44.99", 1) != key(
        "東京都八王子市東中野", "1LDK", "45.00", 1
    )


def test_面積の型が違ってもキーは同じ() -> None:
    values = (25.64, Decimal("25.64"), "25.64")
    keys = {key("東京都足立区東和５丁目", "1K", value, 4) for value in values}
    assert len(keys) == 1


def test_サービスルーム表記を吸収する() -> None:
    # 1SLDK は 1LDK に納戸が付いた形。既存の normalize_layout をそのまま使う
    assert key("東京都足立区東和5丁目", "1SLDK", "40.71", 4) == key(
        "東京都足立区東和5丁目", "1LDK", "40.71", 4
    )


@pytest.mark.parametrize(
    ("layout", "area", "floor"),
    [(None, "30.00", 1), ("1LDK", None, 1), ("1LDK", "30.00", None)],
)
def test_構成要素が欠けたらキーを作らない(layout, area, floor) -> None:
    assert (
        compute_dedup_key(
            family=FAMILY_CHINTAI,
            address_normalized="東京都足立区東和5丁目",
            layout=layout,
            area_sqm=area,
            floor_num=floor,
        )
        is None
    )


def test_住所が解決できなければキーを作らない() -> None:
    assert key("", "1LDK", "30.00", 1) is None


def test_ファミリが違えば別キーになる() -> None:
    """分譲賃貸が「賃貸」と「中古マンション売買」で二重グループ化されるのを防ぐ。"""
    common = {
        "address_normalized": "東京都足立区東和5丁目",
        "layout": "1LDK",
        "area_sqm": "40.71",
        "floor_num": 4,
    }
    assert compute_dedup_key(family=FAMILY_CHINTAI, **common) != compute_dedup_key(
        family=FAMILY_MANSION_BUY, **common
    )


def test_戸建ては土地と建物の2軸で組む() -> None:
    """戸建てに専有面積を流用しない（存在しないため名寄せ事故になる）。"""
    components = dedup_components(
        family=FAMILY_KODATE_BUY,
        address_normalized="埼玉県比企郡川島町上伊草",
        layout="3LDK",
        land_area_sqm="120.00",
        building_area_sqm="84.02",
    )
    assert components == ["v2", "KODATE_BUY", "埼玉県比企郡川島町上伊草", "120.00", "84.02", "3LDK"]
    # 専有面積だけ渡しても戸建てのキーは作れない
    assert (
        compute_dedup_key(
            family=FAMILY_KODATE_BUY,
            address_normalized="埼玉県比企郡川島町上伊草",
            layout="3LDK",
            area_sqm="84.02",
        )
        is None
    )


# --- 既存キーの基準値（→ 課題#61） ----------------------------------------
#
# ⚠ 土地（TOCHI_BUY）の分岐を足すとき、**既存3ファミリのキーが1ビットでも
# 変わると全掲載の名寄せが崩れる**（新旧キーが混在して半端に一致する）。
# 例外にも件数の減少にもならず、グループが静かに割れるだけなので、
# 2026-09-11 の HEAD（65d6e5b）で計算した sha256 をそのまま焼き込んで固定する。
# ⚠ 意図して正規化ルールを変えるときは DEDUP_KEY_VERSION を上げ、ここも作り直す。

_BASELINE_KEYS = [
    (
        "CHINTAI",
        {
            "address_normalized": "東京都足立区東和5丁目",
            "layout": "1K",
            "area_sqm": "25.64",
            "floor_num": 4,
        },
        "ac9ef6550a8c6047c2cf230ca2feb400cd1d0b98655e887abe7eea7e87c8f651",
    ),
    (
        "CHINTAI",
        {
            "address_normalized": "東京都八王子市東中野",
            "layout": "1LDK",
            "area_sqm": "44.99",
            "floor_num": 1,
        },
        "0186ecefbc95b2b82e4199b4c58d78e9257177769a6520d8a9cb373874b45c84",
    ),
    (
        "MANSION_BUY",
        {
            "address_normalized": "東京都千代田区神田多町2丁目",
            "layout": "1LDK",
            "area_sqm": "30.17",
            "floor_num": 3,
        },
        "1fd90fb76951b8d91127259ffe6335d1b6e473a02c18098fccd99bf80dedfde8",
    ),
    (
        "MANSION_BUY",
        {
            "address_normalized": "東京都港区芝浦4丁目",
            "layout": "2LDK",
            "area_sqm": "58.44",
            "floor_num": 12,
        },
        "7fda46dcc7a090cface14fc80d1d5845c229609fe3387a2257d84c1eba2dc0ea",
    ),
    (
        "KODATE_BUY",
        {
            "address_normalized": "埼玉県比企郡川島町上伊草",
            "layout": "3LDK",
            "land_area_sqm": "120.00",
            "building_area_sqm": "84.02",
        },
        "3e2c74c9becdd22eb94338b57d9015cd13a13d28a31f65c50b49eca9eaf2983f",
    ),
    (
        "KODATE_BUY",
        {
            "address_normalized": "東京都八王子市めじろ台2丁目",
            "layout": "4LDK",
            "land_area_sqm": "100.90",
            "building_area_sqm": "79.32",
        },
        "b28e080d448af676f4c2d334671d7d80a4e67c05d1c2fe0c8f85c5ad17a4ec96",
    ),
]


@pytest.mark.parametrize(("family", "kwargs", "expected"), _BASELINE_KEYS)
def test_既存ファミリのキーは基準値から変わらない(family, kwargs, expected) -> None:
    assert compute_dedup_key(family=family, **kwargs) == expected


def test_キーはバージョンタグ付きのsha256() -> None:
    components = dedup_components(
        family=FAMILY_CHINTAI,
        address_normalized="東京都足立区東和5丁目",
        layout="1K",
        area_sqm="25.64",
        floor_num=4,
    )
    assert components is not None
    assert components[0] == "v2"
    value = compute_dedup_key(
        family=FAMILY_CHINTAI,
        address_normalized="東京都足立区東和5丁目",
        layout="1K",
        area_sqm="25.64",
        floor_num=4,
    )
    assert value is not None
    assert len(value) == 64 and set(value) <= set("0123456789abcdef")
