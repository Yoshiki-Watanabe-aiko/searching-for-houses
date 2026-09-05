"""add m_market_rates

⚠ **手書き。** autogenerate は無関係なドリフト（部分ユニーク索引の削除・制約名の
逆戻し）まで拾うので、この表だけを足す差分を自分で書く（→ Phase 5B の教訓）。

Revision ID: e2a490d84445
Revises: e5c8a3f1d904
Create Date: 2026-09-05 14:40

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a490d84445"
down_revision: str | None = "e5c8a3f1d904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "m_market_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="相場ID"),
        sa.Column(
            "family",
            sa.String(length=20),
            nullable=False,
            comment="種別ファミリ。CHINTAI / MANSION_BUY / KODATE_BUY",
        ),
        sa.Column(
            "source",
            sa.String(length=30),
            nullable=False,
            comment=(
                "取得元。suumo_soba=SUUMO家賃相場 / mlit_library=国交省 不動産情報ライブラリ。"
                "⚠ 取得元を差し替えても過去の行を消さずに済むよう列で持つ"
            ),
        ),
        sa.Column(
            "level",
            sa.String(length=10),
            nullable=False,
            comment=(
                "粒度。city=市区。⚠ 都道府県へは落とさない"
                "（相場が一様な範囲ではない。粗い粒度の誤った相場は欠損より有害 → ADR 0013）"
            ),
        ),
        sa.Column("city_id", sa.Integer(), nullable=False, comment="市区町村ID"),
        sa.Column(
            "segment",
            sa.String(length=20),
            nullable=False,
            comment=(
                "区分。賃貸は正規化済みの間取り（1LDK・2DK…）。"
                "⚠ 集計側と採点側で同じ normalize_layout を通す"
                "（別の規則を当てると突き合わせ0件の原因を切り分けられない）"
            ),
        ),
        sa.Column(
            "stat_basis",
            sa.String(length=30),
            nullable=False,
            comment=(
                "何の相場かを行が自己記述する。rent_listed=掲載賃料。"
                "⚠ SUUMO の相場ページには管理費の扱いも平均/中央値の別も**書かれていない**。"
                "取り違えると全掲載が一律「相場より高い」と出て例外にならないので、"
                "best/worst は 1.0 を中心と仮定せず実測した ratio 分布に合わせる"
            ),
        ),
        sa.Column(
            "rate_value",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="相場の値。賃貸は月額（円）、売買は㎡単価（円/㎡）。単位は stat_basis が示す",
        ),
        sa.Column(
            "sample_count",
            sa.Integer(),
            nullable=True,
            comment=(
                "集計に使った件数。⚠ **外部の相場では取れないので NULL になる**。"
                "自前集計に切り替えたときだけ入る（薄いセルを除外する根拠に使う）"
            ),
        ),
        sa.Column(
            "period",
            sa.String(length=20),
            nullable=False,
            comment="相場の対象期間（2026-09 など）。⚠ 履歴を消さずに追記し、採点は最新を採る",
        ),
        sa.Column(
            "acquired_on",
            sa.Date(),
            nullable=False,
            comment="取得日。⚠ 鮮度が切れても採点は続くので、古さに気づく手掛かりとして持つ",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="作成日時",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="更新日時",
        ),
        sa.ForeignKeyConstraint(["city_id"], ["m_cities.id"], name="fk_m_market_rates_city_id"),
        sa.PrimaryKeyConstraint("id", name="pk_m_market_rates"),
        sa.UniqueConstraint(
            "family",
            "source",
            "level",
            "city_id",
            "segment",
            "period",
            name="uq_m_market_rates_key",
        ),
        comment="相場（市区×間取りの家賃相場・売買の単価）。割安さの分母になる",
    )
    op.create_index("ix_m_market_rates_city_id", "m_market_rates", ["city_id"])


def downgrade() -> None:
    op.drop_index("ix_m_market_rates_city_id", table_name="m_market_rates")
    op.drop_table("m_market_rates")
