"""NAVITIME の経路原文と乗車区間の実所要時間の2テーブルを追加する

Revision ID: b8e4c25f7a19
Revises: f3a8b27c9d51
Create Date: 2026-09-03

通勤時間を実ダイヤへ寄せる（Phase 5D）。Phase 5C の所要時間は
``8.7 + 1.14 × 距離km + 5.6 × 乗換回数`` の回帰式で平均誤差5.6分・最大16.0分あり、
優等列車・直通運転・乗換待ちを式で表現できないことが原因だった（→ ADR 0016）。

⚠ **ODPT は使わない。** 列車時刻表はあるが登録とトークン発行に日数がかかり、
京成・北総・東葉高速などが未参加で穴が残る。NAVITIME の乗換案内は登録不要で、
優等列車・直通・待ちを織り込んだ結果をそのまま返す（→ ADR 0017）。

⚠ **既存テーブルへの列追加はしない。** 監査カラムを最終列に保つDB規約から
テーブル再作成が要るため。所要時間は駅から導出できる値なので新規テーブルで足りる
（f3a8b27c9d51 と同じ判断）。

⚠ このマイグレーションは**手書き**である。autogenerate は無関係なドリフトを
大量に拾う（uq_m_cities_jis_code の削除や制約名の逆戻しまで含めてきた）。

⚠ 駅グループコードに外部キーは張らない。``m_stations`` の主キーは ``station_cd`` で、
``station_g_cd`` は一意でないため（同一グループに路線ごとの行が並ぶ）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e4c25f7a19"
down_revision = "f3a8b27c9d51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_navitime_routes",
        sa.Column("id", sa.Integer(), nullable=False, comment="経路ID"),
        sa.Column(
            "origin_station_g_cd",
            sa.Integer(),
            nullable=False,
            comment="出発駅の駅グループコード",
        ),
        sa.Column(
            "destination_station_g_cd",
            sa.Integer(),
            nullable=False,
            comment="到着駅の駅グループコード（勤務先の最寄り駅）",
        ),
        sa.Column(
            "depart_on",
            sa.Date(),
            nullable=False,
            comment="検索した出発日。曜日でダイヤが変わるため条件の一部",
        ),
        sa.Column(
            "depart_at",
            sa.Time(),
            nullable=False,
            comment="検索した出発時刻（この時刻以降の便を探す）",
        ),
        sa.Column(
            "rank",
            sa.SmallInteger(),
            nullable=False,
            comment="NAVITIME が並べた順（1始まり）",
        ),
        sa.Column(
            "total_minutes",
            sa.Integer(),
            nullable=False,
            comment="所要時間（分）。乗換の待ち時間を含む",
        ),
        sa.Column("transfers", sa.Integer(), nullable=False, comment="乗換回数"),
        sa.Column("distance_km", sa.Numeric(7, 2), nullable=True, comment="経路の距離（km）"),
        sa.Column("fare_yen", sa.Integer(), nullable=True, comment="きっぷ運賃（円）"),
        sa.Column(
            "route_depart_at",
            sa.String(length=5),
            nullable=False,
            comment="実際の出発時刻（HH:MM）",
        ),
        sa.Column(
            "route_arrive_at",
            sa.String(length=5),
            nullable=False,
            comment="実際の到着時刻（HH:MM）",
        ),
        sa.Column(
            "origin_label",
            sa.String(length=100),
            nullable=False,
            comment=(
                "NAVITIME が解決した出発駅の表記。同名異駅では『大久保（東京都）』のように"
                "都道府県が付く。意図した駅かを人が確かめるために残す"
            ),
        ),
        sa.Column(
            "destination_label",
            sa.String(length=100),
            nullable=False,
            comment="NAVITIME が解決した到着駅の表記",
        ),
        sa.Column(
            "origin_node_code",
            sa.String(length=20),
            nullable=True,
            comment="NAVITIME の駅ノードコード。次回以降の厳密指定に使える",
        ),
        sa.Column(
            "destination_node_code",
            sa.String(length=20),
            nullable=True,
            comment="到着駅の NAVITIME 駅ノードコード",
        ),
        sa.Column(
            "route_text",
            sa.Text(),
            nullable=False,
            comment=("経路の原文（発着時刻・路線・区間所要が1本のテキストで並ぶ）。再解析の入力"),
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="取得した時刻",
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_t_navitime_routes")),
        sa.UniqueConstraint(
            "origin_station_g_cd",
            "destination_station_g_cd",
            "depart_on",
            "depart_at",
            "rank",
            name=op.f("uq_t_navitime_routes_origin_station_g_cd"),
        ),
        comment="NAVITIME の乗換案内が返した経路候補の原文",
    )

    op.create_table(
        "t_rail_segments",
        sa.Column("id", sa.Integer(), nullable=False, comment="区間ID"),
        sa.Column(
            "from_station_g_cd",
            sa.Integer(),
            nullable=False,
            comment="乗車駅の駅グループコード",
        ),
        sa.Column(
            "to_station_g_cd",
            sa.Integer(),
            nullable=False,
            comment="降車駅の駅グループコード",
        ),
        sa.Column(
            "line_name",
            sa.String(length=100),
            nullable=False,
            comment=("路線名。種別を含む表記のまま持つ（『都営三田線急行』）。徒歩は『徒歩』"),
        ),
        sa.Column(
            "ride_minutes",
            sa.Integer(),
            nullable=False,
            comment="乗車時間（分）の最小観測値。辺の重みに使う代表値",
        ),
        sa.Column(
            "ride_minutes_max",
            sa.Integer(),
            nullable=False,
            comment="同区間で観測した最大値。ばらつきを人が確かめるために持つ",
        ),
        sa.Column(
            "samples",
            sa.Integer(),
            nullable=False,
            comment="観測回数。1件しか無い区間は信用度が低い",
        ),
        sa.Column(
            "is_walk",
            sa.Boolean(),
            nullable=False,
            comment="乗換の徒歩区間か（列車ではない）",
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            comment="採取元（navitime=乗換案内の経路から採取）",
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="最後に観測した時刻",
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_t_rail_segments")),
        sa.UniqueConstraint(
            "from_station_g_cd",
            "to_station_g_cd",
            "line_name",
            name=op.f("uq_t_rail_segments_from_station_g_cd"),
        ),
        comment="乗車区間（駅間）の実所要時間",
    )


def downgrade() -> None:
    op.drop_table("t_rail_segments")
    op.drop_table("t_navitime_routes")
