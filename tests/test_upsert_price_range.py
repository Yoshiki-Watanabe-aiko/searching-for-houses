"""新築の価格レンジと「価格未定」フラグが実際に保存されるかのテスト（→ 課題#4・Phase 6 手順6）。

⚠⚠ **「SQL に ``:price_min`` という文字列がある」ことと「実際に保存される」ことは別**。
``tests/test_persist.py`` の突き合わせは型と SQL のずれを検出するだけで、
``CAST(:type_specific_attrs AS jsonb)`` が意図どおり動くかまでは保証しない。
⚠ 手順4後半で ``repair_reserve_monthly`` が「型に足したのに UPDATE 文へ入れ忘れ」の
まま緑だった（→ 課題#4）のと同じ形の穴を、ここで DB まで通して塞ぐ。

⚠ **``upsert_listings`` にはテストが1件も無かった**（``save_detail`` → 課題#4、
``detail_queue`` → 課題#54、``check_sold`` → 課題#26 と同じ）。

本題は **JSONB のマージ**。``type_specific_attrs`` は詳細取得が入れた項目を
消さないよう ``||`` でマージするので、⚠ **価格が付いたときに
``price_undecided: false`` を書かないとフラグが残り続ける**。
価格があるのに「価格未定」と表示されるが、**例外にならず件数も減らない**。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.pipeline.persist import CityIndex, load_city_index, upsert_listings
from house_search.scrape.base import ScrapedListing


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


@pytest.fixture
def ptype_id(conn: Connection) -> int:
    return conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'SHINCHIKU_MANSION'")
    ).scalar_one()


@pytest.fixture
def city_index(conn: Connection) -> CityIndex:
    return load_city_index(conn)


def _upsert(
    conn: Connection,
    site_id: int,
    ptype_id: int,
    city_index: CityIndex,
    listing: ScrapedListing,
) -> int:
    outcomes = upsert_listings(
        conn,
        [listing],
        site_id=site_id,
        property_type_id=ptype_id,
        city_index=city_index,
    )
    return outcomes[0].listing_id


def _row(conn: Connection, listing_id: int) -> tuple:
    return conn.execute(
        text(
            "SELECT price, price_min, price_max, type_specific_attrs "
            "FROM t_listings WHERE id = :id"
        ),
        {"id": listing_id},
    ).one()


def test_価格レンジが保存される(
    conn: Connection, site_id: int, ptype_id: int, city_index: CityIndex
) -> None:
    """``price`` にレンジ下限、``price_min``/``price_max`` にレンジ（→ 要件定義書 §11.4）。"""
    listing_id = _upsert(
        conn,
        site_id,
        ptype_id,
        city_index,
        ScrapedListing(
            site_code="SUUMO",
            external_id="nc_test_range",
            url="https://example.invalid/nc_test_range/",
            price=84_100_000,
            price_min=84_100_000,
            price_max=146_900_000,
            type_specific_attrs={"price_undecided": False},
        ),
    )
    price, pmin, pmax, attrs = _row(conn, listing_id)
    assert price == 84_100_000
    assert pmin == 84_100_000
    assert pmax == 146_900_000
    assert attrs["price_undecided"] is False


def test_価格未定は価格をNULLにしてフラグで表す(
    conn: Connection, site_id: int, ptype_id: int, city_index: CityIndex
) -> None:
    """⚠ 0 やハイフンにすると「安い」と誤読され、順位だけが静かに狂う。

    ⚠ **「価格が取れなかった（None）」と「価格未定と明記されている」は
    フラグの有無で区別する。** ハザードの「区域外(0)」と「未解決(None)」を
    混ぜてはいけないのと同じ形（→ ADR 0021 決定4）。
    """
    listing_id = _upsert(
        conn,
        site_id,
        ptype_id,
        city_index,
        ScrapedListing(
            site_code="SUUMO",
            external_id="nc_test_undecided",
            url="https://example.invalid/nc_test_undecided/",
            price=None,
            type_specific_attrs={"price_undecided": True},
        ),
    )
    price, pmin, pmax, attrs = _row(conn, listing_id)
    assert price is None
    assert pmin is None and pmax is None
    assert attrs["price_undecided"] is True


def test_価格が付いたら未定フラグが降りる(
    conn: Connection, site_id: int, ptype_id: int, city_index: CityIndex
) -> None:
    """⚠⚠ **本題。** ``type_specific_attrs`` はマージ保存なので、
    価格が付いたときに ``false`` を明示的に書かないとフラグが残り続ける。

    残ると「価格があるのに価格未定と表示される」が、**例外にならず
    件数も減らない**（通知とダイジェストの表示だけが誤る）。
    """
    common = {
        "site_code": "SUUMO",
        "external_id": "nc_test_transition",
        "url": "https://example.invalid/nc_test_transition/",
    }
    listing_id = _upsert(
        conn,
        site_id,
        ptype_id,
        city_index,
        ScrapedListing(**common, price=None, type_specific_attrs={"price_undecided": True}),  # type: ignore[arg-type]
    )
    assert _row(conn, listing_id)[3]["price_undecided"] is True

    _upsert(
        conn,
        site_id,
        ptype_id,
        city_index,
        ScrapedListing(  # type: ignore[arg-type]
            **common,
            price=82_900_000,
            type_specific_attrs={"price_undecided": False},
        ),
    )
    price, _pmin, _pmax, attrs = _row(conn, listing_id)
    assert price == 82_900_000
    assert attrs["price_undecided"] is False, "価格が付いても未定フラグが残っている"


def test_詳細が入れた項目を一覧の再取得が消さない(
    conn: Connection, site_id: int, ptype_id: int, city_index: CityIndex
) -> None:
    """⚠ 一覧は2時間ごとに走るので、上書きにすると詳細由来の項目が毎回消える。

    ``save_detail`` が ``権利形態`` などを入れた行を一覧が再取得しても、
    一覧が書かないキーは残らなければならない。
    """
    common = {
        "site_code": "SUUMO",
        "external_id": "nc_test_merge",
        "url": "https://example.invalid/nc_test_merge/",
    }
    listing_id = _upsert(
        conn,
        site_id,
        ptype_id,
        city_index,
        ScrapedListing(**common, type_specific_attrs={"price_undecided": True}),  # type: ignore[arg-type]
    )
    conn.execute(
        text(
            "UPDATE t_listings SET type_specific_attrs = "
            "type_specific_attrs || '{\"land_right\": \"所有権\"}'::jsonb "
            "WHERE id = :id"
        ),
        {"id": listing_id},
    )

    _upsert(
        conn,
        site_id,
        ptype_id,
        city_index,
        ScrapedListing(  # type: ignore[arg-type]
            **common,
            price=82_900_000,
            type_specific_attrs={"price_undecided": False},
        ),
    )
    attrs = _row(conn, listing_id)[3]
    assert attrs["land_right"] == "所有権", "詳細由来の項目が一覧の再取得で消えた"
    assert attrs["price_undecided"] is False


def test_属性を渡さない掲載は空のまま(
    conn: Connection, site_id: int, ptype_id: int, city_index: CityIndex
) -> None:
    """⚠ 賃貸・中古のアダプタは ``type_specific_attrs`` を返さない。

    既定の空辞書がマージされても既存の値を壊さず、NULL にもならないこと。
    """
    listing_id = _upsert(
        conn,
        site_id,
        ptype_id,
        city_index,
        ScrapedListing(
            site_code="SUUMO",
            external_id="nc_test_empty",
            url="https://example.invalid/nc_test_empty/",
            price=39_800_000,
        ),
    )
    price, pmin, pmax, attrs = _row(conn, listing_id)
    assert price == 39_800_000
    assert pmin is None and pmax is None
    assert attrs == {}
