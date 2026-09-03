"""駅マスタの同期と掲載→駅の同定のDB統合テスト。

``DATABASE_TEST_URL`` が未設定のときは ``conftest.py`` の ``test_engine`` が
テストごとスキップする。同定のテストはトランザクションをロールバックするので
テストDBに行を残さない。
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, text

from house_search.commute.normalize import normalize_key
from house_search.commute.resolve import (
    listing_prefecture_codes,
    load_station_index,
    resolve_listing_stations,
    unmatched_station_names,
)
from house_search.commute.stations import StationRow, sync_stations

pytestmark = pytest.mark.db

TOKYO = 13


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _station(station_cd: int, name: str, group_cd: int | None = None) -> StationRow:
    return StationRow(
        station_cd=station_cd,
        station_g_cd=group_cd or station_cd,
        station_name=name,
        station_name_key=normalize_key(name),
        line_cd=99303,
        line_name="都営三田線",
        company_name="東京都交通局",
        pref_cd=TOKYO,
        lon=Decimal("139.749824"),
        lat=Decimal("35.654074"),
    )


def _insert_station(conn: Connection, row: StationRow) -> None:
    conn.execute(
        text(
            """
            INSERT INTO m_stations (
                station_cd, station_g_cd, station_name, station_name_key,
                line_cd, line_name, company_name, pref_cd, lon, lat,
                created_at, updated_at
            ) VALUES (
                :station_cd, :station_g_cd, :station_name, :station_name_key,
                :line_cd, :line_name, :company_name, :pref_cd, :lon, :lat,
                now(), now()
            )
            """
        ),
        {
            "station_cd": row.station_cd,
            "station_g_cd": row.station_g_cd,
            "station_name": row.station_name,
            "station_name_key": row.station_name_key,
            "line_cd": row.line_cd,
            "line_name": row.line_name,
            "company_name": row.company_name,
            "pref_cd": row.pref_cd,
            "lon": row.lon,
            "lat": row.lat,
        },
    )


def _insert_listing(conn: Connection, external_id: str, station_info: str) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    city_id = conn.execute(
        text("SELECT id FROM m_cities WHERE prefecture = '東京都' AND canonical_name = '足立区'")
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, address, prefecture, city_id, station_info,
                status, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id, :url, :title,
                90000, '東京都足立区東和5丁目', '東京都', :city_id, :station_info,
                'active', now(), now(), now(), now()
            )
            RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "property_type_id": property_type_id,
            "external_id": external_id,
            "url": f"https://example.com/{external_id}",
            "title": external_id,
            "city_id": city_id,
            "station_info": station_info,
        },
    ).scalar_one()


def test_駅マスタの同期は冪等でCSVから消えた駅を削除する(test_engine: Engine) -> None:
    rows = (_station(9_900_001, "テスト芝公園"), _station(9_900_002, "テスト三田"))
    try:
        applied, deleted = sync_stations(test_engine, rows)
        assert (applied, deleted) == (2, 0)
        with test_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM m_stations")).scalar() == 2

        # 2回流しても行数は変わらない
        assert sync_stations(test_engine, rows) == (2, 0)
        with test_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM m_stations")).scalar() == 2

        # CSVから消えた駅はDBからも消える
        applied, deleted = sync_stations(test_engine, rows[:1])
        assert (applied, deleted) == (1, 1)
        with test_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM m_stations")).scalar() == 1
    finally:
        with test_engine.begin() as conn:
            conn.execute(text("DELETE FROM m_stations"))


def test_掲載の駅表記を同定して保存する(conn: Connection) -> None:
    _insert_station(conn, _station(9_900_010, "北千住"))
    _insert_station(conn, _station(9_900_011, "小菅"))
    listing_id = _insert_listing(
        conn,
        "commute-1",
        "東京メトロ日比谷線/北千住駅 徒歩15分 / 東武伊勢崎線/小菅駅 徒歩22分",
    )

    index = load_station_index(conn, [TOKYO])
    stats = resolve_listing_stations(conn, index, listing_ids=[listing_id])

    assert stats.listings == 1
    assert stats.with_station == 1
    rows = conn.execute(
        text(
            "SELECT position, raw_station_name, station_g_cd, match_status "
            "FROM t_listing_stations WHERE listing_id = :id ORDER BY position"
        ),
        {"id": listing_id},
    ).all()
    assert [(r[1], r[2], r[3]) for r in rows] == [
        ("北千住", 9_900_010, "matched"),
        ("小菅", 9_900_011, "matched"),
    ]


def test_同定をやり直しても行が増えない(conn: Connection) -> None:
    """掲載単位の DELETE → INSERT で冪等にしている。"""
    _insert_station(conn, _station(9_900_010, "北千住"))
    listing_id = _insert_listing(conn, "commute-2", "日比谷線 北千住駅 徒歩10分")
    index = load_station_index(conn, [TOKYO])

    resolve_listing_stations(conn, index, listing_ids=[listing_id])
    resolve_listing_stations(conn, index, listing_ids=[listing_id])

    count = conn.execute(
        text("SELECT count(*) FROM t_listing_stations WHERE listing_id = :id"),
        {"id": listing_id},
    ).scalar()
    assert count == 1


def test_同定できない表記は理由つきで残る(conn: Connection) -> None:
    _insert_station(conn, _station(9_900_010, "北千住"))
    listing_id = _insert_listing(conn, "commute-3", "上越新幹線/本庄早稲田駅 徒歩13分")
    index = load_station_index(conn, [TOKYO])

    stats = resolve_listing_stations(conn, index, listing_ids=[listing_id])

    assert stats.with_station == 0
    assert stats.per_site[0].unmatched_rows == 1
    assert ("本庄早稲田", 1) in unmatched_station_names(conn)


def test_掲載のある都道府県だけを照合スコープにする(conn: Connection) -> None:
    _insert_listing(conn, "commute-4", "日比谷線 北千住駅 徒歩10分")
    assert TOKYO in listing_prefecture_codes(conn)


def test_駅マスタが空なら索引も空になる(conn: Connection) -> None:
    assert load_station_index(conn, []).by_key == {}


# --- 回帰式に踏み潰された実ダイヤの復旧 -----------------------------------


def _insert_route(
    conn: Connection,
    *,
    origin: int,
    destination: int,
    rank: int,
    total_minutes: int,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO t_navitime_routes (
                origin_station_g_cd, destination_station_g_cd, depart_on, depart_at,
                rank, total_minutes, transfers, distance_km, route_text,
                route_depart_at, route_arrive_at,
                origin_label, destination_label,
                fetched_at, created_at, updated_at
            )
            VALUES (
                :origin, :destination, DATE '2026-09-09', TIME '08:30',
                :rank, :total_minutes, 1, 12.5, 'dummy',
                '08:30', '09:13',
                '出発駅', '到着駅',
                now(), now(), now()
            )
            """
        ),
        {
            "origin": origin,
            "destination": destination,
            "rank": rank,
            "total_minutes": total_minutes,
        },
    )


