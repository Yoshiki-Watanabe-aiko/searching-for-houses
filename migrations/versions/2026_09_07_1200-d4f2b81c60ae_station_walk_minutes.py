"""t_listing_stations に walk_minutes（駅ごとの徒歩分数）を追加

⚠ **t_listings.walk_minutes はバス停からの徒歩を採っていることがある**（→ 課題#58）。
実測（2026-09-07）で active 掲載の 3,090件が該当し、上位100位に35件入っていた。
``武蔵小金井駅 歩5分 / 新小金井駅 バス12分 (バス停)中町4丁目 歩2分`` が
**徒歩2分**として保存され、``walk_minutes_max: 20`` を不当に通過していた。

アダプタ16本を直す代わりに、駅ごとの徒歩分数をここへ持たせて採点はその最小値を採る
（``t_listings.walk_minutes`` は一覧の upsert で毎回上書きされるので、
後処理で直しても次のスキャンで戻ってしまう）。

⚠ 監査カラム（created_at / updated_at）を最終列に保つため**テーブルを作り直す**
（DB_CONVENTIONS）。既存データはコピーするので ``resolve-stations`` を流すまでの間も
駅の同定結果は失われない（walk_minutes だけが NULL のまま残る）。

Revision ID: d4f2b81c60ae
Revises: b3d17c5f9a24
Create Date: 2026-09-07
"""

from __future__ import annotations

from alembic import op

revision = "d4f2b81c60ae"
down_revision = "b3d17c5f9a24"
branch_labels = None
depends_on = None

_NEW_TABLE = """
CREATE TABLE t_listing_stations_new (
    id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES t_listings(id) ON DELETE CASCADE,
    position SMALLINT NOT NULL,
    raw_station_name VARCHAR(100) NOT NULL,
    station_g_cd INTEGER,
    match_status VARCHAR(10) NOT NULL,
    walk_minutes SMALLINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_t_listing_stations_listing_id_position_new UNIQUE (listing_id, position),
    CONSTRAINT listing_stations_match_status_new
        CHECK (match_status IN ('matched', 'ambiguous', 'unmatched'))
)
"""

_OLD_TABLE = """
CREATE TABLE t_listing_stations_new (
    id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES t_listings(id) ON DELETE CASCADE,
    position SMALLINT NOT NULL,
    raw_station_name VARCHAR(100) NOT NULL,
    station_g_cd INTEGER,
    match_status VARCHAR(10) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_t_listing_stations_listing_id_position_new UNIQUE (listing_id, position),
    CONSTRAINT listing_stations_match_status_new
        CHECK (match_status IN ('matched', 'ambiguous', 'unmatched'))
)
"""

_COLUMN_COMMENTS = {
    "id": "同定結果ID",
    "listing_id": "掲載ID",
    "position": "station_info 内での出現順（0始まり）",
    "raw_station_name": "抽出した駅名の原文。同定に失敗した表記を後から調べるために残す",
    "station_g_cd": "同定できた駅グループコード。ambiguous / unmatched では NULL",
    "match_status": (
        "matched=一意に同定 / ambiguous=同名の駅が複数あり路線でも絞れない / "
        "unmatched=マスタに無い（バス停・施設名など）"
    ),
    "walk_minutes": (
        "その駅からの徒歩分数。バス便・判別不能は NULL。"
        "t_listings.walk_minutes はバス停からの徒歩を採っていることがあるため"
        "（実測 3,090件）、採点はこちらの最小値を使う"
    ),
    "created_at": "作成日時",
    "updated_at": "更新日時",
}

_TABLE_COMMENT = "掲載の駅表記と駅マスタの同定結果"


def _rebuild(create_sql: str, columns: list[str], comments: dict[str, str]) -> None:
    op.execute(create_sql)
    cols = ", ".join(columns)
    op.execute(
        f"INSERT INTO t_listing_stations_new ({cols}) SELECT {cols} FROM t_listing_stations"
    )
    op.execute(
        "SELECT setval("
        "pg_get_serial_sequence('t_listing_stations_new', 'id'), "
        "COALESCE((SELECT max(id) FROM t_listing_stations_new), 1))"
    )
    op.execute("DROP TABLE t_listing_stations")
    op.execute("ALTER TABLE t_listing_stations_new RENAME TO t_listing_stations")
    op.execute(
        "ALTER INDEX t_listing_stations_new_pkey RENAME TO t_listing_stations_pkey"
    )
    # 制約名も戻す（旧テーブルと同名にできないので _new を付けて作ってある）
    op.execute(
        "ALTER TABLE t_listing_stations RENAME CONSTRAINT "
        "uq_t_listing_stations_listing_id_position_new TO "
        "uq_t_listing_stations_listing_id_position"
    )
    op.execute(
        "ALTER TABLE t_listing_stations RENAME CONSTRAINT "
        "listing_stations_match_status_new TO listing_stations_match_status"
    )
    op.execute(
        "CREATE INDEX ix_t_listing_stations_station_g_cd ON t_listing_stations (station_g_cd) "
        "WHERE station_g_cd IS NOT NULL"
    )
    op.execute(f"COMMENT ON TABLE t_listing_stations IS '{_TABLE_COMMENT}'")
    for column, comment in comments.items():
        op.execute(f"COMMENT ON COLUMN t_listing_stations.{column} IS '{comment}'")


_COMMON = [
    "id",
    "listing_id",
    "position",
    "raw_station_name",
    "station_g_cd",
    "match_status",
    "created_at",
    "updated_at",
]


def upgrade() -> None:
    _rebuild(_NEW_TABLE, _COMMON, _COLUMN_COMMENTS)


def downgrade() -> None:
    comments = {k: v for k, v in _COLUMN_COMMENTS.items() if k != "walk_minutes"}
    _rebuild(_OLD_TABLE, _COMMON, comments)
