"""駅同定のテスト。

回帰データはすべて 2026-09-03 の実DB（active 10,322掲載）から採った実表記。
サイトごとの癖（全角ＪＲ・会社名の前置・区切り無しの連結・バス停の併記）を
そのまま焼き込んである。
"""

from __future__ import annotations

import pytest

from house_search.commute.matcher import (
    MATCH_AMBIGUOUS,
    MATCH_MATCHED,
    MATCH_UNMATCHED,
    StationIndex,
    candidate_variants,
    extract_station_names,
    match_stations,
)
from house_search.commute.normalize import normalize_key

# 都道府県コード（JIS X 0401）
SAITAMA, CHIBA, TOKYO, KANAGAWA = 11, 12, 13, 14

# テスト用の索引。実マスタから必要な駅だけを抜き出したもの。
# 「浅草」は東京都内に2グループある（メトロ/都営/東武 と つくばエクスプレス）。
# 「小川町」は埼玉と東京にそれぞれあり、都道府県で絞らないと曖昧になる。
_STATIONS: tuple[tuple[str, int, int], ...] = (
    ("八王子", 1_001, TOKYO),
    ("京王八王子", 1_002, TOKYO),
    ("北千住", 1_003, TOKYO),
    ("六町", 1_004, TOKYO),
    ("北綾瀬", 1_005, TOKYO),
    ("青井", 1_006, TOKYO),
    ("竹ノ塚", 1_007, TOKYO),
    ("押上〈スカイツリー前〉", 1_008, TOKYO),
    ("明治神宮前〈原宿〉", 1_009, TOKYO),
    ("鉄道博物館", 1_010, SAITAMA),
    ("川間", 1_011, CHIBA),
    ("千葉", 1_012, CHIBA),
    ("本納", 1_013, CHIBA),
    ("成田空港（第１旅客ターミナル）", 1_014, CHIBA),
    ("元町・中華街", 1_015, KANAGAWA),
    ("上永谷", 1_016, KANAGAWA),
    ("青堀", 1_017, CHIBA),
    ("君津", 1_018, CHIBA),
    ("木更津", 1_019, CHIBA),
    ("指扇", 1_020, SAITAMA),
    ("大宮", 1_021, SAITAMA),
    ("高尾", 1_022, TOKYO),
    ("小川町", 1_023, SAITAMA),
    ("小川町", 1_024, TOKYO),
    ("浅草", 1_025, TOKYO),
    ("浅草", 1_026, TOKYO),
    ("番田", 1_027, KANAGAWA),
)


@pytest.fixture(scope="module")
def index() -> StationIndex:
    return StationIndex.build(
        (normalize_key(name), group_code, pref) for name, group_code, pref in _STATIONS
    )


def matched_codes(info: str, index: StationIndex, pref: int | None = None) -> list[int]:
    return [
        m.station_g_cd for m in match_stations(info, index, pref) if m.match_status == MATCH_MATCHED
    ]


# --- サイトごとの実表記 ---------------------------------------------------


