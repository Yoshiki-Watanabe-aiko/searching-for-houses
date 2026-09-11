"""ファミリをまたいだ external_id の衝突で既存行を上書きしない（→ 課題#61・計画書 §6.5-2）。

⚠⚠ 一意キーは ``(site_id, external_id)`` で**種別を含まない**。同じ ``nc_`` が戸建てと
土地の両方に出てくると、素朴な UPSERT は**戸建ての行の価格や面積を土地の値で上書きし、
種別は戸建てのまま**にする。⚠ 例外にならず件数も減らない（戸建ての順位が土地の価格で
静かに狂う）。

2026-09-11 の実測では、土地の一覧から集めた ``nc_`` 225件と既存の SUUMO 掲載の重なりは
0件だった。**それでも SQL の側で止める**——掲載は入れ替わり、重なりが出た瞬間から
黙って壊れる類なので、「いま実害が無い」を理由に先送りしない（課題#43 と同じ判断）。

⚠⚠ **同じファミリの別種別（新築⇔中古）も、既存行が掲載中なら上書きしない**（→ 課題#62・
2026-09-11 ユーザー判断で案(c)）。SUUMO は新築と中古で同じ ``nc_`` を使い回し、以前は
「種別は最初に入った方のまま、値は後から走った側」で上書きされていた（本番2件）。
**既存行が掲載終了（sold / removed）なら、新しい種別で引き継ぐ**（旧種別の値は消す）。
⚠ ファミリが違う場合は掲載終了でも引き継がない（名寄せ・相場比・通知先まで変わるため）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.db.models.transactions import Listing
from house_search.pipeline.persist import (
    NEW,
    PRICE_DOWN,
    RETAKE_KEEP_COLUMNS,
    RETAKE_NULL_COLUMNS,
    RETAKE_SET_COLUMNS,
    CityIndex,
    ExistingRelation,
    classify_existing,
    detail_queue,
    load_city_index,
    save_score,
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


def test_同じファミリの別種別でも掲載中なら上書きしない(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    """⚠ 仕様変更で書き換えたテスト（→ 課題#62・2026-09-11 ユーザー判断で案(c)）。

    旧テストは「種別は最初に入った方のまま、値は後から走った側で上書きされる」という
    **現状維持を固定していた**。その状態が本番2件の「新築の行に中古の値・中古の URL」で、
    新築マンションの80位が中古の値で採点されていた（例外にならない）。
    新築と中古の両方に同時に載っている間は、最初の種別で安定させる。
    """
    shinchiku_id = _ptype(conn, "SHINCHIKU_KODATE")
    chuko_id = _ptype(conn, "CHUKO_KODATE")
    first = upsert_listings(
        conn, [_kodate(58_800_000, path="ikkodate")], site_id=site_id,
        property_type_id=shinchiku_id, city_index=city_index,
    )
    listing_id = first.outcomes[0].listing_id
    before = _row(conn, listing_id)

    second = upsert_listings(
        conn, [_kodate(55_000_000)], site_id=site_id, property_type_id=chuko_id,
        city_index=city_index,
    )

    # 種別も値も URL も新築のまま（中古の値で上書きしない）
    assert _row(conn, listing_id) == before
    assert before[0] == shinchiku_id and "/ikkodate/" in before[4]
    # ⚠ 見送った掲載は新着・価格変動として扱わない（別の種別の価格と比べることになる）
    assert second.outcomes == ()
    # ⚠ 黙って捨てない。ファミリ違いとは別の欄で返す（実行サマリで出し分ける）
    assert second.type_mismatch == (_EXTERNAL_ID,)
    assert second.family_mismatch == ()


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


# ---- 課題#62: 掲載終了なら新しい種別で引き継ぐ -------------------------------


def _mark_sold(conn: Connection, listing_id: int) -> None:
    conn.execute(text("UPDATE t_listings SET status = 'sold' WHERE id = :id"), {"id": listing_id})


def _add_old_traces(conn: Connection, listing_id: int, *, pattern_name: str) -> None:
    """旧種別のときに付いた値（詳細・設備・採点）を置く。引き継いだら残ってはいけないもの。"""
    conn.execute(
        text(
            """
            UPDATE t_listings SET
                raw_features_text = '旧種別の設備原文',
                built_on = DATE '2026-10-01',
                repair_reserve_monthly = 12000,
                floor_num = 3,
                total_floors = 5,
                layout = '4LDK',
                detail_fetched_at = now(),
                type_specific_attrs = type_specific_attrs
                    || '{"price_undecided": true, "建築条件": "付"}'::jsonb
            WHERE id = :id
            """
        ),
        {"id": listing_id},
    )
    conn.execute(
        text(
            "INSERT INTO t_listing_features (listing_id, condition_id) "
            "SELECT :id, id FROM m_conditions ORDER BY id LIMIT 1"
        ),
        {"id": listing_id},
    )
    save_score(
        conn,
        listing_id=listing_id,
        pattern_name=pattern_name,
        must_result="pass",
        score=50.0,
        breakdown=[],
        config_hash="test",
    )


def _count(conn: Connection, table: str, listing_id: int) -> int:
    return conn.execute(
        text(f"SELECT count(*) FROM {table} WHERE listing_id = :id"), {"id": listing_id}
    ).scalar_one()


def test_掲載終了なら新しい種別で引き継ぐ(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    """新築が掲載終了になった後、同じ ``nc_`` が中古に出たら中古の行として作り直す。

    ⚠ 本番2件（nc_21640666 / nc_21620507）はどちらも新築側の URL が 404 だった
    （2026-09-11 実測）。つまり実態は「新築は終わり、中古として出ている」。
    """
    shinchiku_id = _ptype(conn, "SHINCHIKU_KODATE")
    chuko_id = _ptype(conn, "CHUKO_KODATE")
    first = upsert_listings(
        conn, [_kodate(58_800_000, path="ikkodate")], site_id=site_id,
        property_type_id=shinchiku_id, city_index=city_index,
    )
    listing_id = first.outcomes[0].listing_id
    _mark_sold(conn, listing_id)

    second = upsert_listings(
        conn, [_kodate(55_000_000)], site_id=site_id, property_type_id=chuko_id,
        city_index=city_index,
    )

    row = conn.execute(
        text(
            "SELECT property_type_id, status, price, price_prev, url "
            "FROM t_listings WHERE id = :id"
        ),
        {"id": listing_id},
    ).one()
    assert row.property_type_id == chuko_id  # 種別を付け替えた
    assert row.status == "active"
    assert row.price == 55_000_000 and "/chukoikkodate/" in row.url
    # ⚠ 別の種別の価格を「直前価格」にしない（値下げ通知が誤発火する）
    assert row.price_prev is None

    assert second.type_mismatch == () and second.family_mismatch == ()
    (outcome,) = second.outcomes
    assert outcome.listing_id == listing_id
    assert outcome.is_retyped is True
    # 案(c) の定義どおり「再掲載」として新しい種別のパターンで新着を出す（ユーザー判断）
    assert outcome.is_reinstated is True
    assert outcome.notification_type == NEW
    assert outcome.price_event is None and outcome.price_prev is None


def test_引き継いだら旧種別の値を残さない(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    """⚠ 一覧の UPSERT は多くの列を COALESCE し、``type_specific_attrs`` は JSONB で
    マージするので、**作り直さないと旧種別の値が黙って残る**（新築の「価格未定」の
    フラグ・棟の所在階・旧い詳細の設備原文と抽出結果・旧パターンの順位）。
    """
    shinchiku_id = _ptype(conn, "SHINCHIKU_KODATE")
    chuko_id = _ptype(conn, "CHUKO_KODATE")
    first = upsert_listings(
        conn, [_kodate(58_800_000, path="ikkodate")], site_id=site_id,
        property_type_id=shinchiku_id, city_index=city_index,
    )
    listing_id = first.outcomes[0].listing_id
    _add_old_traces(conn, listing_id, pattern_name="新築一戸建て")
    _mark_sold(conn, listing_id)

    upsert_listings(
        conn, [_kodate(55_000_000)], site_id=site_id, property_type_id=chuko_id,
        city_index=city_index,
    )

    row = conn.execute(
        text(
            "SELECT raw_features_text, built_on, repair_reserve_monthly, floor_num, "
            "total_floors, layout, detail_fetched_at, type_specific_attrs "
            "FROM t_listings WHERE id = :id"
        ),
        {"id": listing_id},
    ).one()
    assert row.raw_features_text is None
    assert row.built_on is None and row.repair_reserve_monthly is None
    assert row.floor_num is None and row.total_floors is None and row.layout is None
    # ⚠ 新しい種別の詳細キューに入れ直す（詳細ページは種別で別物 → 課題#4）
    assert row.detail_fetched_at is None
    # 旧種別のキー（価格未定・建築条件）を残さず、新しい一覧の値だけにする
    assert row.type_specific_attrs == {"price_undecided": False}
    assert _count(conn, "t_listing_features", listing_id) == 0
    # ⚠ 旧パターンの順位を残さない（digest は種別で絞らずに読む）
    assert _count(conn, "t_listing_scores", listing_id) == 0


def test_引き継いだ掲載は新しい種別の詳細キューに入る(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    shinchiku_id = _ptype(conn, "SHINCHIKU_KODATE")
    chuko_id = _ptype(conn, "CHUKO_KODATE")
    first = upsert_listings(
        conn, [_kodate(58_800_000, path="ikkodate")], site_id=site_id,
        property_type_id=shinchiku_id, city_index=city_index,
    )
    listing_id = first.outcomes[0].listing_id
    _add_old_traces(conn, listing_id, pattern_name="新築一戸建て")
    _mark_sold(conn, listing_id)

    upsert_listings(
        conn, [_kodate(55_000_000)], site_id=site_id, property_type_id=chuko_id,
        city_index=city_index,
    )

    def queued(property_type_id: int) -> set[int]:
        return {
            row_id
            for row_id, _url in detail_queue(
                conn, site_id=site_id, property_type_id=property_type_id, limit=10_000,
                listing_ids=[listing_id],
            )
        }

    assert queued(chuko_id) == {listing_id}
    assert queued(shinchiku_id) == set()


def test_同じ種別の再掲載では詳細と設備を消さない(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    """守りのテスト（修正前から通る）。作り直すのは種別が変わるときだけ。"""
    chuko_id = _ptype(conn, "CHUKO_KODATE")
    first = upsert_listings(
        conn, [_kodate(30_000_000)], site_id=site_id, property_type_id=chuko_id,
        city_index=city_index,
    )
    listing_id = first.outcomes[0].listing_id
    _add_old_traces(conn, listing_id, pattern_name="中古一戸建て")
    _mark_sold(conn, listing_id)

    second = upsert_listings(
        conn, [_kodate(29_000_000)], site_id=site_id, property_type_id=chuko_id,
        city_index=city_index,
    )

    (outcome,) = second.outcomes
    assert outcome.is_reinstated is True
    assert getattr(outcome, "is_retyped", False) is False
    row = conn.execute(
        text("SELECT raw_features_text, detail_fetched_at FROM t_listings WHERE id = :id"),
        {"id": listing_id},
    ).one()
    assert row.raw_features_text == "旧種別の設備原文" and row.detail_fetched_at is not None
    assert _count(conn, "t_listing_features", listing_id) == 1
    assert _count(conn, "t_listing_scores", listing_id) == 1


def test_掲載終了でもファミリ違いは引き継がない(
    conn: Connection, site_id: int, city_index: CityIndex
) -> None:
    """守りのテスト。ファミリが変わると名寄せの名前空間・相場比・通知先まで変わる
    （ユーザー判断 2026-09-11 で適用しない）。"""
    kodate_id = _ptype(conn, "CHUKO_KODATE")
    tochi_id = _ptype(conn, "TOCHI")
    first = upsert_listings(
        conn, [_kodate(30_000_000)], site_id=site_id, property_type_id=kodate_id,
        city_index=city_index,
    )
    listing_id = first.outcomes[0].listing_id
    _mark_sold(conn, listing_id)
    before = _row(conn, listing_id)

    second = upsert_listings(
        conn, [_tochi(10_000_000)], site_id=site_id, property_type_id=tochi_id,
        city_index=city_index,
    )

    assert _row(conn, listing_id) == before
    assert second.outcomes == ()
    assert second.family_mismatch == (_EXTERNAL_ID,)


# ---- 純関数（DB 不要） ----------------------------------------------------


@pytest.mark.parametrize(
    ("existing", "new_type", "new_family", "expected"),
    [
        (None, 11, "KODATE_BUY", ExistingRelation.NEW),
        ((11, "KODATE_BUY", "active"), 11, "KODATE_BUY", ExistingRelation.SAME_TYPE),
        # 掲載終了の同じ種別は従来どおり「再掲載」（作り直さない）
        ((11, "KODATE_BUY", "sold"), 11, "KODATE_BUY", ExistingRelation.SAME_TYPE),
        ((12, "KODATE_BUY", "active"), 11, "KODATE_BUY", ExistingRelation.TYPE_MISMATCH),
        ((12, "KODATE_BUY", "sold"), 11, "KODATE_BUY", ExistingRelation.RETYPE),
        ((12, "KODATE_BUY", "removed"), 11, "KODATE_BUY", ExistingRelation.RETYPE),
        ((12, "KODATE_BUY", "active"), 13, "TOCHI_BUY", ExistingRelation.FAMILY_MISMATCH),
        ((12, "KODATE_BUY", "sold"), 13, "TOCHI_BUY", ExistingRelation.FAMILY_MISMATCH),
    ],
)
def test_既存行との関係の分類(
    existing: tuple[int, str, str] | None,
    new_type: int,
    new_family: str,
    expected: ExistingRelation,
) -> None:
    relation = classify_existing(
        existing_type_id=existing[0] if existing else None,
        existing_family=existing[1] if existing else None,
        existing_status=existing[2] if existing else None,
        new_type_id=new_type,
        new_family=new_family,
    )
    assert relation is expected


def test_引き継ぎで作り直す列と残す列はモデルの全列を覆う() -> None:
    """⚠ 将来 ``t_listings`` に列を足したとき、分類し忘れると**旧種別の値が黙って残る**。

    ``tests/test_persist.py``（``ScrapedDetail`` の全フィールドが ``save_detail`` に
    現れる）と同じ考え方で、モデルの全列が「NULL にする／値を入れ直す／残す」の
    どれか1つに入っていることを機械的に固定する。
    """
    columns = {c.name for c in Listing.__table__.columns}
    groups = [set(RETAKE_NULL_COLUMNS), set(RETAKE_SET_COLUMNS), set(RETAKE_KEEP_COLUMNS)]

    assert set().union(*groups) == columns, (
        f"分類されていない列: {sorted(columns - set().union(*groups))} / "
        f"モデルに無い列: {sorted(set().union(*groups) - columns)}"
    )
    for i, a in enumerate(groups):
        for b in groups[i + 1 :]:
            assert not (a & b), f"2つの分類に重複している列: {sorted(a & b)}"
