"""取引上の注意事項（建築条件付き・借地権・再建築不可）が通知まで届くか（→ 課題#61 論点5）。

⚠⚠ アダプタは ``type_specific_attrs``（JSONB）に書くだけで、採点ビュー
（``ListingView``）はこれまで JSONB を1列も読んでいなかった。表示の関数を
足しても、**読み込み（``load_listing_views``）が渡さなければ通知には1件も出ない**
——例外にならない「実装済みだが未配線」の形（→ 要件定義書 §17）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.pipeline.persist import (
    load_city_index,
    load_listing_views,
    save_detail,
    upsert_listings,
)
from house_search.scrape.base import CAVEAT_LABELS, ScrapedDetail, ScrapedListing, caveats_of


class Test判定:
    def test_Trueのフラグだけを出す(self) -> None:
        """⚠ False（記載が無い）と None（読めない表記）は出さない。"""
        attrs = {"build_condition": True, "leasehold": False, "rebuild_prohibited": None}
        assert caveats_of(attrs) == ("建築条件付き",)

    def test_真偽値以外は出さない(self) -> None:
        """⚠ 原文の ``"付"`` をそのまま True 扱いにしない（原文は別キーに残る）。"""
        assert caveats_of({"build_condition": "付"}) == ()

    def test_空でも落ちない(self) -> None:
        assert caveats_of(None) == ()
        assert caveats_of({}) == ()

    def test_出す順番は定義の順(self) -> None:
        """決定的な順にする（通知の文面が実行ごとに揺れないように）。"""
        attrs = {key: True for key in reversed(list(CAVEAT_LABELS))}
        assert caveats_of(attrs) == tuple(CAVEAT_LABELS.values())


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def test_採点ビューが注意事項を読む(conn: Connection) -> None:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    tochi_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'TOCHI'")
    ).scalar_one()
    batch = upsert_listings(
        conn,
        [
            ScrapedListing(
                site_code="SUUMO",
                external_id="nc_99999911",
                url="https://suumo.jp/tochi/tokyo/sc_setagaya/nc_99999911/",
                price=118_000_000,
                land_area_sqm=98.66,
                address="東京都世田谷区船橋１",
                type_specific_attrs={"price_undecided": False},
            )
        ],
        site_id=site_id,
        property_type_id=tochi_id,
        city_index=load_city_index(conn),
    )
    listing_id = batch.outcomes[0].listing_id
    save_detail(
        conn,
        listing_id,
        ScrapedDetail(
            type_specific_attrs={
                "建築条件": "付",
                "build_condition": True,
                "leasehold": False,
                "rebuild_prohibited": False,
            }
        ),
    )

    view = load_listing_views(conn, listing_ids=[listing_id])[listing_id]

    assert view.caveats == ("建築条件付き",)
    assert view.land_area_sqm == 98.66
    assert view.property_family == "TOCHI_BUY"
