"""SQLAlchemy の共通基底クラスと監査カラム Mixin。

DB規約により監査カラム ``created_at`` / ``updated_at`` は常にテーブルの最終列に置く。
Mixin 側の ``mapped_column(sort_order=...)`` に大きな値を与えることで、
サブクラスが業務カラムを何本追加しても末尾化がドリフトしないようモデル定義側で機械的に保証する
(業務カラムの既定 sort_order は 0)。列順は ``tests/test_schema_conventions.py`` の
``information_schema.columns`` 突き合わせで回帰テストしている。
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 制約・インデックス名を機械的に決めることで、Alembic の autogenerate が
# 名前なし制約を「差分あり」と誤検出するのを防ぐ。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全モデルの基底クラス。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CreatedAtMixin:
    """作成日時のみを持つ追記専用テーブル向けの Mixin。

    通知履歴・ログのように行を更新しないテーブルでは ``updated_at`` を持たない
    (DB規約「書き込み専用の追記テーブルで updated_at が不要なら created_at のみ」)。
    """

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        sort_order=100,
        comment="レコード作成日時",
    )


class TimestampMixin(CreatedAtMixin):
    """作成日時・更新日時を持つ通常テーブル向けの Mixin。

    ``onupdate`` は SQLAlchemy の ORM/Core UPDATE でのみ発火する。
    スクレイパーの一括 upsert (``ON CONFLICT DO UPDATE``) では発火しないため、
    upsert 側で ``updated_at`` を明示的にセットすること。
    """

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        sort_order=101,
        comment="レコード更新日時",
    )
