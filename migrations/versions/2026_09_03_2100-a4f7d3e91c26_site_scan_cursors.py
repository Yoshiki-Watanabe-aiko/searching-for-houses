"""市区ローテーションのカーソル表 t_site_scan_cursors を追加する

Revision ID: a4f7d3e91c26
Revises: b8e4c25f7a19
Create Date: 2026-09-03

HOMES・ATHOME は**1回の実行で取れるリクエスト数に上限がある**（実測 2026-09-03。
HOMES 5件で HTTP 202＋空ボディ、ATHOME 4件でパズル認証ページ → 課題#36）。
⚠ **間隔を広げても上限は動かない**（HOMES は4秒でも10秒でも6件目）ため、
82市区を毎回先頭から舐める限り後ろの市区は永久に取れない。
1回の実行では上限ぶんの市区だけ取り、**次回は続きの市区から**始める。

⚠ **キーが (サイト, パターン) なのは帯が2つあるため。** HOMES は両帯の
``sites:`` に載っており、素朴に実装すると1回の ``scan`` で 5+5=10 リクエストが
飛び、後半の帯が全部 202 になる。

⚠ **``m_sites`` へ列を足さない。** 監査カラムを最終列に保つDB規約から
列追加はテーブル再作成を伴うが、**テーブル新設にはそのコストが掛からない**
（f3a8b27c9d51・b8e4c25f7a19 と同じ判断）。

⚠ このマイグレーションは**手書き**である。autogenerate は無関係なドリフトを
大量に拾う（b8e4c25f7a19 の注記と同じ理由）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4f7d3e91c26"
down_revision = "b8e4c25f7a19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_site_scan_cursors",
        sa.Column("site_id", sa.Integer(), nullable=False, comment="対象サイトID"),
        sa.Column(
            "pattern_name",
            sa.String(length=255),
            nullable=False,
            comment="検索パターン名（YAML の name）",
        ),
        sa.Column(
            "last_city_jis",
            sa.String(length=5),
            nullable=True,
            comment="最後に取得した市区のJIS5桁。次回はこれより大きい最初の市区から始める",
        ),
        sa.Column(
            "last_scanned_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="この組で最後にローテーションを回した日時。NULL=未実行（最優先で回す）",
        ),
        sa.Column(
            "last_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="最後にローテーションを回した実行ID。同一実行で予算を二重消費しないための印",
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
        sa.ForeignKeyConstraint(
            ["site_id"],
            ["m_sites.id"],
            name="fk_t_site_scan_cursors_site_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("site_id", "pattern_name", name="pk_t_site_scan_cursors"),
        comment="サイト×検索パターンごとの市区ローテーション位置",
    )


def downgrade() -> None:
    op.drop_table("t_site_scan_cursors")
