"""住所マスタ m_address_points を追加する

Revision ID: c9a1f4e2b70d
Revises: a4f7d3e91c26
Create Date: 2026-09-05

``normalize_address`` は「丁目表記が無ければ最初の数字塊を丁目とみなす」規則で
都市部の ``西早稲田3-1-1`` を丁目まで切り詰めている（→ ADR 0012）。
⚠ **丁目が存在しない町に番地が付くと、その番地がそのまま丁目になる**
（``埼玉県深谷市中瀬1480丁目``）。実測（2026-09-05）で active 掲載の
**5.4%（1,074件）** が存在しない住所を ``dedup_key`` にしていた（→ 課題#48・ADR 0020）。
⚠ **例外にならず件数も減らない**（ユニーク率が高く見えるだけ）ので、
外部の正典と突き合わせるまで検出できない。

この表は「その町に丁目が実在するか」の判定に使う。正典は国土交通省
「位置参照情報」で、原典の ``大字・字・丁目区分コード``（1=大字 / 2=字 / 3=丁目）が
そのまま判定材料になるため、町名の正規表現から推測せずに済む。
ハザード評価（→ 課題#46）の丁目代表点も同じ表から引く。

⚠ **``city_jis_code`` に FK を張らない。** 参照先の ``m_cities.jis_code`` は
部分ユニーク索引（NULL を許す）で、FK の参照先にできない。

⚠ **``t_listings`` は無変更。** 掲載との紐付けは ``address_normalized`` からの
JOIN で引けるので中間テーブルも要らない。テーブル新設には監査カラム末尾維持のための
再作成コストが掛からない（f3a8b27c9d51・b8e4c25f7a19・a4f7d3e91c26 と同じ判断）。

⚠ このマイグレーションは**手書き**である。autogenerate は無関係なドリフトを
大量に拾う（b8e4c25f7a19 の注記と同じ理由）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9a1f4e2b70d"
down_revision = "a4f7d3e91c26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "m_address_points",
        sa.Column("id", sa.Integer(), nullable=False, comment="住所ポイントID"),
        sa.Column(
            "city_jis_code",
            sa.String(length=5),
            nullable=False,
            comment=(
                "市区町村コード（JIS5桁）。m_cities.jis_code と同じ体系だがFKは張らない"
            ),
        ),
        sa.Column(
            "town_key",
            sa.String(length=200),
            nullable=False,
            comment=(
                "町名までの正規化キー（例: 埼玉県深谷市中瀬）。"
                "⚠ 丁目の実在判定の主キーになるので、SQLの文字列操作で導かず物理列で持つ"
            ),
        ),
        sa.Column(
            "chome_number",
            sa.SmallInteger(),
            nullable=True,
            comment="丁目番号。町名までの行（大字・字）は NULL",
        ),
        sa.Column(
            "normalized_key",
            sa.String(length=200),
            nullable=False,
            comment=(
                "丁目まで（丁目の無い町は町名まで）の正規化キー。"
                "t_listings.address_normalized と同じ normalize_address を通して作る"
            ),
        ),
        sa.Column(
            "level",
            sa.String(length=10),
            nullable=False,
            comment=(
                "粒度。chome=丁目 / town=大字・字（原典の区分コード 3 かどうかで決まる）"
            ),
        ),
        sa.Column(
            "lon",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
            comment="代表点の経度。ハザードのポリゴン照合に使う",
        ),
        sa.Column(
            "lat",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
            comment="代表点の緯度。ハザードのポリゴン照合に使う",
        ),
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=False,
            comment=(
                "出典と版（例: mlit_isj_19.0b）。原典が改訂されたことを後から言えるようにする"
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
            "(level = 'chome' AND chome_number IS NOT NULL)"
            " OR (level = 'town' AND chome_number IS NULL)",
            name="ck_m_address_points_address_points_level",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_m_address_points"),
        sa.UniqueConstraint(
            "normalized_key", "level", name="uq_m_address_points_normalized_key_level"
        ),
        comment=(
            "住所マスタ（位置参照情報が正典。丁目の実在判定とハザードの代表点に使う）"
        ),
    )
    op.create_index(
        "ix_m_address_points_city_jis_code", "m_address_points", ["city_jis_code"]
    )
    op.create_index("ix_m_address_points_town_key", "m_address_points", ["town_key"])


def downgrade() -> None:
    op.drop_index("ix_m_address_points_town_key", table_name="m_address_points")
    op.drop_index("ix_m_address_points_city_jis_code", table_name="m_address_points")
    op.drop_table("m_address_points")
