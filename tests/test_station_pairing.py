"""徒歩と通勤を「同じ駅」から採ることの回帰テスト（→ 課題#58）。

⚠⚠ 別々に最小化すると「徒歩4分（A駅）＋通勤30分（B駅）」という**実在しない
組み合わせ**で採点される。実測（2026-09-07）で順位付きの掲載の**約6割**が
これに当たり、その大半で**実際より良いスコア**が付いていた。
⚠ 例外にも件数の変化にもならないので、この形を固定しておかないと黙って戻る。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, text

from house_search.pipeline import persist
from house_search.scoring.listing_view import ListingView

# 検証用の駅グループコード。⚠ m_stations には入れない（同定は済んだ前提で
# t_listing_stations へ直接入れる）。実在のコードとぶつからない値を使う。
_NEAR = 990001  # 徒歩は近いが通勤は遠い
_FAR = 990002  # 徒歩は遠いが通勤は近い
_BUS = 990003  # バス便（徒歩不明）だが通勤は最短
_DEST = 990009


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _insert_listing(conn: Connection, *, external_id: str) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, area_sqm, layout, walk_minutes, address, prefecture,
                status, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id, :url, '駅ペアテスト',
                90000, 30.0, '1LDK', 99, '東京都足立区東和5-1-1', '東京都',
                'active', now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "property_type_id": property_type_id,
            "external_id": external_id,
            "url": f"https://example.test/pair/{external_id}",
        },
    ).scalar_one()


def _add_station(
    conn: Connection,
    listing_id: int,
    *,
    position: int,
    g_cd: int,
    name: str,
    walk: int | None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO t_listing_stations (
                listing_id, position, raw_station_name, station_g_cd, match_status,
                walk_minutes, created_at, updated_at
            ) VALUES (
                :listing_id, :position, :name, :g_cd, 'matched', :walk, now(), now()
            )
            """
        ),
        {
            "listing_id": listing_id,
            "position": position,
            "name": name,
            "g_cd": g_cd,
            "walk": walk,
        },
    )


def _add_commute(conn: Connection, g_cd: int, minutes: int) -> None:
    conn.execute(
        text(
            """
            INSERT INTO t_station_commutes (
                origin_station_g_cd, destination_station_g_cd, commute_minutes,
                transfers, status, source, computed_at, created_at, updated_at
            ) VALUES (
                :origin, :dest, :minutes, 0, 'ok', 'navitime', now(), now(), now()
            )
            ON CONFLICT (origin_station_g_cd, destination_station_g_cd) DO UPDATE
              SET commute_minutes = EXCLUDED.commute_minutes, status = 'ok'
            """
        ),
        {"origin": g_cd, "dest": _DEST, "minutes": minutes},
    )


def _load(conn: Connection, listing_id: int) -> ListingView:
    return persist.load_listing_views(
        conn, listing_ids=[listing_id], commute_destination_g_cd=_DEST
    )[listing_id]


def test_徒歩と通勤は同じ駅から採る(conn: Connection) -> None:
    """⚠ 徒歩4分（近い駅）＋通勤30分（遠い駅）という組み合わせを作らない。"""
    listing_id = _insert_listing(conn, external_id="pair-both")
    _add_station(conn, listing_id, position=0, g_cd=_NEAR, name="近い駅", walk=4)
    _add_station(conn, listing_id, position=1, g_cd=_FAR, name="遠い駅", walk=20)
    _add_commute(conn, _NEAR, 50)  # 4 + 50 = 54
    _add_commute(conn, _FAR, 30)  # 20 + 30 = 50 ← こちらが door-to-door 最小

    view = _load(conn, listing_id)
    assert (view.walk_minutes, view.commute_minutes) == (20, 30)


def test_合計が同じなら徒歩の短い駅を採る(conn: Connection) -> None:
    """同点で順位が実行ごとに揺れないよう、決定的に選ぶ。"""
    listing_id = _insert_listing(conn, external_id="pair-tie")
    _add_station(conn, listing_id, position=0, g_cd=_NEAR, name="近い駅", walk=10)
    _add_station(conn, listing_id, position=1, g_cd=_FAR, name="遠い駅", walk=20)
    _add_commute(conn, _NEAR, 40)  # 50
    _add_commute(conn, _FAR, 30)  # 50

    view = _load(conn, listing_id)
    assert (view.walk_minutes, view.commute_minutes) == (10, 40)


def test_徒歩不明の駅は通勤の候補にしない(conn: Connection) -> None:
    """⚠ 歩けない駅からの通勤時間を採ると、実際には行けない経路で採点される。"""
    listing_id = _insert_listing(conn, external_id="pair-bus")
    _add_station(conn, listing_id, position=0, g_cd=_NEAR, name="歩ける駅", walk=15)
    _add_station(conn, listing_id, position=1, g_cd=_BUS, name="バス便の駅", walk=None)
    _add_commute(conn, _NEAR, 50)
    _add_commute(conn, _BUS, 30)  # 最短だが歩けない

    view = _load(conn, listing_id)
    assert (view.walk_minutes, view.commute_minutes) == (15, 50)


def test_徒歩が取れる駅が無ければ通勤だけ採る(conn: Connection) -> None:
    """⚠ フォールバック。通勤時間まで捨てると情報が減る（実測で77・224件）。"""
    listing_id = _insert_listing(conn, external_id="pair-nowalk")
    _add_station(conn, listing_id, position=0, g_cd=_BUS, name="バス便の駅", walk=None)
    _add_commute(conn, _BUS, 30)

    view = _load(conn, listing_id)
    assert view.walk_minutes is None
    assert view.commute_minutes == 30


def test_通勤が取れなければ徒歩だけ採る(conn: Connection) -> None:
    """目的地を設定していないパターン・未取得の駅でも徒歩は出す。"""
    listing_id = _insert_listing(conn, external_id="pair-nocommute")
    _add_station(conn, listing_id, position=0, g_cd=_NEAR, name="近い駅", walk=7)

    view = _load(conn, listing_id)
    assert view.walk_minutes == 7
    assert view.commute_minutes is None


def test_交通欄には候補外の駅も出す(conn: Connection) -> None:
    """⚠ 採点に使わない駅も**表示には出す**（どの駅が何分か読めるようにするため）。"""
    listing_id = _insert_listing(conn, external_id="pair-display")
    _add_station(conn, listing_id, position=0, g_cd=_NEAR, name="近い駅", walk=4)
    _add_station(conn, listing_id, position=1, g_cd=_BUS, name="バス便の駅", walk=None)
    _add_commute(conn, _NEAR, 50)
    _add_commute(conn, _BUS, 30)

    view = _load(conn, listing_id)
    names = {s.name for s in view.stations}
    assert names == {"近い駅", "バス便の駅"}
