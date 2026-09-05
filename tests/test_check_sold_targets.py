"""成約確認の対象選択のテスト（→ 課題#26）。

⚠ **「一巡に何日かかるか」という指標では実害が見えなかった。**
``last_seen_at`` の古い順だけで選ぶと順位がまったく考慮されず、実測（2026-09-05）で
**東京23区帯の1位・2位がどちらも掲載終了（HTTP 404）のまま**ダイジェストの先頭を
占めていた。平均滞留が3日でも、滞留した掲載がたまたま上位だと影響は桁違いに大きい。
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.pipeline.tasks import select_check_targets

_PATTERN = SimpleNamespace(
    name="対象選択テスト", property_type="CHINTAI", sites=("SUUMO",)
)


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _insert(
    conn: Connection,
    *,
    external_id: str,
    seen_days_ago: int,
    rank: int | None,
    must_result: str = "pass",
    pattern_name: str = _PATTERN.name,
) -> int:
    """掲載と、そのパターンでのスコア行を1組作る。"""
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    listing_id = conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, area_sqm, layout, status,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id, :url, '対象選択テスト',
                80000, 40.0, '2DK', 'active',
                now(), now() - make_interval(days => :days), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "property_type_id": property_type_id,
            "external_id": external_id,
            "url": f"https://example.test/check/{external_id}",
            "days": seen_days_ago,
        },
    ).scalar_one()
    conn.execute(
        text(
            """
            INSERT INTO t_listing_scores (
                listing_id, pattern_name, must_result, score, rank_in_pattern,
                config_hash, scored_at, created_at, updated_at
            ) VALUES (
                :listing_id, :pattern_name, :must_result, 50.0, :rank,
                'testhash', now(), now(), now()
            )
            """
        ),
        {
            "listing_id": listing_id,
            "pattern_name": pattern_name,
            "must_result": must_result,
            "rank": rank,
        },
    )
    return listing_id


def _select(conn: Connection, *, limit: int = 2, top_rank_limit: int = 50) -> list[int]:
    rows = select_check_targets(
        conn, _PATTERN, limit=limit, top_rank_limit=top_rank_limit
    )
    return [row.id for row in rows]


@pytest.fixture(autouse=True)
def _clean(conn: Connection) -> None:
    """他のテストが残した掲載と混ざらないようにする。"""
    conn.execute(
        text("DELETE FROM t_listing_scores WHERE pattern_name = :p"),
        {"p": _PATTERN.name},
    )


def test_上位の掲載は最終確認が新しくても対象になる(conn: Connection) -> None:
    """⚠ **このテストが本修正の主目的**（修正前は落ちる）。

    上位の掲載は毎回の増分スキャンで ``last_seen_at`` が更新されるため、
    「古い順」だけで選ぶと**永久に確認対象にならない**。
    """
    top = _insert(conn, external_id="top-1", seen_days_ago=0, rank=1)
    # 枠（limit=2）を埋めるだけの、順位が無い古い掲載
    _insert(conn, external_id="stale-1", seen_days_ago=10, rank=None)
    _insert(conn, external_id="stale-2", seen_days_ago=9, rank=None)

    assert top in _select(conn, limit=2)


def test_最終確認が古い掲載も従来どおり対象になる(conn: Connection) -> None:
    """上位N位の追加は**和集合**であって、置き換えではない。"""
    _insert(conn, external_id="top-2", seen_days_ago=0, rank=1)
    stale = _insert(conn, external_id="stale-3", seen_days_ago=30, rank=None)

    assert stale in _select(conn, limit=2)


def test_上位かつ最終確認も古い掲載が二重に出ない(conn: Connection) -> None:
    """⚠ 和集合なので重複を除く。数えると確認件数が水増しされる。"""
    both = _insert(conn, external_id="both-1", seen_days_ago=30, rank=1)

    selected = _select(conn, limit=5)

    assert selected.count(both) == 1


def test_順位のある掲載が先に来る(conn: Connection) -> None:
    """途中で打ち切られてもダイジェストに出る範囲が守られるようにするため。"""
    _insert(conn, external_id="order-stale", seen_days_ago=30, rank=None)
    top = _insert(conn, external_id="order-top", seen_days_ago=0, rank=3)

    assert _select(conn, limit=5)[0] == top


def test_上位限度を0にすると従来の挙動へ戻る(conn: Connection) -> None:
    """事故時の逃げ道。⚠ これが無いと切り戻せない。"""
    top = _insert(conn, external_id="off-top", seen_days_ago=0, rank=1)
    _insert(conn, external_id="off-stale-1", seen_days_ago=10, rank=None)
    _insert(conn, external_id="off-stale-2", seen_days_ago=9, rank=None)

    assert top not in _select(conn, limit=2, top_rank_limit=0)


def test_上位限度より下の順位は毎回の対象にならない(conn: Connection) -> None:
    """⚠ 和集合なので、stale 枠を別の掲載で埋めてから確かめる。

    埋めないと「古い順 LIMIT 1」に拾われてしまい、
    **上位由来で選ばれたのか古い順で選ばれたのかを区別できない**。
    """
    low = _insert(conn, external_id="low-rank", seen_days_ago=0, rank=51)
    _insert(conn, external_id="low-filler", seen_days_ago=30, rank=None)

    assert low not in _select(conn, limit=1, top_rank_limit=50)


def test_MUSTがfailの掲載は対象外(conn: Connection) -> None:
    """既存の不変条件。採点対象から外れた掲載を追っても意味がない。"""
    failed = _insert(conn, external_id="fail-1", seen_days_ago=30, rank=1, must_result="fail")

    assert failed not in _select(conn, limit=5)


def test_別パターンで採点された掲載は対象外(conn: Connection) -> None:
    """⚠ エリア帯を絞ると帯外の掲載が「最も古い」になるため、この絞りが要る。"""
    other = _insert(
        conn, external_id="other-pattern", seen_days_ago=30, rank=1, pattern_name="別の帯"
    )
    assert other not in _select(conn, limit=5)
