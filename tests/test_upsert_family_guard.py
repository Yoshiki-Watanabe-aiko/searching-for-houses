"""ファミリをまたいだ external_id の衝突で既存行を上書きしない（→ 課題#61・計画書 §6.5-2）。

⚠⚠ 一意キーは ``(site_id, external_id)`` で**種別を含まない**。同じ ``nc_`` が戸建てと
土地の両方に出てくると、素朴な UPSERT は**戸建ての行の価格や面積を土地の値で上書きし、
種別は戸建てのまま**にする。⚠ 例外にならず件数も減らない（戸建ての順位が土地の価格で
静かに狂う）。

2026-09-11 の実測では、土地の一覧から集めた ``nc_`` 225件と既存の SUUMO 掲載の重なりは
0件だった。**それでも SQL の側で止める**——掲載は入れ替わり、重なりが出た瞬間から
黙って壊れる類なので、「いま実害が無い」を理由に先送りしない（課題#43 と同じ判断）。

⚠ **同じファミリの別種別（新築⇔中古）は見送らない。** SUUMO は新築と中古で同じ ``nc_``
を使い回す実例が本番DBに2件あった（2026-09-11）。どちらの種別に持たせるかは
ユーザー判断が要るので、ここでは稼働中の挙動を変えない（→ 課題#62）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.pipeline.persist import (
    PRICE_DOWN,
    CityIndex,
    load_city_index,
    upsert_listings,
)
from house_search.scrape.base import ScrapedListing

_EXTERNAL_ID = "nc_99999901"


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


def _ptype(conn: Connection, code: str) -> int:
    return conn.execute(
        text("SELECT id FROM m_property_types WHERE code = :code"), {"code": code}
    ).scalar_one()


@pytest.fixture
def city_index(conn: Connection) -> CityIndex:
    return load_city_index(conn)


def _kodate(price: int, *, path: str = "chukoikkodate") -> ScrapedListing:
    return ScrapedListing(
        site_code="SUUMO",
        external_id=_EXTERNAL_ID,
        url=f"https://suumo.jp/{path}/tokyo/sc_hachioji/{_EXTERNAL_ID}/",
        price=price,
        land_area_sqm=100.0,
        building_area_sqm=90.0,
        type_specific_attrs={"price_undecided": False},
    )


def _tochi(price: int) -> ScrapedListing:
    return ScrapedListing(
        site_code="SUUMO",
        external_id=_EXTERNAL_ID,
        url=f"https://suumo.jp/tochi/tokyo/sc_hachioji/{_EXTERNAL_ID}/",
        price=price,
        land_area_sqm=200.0,
        type_specific_attrs={"price_undecided": False},
    )


def _row(conn: Connection, listing_id: int) -> tuple:
    return tuple(
        conn.execute(
            text(
                "SELECT property_type_id, price, land_area_sqm, building_area_sqm, url "
                "FROM t_listings WHERE id = :id"
            ),
            {"id": listing_id},
        ).one()
    )


def test_別のファミリの既存行は上書きしない(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    kodate_id = _ptype(conn, "CHUKO_KODATE")
    tochi_id = _ptype(conn, "TOCHI")
    first = upsert_listings(
        conn, [_kodate(30_000_000)], site_id=site_id, property_type_id=kodate_id,
        city_index=city_index,
    )
    listing_id = first.outcomes[0].listing_id
    before = _row(conn, listing_id)

    second = upsert_listings(
        conn, [_tochi(10_000_000)], site_id=site_id, property_type_id=tochi_id,
        city_index=city_index,
    )

    # 戸建ての行は種別も値もそのまま
    assert _row(conn, listing_id) == before
    assert before[0] == kodate_id and before[1] == 30_000_000
    # ⚠ 見送った掲載は新着・価格変動として扱わない（通知もしない）
    assert second.outcomes == ()
    # ⚠ 黙って捨てない。見送った ID を数えて返す
    assert second.family_mismatch == (_EXTERNAL_ID,)


def test_同じ種別なら従来どおり更新する(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    kodate_id = _ptype(conn, "CHUKO_KODATE")
    first = upsert_listings(
        conn, [_kodate(30_000_000)], site_id=site_id, property_type_id=kodate_id,
        city_index=city_index,
    )
    second = upsert_listings(
        conn, [_kodate(28_000_000)], site_id=site_id, property_type_id=kodate_id,
        city_index=city_index,
    )

    assert first.family_mismatch == ()
    assert second.family_mismatch == ()
    assert len(second.outcomes) == 1
    assert second.outcomes[0].listing_id == first.outcomes[0].listing_id
    assert second.outcomes[0].price_event == PRICE_DOWN
    assert _row(conn, first.outcomes[0].listing_id)[1] == 28_000_000


def test_同じファミリの別種別は従来どおり更新する(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    """⚠ **現状維持を固定するテスト**（→ 課題#62）。新築⇔中古で同じ ``nc_`` が
    使い回される実例が本番DBに2件ある。種別は最初に入った方のまま、値は後から
    走った側で上書きされる（2026-09-11 以前からの挙動）。方針を決めたらここを直す。
    """
    shinchiku_id = _ptype(conn, "SHINCHIKU_KODATE")
    chuko_id = _ptype(conn, "CHUKO_KODATE")
    first = upsert_listings(
        conn, [_kodate(58_800_000, path="ikkodate")], site_id=site_id,
        property_type_id=shinchiku_id, city_index=city_index,
    )
    second = upsert_listings(
        conn, [_kodate(55_000_000)], site_id=site_id, property_type_id=chuko_id,
        city_index=city_index,
    )

    assert second.family_mismatch == ()
    assert len(second.outcomes) == 1
    row = _row(conn, first.outcomes[0].listing_id)
    assert row[0] == shinchiku_id  # 種別は最初に入った方のまま
    assert row[1] == 55_000_000  # 値は後から走った側
    assert "/chukoikkodate/" in row[4]


def test_見送った掲載があっても同じ呼び出しの他の掲載は保存する(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    """⚠ 1件の食い違いでバッチ全体を落とさない（1ページの失敗でサイトを止めないのと同じ）。"""
    kodate_id = _ptype(conn, "CHUKO_KODATE")
    tochi_id = _ptype(conn, "TOCHI")
    upsert_listings(
        conn, [_kodate(30_000_000)], site_id=site_id, property_type_id=kodate_id,
        city_index=city_index,
    )
    other = ScrapedListing(
        site_code="SUUMO",
        external_id="nc_99999902",
        url="https://suumo.jp/tochi/tokyo/sc_hachioji/nc_99999902/",
        price=12_000_000,
        land_area_sqm=150.0,
        type_specific_attrs={"price_undecided": False},
    )

    batch = upsert_listings(
        conn, [_tochi(10_000_000), other], site_id=site_id, property_type_id=tochi_id,
        city_index=city_index,
    )

    assert batch.family_mismatch == (_EXTERNAL_ID,)
    assert [o.external_id for o in batch.outcomes] == ["nc_99999902"]
    assert batch.outcomes[0].is_new is True
    assert _row(conn, batch.outcomes[0].listing_id)[0] == tochi_id
