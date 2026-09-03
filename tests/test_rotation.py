"""市区ローテーションのテスト（→ 課題#36・Phase 5E）。

取得数に上限があるサイト（HOMES 5・ATHOME 4リクエスト）を、
1回の実行では上限ぶんの市区だけ取り、次回は続きから回す仕組み。

前半（``rotate_areas`` / ``next_cursor``）はDBに触らない純関数のテスト。
後半はカーソル表の排他をDBで確かめる（``DATABASE_TEST_URL`` 設定時のみ）。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from house_search.pipeline import persist
from house_search.scrape.area import AreaTarget
from house_search.scrape.rotation import next_cursor, rotate_areas

# ``resolve_areas`` と同じく JIS5桁の昇順。実データの並びを模した5市区。
AREAS = [
    AreaTarget(prefecture="東京都", city_name="千代田区", jis_code="13101", value="a"),
    AreaTarget(prefecture="東京都", city_name="中央区", jis_code="13102", value="b"),
    AreaTarget(prefecture="東京都", city_name="港区", jis_code="13103", value="c"),
    AreaTarget(prefecture="東京都", city_name="新宿区", jis_code="13104", value="d"),
    AreaTarget(prefecture="東京都", city_name="文京区", jis_code="13105", value="e"),
]


def _names(areas: list[AreaTarget]) -> list[str | None]:
    return [area.city_name for area in areas]


def test_カーソルが無ければ先頭から切り出す() -> None:
    assert _names(rotate_areas(AREAS, last_city_jis=None, size=2)) == ["千代田区", "中央区"]


def test_カーソルの次の市区から切り出す() -> None:
    rotated = rotate_areas(AREAS, last_city_jis="13102", size=2)
    assert _names(rotated) == ["港区", "新宿区"]


def test_末尾に達したら先頭へ戻る() -> None:
    """周回できないと後ろの市区で止まり、先頭の市区が二度と更新されない。"""
    rotated = rotate_areas(AREAS, last_city_jis="13104", size=3)
    assert _names(rotated) == ["文京区", "千代田区", "中央区"]


def test_カーソルの市区がYAMLから外れても次へ進む() -> None:
    """カーソルは位置番号でなくJIS5桁で持つので、市区リストの増減でずれない。

    課題#32 で実際に4市区を外している。「13102 の次」を探すので、
    13102 自体がリストに無くても 13103 から再開できる。
    """
    without_chuo = [area for area in AREAS if area.jis_code != "13102"]
    assert _names(rotate_areas(without_chuo, last_city_jis="13102", size=2)) == [
        "港区",
        "新宿区",
    ]


def test_カーソルが最大値より大きければ先頭へ戻る() -> None:
    # 帯から市区を外してカーソルだけ取り残されたときも止まらない
    assert _names(rotate_areas(AREAS, last_city_jis="99999", size=2)) == ["千代田区", "中央区"]


def test_市区数が上限以下ならすべて返す() -> None:
    assert rotate_areas(AREAS, last_city_jis="13103", size=5) == AREAS


def test_都道府県単位のエリアはローテーションしない() -> None:
    """``search.cities`` が空だと都道府県1本のURLになり、そもそも上限に収まる。

    ここで無理に絞ると逆に取得できなくなる。
    """
    prefectures = [AreaTarget(prefecture="東京都"), AreaTarget(prefecture="千葉県")]
    assert rotate_areas(prefectures, last_city_jis=None, size=1) == prefectures


def test_件数指定が0以下なら例外にする() -> None:
    with pytest.raises(ValueError, match="1以上"):
        rotate_areas(AREAS, last_city_jis=None, size=0)


def test_次回のカーソルは切り出した区間の末尾() -> None:
    rotated = rotate_areas(AREAS, last_city_jis=None, size=3)
    assert next_cursor(rotated) == "13103"
    assert next_cursor([]) is None


def test_一巡すると全市区をちょうど1回ずつ通る() -> None:
    """5市区を2件ずつ回したとき、3回で全市区を覆えることを固定する。

    ⚠ 周回の途中で同じ市区ばかり引くと「一巡した」ことにならない。
    """
    cursor: str | None = None
    visited: list[str | None] = []
    for _ in range(3):
        rotated = rotate_areas(AREAS, last_city_jis=cursor, size=2)
        visited.extend(_names(rotated))
        cursor = next_cursor(rotated)
    # 3周目で先頭2件を再訪するので、5市区すべてを最低1回は通っている
    assert set(visited) == {area.city_name for area in AREAS}


# --- カーソル表（DB統合。DATABASE_TEST_URL 未設定ならスキップ） ---------------


@pytest.fixture()
def homes_site_id(test_engine) -> int:
    with test_engine.connect() as conn:
        site_id = conn.execute(
            text("SELECT id FROM m_sites WHERE code = 'HOMES'")
        ).scalar_one()
    with test_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM t_site_scan_cursors WHERE site_id = :id"), {"id": site_id}
        )
    yield int(site_id)
    with test_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM t_site_scan_cursors WHERE site_id = :id"), {"id": site_id}
        )


def test_取得数に上限があるサイトだけが回転量を宣言している() -> None:
    """⚠ 「実装済みだが未配線」を防ぐ。

    回転量はアダプタのクラス属性で宣言し、``scan`` は ``getattr`` で拾う。
    宣言が消えるとローテーションは黙って止まり、先頭の市区だけを毎回取り続ける
    （エラーにならないので気づけない）。
    """
    from house_search.scrape import SCRAPERS

    declared = {
        code: getattr(scraper, "city_rotation_limit", None)
        for code, scraper in SCRAPERS.items()
        if getattr(scraper, "city_rotation_limit", None)
    }
    assert declared == {"HOMES": 5, "ATHOME": 4}


def test_同じ実行では1つのパターンだけが取得枠を使う(test_engine, homes_site_id) -> None:
    """⚠ 帯が2つあるので、素朴に実装すると予算が2倍消費される。

    HOMES は両帯の ``sites:`` に載っており、1回の ``scan`` で 5+5=10 リクエストが
    飛ぶと後半の帯が全部 HTTP 202 になる。
    """
    run_id = uuid.uuid4()
    with test_engine.begin() as conn:
        first = persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯1", run_id=run_id
        )
        second = persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯2", run_id=run_id
        )
    assert first.claimed is True
    assert second.claimed is False


def test_次の実行では別のパターンへ枠が回る(test_engine, homes_site_id) -> None:
    """``last_scanned_at`` の古い帯から順に回す（未実行が最優先）。

    ``scan`` は1プロセスで全パターンを回すので、1回目の実行で両方の行が登録される
    （枠を取れなかった帯も行だけは作られる）。
    """
    first_run = uuid.uuid4()
    with test_engine.begin() as conn:
        assert persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯1", run_id=first_run
        ).claimed
        assert not persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯2", run_id=first_run
        ).claimed
    with test_engine.begin() as conn:
        # 2回目の実行。帯1 が先に処理されても、未実行の帯2 へ譲る
        assert not persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯1", run_id=uuid.uuid4()
        ).claimed
    with test_engine.begin() as conn:
        assert persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯2", run_id=uuid.uuid4()
        ).claimed


def test_カーソルを進めると次回はそこから再開する(test_engine, homes_site_id) -> None:
    with test_engine.begin() as conn:
        persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯1", run_id=uuid.uuid4()
        )
        persist.advance_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯1", last_city_jis="13105"
        )
    with test_engine.begin() as conn:
        claim = persist.claim_city_rotation(
            conn, site_id=homes_site_id, pattern_name="帯1", run_id=uuid.uuid4()
        )
    assert claim.claimed is True
    assert claim.last_city_jis == "13105"


def test_パターンが1つだけなら毎回そのパターンが回る(test_engine, homes_site_id) -> None:
    for _ in range(3):
        with test_engine.begin() as conn:
            assert persist.claim_city_rotation(
                conn, site_id=homes_site_id, pattern_name="唯一の帯", run_id=uuid.uuid4()
            ).claimed
