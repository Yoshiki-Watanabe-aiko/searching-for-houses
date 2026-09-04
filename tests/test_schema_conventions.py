"""DBスキーマがDB規約を守っているかの回帰テスト。

Alembic の autogenerate は物理列順を見ず、コメントの付け忘れも検知しない。
規約（m_/t_ 接頭辞・日本語コメント・監査カラムの最終列）は
``information_schema`` と ``pg_description`` を直接読んで固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from house_search.db.models import Base

pytestmark = pytest.mark.db

EXPECTED_MASTER_TABLES = {
    "m_cities",
    "m_city_site_values",
    "m_condition_categories",
    "m_condition_property_types",
    "m_condition_synonyms",
    "m_conditions",
    "m_property_types",
    "m_site_search_params",
    "m_sites",
    "m_stations",
}
EXPECTED_TRANSACTION_TABLES = {
    "t_notifications",
    "t_listings",
    "t_listing_features",
    "t_listing_groups",
    "t_listing_scores",
    "t_listing_stations",
    "t_navitime_routes",
    "t_rail_segments",
    "t_ranking_digests",
    "t_scrape_logs",
    "t_scrape_runs",
    "t_site_scan_cursors",
    "t_station_commutes",
    "t_unknown_tokens",
}
# 追記専用のため updated_at を持たないテーブル。
APPEND_ONLY_TABLES = {"t_notifications", "t_ranking_digests", "t_scrape_logs"}


def _app_tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalars()
        return set(rows)


def test_テーブル名がm_t接頭辞規約に準拠している(test_engine: Engine) -> None:
    tables = _app_tables(test_engine)
    assert tables == EXPECTED_MASTER_TABLES | EXPECTED_TRANSACTION_TABLES


def test_モデル定義とDBのテーブル集合が一致する(test_engine: Engine) -> None:
    assert _app_tables(test_engine) == set(Base.metadata.tables)


def test_監査カラムが最終列にある(test_engine: Engine) -> None:
    """ADD COLUMN は物理的に末尾へ追加されるため、列を足すとこのテストが落ちる。

    落ちたら「テーブル再作成で監査カラムを末尾へ戻す」手順を踏むこと
    （DB_CONVENTIONS.md の「列追加時のテーブル再作成手順」）。
    """
    with test_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT table_name, column_name, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name <> 'alembic_version'
                ORDER BY table_name, ordinal_position
            """)
        ).all()

    by_table: dict[str, list[str]] = {}
    for table_name, column_name, _ in rows:
        by_table.setdefault(table_name, []).append(column_name)

    for table_name, columns in by_table.items():
        if table_name in APPEND_ONLY_TABLES:
            assert columns[-1] == "created_at", (
                f"{table_name}: created_at が最終列にない（末尾3列={columns[-3:]}）"
            )
            assert "updated_at" not in columns, f"{table_name}: 追記専用なのに updated_at がある"
        else:
            assert columns[-2:] == ["created_at", "updated_at"], (
                f"{table_name}: 監査カラムが最終列にない（末尾3列={columns[-3:]}）"
            )


def test_全テーブルに日本語コメントがある(test_engine: Engine) -> None:
    with test_engine.connect() as conn:
        missing = (
            conn.execute(
                text("""
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND c.relname <> 'alembic_version'
                  AND obj_description(c.oid, 'pg_class') IS NULL
                ORDER BY 1
            """)
            )
            .scalars()
            .all()
        )
    assert not missing, f"テーブルコメント未設定: {missing}"


def test_全カラムに日本語コメントがある(test_engine: Engine) -> None:
    with test_engine.connect() as conn:
        missing = (
            conn.execute(
                text("""
                SELECT col.table_name || '.' || col.column_name
                FROM information_schema.columns col
                JOIN pg_class c ON c.relname = col.table_name
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                LEFT JOIN pg_description d
                       ON d.objoid = c.oid AND d.objsubid = col.ordinal_position
                WHERE col.table_schema = 'public'
                  AND col.table_name <> 'alembic_version'
                  AND d.description IS NULL
                ORDER BY 1
            """)
            )
            .scalars()
            .all()
        )
    assert not missing, f"カラムコメント未設定: {missing}"


