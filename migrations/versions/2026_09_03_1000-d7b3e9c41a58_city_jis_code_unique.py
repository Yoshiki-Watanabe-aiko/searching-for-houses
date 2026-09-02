"""市区町村マスタ: 誤った jis_code を訂正し、部分ユニーク索引を張る

Revision ID: d7b3e9c41a58
Revises: c2d5f8a13e47
Create Date: 2026-09-03

``m_cities.jis_code`` は市区必須サイト（SUUMO/GOO/ABLE/賃貸EX/EHEYA/SMOCCA/
APAMAN）の検索URLを組み立てる値そのものなので、誤りは「別の市区の一覧を
叩く」に直結する。ところが Phase 2 でエイブルのエリア索引から部分文字列一致で
補完したため、実測で5件の誤りが見つかった（-> ADR 0014・課題#16）。

    静岡県 浜松市中央区  22131 -> 22138  ) 2024年の区再編（7区->3区）に追随して
    静岡県 浜松市浜名区  22132 -> 22139  ) おらず、新しい区名に旧区のコードが
    静岡県 浜松市天竜区  22133 -> 22140  ) 付いていた
    愛知県 名古屋市      23234 -> 23100    北名古屋市のコードが混入していた
    大阪府 大阪市        27227 -> 27100    東大阪市のコードが混入していた

後者2件は **同じコードを持つ行が2つある**状態だった（名古屋市と北名古屋市が
ともに 23234）。訂正してから部分ユニーク索引を張り、以後この壊れ方を
DB側で弾く。訂正が先でないと索引の作成そのものが失敗する。

列の追加はしないので、監査カラムを末尾に保つためのテーブル再作成は不要
（DB規約の再作成手順は列の「追加」に対するもの）。データの訂正は
db/seed/06_cities.sql でも冪等に行われるが、索引を張れる状態にするために
マイグレーション側にも置く。
"""

from __future__ import annotations

from alembic import op

revision = "d7b3e9c41a58"
down_revision = "c2d5f8a13e47"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_m_cities_jis_code"

# (都道府県, canonical_name, 誤コード, 正コード)
CODE_FIXES = [
    ("静岡県", "浜松市中央区", "22131", "22138"),
    ("静岡県", "浜松市浜名区", "22132", "22139"),
    ("静岡県", "浜松市天竜区", "22133", "22140"),
    ("愛知県", "名古屋市", "23234", "23100"),
    ("大阪府", "大阪市", "27227", "27100"),
]


def upgrade() -> None:
    for prefecture, canonical_name, wrong, correct in CODE_FIXES:
        op.execute(
            f"""
            UPDATE m_cities
               SET jis_code = '{correct}', updated_at = now()
             WHERE prefecture = '{prefecture}'
               AND canonical_name = '{canonical_name}'
               AND jis_code IS DISTINCT FROM '{correct}'
            """
        )
    # NULL を許したまま一意にしたいので部分索引にする。総務省コード表に
    # 載っていない自治体（廃置分合の残り）は jis_code NULL のまま残せる。
    op.execute(
        f"CREATE UNIQUE INDEX {INDEX_NAME} ON m_cities (jis_code) WHERE jis_code IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    # 誤コードは戻さない。戻すと重複が復活して索引を張り直せなくなるうえ、
    # 「別の市区のURLを叩く」という実害のある状態へ意図的に戻すことになる。
