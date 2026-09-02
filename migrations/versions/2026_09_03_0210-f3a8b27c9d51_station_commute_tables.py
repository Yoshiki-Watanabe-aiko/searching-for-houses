"""駅マスタ・掲載駅の同定結果・通勤時間キャッシュの3テーブルを追加する

Revision ID: f3a8b27c9d51
Revises: e5f1a97c2b64
Create Date: 2026-09-03

通勤時間をランキングに組み込む（Phase 5C・課題#24）。スコアに立地の配点が無いため
「安くて広い郊外」が構造的に上位を占める問題が残っており、これを埋める。

⚠ **既存テーブルへの列追加はしない。** t_listings に通勤時間の列を足すと、監査カラムを
最終列に保つDB規約からテーブル再作成（生成列 rent_total・部分インデックス・外部キーの
張り直し）が要る。通勤時間は駅から導出できる値なので新規テーブルに置けば足りる。

⚠ このマイグレーションは**手書き**である。autogenerate は無関係なドリフトを大量に拾い、
uq_m_cities_jis_code の削除や制約名の逆戻しまで含めてきた（→ e5f1a97c2b64 と同じ理由）。

⚠ t_listing_stations.station_g_cd に外部キーは張らない。m_stations の主キーは
station_cd であり、station_g_cd は一意でないため（同一グループに路線ごとの行が並ぶ）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a8b27c9d51"
down_revision = "e5f1a97c2b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "m_stations",
        sa.Column(
            "station_cd",
            sa.Integer(),
            autoincrement=False,
            nullable=False,
            comment="駅コード（路線ごとに別コード）",
        ),
        sa.Column(
            "station_g_cd",
            sa.Integer(),
            nullable=False,
            comment=(
                "駅グループコード。乗換駅を1つに束ね、同名異駅を区別する単位。"
                "通勤時間キャッシュ（t_station_commutes）のキーになる"
            ),
        ),
        sa.Column(
            "station_name",
            sa.String(length=100),
            nullable=False,
            comment="駅名（「駅」を含まない原文表記）",
        ),
        sa.Column(
            "station_name_key",
            sa.String(length=100),
            nullable=False,
            comment=(
                "照合用の正規化キー（NFKC・ヶ/ヵ・之/の の統一・小文字化）。"
                "掲載側の駅表記も同じ関数を通してから突き合わせる"
            ),
        ),
        sa.Column("line_cd", sa.Integer(), nullable=False, comment="路線コード"),
        sa.Column(
            "line_name",
            sa.String(length=100),
            nullable=False,
            comment=(
                "路線名（例: 都営三田線）。"
                "⚠ 同名の別路線が実在する（「三田線」は神戸電鉄）ため、駅名の照合は都道府県で絞る"
            ),
        ),
        sa.Column(
            "company_name",
            sa.String(length=100),
            nullable=True,
            comment=(
                "事業者名（例: 東京都交通局）。"
                "掲載側が路線名に会社名を前置することがある（「東武鉄道東上線」）ため照合に使う"
            ),
        ),
        sa.Column(
            "pref_cd",
            sa.SmallInteger(),
            nullable=False,
            comment="都道府県コード（JIS X 0401。m_cities.jis_code の上位2桁と同じ体系）",
        ),
        sa.Column(
            "lon",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
            comment="経度。Routes API の出発地・目的地に渡す",
        ),
        sa.Column(
            "lat",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
            comment="緯度。Routes API の出発地・目的地に渡す",
        ),
        # 監査カラムは最終列（DB規約）
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="レコード作成日時",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="レコード更新日時",
        ),
        sa.PrimaryKeyConstraint("station_cd", name=op.f("pk_m_stations")),
        comment="駅マスタ（駅データ.jp 無料版が正典・通勤時間の算出に使う）",
    )
    op.create_index(
        op.f("ix_m_stations_station_g_cd"), "m_stations", ["station_g_cd"], unique=False
    )
    op.create_index(
        op.f("ix_m_stations_station_name_key"),
        "m_stations",
        ["station_name_key"],
        unique=False,
    )

    op.create_table(
        "t_listing_stations",
        sa.Column("id", sa.Integer(), nullable=False, comment="同定結果ID"),
        sa.Column("listing_id", sa.Integer(), nullable=False, comment="掲載ID"),
        sa.Column(
            "position",
            sa.SmallInteger(),
            nullable=False,
            comment="station_info 内での出現順（0始まり）",
        ),
        sa.Column(
            "raw_station_name",
            sa.String(length=100),
            nullable=False,
            comment="抽出した駅名の原文。同定に失敗した表記を後から調べるために残す",
        ),
        sa.Column(
            "station_g_cd",
            sa.Integer(),
            nullable=True,
            comment="同定できた駅グループコード。ambiguous / unmatched では NULL",
        ),
        sa.Column(
            "match_status",
            sa.String(length=10),
            nullable=False,
            comment=(
                "matched=一意に同定 / ambiguous=同名の駅が複数あり路線でも絞れない / "
                "unmatched=マスタに無い（バス停・施設名など）"
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="レコード作成日時",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="レコード更新日時",
        ),
        sa.CheckConstraint(
            "match_status IN ('matched', 'ambiguous', 'unmatched')",
            name=op.f("ck_t_listing_stations_listing_stations_match_status"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["t_listings.id"],
            name=op.f("fk_t_listing_stations_listing_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_t_listing_stations")),
        sa.UniqueConstraint(
            "listing_id",
            "position",
            name=op.f("uq_t_listing_stations_listing_id_position"),
        ),
        comment="掲載の駅表記と駅マスタの同定結果",
    )
    op.create_index(
        "ix_t_listing_stations_station_g_cd",
        "t_listing_stations",
        ["station_g_cd"],
        unique=False,
        postgresql_where=sa.text("station_g_cd IS NOT NULL"),
    )

    op.create_table(
        "t_station_commutes",
        sa.Column("id", sa.Integer(), nullable=False, comment="キャッシュID"),
        sa.Column(
            "origin_station_g_cd",
            sa.Integer(),
            nullable=False,
            comment="出発駅の駅グループコード（物件側の最寄り駅）",
        ),
        sa.Column(
            "destination_station_g_cd",
            sa.Integer(),
            nullable=False,
            comment="到着駅の駅グループコード（勤務先の最寄り駅）",
        ),
        sa.Column(
            "status",
            sa.String(length=10),
            nullable=False,
            comment=(
                "ok=所要時間を取得 / no_route=経路なし（APIが200で空応答）/ error=取得失敗。"
                "再取得の対象は error だけ"
            ),
        ),
        sa.Column(
            "commute_minutes",
            sa.Integer(),
            nullable=True,
            comment="所要時間（分）。status='ok' のときだけ入る。秒は切り上げる",
        ),
        sa.Column(
            "raw_duration_sec",
            sa.Integer(),
            nullable=True,
            comment="APIが返した所要時間（秒）の生値。丸め方を変えても取り直さずに済むように残す",
        ),
        sa.Column(
            "departure_time",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=(
                "計算に使った出発時刻。所要時間はダイヤに依存するので、"
                "駅ペア間の比較可能性を保つには全ペアで時刻を揃える必要がある"
            ),
        ),
        sa.Column(
            "error_detail",
            sa.Text(),
            nullable=True,
            comment="status='error' のときのHTTPステータスと本文抜粋",
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="APIを叩いた時刻。error 行の再取得判断に使う",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="レコード作成日時",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="レコード更新日時",
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'no_route', 'error')",
            name=op.f("ck_t_station_commutes_station_commutes_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_t_station_commutes")),
        sa.UniqueConstraint(
            "origin_station_g_cd",
            "destination_station_g_cd",
            name=op.f(
                "uq_t_station_commutes_origin_station_g_cd_destination_station_g_cd"
            ),
        ),
        comment="駅ペアの通勤所要時間キャッシュ（Routes API・TRANSIT）",
    )


def downgrade() -> None:
    op.drop_table("t_station_commutes")
    op.drop_index("ix_t_listing_stations_station_g_cd", table_name="t_listing_stations")
    op.drop_table("t_listing_stations")
    op.drop_index(op.f("ix_m_stations_station_name_key"), table_name="m_stations")
    op.drop_index(op.f("ix_m_stations_station_g_cd"), table_name="m_stations")
    op.drop_table("m_stations")
