"""保存まわり（住所からの市区解決・詳細の書き戻し）のテスト。

索引はテーブルから作るのではなく手で組む。市区名の重複という
「どう解決すべきか」の判断だけを固定したいので、実データに依存させない。
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from house_search.pipeline import persist
from house_search.pipeline.persist import CityIndex, resolve_city
from house_search.scrape.base import ScrapedDetail

# ``load_city_index`` と同じ形（都道府県, 正規名, city_id）。
# 実物と同じく正規名の長い順に並べる
ROWS = [
    ("神奈川県", "横浜市西区", 195),
    ("大阪府", "大阪市北区", 400),
    ("東京都", "八王子市", 24),
    ("東京都", "足立区", 21),
    ("東京都", "北区", 17),
    ("新潟県", "北区", 500),
]
INDEX = CityIndex.build(ROWS)


def test_都道府県から始まる住所を解決する() -> None:
    assert resolve_city("東京都足立区竹の塚６", INDEX) == ("東京都", 21)


def test_長い市区名を優先して部分一致の取り違えを防ぐ() -> None:
    # 「横浜市西区」を「西区」で拾わないための並び順が効いていることの確認
    assert resolve_city("神奈川県横浜市西区みなとみらい", INDEX) == ("神奈川県", 195)


def test_都道府県が無い住所も一意な市区名なら解決する() -> None:
    # 賃貸EX の圧縮レイアウトは「足立区竹の塚６」と都道府県を書かない
    assert resolve_city("足立区竹の塚６", INDEX) == ("東京都", 21)


def test_複数県にある市区名は解決しない() -> None:
    # 「北区」は東京都と新潟県の両方にある。取り違えると誤った市区に紐づく
    assert resolve_city("北区赤羽１", INDEX) == (None, None)


def test_都道府県だけ判る住所は市区をNoneにする() -> None:
    assert resolve_city("東京都のどこか", INDEX) == ("東京都", None)


def test_住所が無ければ何も返さない() -> None:
    assert resolve_city(None, INDEX) == (None, None)
    assert resolve_city("", INDEX) == (None, None)


def test_エリア帯で絞る採点クエリが実行できる(test_engine) -> None:
    """``city_names`` を渡したときのSQLが壊れていないことを固定する。

    エリア帯は取得URLを絞るだけなので、採点側でも閉じないと帯外の既存データに
    帯のスコアが付き、23区のランキングが群馬県境の掲載で埋まる。
    絞り込みの効き目そのものは実データで確認する（掲載0件でも構文は検証できる）。
    """
    from house_search.pipeline import persist

    with test_engine.connect() as conn:
        views = persist.load_listing_views(
            conn, property_type_code="CHINTAI", city_names=["足立区", "本庄市"]
        )
    assert isinstance(views, dict)


@pytest.mark.parametrize(
    "address",
    ["千葉県鎌ヶ谷市丸山１", "千葉県鎌ケ谷市丸山１"],
)
def test_小書き仮名の表記ゆれを吸収して市区を解決する(address: str) -> None:
    """``m_cities`` は「鎌ケ谷市」だがサイトの住所は「鎌ヶ谷市」で来る。

    NFKC 正規化ではこの2文字は区別されるため、そのまま照合すると
    city_id が NULL のまま残る。実測（2026-09-02）で SUUMO の新規485件中
    34件がこれで落ち、**帯に属さない掲載が両方の帯に採点された**。
    """
    index = CityIndex.build([("千葉県", "鎌ケ谷市", 12224)])
    assert resolve_city(address, index) == ("千葉県", 12224)


# 全国マスタでは市区名が全国一意でなくなる。「府中市」は東京都と広島県、
# 「伊達市」は北海道と福島県にある。都道府県を前置しない住所（賃貸EX 形式）を
# 引き当てるには、検索パターンの対象都道府県まで範囲を絞る必要がある。
NATIONWIDE = [
    ("東京都", "府中市", 30),
    ("広島県", "府中市", 900),
    ("東京都", "八王子市", 24),
]


def test_検索範囲を絞れば衝突する市区名でも解決できる() -> None:
    index = CityIndex.build(NATIONWIDE, search_prefectures=["東京都"])
    assert resolve_city("府中市白糸台２", index) == ("東京都", 30)


def test_検索範囲に衝突が残るときは解決しない() -> None:
    """取り違えるくらいなら未解決にする（名寄せの偽陽性より害が大きい）。"""
    index = CityIndex.build(NATIONWIDE, search_prefectures=["東京都", "広島県"])
    assert resolve_city("府中市白糸台２", index) == (None, None)


def test_範囲を絞っても都道府県付きの住所は全国から引ける() -> None:
    """対象外の県の掲載がサイトから返ってくることは普通にあり、落とす理由がない。"""
    index = CityIndex.build(NATIONWIDE, search_prefectures=["東京都"])
    assert resolve_city("広島県府中市府川町", index) == ("広島県", 900)


def test_scoped_toで範囲だけを差し替えられる() -> None:
    """Runtime は実行ごとに1つだが、対象都道府県は検索パターンごとに違う。"""
    index = CityIndex.build(NATIONWIDE)
    assert resolve_city("府中市白糸台２", index) == (None, None)
    assert resolve_city("府中市白糸台２", index.scoped_to(["東京都"])) == ("東京都", 30)


def test_サイトの既存掲載数を数えられる(test_engine) -> None:
    """「一覧0件」が異常かどうかの判断材料（→ 課題#29）。

    過去に1件も取れていないサイトなら0件は正常でありうるが、実績のあるサイトの
    0件は取得が壊れた疑いが濃い。⚠ 0件は例外にならないので、こう突き合わせないと
    「取れているつもり」のまま気づけない。
    """
    from house_search.pipeline import persist

    with test_engine.connect() as conn:
        known = persist.site_listing_count(conn, site_id=1)
        missing = persist.site_listing_count(conn, site_id=-1)
    assert isinstance(known, int)
    assert known >= 0
    assert missing == 0


# 町名が他県の自治体名と同じ住所。都道府県が前置されているのに第3パス
# （範囲内で一意な市区名の部分一致）へ落ちると、別の県の自治体として解決される。
NAMESAKE_ROWS = [
    ("東京都", "立川市", 25),
    ("東京都", "小平市", 176),
    ("長野県", "富士見町", 900),
    ("埼玉県", "小川町", 901),
    ("埼玉県", "加須市", 138),
]
NAMESAKE_INDEX = CityIndex.build(NAMESAKE_ROWS)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("東京都立川市富士見町4丁目", ("東京都", 25)),
        ("東京都小平市小川町1丁目", ("東京都", 176)),
        ("埼玉県加須市富士見町", ("埼玉県", 138)),
    ],
)
def test_町名が他県の自治体名と同じでも前置の都道府県を優先する(
    address: str, expected: tuple[str, int]
) -> None:
    """⚠ 実データで `長野県東京都立川市…` という住所ができていた（→ 課題#48）。

    住所に都道府県が書いてあるなら、それより弱い手がかり（一意な市区名の部分一致）
    に頼ってはいけない。
    """
    assert resolve_city(address, NAMESAKE_INDEX) == expected


# ``ScrapedDetail`` にあるが ``t_listings`` の列ではない項目。
# ⚠ **ここへ足すのは「列が無い」ことを確かめてから**。安易に足すと、
# 保存漏れを検出するというこのテストの目的そのものが空洞になる。
_NOT_COLUMNS = frozenset(
    {
        # 未知表記の収集元を差し替えるためだけの値（→ 課題#19）。列は無い
        "unknown_token_text",
    }
)


def test_詳細の全項目が保存対象になっている() -> None:
    """⚠⚠ **``ScrapedDetail`` に足しただけでは保存されない。**

    実際に ``repair_reserve_monthly`` が「型には足したが ``save_detail`` の
    UPDATE 文に無い」状態で入っていた（→ 課題#4 手順4）。⚠ この漏れは
    **例外にならず件数も減らない**——列が NULL のまま残るだけなので、
    ``monthly_cost`` metric が永久に欠損して静かに採点から外れる。

    ⚠ ``detail_url`` の実装漏れ（→ 課題#37）と同じで、**フィクスチャテストは
    パーサしか呼ばないので緑のまま通る**。ここで機械的に突き合わせる。
    """
    source = inspect.getsource(persist.save_detail)
    missing = [
        f.name
        for f in dataclasses.fields(ScrapedDetail)
        if f.name not in _NOT_COLUMNS and f":{f.name}" not in source
    ]
    assert not missing, f"save_detail が保存していない項目: {missing}"