@pytest.mark.parametrize(
    ("site", "info", "pref", "expected"),
    [
        (
            "SUUMO",
            "ＪＲ中央線/八王子駅 バス18分 (バス停)滝山城址 歩2分",
            TOKYO,
            [1_001],
        ),
        (
            "ABLE",
            "東武野田線<アーバンパークライン>/川間駅 バス20分:停歩8分 "
            "東武伊勢崎線・スカイツリーライン/竹ノ塚駅 徒歩10分",
            CHIBA,
            [1_011, 1_007],
        ),
        (
            "APAMAN",
            "ＪＲ総武本線 千葉駅/バス乗車30分/千葉中央バス㈱ 大和田入口/徒歩2分",
            CHIBA,
            [1_012],
        ),
        (
            "HOMES",
            "JR内房線 青堀駅 バス4分 下安知郡下車 徒歩2分 / "
            "JR内房線 君津駅 バス24分 イオンモール富津下車 徒歩17分",
            CHIBA,
            [1_017, 1_018],
        ),
        (
            "EHEYA",
            "みなとみらい線 元町・中華街（山下公園）駅 バス8分 本牧１丁目 徒歩4分",
            KANAGAWA,
            [1_015],
        ),
        (
            "NIFTY",
            "指扇駅 バス8分 歩4分 （川越線） / 大宮駅 バス8分 歩4分 （東北新幹線など）",
            SAITAMA,
            [1_020, 1_021],
        ),
        (
            # goo は路線名と駅名が地続きになり、距離が km 表記になる
            "GOO",
            "ＪＲ外房線本納駅徒歩8000m",
            CHIBA,
            [1_013],
        ),
        (
            # スモッカは「駅」が付かず区切りも無い（第2パス）
            "SMOCCA",
            "東京地下鉄千代田線/北綾瀬 徒歩8分つくばエクスプレス/六町 徒歩14分"
            "つくばエクスプレス/青井 徒歩16分",
            TOKYO,
            [1_005, 1_004, 1_006],
        ),
        (
            # 賃貸EX も「駅」なし。バス停が「から」で続く
            "CHINTAI_EX",
            "高尾 バス6分 元八王子2丁目バス停から徒歩4分八王子 バス28分 "
            "城山手バス停から徒歩5分京王八王子 バス30分",
            TOKYO,
            [1_022, 1_001, 1_002],
        ),
    ],
)
def test_サイトごとの実表記から駅を同定できる(
    site: str, info: str, pref: int, expected: list[int], index: StationIndex
) -> None:
    assert matched_codes(info, index, pref) == expected, site


# --- バス停を駅として拾わない ---------------------------------------------


@pytest.mark.parametrize(
    ("info", "pref"),
    [
        # 「(バス停)一之江五丁目」「(バス停)栄町（神奈川県）」のような紛らわしい名前
        ("ＪＲ中央線/八王子駅 バス18分 (バス停)栄町（神奈川県） 歩2分", TOKYO),
        # APAMAN のバス会社＋バス停
        ("ＪＲ総武本線 千葉駅/バス乗車30分/小湊バス㈱ 千葉県がんセンター/徒歩20分", CHIBA),
        # goo の「」つきバス停
        ("ＪＲ中央線 八王子駅 バス25分/「品の木・ハイランドホテル前」バス停 停歩9分", TOKYO),
        # いい部屋ネットのバス停（バス分数と徒歩の間に挟まる）
        ("ブルーライン 上永谷駅 バス9分 神奈川中央交通バス吉原小学校前 徒歩6分", KANAGAWA),
    ],
)
def test_バス停の名前を駅として拾わない(info: str, pref: int, index: StationIndex) -> None:
    with_suffix, _ = extract_station_names(info)
    matches = match_stations(info, index, pref)
    # 「◯◯駅」で拾えた候補以外が混ざっていないこと
    assert [m.raw_name for m in matches if m.match_status == MATCH_MATCHED] == list(with_suffix)


# --- 表記ゆれの吸収 -------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "expected_code"),
    [
        ("東武伊勢崎線 押上駅 徒歩5分", 1_008),  # マスタ側に副名称〈スカイツリー前〉
        ("東京メトロ千代田線 明治神宮前駅 徒歩3分", 1_009),  # 同上〈原宿〉
        ("京成本線 成田空港駅 徒歩5分", 1_014),  # マスタ側に（第１旅客ターミナル）
        ("みなとみらい線 元町中華街駅 徒歩4分", 1_015),  # 掲載側に中黒が無い
        ("ＪＲ中央線/八王子駅 歩4分", 1_001),  # 全角ＪＲ
    ],
)
def test_副名称や中黒の違いを吸収して同定する(
    info: str, expected_code: int, index: StationIndex
) -> None:
    assert matched_codes(info, index) == [expected_code]


def test_路線名を剥がす規則が実在の駅名を壊さない(index: StationIndex) -> None:
    """「鉄道博物館」は路線名の接頭辞を剥がす規則に引っかかるが、原文を先に試すので壊れない。"""
    assert candidate_variants("鉄道博物館")[0] == "鉄道博物館"
    assert matched_codes("ニューシャトル 鉄道博物館駅 徒歩5分", index, SAITAMA) == [1_010]


