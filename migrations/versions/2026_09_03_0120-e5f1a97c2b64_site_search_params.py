"""サイト側の絞り込みパラメータ定義テーブルを追加する

Revision ID: e5f1a97c2b64
Revises: d7b3e9c41a58
Create Date: 2026-09-03

MUST に限りサイト側のフォームへ条件を渡せるようにする（-> ADR 0015）。
正典は data/site_search_params.yaml で、sync-site-params でこの表へ同期する
（m_condition_synonyms と同じ構成）。

⚠ これは旧 site_condition_map（サイト×設備条件の対応表）の復活ではない。
扱うのは数値系の MUST と間取りだけで、設備条件は永久に含めない。

⚠ このマイグレーションは**手書き**である。autogenerate は無関係なドリフトを
大量に拾い、uq_m_cities_jis_code の削除や制約名の逆戻しまで含めてきた
（モデル定義と実DBの命名規約の差によるもの）。新テーブルの追加だけを書く。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5f1a97c2b64"
down_revision = "d7b3e9c41a58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "m_site_search_params",
        sa.Column("id", sa.Integer(), nullable=False, comment="パラメータ定義ID"),
        sa.Column("site_id", sa.Integer(), nullable=False, comment="サイトID"),
        sa.Column(
            "property_type_id", sa.Integer(), nullable=False, comment="物件種別ID"
        ),
        sa.Column(
            "axis",
            sa.String(length=50),
            nullable=False,
            comment=(
                "MUSTの軸名（area_min / area_max / walk_minutes_max / age_max / layouts）。"
                "丸めの向きは軸から決まるのでここには持たせない"
            ),
        ),
        sa.Column(
            "param_name",
            sa.String(length=100),
            nullable=False,
            comment="URLクエリのキー（SUUMO の mb / et / md など）",
        ),
        sa.Column(
            "value_kind",
            sa.String(length=20),
            nullable=False,
            comment=(
                "値の表し方。stepped=等間隔の選択肢 / enum=不等間隔の選択肢 / "
                "multi=複数値を並べて送る（間取り）"
            ),
        ),
        sa.Column(
            "unit",
            sa.String(length=20),
            nullable=False,
            comment=(
                "サイトが受け取る単位（yen / man_yen / sqm / minutes / years）。"
                "MUST側の値から換算する"
            ),
        ),
        sa.Column(
            "value_spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment=(
                "値の空間。stepped は min/max/step、enum は choices、multi は mapping。"
                "いずれも format（Python の書式文字列）を伴う"
            ),
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="この軸を実際に送るか。実測で効かないと分かったら false にする",
        ),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=True,
            comment="実測メモ（件数の変化・0件になる条件など）",
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
        sa.ForeignKeyConstraint(
            ["property_type_id"],
            ["m_property_types.id"],
            name=op.f("fk_m_site_search_params_property_type_id"),
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["m_sites.id"], name=op.f("fk_m_site_search_params_site_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_m_site_search_params")),
        sa.UniqueConstraint(
            "site_id",
            "property_type_id",
            "axis",
            name=op.f("uq_m_site_search_params_site_id_property_type_id_axis"),
        ),
        comment="サイト側の絞り込みパラメータ定義（MUST限定・サイト×物件種別×軸）",
    )


def downgrade() -> None:
    op.drop_table("m_site_search_params")
