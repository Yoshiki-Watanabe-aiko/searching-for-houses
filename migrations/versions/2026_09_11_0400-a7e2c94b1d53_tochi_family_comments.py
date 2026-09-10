"""種別ファミリを列挙するコメントへ土地（TOCHI_BUY）を足す

土地（Phase 9 → ADR 0024・課題#61）を6種別目として足すと、ファミリを列挙している
次のコメントが事実と食い違う。

* ``m_property_types``（表）: 5種別しか並べていない
* ``m_property_types.family``: TOCHI_BUY が無い
* ``m_condition_synonyms.property_family``: TOCHI_BUY が無いうえ、
  「辞書を2部構成にする」は Phase 6 手順5 で `common` を足した時点から古い（実際は3セクション）
* ``m_market_rates.family``: TOCHI_BUY が無い（土地の相場はまだ入れていないことを明記する）

⚠ 実装とコメントの食い違いは、次に読む人が「実装済みのはず」と誤解する温床になる
（→ 課題#28 で `digest_group` の説明が実装と食い違っていた）。

⚠ **手書き。** autogenerate はコメントだけの差分に無関係なドリフトを混ぜるので、
この4つを差し替える差分だけを書く（→ f3c8b1a47d02 と同じ形）。

⚠ 列の追加ではなくコメントの更新なので、監査カラムを最終列に保つための
テーブル再作成は要らない。

Revision ID: a7e2c94b1d53
Revises: f3c8b1a47d02
Create Date: 2026-09-11 04:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a7e2c94b1d53"
down_revision: str | None = "f3c8b1a47d02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ⚠ ORM（`db/models/masters.py`）の comment と**同じ文字列**にしてある。
#    片方だけ直すと、次に autogenerate を回した人が差分の意味を読み違える
NEW_TABLE_COMMENT = "物件種別マスタ（賃貸・新築M・中古M・新築K・中古K・土地）"
OLD_TABLE_COMMENT = "物件種別マスタ（賃貸・新築M・中古M・新築K・中古K）"

NEW_COLUMN_COMMENTS = {
    ("m_property_types", "family"): (
        "種別ファミリ。metric体系・dedup_key構成要素・YAMLスキーマの分岐単位。"
        "CHINTAI=賃貸 / MANSION_BUY=マンション売買 / KODATE_BUY=戸建て売買 / "
        "TOCHI_BUY=土地売買（建物を伴わない）"
    ),
    ("m_condition_synonyms", "property_family"): (
        "適用する種別ファミリ（CHINTAI / MANSION_BUY / KODATE_BUY / TOCHI_BUY）。"
        "NULL=全ファミリ。辞書YAMLのセクション（chintai / common / buy）から機械的に展開する。"
        "⚠ TOCHI_BUY の辞書はまだ無い（土地の設備条件は validate-config が弾く）"
    ),
    ("m_market_rates", "family"): (
        "種別ファミリ。CHINTAI / MANSION_BUY / KODATE_BUY（TOCHI_BUY の相場はまだ入れていない）"
    ),
}

OLD_COLUMN_COMMENTS = {
    ("m_property_types", "family"): (
        "種別ファミリ。metric体系・dedup_key構成要素・YAMLスキーマの分岐単位。"
        "CHINTAI=賃貸 / MANSION_BUY=マンション売買 / KODATE_BUY=戸建て売買"
    ),
    ("m_condition_synonyms", "property_family"): (
        "適用する種別ファミリ（CHINTAI / MANSION_BUY / KODATE_BUY）。NULL=全ファミリ。"
        "売買の証明書・性能評価系の語彙は賃貸と別体系のため辞書を2部構成にする"
    ),
    ("m_market_rates", "family"): "種別ファミリ。CHINTAI / MANSION_BUY / KODATE_BUY",
}


def _escape(comment: str) -> str:
    # ⚠ COMMENT ON はDDLなのでバインド変数を使えない。素直にエスケープする
    return comment.replace("'", "''")


def _apply(table_comment: str, column_comments: dict[tuple[str, str], str]) -> None:
    op.execute(f"COMMENT ON TABLE m_property_types IS '{_escape(table_comment)}'")
    for (table, column), comment in column_comments.items():
        op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{_escape(comment)}'")


def upgrade() -> None:
    _apply(NEW_TABLE_COMMENT, NEW_COLUMN_COMMENTS)


def downgrade() -> None:
    _apply(OLD_TABLE_COMMENT, OLD_COLUMN_COMMENTS)
