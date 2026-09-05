"""ハザード評価 m_hazard_levels を追加する

Revision ID: e5c8a3f1d904
Revises: c9a1f4e2b70d
Create Date: 2026-09-05

洪水（国土数値情報 A31・想定最大規模）と土砂災害（同 A33）の危険度を
**丁目・町の単位**で持つ（→ 課題#46）。丁目の面は e-Stat「町丁・字等境界データ」。

⚠ **ポリゴンの交差計算はこの表に入る前に終わっている。**
``scripts/tools/build_hazard_levels.py`` がオフラインで計算し、
``sync-hazards`` が集計済みの値を入れる。``scan`` / ``rescore`` は JOIN するだけ。
再採点が「DBの属性からの純関数」であること（→ requirements.md §6.1）を崩さないため。

⚠ **``m_address_points.id`` を参照しない。** ``sync-addresses`` は全置換なので
id が振り直される。突き合わせは ``normalized_key`` で行う（c9a1f4e2b70d の注記と同じ）。

⚠⚠ **「区域外」と「未解決」を区別する設計になっている。**
丁目を照合できたら、区域に掛からなくても ``value = 0`` の行を書く。
行が無い＝照合できなかった、という意味。混ぜると
「危険なのに情報が無いから減点されない」掲載が「安全」と同じ扱いになり、
**例外にならないまま順位が狂う**（UR の賃料 NULL・ATHOME の駅同定0件と同型）。

⚠ **縦持ち**（``hazard_type`` × ``aggregation`` の行）にしてある。高潮・津波の追加や
集計方式の変更が**行の追加だけ**で済む。列で持つと監査カラム末尾維持のための
テーブル再作成が要る。

⚠ **``t_listings`` は無変更。** 掲載との紐付けは ``address_normalized`` からの
JOIN で引ける（c9a1f4e2b70d・b8e4c25f7a19・a4f7d3e91c26 と同じ判断）。

⚠ **CHECK 制約を張っていない。** ``hazard_type`` は第2弾で高潮・津波が増える予定で、
DDL 変更なしに足せることを優先した。値域の検証は ``sync-hazards`` のコード側で行う。

⚠ このマイグレーションは**手書き**である（autogenerate は無関係なドリフトを拾う）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5c8a3f1d904"
down_revision = "c9a1f4e2b70d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "m_hazard_levels",
        sa.Column("id", sa.Integer(), nullable=False, comment="ハザード評価ID"),
        sa.Column(
            "normalized_key",
            sa.String(length=200),
            nullable=False,
            comment=(
                "丁目まで（丁目の無い町は町名まで）の正規化キー。"
                "m_address_points.normalized_key・t_listings.address_normalized と同じ規則"
            ),
        ),
        sa.Column(
            "level",
            sa.String(length=10),
            nullable=False,
            comment=(
                "粒度。chome=丁目 / town=町"
                "（配下の丁目を集約した値。町名までしか出さないサイト向け）"
            ),
        ),
        sa.Column(
            "hazard_type",
            sa.String(length=30),
            nullable=False,
            comment=(
                "災害の種類。flood=洪水浸水想定（A31 想定最大規模） / "
                "landslide=土砂災害警戒区域（A33 警戒＋特別） / "
                "landslide_special=同 特別警戒のみ"
            ),
        ),
        sa.Column(
            "aggregation",
            sa.String(length=20),
            nullable=False,
            comment=(
                "集計方式。area_ratio=丁目に占める区域の面積比（0〜1） / "
                "rank_avg=丁目全面積で加重した平均ランク（区域外を0として含む） / "
                "rank_max=丁目内の最大ランク"
            ),
        ),
        sa.Column(
            "value",
            sa.Numeric(precision=8, scale=4),
            nullable=False,
            comment="集計値。⚠ 区域外は 0 を明示的に書く（行が無い＝未解決と区別するため）",
        ),
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=False,
            comment="出典と版（例: mlit_a31-22 / mlit_a33-23）。区域の指定替えを追うために持つ",
        ),
        sa.Column(
            "acquired_on",
            sa.Date(),
            nullable=False,
            comment="原典の取得日。年次更新の判断に使う",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_key",
            "level",
            "hazard_type",
            "aggregation",
            name="uq_m_hazard_levels_normalized_key_level_hazard_type_aggregation",
        ),
        comment="ハザード評価（丁目・町単位。国土数値情報 A31・A33 が正典）",
    )
    op.create_index(
        "ix_m_hazard_levels_normalized_key",
        "m_hazard_levels",
        ["normalized_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_m_hazard_levels_normalized_key", table_name="m_hazard_levels")
    op.drop_table("m_hazard_levels")
