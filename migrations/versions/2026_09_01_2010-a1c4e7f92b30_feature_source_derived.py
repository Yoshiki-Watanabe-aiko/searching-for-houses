"""t_property_features.source に DERIVED を追記

Phase 1 で「2階以上」「最上階」のような閾値条件を型付き列から導出するようにしたため、
抽出元の説明に DERIVED を加える。列そのものは変えないのでテーブル再作成は不要。

Revision ID: a1c4e7f92b30
Revises: 118b7160b30d
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op

revision = "a1c4e7f92b30"
down_revision = "118b7160b30d"
branch_labels = None
depends_on = None

NEW_COMMENT = (
    "抽出元。LIST=一覧ページ / DETAIL=詳細ページ / SITE_TAG=サイトの構造化タグ / "
    "DERIVED=型付き列からの導出（2階以上・最上階など閾値条件）"
)
OLD_COMMENT = "抽出元。LIST=一覧ページ / DETAIL=詳細ページ / SITE_TAG=サイトの構造化タグ"


def upgrade() -> None:
    op.alter_column("t_property_features", "source", comment=NEW_COMMENT)


def downgrade() -> None:
    op.alter_column("t_property_features", "source", comment=OLD_COMMENT)
