"""m_condition_synonyms の一意性制約に property_family を含める

⚠⚠ **同じ表記を複数の物件ファミリへ展開できなかった。**
辞書の1セクションは複数ファミリへ展開する設計（``FAMILY_SECTIONS``）だが、
制約が ``(condition_id, site_id, pattern, is_negative)`` だったため、
`common`（賃貸＋売買）や `buy`（マンション＋戸建て）のように
**同じ表記を2つ以上のファミリへ入れると `sync-dict` が UniqueViolation で落ちる**。

⚠ **`buy` セクションが空だったので表面化していなかった**（→ 課題#4 手順1 で
両ファミリ展開に直したとき、DB側の制約を確かめていなかった）。

⚠ **手書き。** autogenerate は無関係なドリフト（部分ユニーク索引の削除・制約名の
逆戻し）まで拾うので、この制約だけを差し替える差分を自分で書く（→ Phase 5B の教訓）。

⚠ 列の追加ではなく制約の付け替えなので、監査カラムを最終列に保つための
テーブル再作成は要らない。

Revision ID: b3d17c5f9a24
Revises: e2a490d84445
Create Date: 2026-09-06 22:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b3d17c5f9a24"
down_revision: str | None = "e2a490d84445"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "m_condition_synonyms"
OLD_NAME = "uq_m_condition_synonyms_condition_id_site_id_pattern_is_a35c"
NEW_NAME = "uq_m_condition_synonyms_condition_site_family_pattern_neg"
OLD_COLUMNS = ("condition_id", "site_id", "pattern", "is_negative")
NEW_COLUMNS = ("condition_id", "site_id", "property_family", "pattern", "is_negative")


def _recreate(name: str, columns: tuple[str, ...], drop: str) -> None:
    # ⚠ NULLS NOT DISTINCT を維持する。site_id NULL（全サイト共通パターン）同士を
    # 重複とみなすためで、外すと同じ表記を何度でも入れられてしまう。
    op.drop_constraint(drop, TABLE, type_="unique")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT {name} "
        f"UNIQUE NULLS NOT DISTINCT ({', '.join(columns)})"
    )


def upgrade() -> None:
    _recreate(NEW_NAME, NEW_COLUMNS, OLD_NAME)


def downgrade() -> None:
    # ⚠ 戻すときは、同じ表記が複数ファミリに入っている行を先に消さないと制約を張れない。
    # 賃貸（CHINTAI）を残し、売買側の重複だけを落とす。
    op.execute(
        f"""
        DELETE FROM {TABLE} a
         USING {TABLE} b
         WHERE a.id > b.id
           AND a.condition_id = b.condition_id
           AND a.pattern = b.pattern
           AND a.is_negative = b.is_negative
           AND a.site_id IS NOT DISTINCT FROM b.site_id
        """
    )
    _recreate(OLD_NAME, OLD_COLUMNS, NEW_NAME)