def test_マスタデータが投入されている(test_engine: Engine) -> None:
    from house_search.db.seed import EXPECTED_MIN_ROWS

    with test_engine.connect() as conn:
        for table, expected in EXPECTED_MIN_ROWS.items():
            actual = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            assert actual >= expected, f"{table}: {actual} < {expected}"


def test_サイトマスタが登録されている(test_engine: Engine) -> None:
    """``db/seed/02_sites.sql`` の全サイトが入っていること。

    ⚠ **件数のハードコードをやめた。** Phase 5F 以降で UR・レオパレス21・D-room・
    ハウスコム・ホームメイトを足したが、テストDBへ ``db-seed --test-db`` を
    流していなかったため「12サイト」の期待値が**残ったまま緑だった**。
    seed の実データと突き合わせるほうが、サイトを足すたびに黙って古くなることがない。
    """
    seed = (Path(__file__).parents[1] / "db" / "seed" / "02_sites.sql").read_text(encoding="utf-8")
    expected = set(re.findall(r"^\s*\('([A-Z_]+)',", seed, re.MULTILINE))
    with test_engine.connect() as conn:
        codes = set(conn.execute(text("SELECT code FROM m_sites")).scalars())
    assert "CHINTAI_EX" in codes
    assert expected, "02_sites.sql からサイトコードを1件も読めていない"
    assert expected <= codes, f"seed にあるのにDBへ入っていない: {sorted(expected - codes)}"


def test_全サイトがHTTP取得(test_engine: Engine) -> None:
    """Phase 3 の実測で Playwright 必須サイトは無くなった（→ ADR 0010）。

    v1 が go-rod / Playwright を使っていた5サイト（ATHOME・EHEYA・NIFTY・
    APAMAN・SMOCCA）も、いずれもサーバレンダリング済みHTMLを返すため
    素のHTTPで取得できる。ブラウザを使っていたのは検索フォームを操作していた
    ためで、URLを直接組み立てる v2 では不要。
    """
    with test_engine.connect() as conn:
        methods = set(conn.execute(text("SELECT DISTINCT fetch_method FROM m_sites")).scalars())
    assert methods == {"HTTP"}


def test_市区町村サイト値が縦持ちで引ける(test_engine: Engine) -> None:
    """ワイドテーブルから縦持ちへ転換した結果が引けることの確認。"""
    with test_engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT s.code, v.value
                FROM m_city_site_values v
                JOIN m_cities c ON c.id = v.city_id
                JOIN m_sites  s ON s.id = v.site_id
                WHERE c.prefecture = '東京都' AND c.canonical_name = '新宿区'
                  AND s.code = 'SUUMO'
            """)
        ).one()
    assert row.value == "sc_shinjuku"


def test_生成列rent_totalが賃料と管理費の合計になる(test_engine: Engine) -> None:
    """アプリ側の算出漏れで賃料合計がドリフトしないことをDB側で担保する。"""
    with test_engine.begin() as conn:
        listing_id = conn.execute(
            text("""
                INSERT INTO t_listings
                    (site_id, property_type_id, external_id, url, price, mgmt_fee_monthly)
                SELECT s.id, p.id, 'TEST-RENT-TOTAL', 'https://example.com/test', 65000, 5000
                FROM m_sites s, m_property_types p
                WHERE s.code = 'SUUMO' AND p.code = 'CHINTAI'
                RETURNING id
            """)
        ).scalar_one()
        try:
            rent_total = conn.execute(
                text("SELECT rent_total FROM t_listings WHERE id = :id"),
                {"id": listing_id},
            ).scalar_one()
            assert rent_total == 70000

            # 価格が未定（NULL）なら合計も NULL になる（新築の価格未定対応）
            conn.execute(
                text("UPDATE t_listings SET price = NULL WHERE id = :id"), {"id": listing_id}
            )
            assert (
                conn.execute(
                    text("SELECT rent_total FROM t_listings WHERE id = :id"),
                    {"id": listing_id},
                ).scalar_one()
                is None
            )
        finally:
            conn.execute(text("DELETE FROM t_listings WHERE id = :id"), {"id": listing_id})
