"""名寄せグループのDB統合テスト。

``DATABASE_TEST_URL`` が未設定のときは ``conftest.py`` の ``test_engine`` が
テストごとスキップする。各テストはトランザクションをロールバックするので
テストDBに行を残さない。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, text

from house_search.dedup import groups as dedup_groups
from house_search.pipeline import persist


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _ids(conn: Connection) -> tuple[dict[str, int], int]:
    sites = {code: site_id for code, site_id in conn.execute(text("SELECT code, id FROM m_sites"))}
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    return sites, property_type_id


def _insert(
    conn: Connection,
    *,
    site_code: str,
    external_id: str,
    address: str = "東京都足立区東和5丁目",
    layout: str | None = "1K",
    area_sqm: str | None = "25.64",
    floor_num: int | None = 4,
    price: int | None = 100000,
    mgmt_fee_monthly: int | None = 5000,
    status: str = "active",
) -> int:
    sites, property_type_id = _ids(conn)
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, mgmt_fee_monthly, area_sqm, layout, floor_num,
                address, prefecture, status, first_seen_at, last_seen_at,
                created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id, :url, :title,
                :price, :mgmt_fee_monthly, :area_sqm, :layout, :floor_num,
                :address, '東京都', :status, now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": sites[site_code],
            "property_type_id": property_type_id,
            "external_id": external_id,
            "url": f"https://example.test/{site_code}/{external_id}",
            "title": f"テスト物件 {external_id}",
            "price": price,
            "mgmt_fee_monthly": mgmt_fee_monthly,
            "area_sqm": area_sqm,
            "layout": layout,
            "floor_num": floor_num,
            "address": address,
            "status": status,
        },
    ).scalar_one()


def _add_feature(conn: Connection, listing_id: int, condition_code: str) -> None:
    conn.execute(
        text(
            "INSERT INTO t_listing_features "
            "(listing_id, condition_id, source, extracted_at, created_at, updated_at) "
            "SELECT :listing_id, id, 'DETAIL', now(), now(), now() "
            "FROM m_conditions WHERE code = :code"
        ),
        {"listing_id": listing_id, "code": condition_code},
    )


def _group_of(conn: Connection, listing_id: int) -> int | None:
    return conn.execute(
        text("SELECT group_id FROM t_listings WHERE id = :id"), {"id": listing_id}
    ).scalar_one()


def _representative(conn: Connection, group_id: int) -> int | None:
    return conn.execute(
        text("SELECT representative_listing_id FROM t_listing_groups WHERE id = :id"),
        {"id": group_id},
    ).scalar_one()


# --- グループ化 ----------------------------------------------------------


def test_同一住戸の別サイト掲載が1グループになる(conn: Connection) -> None:
    # いい部屋ネットは「東和５丁目」、スモッカは「東和５」と書く（実測）
    a = _insert(conn, site_code="EHEYA", external_id="e1", address="東京都足立区東和５丁目")
    b = _insert(conn, site_code="SMOCCA", external_id="s1", address="東京都足立区東和５")
    dedup_groups.refresh_dedup_keys(conn, [a, b])
    dedup_groups.sync_groups(conn)

    assert _group_of(conn, a) is not None
    assert _group_of(conn, a) == _group_of(conn, b)
    member_count = conn.execute(
        text("SELECT member_count FROM t_listing_groups WHERE id = :id"),
        {"id": _group_of(conn, a)},
    ).scalar_one()
    assert member_count == 2


def test_階が違えば別グループになる(conn: Connection) -> None:
    a = _insert(conn, site_code="SUUMO", external_id="f1", floor_num=1)
    b = _insert(conn, site_code="SUUMO", external_id="f2", floor_num=2)
    dedup_groups.refresh_dedup_keys(conn, [a, b])
    dedup_groups.sync_groups(conn)
    assert _group_of(conn, a) != _group_of(conn, b)


def test_構成要素が欠けた掲載はグループ化されない(conn: Connection) -> None:
    orphan = _insert(conn, site_code="SUUMO", external_id="n1", floor_num=None)
    dedup_groups.refresh_dedup_keys(conn, [orphan])
    dedup_groups.sync_groups(conn)
    assert _group_of(conn, orphan) is None
    key = conn.execute(
        text("SELECT dedup_key FROM t_listings WHERE id = :id"), {"id": orphan}
    ).scalar_one()
    assert key is None


def test_同期は冪等(conn: Connection) -> None:
    a = _insert(conn, site_code="SUUMO", external_id="i1")
    b = _insert(conn, site_code="GOO", external_id="i2")
    dedup_groups.refresh_dedup_keys(conn, [a, b])
    dedup_groups.sync_groups(conn)

    # 2回目は何も変わらない（代表の交代も発生しない）
    assert dedup_groups.sync_groups(conn) == []
    assert dedup_groups.refresh_dedup_keys(conn, [a, b]) == 0


# --- 代表選定 ------------------------------------------------------------


def test_代表は月額が最安の掲載になる(conn: Connection) -> None:
    expensive = _insert(conn, site_code="SUUMO", external_id="r1", price=100000)
    cheap = _insert(conn, site_code="SMOCCA", external_id="r2", price=90000)
    dedup_groups.refresh_dedup_keys(conn, [expensive, cheap])
    dedup_groups.sync_groups(conn)
    assert _representative(conn, _group_of(conn, cheap)) == cheap


def test_月額が同額なら設備抽出数が多いほうが代表になる(conn: Connection) -> None:
    poor = _insert(conn, site_code="SUUMO", external_id="q1")
    rich = _insert(conn, site_code="SMOCCA", external_id="q2")
    _add_feature(conn, rich, "SEC_AUTOLOCK")
    _add_feature(conn, rich, "BATH_SEPARATE")
    dedup_groups.refresh_dedup_keys(conn, [poor, rich])
    dedup_groups.sync_groups(conn)
    # SUUMO のほうがサイト優先順は上だが、設備抽出数で SMOCCA が勝つ
    assert _representative(conn, _group_of(conn, rich)) == rich


def test_月額も設備数も同じならサイト優先順で決まる(conn: Connection) -> None:
    smocca = _insert(conn, site_code="SMOCCA", external_id="p1")
    suumo = _insert(conn, site_code="SUUMO", external_id="p2")
    dedup_groups.refresh_dedup_keys(conn, [smocca, suumo])
    dedup_groups.sync_groups(conn)
    # m_sites.representative_priority は SUUMO=10 / SMOCCA=110
    assert _representative(conn, _group_of(conn, suumo)) == suumo


def test_代表が成約したら次の代表へ移る(conn: Connection) -> None:
    cheap = _insert(conn, site_code="SUUMO", external_id="s1", price=90000)
    other = _insert(conn, site_code="GOO", external_id="s2", price=95000)
    dedup_groups.refresh_dedup_keys(conn, [cheap, other])
    dedup_groups.sync_groups(conn)
    group_id = _group_of(conn, cheap)
    assert _representative(conn, group_id) == cheap

    persist.mark_status(conn, [cheap], "sold")
    changes = dedup_groups.sync_groups(conn)

    assert _representative(conn, group_id) == other
    change = next(c for c in changes if c.group_id == group_id)
    assert change.previous_listing_id == cheap
    assert change.current_listing_id == other
    # 高いほうへ移ったので安値通知の対象にはならない
    assert change.is_cheaper is False


def test_より安い掲載が現れたら安値通知の候補になる(conn: Connection) -> None:
    first = _insert(conn, site_code="SUUMO", external_id="c1", price=100000)
    dedup_groups.refresh_dedup_keys(conn, [first])
    dedup_groups.sync_groups(conn)

    cheaper = _insert(conn, site_code="GOO", external_id="c2", price=80000)
    dedup_groups.refresh_dedup_keys(conn, [cheaper])
    changes = dedup_groups.sync_groups(conn)

    change = next(c for c in changes if c.current_listing_id == cheaper)
    assert change.is_cheaper is True
    assert change.previous_cost == 105000  # 100000 + 管理費5000
    assert change.current_cost == 85000


def test_掲載が全て消えたグループは削除される(conn: Connection) -> None:
    only = _insert(conn, site_code="SUUMO", external_id="d1")
    dedup_groups.refresh_dedup_keys(conn, [only])
    dedup_groups.sync_groups(conn)
    group_id = _group_of(conn, only)

    conn.execute(text("DELETE FROM t_listings WHERE id = :id"), {"id": only})
    dedup_groups.sync_groups(conn)

    remaining = conn.execute(
        text("SELECT count(*) FROM t_listing_groups WHERE id = :id"), {"id": group_id}
    ).scalar_one()
    assert remaining == 0


# --- 採点への反映 --------------------------------------------------------


def test_設備はグループ内の和集合で読まれる(conn: Connection) -> None:
    a = _insert(conn, site_code="SUUMO", external_id="u1")
    b = _insert(conn, site_code="GOO", external_id="u2")
    _add_feature(conn, a, "SEC_AUTOLOCK")
    _add_feature(conn, b, "BATH_SEPARATE")
    dedup_groups.refresh_dedup_keys(conn, [a, b])
    dedup_groups.sync_groups(conn)

    views = persist.load_listing_views(conn, listing_ids=[a, b])
    # サイトAでしか判らない設備とサイトBでしか判らない設備がマージされる
    assert {"SEC_AUTOLOCK", "BATH_SEPARATE"} <= views[a].feature_codes
    assert {"SEC_AUTOLOCK", "BATH_SEPARATE"} <= views[b].feature_codes


def test_詳細取得済みはグループ内の誰かが取れていれば真になる(conn: Connection) -> None:
    fetched = _insert(conn, site_code="SUUMO", external_id="v1")
    pending = _insert(conn, site_code="GOO", external_id="v2")
    conn.execute(
        text("UPDATE t_listings SET detail_fetched_at = now() WHERE id = :id"), {"id": fetched}
    )
    dedup_groups.refresh_dedup_keys(conn, [fetched, pending])
    dedup_groups.sync_groups(conn)

    views = persist.load_listing_views(conn, listing_ids=[pending])
    assert views[pending].detail_fetched is True


def test_順位は代表と未グループ物件にだけ振られる(conn: Connection) -> None:
    representative = _insert(conn, site_code="SUUMO", external_id="k1", price=90000)
    member = _insert(conn, site_code="GOO", external_id="k2", price=95000)
    alone = _insert(conn, site_code="ABLE", external_id="k3", floor_num=9, price=80000)
    dedup_groups.refresh_dedup_keys(conn, [representative, member, alone])
    dedup_groups.sync_groups(conn)

    for listing_id, score in ((representative, 70.0), (member, 69.0), (alone, 80.0)):
        persist.save_score(
            conn,
            listing_id=listing_id,
            pattern_name="テスト",
            must_result="pass",
            score=score,
            breakdown=[],
            config_hash="test",
        )
    persist.update_ranks(conn, "テスト")

    ranks = {
        listing_id: rank
        for listing_id, rank in conn.execute(
            text(
                "SELECT listing_id, rank_in_pattern FROM t_listing_scores "
                "WHERE pattern_name = 'テスト'"
            )
        )
    }
    assert ranks[alone] == 1
    assert ranks[representative] == 2
    # 非代表メンバーはランキングに現れない（重複が上位枠を食わない）
    assert ranks[member] is None


# --- 通知の抑制と表示 ----------------------------------------------------


def test_同じグループの別掲載は新着として二重通知されない(conn: Connection) -> None:
    first = _insert(conn, site_code="SUUMO", external_id="w1")
    second = _insert(conn, site_code="GOO", external_id="w2")
    dedup_groups.refresh_dedup_keys(conn, [first, second])
    dedup_groups.sync_groups(conn)
    group_id = _group_of(conn, first)

    persist.record_notification(
        conn,
        listing_id=first,
        group_id=group_id,
        pattern_name="テスト",
        notification_type="new",
        price_at_notify=100000,
        score_at_notify=70.0,
        status="sent",
    )

    assert persist.already_notified(
        conn,
        listing_id=second,
        pattern_name="テスト",
        notification_type="new",
        group_id=group_id,
    )
    # グループを渡さなければ別物件として扱われる
    assert not persist.already_notified(
        conn, listing_id=second, pattern_name="テスト", notification_type="new"
    )


def test_安値通知は同額なら再送しないが更に安くなれば送る(conn: Connection) -> None:
    listing_id = _insert(conn, site_code="SUUMO", external_id="x1")
    dedup_groups.refresh_dedup_keys(conn, [listing_id])
    dedup_groups.sync_groups(conn)
    group_id = _group_of(conn, listing_id)

    persist.record_notification(
        conn,
        listing_id=listing_id,
        group_id=group_id,
        pattern_name="テスト",
        notification_type="cheaper_listing",
        price_at_notify=90000,
        score_at_notify=70.0,
        status="sent",
    )
    assert persist.cheaper_listing_notified_at(
        conn, group_id=group_id, pattern_name="テスト", price=90000
    )
    assert not persist.cheaper_listing_notified_at(
        conn, group_id=group_id, pattern_name="テスト", price=85000
    )


def test_グループ所属から他サイトの掲載が引ける(conn: Connection) -> None:
    a = _insert(conn, site_code="SUUMO", external_id="y1")
    b = _insert(conn, site_code="GOO", external_id="y2")
    alone = _insert(conn, site_code="ABLE", external_id="y3", floor_num=7)
    dedup_groups.refresh_dedup_keys(conn, [a, b, alone])
    dedup_groups.sync_groups(conn)

    memberships = dedup_groups.group_membership(conn, [a, b, alone])
    assert memberships[a].other_site_codes == ("GOO",)
    assert memberships[b].other_site_codes == ("SUUMO",)
    assert memberships[a].member_count == 2
    # 未グループの物件も既定値で埋まる
    assert memberships[alone].group_id is not None
    assert memberships[alone].other_site_codes == ()


def test_名寄せ実測がサイト別に集計される(conn: Connection) -> None:
    a = _insert(conn, site_code="SUUMO", external_id="z1")
    _insert(conn, site_code="GOO", external_id="z2")
    dedup_groups.refresh_dedup_keys(conn)
    dedup_groups.sync_groups(conn)

    stats = {row.site_code: row for row in dedup_groups.dedup_stats(conn)}
    assert stats["SUUMO"].with_key >= 1
    assert stats["SUUMO"].shared_with_other_sites >= 1
    assert stats["SUUMO"].unique_rate < 1.0
    assert _group_of(conn, a) is not None
