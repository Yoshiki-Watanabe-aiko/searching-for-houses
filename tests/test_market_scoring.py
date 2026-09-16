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


# --- 相場の行そのものを通知へ運ぶ（表示専用 → 課題#70） -------------------------


def test_採点に使った相場の行が表示用に届く(conn: Connection) -> None:
    """⚠ 比だけでは「何と比べた結果か」を通知に書けない（→ 課題#70）。"""
    conn.execute(text("DELETE FROM m_market_rates"))
    _seed_rate(conn, segment="1LDK", value=150_000, period="2026-09")
    listing_id = _insert_listing(conn, external_id="mr-ref", layout="1LDK", price=90_000)

    view = _load(conn, listing_id)

    assert view.market_rate is not None
    assert view.market_rate.rate_value == pytest.approx(150_000.0)
    assert view.market_rate.segment == "1LDK"
    assert view.market_rate.stat_basis == "rent_listed_mansion"
    assert view.market_rate.period == "2026-09"
    assert view.city_name == "足立区"


def test_相場が無くても市区名は届く(conn: Connection) -> None:
    """⚠ 市区名は相場と独立に取る。無いと「相場不明」の理由を書き分けられない。"""
    conn.execute(text("DELETE FROM m_market_rates"))
    listing_id = _insert_listing(conn, external_id="mr-nocity", layout="2DK", price=90_000)

    view = _load(conn, listing_id)

    assert view.market_rate is None
    assert view.city_name == "足立区"


def test_相場はあるが賃料が無ければ比だけがNoneになる(conn: Connection) -> None:
    """⚠⚠ **比の None と相場の None を混ぜない**（→ 課題#70）。

    混ぜると「相場そのものが無い」掲載と「相場はあるが比べる材料が無い」掲載が
    同じ文面になり、通知を見ても相場の収録漏れなのか掲載の欠損なのか分からない。
    """
    conn.execute(text("DELETE FROM m_market_rates"))
    _seed_rate(conn, segment="1LDK", value=150_000, period="2026-09")
    listing_id = _insert_listing(conn, external_id="mr-noprice", layout="1LDK", price=90_000)
    # rent_total は price + 管理費 の生成列。price を NULL にすると rent_total も NULL
    conn.execute(
        text("UPDATE t_listings SET price = NULL WHERE id = :id"), {"id": listing_id}
    )

    view = _load(conn, listing_id)

    assert view.market_rate_ratio is None
    assert view.market_rate is not None
    assert view.market_rate.rate_value == pytest.approx(150_000.0)


def _insert_kodate(conn: Connection, *, external_id: str, price: int, floor: float) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHUKO_KODATE'")
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, area_sqm, building_area_sqm, land_area_sqm,
                address, prefecture, city_id,
                status, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id, :url, '相場テスト戸建て',
                :price, 999.0, :floor, 120.0,
                '東京都足立区東和5丁目', '東京都', :city_id,
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
            "floor": floor,
            "city_id": _city_id(conn),
        },
    ).scalar_one()


def _seed_buy_rate(conn: Connection, *, segment: str, value: int) -> None:
    conn.execute(
        text(
            """
            INSERT INTO m_market_rates (
                family, source, level, city_id, segment, stat_basis,
                rate_value, sample_count, period, acquired_on, created_at, updated_at
            ) VALUES (
                'KODATE_BUY', 'test', 'city', :city_id, :segment, 'trade_unit_price_01',
                :value, 40, '2025Q2-2026Q1', DATE '2026-09-05', now(), now()
            )
            """
        ),
        {"city_id": _city_id(conn), "segment": segment, "value": value},
    )


def test_戸建ては延床の相場を採り土地単価を採らない(conn: Connection) -> None:
    """⚠⚠ **分子と segment は必ず対にする**（→ 課題#70 risk 1）。

    延床と土地の両方が相場マスタにあるので、``LAND_SQM`` を採ってしまっても
    **例外にならず比の水準だけが静かにずれる**。通知の「同じ延床なら相場◯◯万円」も
    同じ面積で計算するため、ここがずれると表示と採点が食い違う。
    """
    conn.execute(text("DELETE FROM m_market_rates"))
    _seed_buy_rate(conn, segment="FLOOR_SQM", value=340_000)
    _seed_buy_rate(conn, segment="LAND_SQM", value=250_000)
    listing_id = _insert_kodate(conn, external_id="mr-kodate", price=30_000_000, floor=95.0)

    view = _load(conn, listing_id)

    assert view.market_rate is not None
    assert view.market_rate.segment == "FLOOR_SQM"
    assert view.market_rate.rate_value == pytest.approx(340_000.0)
    # 延床で割った㎡単価（315,789円）÷ 相場（340,000円）
    assert view.market_rate_ratio == pytest.approx(30_000_000 / 95.0 / 340_000)
    # ⚠ SQL の分子（building_area_sqm）と通知の換算（segment → 延床）が同じ面積を指すこと。
    #    area_sqm=999.0 を使っていればこの恒等式は成り立たない
    assert view.price is not None and view.market_rate_ratio is not None
    assert view.price / view.market_rate_ratio == pytest.approx(
        view.market_rate.rate_value * 95.0
    )
