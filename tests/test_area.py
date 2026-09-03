"""検索対象エリア解決のテスト。

``m_cities`` / ``m_city_site_values`` を読むのでテストDBが要る。
``DATABASE_TEST_URL`` が未設定なら conftest がスキップする。
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from house_search.scrape.area import (
    CITY_VALUE_JIS,
    CITY_VALUE_MAPPING,
    AreaTarget,
    resolve_areas,
)

PREFECTURES = ["東京都", "千葉県"]


def _resolve(engine: Engine, **kwargs) -> list[AreaTarget]:
    defaults = {
        "site_code": "ABLE",
        "prefectures": PREFECTURES,
        "cities": [],
        "requires_city": True,
        "city_value_source": CITY_VALUE_JIS,
    }
    with engine.connect() as conn:
        return resolve_areas(conn, **{**defaults, **kwargs})


def test_市区不要のサイトは都道府県単位にまとまる(test_engine: Engine) -> None:
    # 取得URL数を抑えるため、市区を指定していないサイトは都道府県で1本にする
    areas = _resolve(test_engine, site_code="HOMES", requires_city=False)
    assert [a.prefecture for a in areas] == PREFECTURES
    assert all(a.is_prefecture for a in areas)


def test_市区必須のサイトは全市区へ自動展開される(test_engine: Engine) -> None:
    # ABLE・SMOCCA は都道府県だけでは0件になる（課題#1）
    areas = _resolve(test_engine)
    assert len(areas) > 50
    assert all(a.city_name for a in areas)
    assert {a.prefecture for a in areas} == set(PREFECTURES)


def test_JIS系サイトはマッピング未登録の市区も指定できる(test_engine: Engine) -> None:
    # m_city_site_values の行は対象4都県で67件しかないが、
    # JIS コードは m_cities から導けるので市部も対象にできる
    areas = _resolve(test_engine)
    names = {a.city_name for a in areas}
    assert "八王子市" in names
    hachioji = next(a for a in areas if a.city_name == "八王子市")
    assert hachioji.value == "13201"
    assert hachioji.value == hachioji.jis_code


def test_マッピング系サイトは登録済みの市区だけを返す(test_engine: Engine) -> None:
    """⚠ **未登録の市区は黙って落ちる**（エラーにならない → 課題#36）。

    市区ローテーションはこの挙動の上に載るので、スラグが歯抜けだと
    「一巡した」つもりでその市区を永久に取らないことになる。
    落とすこと自体は正しい（指定しようがない）ので、
    **落ちた市区がスラグ未登録のものと一致する**ことを固定する。

    ⚠ 特定の市区名を焼き込まない。Phase 5E で HOMES のスラグを追補し、
    かつて未登録だった八王子市が登録済みになった（→ 課題#36）。
    """
    areas = _resolve(test_engine, site_code="HOMES", city_value_source=CITY_VALUE_MAPPING)
    assert areas
    assert all(a.value and "/" in a.value for a in areas)

    with test_engine.connect() as conn:
        unregistered = {
            name
            for name, in conn.execute(
                text(
                    """
                    SELECT c.canonical_name FROM m_cities c
                    WHERE c.prefecture = ANY(:prefectures)
                      AND NOT EXISTS (
                            SELECT 1 FROM m_city_site_values v
                             WHERE v.city_id = c.id
                               AND v.site_id = (SELECT id FROM m_sites WHERE code = 'HOMES')
                      )
                    """
                ),
                {"prefectures": PREFECTURES},
            )
        }
    assert unregistered, "未登録の市区が1つも無いとこのテストは何も検証できない"
    assert not (unregistered & {a.city_name for a in areas})


def test_市区を明示したらその市区だけになる(test_engine: Engine) -> None:
    areas = _resolve(test_engine, cities=["千代田区", "中央区"])
    assert {a.city_name for a in areas} == {"千代田区", "中央区"}


def test_市区不要のサイトでも市区指定があれば展開する(test_engine: Engine) -> None:
    areas = _resolve(
        test_engine,
        site_code="SUUMO",
        requires_city=False,
        cities=["千代田区"],
    )
    assert [(a.city_name, a.value) for a in areas] == [("千代田区", "13101")]


def test_市区必須のサイトで解決できなければ空を返す(test_engine: Engine) -> None:
    # 都道府県へフォールバックしても0件になるだけなので、
    # 呼び出し側が理由を残してスキップできるよう空のまま返す
    areas = _resolve(test_engine, cities=["存在しない市"])
    assert areas == []


def test_市区不要のサイトは解決できなければ都道府県へ落ちる(test_engine: Engine) -> None:
    areas = _resolve(
        test_engine,
        site_code="HOMES",
        requires_city=False,
        cities=["存在しない市"],
        city_value_source=CITY_VALUE_MAPPING,
    )
    assert [a.prefecture for a in areas] == PREFECTURES
    assert all(a.is_prefecture for a in areas)


def test_同じ条件なら並び順が変わらない(test_engine: Engine) -> None:
    # 一覧URLの順序が実行ごとに変わるとログの突き合わせができなくなる
    first = _resolve(test_engine)
    second = _resolve(test_engine)
    assert [a.value for a in first] == [a.value for a in second]
    assert [a.value for a in first] == sorted(a.value for a in first)


@pytest.mark.parametrize("source", [CITY_VALUE_JIS, CITY_VALUE_MAPPING])
def test_値の無いエリアは返さない(test_engine: Engine, source: str) -> None:
    areas = _resolve(test_engine, site_code="HOMES", city_value_source=source)
    assert all(a.value for a in areas)


def test_行政区を持つ政令市の親行は展開対象から外れる(test_engine: Engine) -> None:
    """マスタは横浜市（14100）と横浜市西区（14103）の両方を持つ。

    親行を外さないと、同じ掲載を市と区で二重に取りに行くことになる。
    全国マスタの投入で政令市の親行が20市ぶん入ったので、ここを外さないと
    神奈川県の取得URLが実際に増える（→ ADR 0014）。
    """
    areas = _resolve(test_engine, prefectures=["神奈川県"], cities=[])
    names = {a.city_name for a in areas}
    assert "横浜市西区" in names
    assert "横浜市" not in names
    assert "川崎市" not in names
    # 政令市でない市はそのまま残る
    assert "藤沢市" in names


def test_政令市名を指定したらその行政区へ展開される(test_engine: Engine) -> None:
    """「横浜市」と書いたときに市そのもの（14100）を送るか区を送るかは
    サイトごとに違って確かめようがないので、確実に引ける区へ寄せる。
    """
    areas = _resolve(test_engine, prefectures=["神奈川県"], cities=["横浜市"])
    names = sorted(a.city_name or "" for a in areas)
    assert names, "横浜市の行政区が1つも返っていない"
    assert all(n.startswith("横浜市") and n != "横浜市" for n in names)
    assert "横浜市西区" in names


def test_全国マスタでも対象外の都道府県は返さない(test_engine: Engine) -> None:
    """マスタを全国化しても、取りに行くのは search.prefectures の範囲だけ。"""
    areas = _resolve(test_engine, prefectures=["東京都"], cities=[])
    assert areas
    assert {a.prefecture for a in areas} == {"東京都"}
