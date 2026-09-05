"""詳細取得キューの対象選択のテスト（→ 課題#54）。

⚠ **``ORDER BY first_seen_at DESC``（新しい順）だけだと古い掲載に枠が回らない。**
実測（2026-09-06）で SUUMO の詳細未取得 1,342件が 09-03・09-04 のまま滞留し、
**設備0件**（取得済みは平均18.9件）のまま採点されていた。設備の weight は
263点中118点なので、これらは構造的に45%ぶん沈む。
⚠ **例外にならず件数も減らない**（順位が付いてしまうので気づけない）。
近郊60分圏帯では詳細未取得の掲載が **4位**＝ダイジェストに載っていた。

対処は課題#26（成約確認）と同じ**和集合**。古い順の枠を必ず確保する。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.pipeline.persist import detail_queue


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
def site_id(conn: Connection) -> int:
    return conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()


def _insert(
    conn: Connection, *, external_id: str, seen_days_ago: int, fetched: bool = False
) -> int:
    """詳細未取得（または取得済み）の掲載を1件作る。"""
    site = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    ptype = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, area_sqm, layout, status,
                first_seen_at, last_seen_at, detail_fetched_at, created_at, updated_at
            ) VALUES (
                :site, :ptype, :external_id, :url, 'キューテスト',
                80000, 40.0, '2DK', 'active',
                now() - make_interval(days => :days), now(),
                CASE WHEN :fetched THEN now() ELSE NULL END, now(), now()
            ) RETURNING id
            """
        ),
        {
            "site": site,
            "ptype": ptype,
            "external_id": external_id,
            "url": f"https://example.test/{external_id}",
            "days": seen_days_ago,
            "fetched": fetched,
        },
    ).scalar_one()


def test_既定は新しい順に埋める(conn: Connection, site_id: int) -> None:
    """古い滞留が無ければ、従来どおり新しい掲載から取る。"""
    new_ids = [_insert(conn, external_id=f"new{i}", seen_days_ago=i) for i in range(5)]

    got = [row[0] for row in detail_queue(conn, site_id=site_id, limit=3, oldest_limit=0)]

    assert got == new_ids[:3]


def test_古い枠が0なら従来の挙動へ戻る(conn: Connection, site_id: int) -> None:
    """⚠ 事故時の切り戻し口。0 で「新しい順だけ」に戻せることを固定する。"""
    _insert(conn, external_id="old", seen_days_ago=30)
    new_ids = [_insert(conn, external_id=f"new{i}", seen_days_ago=i) for i in range(3)]

    got = [row[0] for row in detail_queue(conn, site_id=site_id, limit=3, oldest_limit=0)]

    assert got == new_ids
    assert len(got) == 3


def test_古い掲載が必ず枠を得る(conn: Connection, site_id: int) -> None:
    """⚠ 本題。新しい掲載が枠を埋め尽くしても古い滞留を必ず削る。"""
    old_ids = [
        _insert(conn, external_id=f"old{i}", seen_days_ago=30 - i) for i in range(3)
    ]
    for i in range(10):
        _insert(conn, external_id=f"new{i}", seen_days_ago=i)

    got = [row[0] for row in detail_queue(conn, site_id=site_id, limit=5, oldest_limit=2)]

    assert len(got) == 5
    # 最も古い2件が入る（3件目の old は入らない）
    assert set(old_ids[:2]) <= set(got)
    assert old_ids[2] not in got


def test_上限を超えない(conn: Connection, site_id: int) -> None:
    """⚠ 上限はリクエスト数なので、和集合でも limit を超えてはいけない。"""
    for i in range(10):
        _insert(conn, external_id=f"old{i}", seen_days_ago=30 - i)
        _insert(conn, external_id=f"new{i}", seen_days_ago=i)

    got = detail_queue(conn, site_id=site_id, limit=4, oldest_limit=3)

    assert len(got) == 4
    assert len({row[0] for row in got}) == 4


def test_古い枠が母集団より大きくても壊れない(conn: Connection, site_id: int) -> None:
    ids = [_insert(conn, external_id=f"x{i}", seen_days_ago=i) for i in range(2)]

    got = [row[0] for row in detail_queue(conn, site_id=site_id, limit=9, oldest_limit=9)]

    assert sorted(got) == sorted(ids)


def test_詳細取得済みは対象外(conn: Connection, site_id: int) -> None:
    """⚠ 古い順に取るようにしても、取得済みを再取得してはいけない。"""
    _insert(conn, external_id="done", seen_days_ago=30, fetched=True)
    pending = _insert(conn, external_id="pending", seen_days_ago=1)

    got = [row[0] for row in detail_queue(conn, site_id=site_id, limit=5, oldest_limit=3)]

    assert got == [pending]


def test_listing_ids_で絞れる(conn: Connection, site_id: int) -> None:
    """UR の3段取得が使う経路。古い枠を足しても絞り込みは効く。"""
    _insert(conn, external_id="old", seen_days_ago=30)
    target = _insert(conn, external_id="target", seen_days_ago=1)

    got = detail_queue(
        conn, site_id=site_id, limit=5, oldest_limit=3, listing_ids=[target]
    )

    assert [row[0] for row in got] == [target]


def test_同着でも順序が決定的(conn: Connection, site_id: int) -> None:
    """⚠ first_seen_at が同値でも実行ごとに順序が揺れないこと。"""
    for i in range(6):
        _insert(conn, external_id=f"same{i}", seen_days_ago=5)

    first = detail_queue(conn, site_id=site_id, limit=4, oldest_limit=2)
    second = detail_queue(conn, site_id=site_id, limit=4, oldest_limit=2)

    assert first == second