# --- 都道府県スコープ -----------------------------------------------------


def test_都道府県で絞ると同名駅の曖昧さが解ける(index: StationIndex) -> None:
    """「小川町」は埼玉（東武東上線）と東京（都営新宿線）にある。"""
    assert matched_codes("東武東上線/小川町駅 徒歩3分", index, SAITAMA) == [1_023]
    assert matched_codes("都営新宿線/小川町駅 徒歩3分", index, TOKYO) == [1_024]


def test_都道府県が不明なら同名駅は曖昧のままにする(index: StationIndex) -> None:
    matches = match_stations("小川町駅 徒歩3分", index, None)
    assert [m.match_status for m in matches] == [MATCH_AMBIGUOUS]
    assert matches[0].station_g_cd is None


def test_同一都道府県内で割れる駅は曖昧にする(index: StationIndex) -> None:
    """浅草は東京都内に2グループある（東武ほか / つくばエクスプレス）。

    路線名でも座標でも絞らない。実測で ambiguous は 10,322掲載中23件しかなく、
    大半は同じ掲載の別の駅で補える（→ ADR 0016）。
    """
    matches = match_stations("東武伊勢崎線 浅草駅 徒歩8分", index, TOKYO)
    assert [m.match_status for m in matches] == [MATCH_AMBIGUOUS]


def test_県外の駅は同定できないと記録する(index: StationIndex) -> None:
    matches = match_stations("上越新幹線/本庄早稲田駅 徒歩13分", index, SAITAMA)
    assert [(m.raw_name, m.match_status) for m in matches] == [
        ("本庄早稲田", MATCH_UNMATCHED)
    ]


# --- 第2パスの安全側の扱い ------------------------------------------------


def test_駅の接尾辞が無い候補はマスタに当たったものだけ採る(index: StationIndex) -> None:
    """アンカーが無い第2パスは推測なので、外れた候補を記録に残さない。"""
    matches = match_stations("愛川・高峰ルート/半原小学校入口 歩7分", index, KANAGAWA)
    assert matches == ()


def test_駅の接尾辞がある候補は外れても記録する(index: StationIndex) -> None:
    """「◯◯駅」で拾えた候補は、同定に失敗しても規則を直す材料として残す。"""
    matches = match_stations("秩父鉄道/広瀬川原駅 徒歩5分", index, SAITAMA)
    assert [(m.raw_name, m.match_status) for m in matches] == [("広瀬川原", MATCH_UNMATCHED)]


def test_駅情報が空なら何も返さない(index: StationIndex) -> None:
    assert match_stations(None, index) == ()
    assert match_stations("   ", index) == ()


def test_同じ駅が複数回出ても1件にまとめる(index: StationIndex) -> None:
    info = (
        "東武伊勢崎線/竹ノ塚駅 歩15分 / 東武伊勢崎線/竹ノ塚駅 バス5分 (バス停)弁天町 歩3分"
    )
    assert matched_codes(info, index, TOKYO) == [1_007]


# --- 照合キーの正規化 -----------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("押上〈スカイツリー前〉", "押上駅"),
        ("成田空港（第１旅客ターミナル）", "成田空港"),
        ("霞ヶ関", "霞ケ関"),  # 小書き仮名（鎌ケ谷市で踏んだのと同じ揺れ）
        ("一之江", "一ノ江"),
        ("元町・中華街", "元町中華街"),
        ("ＪＲ相模線", "JR相模線"),  # 全角英字
        ("センター北", "センタ-北"),  # 長音とハイフン
    ],
)
def test_表記が違っても同じ照合キーになる(left: str, right: str) -> None:
    assert normalize_key(left) == normalize_key(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("八王子", "京王八王子"),
        ("大宮", "西大宮"),
        ("日ノ出", "日ノ出町"),
    ],
)
def test_別の駅は別の照合キーになる(left: str, right: str) -> None:
    assert normalize_key(left) != normalize_key(right)