def test_経路の原文から所要時間を実ダイヤへ戻せる(conn: Connection) -> None:
    """⚠ ``scan`` が回帰式で踏み潰しても、再取得せずに戻せることの回帰テスト。

    実測（2026-09-04）で芝公園ゆき1,155駅すべてが ``rail_graph`` に戻っていた。
    取り直すと4.8時間かかるが、原文に所要時間が残っているので不要。
    """
    from house_search.commute.resolve import save_commutes
    from house_search.commute.timetable import restore_commutes

    origin, destination = 9_999_001, 9_999_002
    # 実ダイヤ。**並び順の1本目が最短とは限らない**ので rank ではなく分で選ぶ
    _insert_route(conn, origin=origin, destination=destination, rank=1, total_minutes=43)
    _insert_route(conn, origin=origin, destination=destination, rank=2, total_minutes=39)
    # 回帰式が上書きしてしまった状態を作る
    save_commutes(conn, destination_g_cd=destination, rows=[(origin, "ok", 55, 2, 20.0)])

    assert restore_commutes(conn, destination_g_cd=destination) == 1

    row = conn.execute(
        text(
            """
            SELECT commute_minutes, source FROM t_station_commutes
             WHERE origin_station_g_cd = :o AND destination_station_g_cd = :d
            """
        ),
        {"o": origin, "d": destination},
    ).one()
    assert row.commute_minutes == 39
    assert row.source == "navitime"
