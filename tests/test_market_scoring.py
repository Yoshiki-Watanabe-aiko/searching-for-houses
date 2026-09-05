"""相場との比較（``market_rate_ratio``）が採点まで届くかのテスト（→ 課題#49）。

⚠ **「実装済みだが未配線」を防ぐのが主目的。** metric を定義しても
``load_listing_views`` が値を運ばなければ、**全件 missing のまま再正規化されて
正常終了する**（例外にも件数の減少にもならない）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.pipeline import persist
from house_search.scoring.listing_view import ListingView

_CITY_JIS = "13121"  # 足立区


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _city_id(conn: Connection) -> int:
    return conn.execute(
        text("SELECT id FROM m_cities WHERE jis_code = :jis"), {"jis": _CITY_JIS}
    ).scalar_one()


def _seed_rate(conn: Connection, *, segment: str, value: int, period: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO m_market_rates (
                family, source, level, city_id, segment, stat_basis,
                rate_value, sample_count, period, acquired_on, created_at, updated_at
            ) VALUES (
                'CHINTAI', 'test', 'city', :city_id, :segment, 'rent_listed_mansion',
                :value, NULL, :period, DATE '2026-09-05', now(), now()
            )
            """
        ),
        {"city_id": _city_id(conn), "segment": segment, "value": value, "period": period},
    )


def _insert_listing(conn: Connection, *, external_id: str, layout: str, price: int) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, area_sqm, layout, address, prefecture, city_id,
                status, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id, :url, '相場テスト',
                :price, 30.0, :layout, '東京都足立区東和5丁目', '東京都', :city_id,
                'active', now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "property_type_id": property_type_id,
            "external_id": external_id,
            "url": f"https://example.test/market/{external_id}",
            "price": price,
            "layout": layout,
            "city_id": _city_id(conn),
        },
    ).scalar_one()


def _load(conn: Connection, listing_id: int) -> ListingView:
    return persist.load_listing_views(conn, listing_ids=[listing_id])[listing_id]


def test_同じ市区_同じ間取りの相場と比べた比が引ける(conn: Connection) -> None:
    conn.execute(text("DELETE FROM m_market_rates"))
    _seed_rate(conn, segment="1LDK", value=150_000, period="2026-09")
    listing_id = _insert_listing(conn, external_id="mr-hit", layout="1LDK", price=90_000)

    view = _load(conn, listing_id)

    assert view.market_rate_ratio == pytest.approx(0.6)


def test_相場が無ければ未解決のNoneになる(conn: Connection) -> None:
    """⚠ 0 にしない。0 は「相場ちょうど（＝タダ同然に安い）」と区別がつかない。"""
    conn.execute(text("DELETE FROM m_market_rates"))
    _seed_rate(conn, segment="1LDK", value=150_000, period="2026-09")
    # 相場のある 1LDK ではなく、相場を入れていない 2DK の掲載
    listing_id = _insert_listing(conn, external_id="mr-miss", layout="2DK", price=90_000)

    assert _load(conn, listing_id).market_rate_ratio is None


def test_相場の履歴があれば最新の期間を採る(conn: Connection) -> None:
    """⚠ ``m_market_rates`` は履歴を残す設計（period が違えば別の行）。

    絞らないと古い相場と混ざり、**例外にならないまま比だけがずれる**。
    """
    conn.execute(text("DELETE FROM m_market_rates"))
    _seed_rate(conn, segment="1LDK", value=100_000, period="2026-08")
    _seed_rate(conn, segment="1LDK", value=150_000, period="2026-09")
    listing_id = _insert_listing(conn, external_id="mr-latest", layout="1LDK", price=90_000)

    # 古い 2026-08（100,000円）を採ると 0.9 になる
    assert _load(conn, listing_id).market_rate_ratio == pytest.approx(0.6)
