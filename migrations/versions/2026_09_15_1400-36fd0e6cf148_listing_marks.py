"""掲載への手動の印 t_listing_marks を追加する（お気に入り・除外・メモ）

ブラウザ閲覧画面（→ ADR 0026・課題#68）から付ける印を置く。

⚠ **印は掲載（``t_listings.id``）に付け、グループには付けない。** ``sync_groups`` は
メンバー0件のグループを消し、組み直しでグループIDが振り直されうるので、グループに
付けると印が黙って外れる。グループへの効果は読み出し時に導く。

⚠ **除外は採点・順位・個別通知を変えない。** 効くのは閲覧画面の既定表示と
日次ダイジェストの抽出だけ（ユーザー判断 2026-09-15）。

⚠ テーブル新設なので、監査カラムを最終列に保つための再作成は要らない
（a4f7d3e91c26 と同じ判断）。

⚠ **手書き。** autogenerate は無関係なドリフトを拾う（a4f7d3e91c26 の注記と同じ理由）。

⚠ 本番へ適用するときは ``scan`` が走っていない時間に流す。FK の作成で
``t_listings`` に SHARE ROW EXCLUSIVE ロックを取るので、取得中の upsert と競合すると
``env.py`` の lock_timeout で失敗する。

Revision ID: 36fd0e6cf148
Revises: a7e2c94b1d53
Create Date: 2026-09-15 14:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "36fd0e6cf148"
down_revision = "a7e2c94b1d53"
branch_labels = None
depends_on = None

# メモの上限（文字数）。モデルの MARK_MEMO_MAX_CHARS と同じ値
# （マイグレーションはその時点の値で固定するため import しない）。
_MEMO_MAX_CHARS = 2000


def upgrade() -> None:
    op.create_table(
        "t_listing_marks",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="印ID"),
        sa.Column(
            "listing_id",
            sa.BigInteger(),
            nullable=False,
            comment=(
                "掲載ID。印は掲載ごとに持ち、同じ名寄せグループのどれか1件に印があれば"
                "グループ全体に効かせる（読み出し時に導く）"
            ),
        ),
        sa.Column(
            "is_favorite",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="お気に入り。閲覧画面の絞り込みにだけ使う（採点・順位・通知には効かない）",
        ),
        sa.Column(
            "is_excluded",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=(
                "除外。閲覧画面の既定表示から隠し、日次ダイジェストでは飛ばして繰り上げる。"
                "rank_in_pattern・採点・個別通知・成約確認は変えない"
            ),
        ),
        sa.Column(
            "memo",
            sa.Text(),
            nullable=True,
            comment=f"自由メモ（{_MEMO_MAX_CHARS}字まで）。利用者の入力でスクレイピング由来ではない",
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
            f"memo IS NULL OR char_length(memo) <= {_MEMO_MAX_CHARS}",
            name="ck_t_listing_marks_memo_length",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["t_listings.id"],
            name="fk_t_listing_marks_listing_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_t_listing_marks"),
        sa.UniqueConstraint("listing_id", name="uq_t_listing_marks_listing_id"),
        comment="掲載への手動の印（お気に入り・除外・メモ）。ブラウザ閲覧画面から付ける",
    )


def downgrade() -> None:
    op.drop_table("t_listing_marks")
