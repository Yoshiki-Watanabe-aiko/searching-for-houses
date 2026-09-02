"""保存まわり（住所からの市区解決）のテスト。

索引はテーブルから作るのではなく手で組む。市区名の重複という
「どう解決すべきか」の判断だけを固定したいので、実データに依存させない。
"""

from __future__ import annotations

from house_search.pipeline.persist import resolve_city

# ``load_city_index`` と同じ形（都道府県, 正規名, city_id）。
# 実物と同じく正規名の長い順に並べる
INDEX = [
    ("神奈川県", "横浜市西区", 195),
    ("大阪府", "大阪市北区", 400),
    ("東京都", "八王子市", 24),
    ("東京都", "足立区", 21),
    ("東京都", "北区", 17),
    ("新潟県", "北区", 500),
]


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
        views = persist.load_property_views(
            conn, property_type_code="CHINTAI", city_names=["足立区", "本庄市"]
        )
    assert isinstance(views, dict)
