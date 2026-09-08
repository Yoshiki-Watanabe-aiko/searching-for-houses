"""m_market_rates のカラムコメントを売買（㎡単価）対応の記述へ更新する

⚠ **事実と食い違うコメントを残さない。** 売買の相場（不動産情報ライブラリ・課題#49）を
入れると、次の3列の説明が実態とずれる。

* ``sample_count``: 「外部の相場では取れないので NULL になる」と書いてあったが、
  不動産情報ライブラリは明細から数えるので**埋まる**。
* ``stat_basis``: 賃貸の値しか列挙していなかった。
* ``segment``: 「賃貸は間取り」しか書いておらず、売買の単価区分に触れていない。

⚠ 実装とコメントの食い違いは、次に読む人が「実装済みのはず」と誤解する温床になる
（→ 課題#28 で `digest_group` の説明が実装と食い違っていた）。

⚠ **手書き。** autogenerate はコメントだけの差分に無関係なドリフトを混ぜるので、
この3列のコメントを差し替える差分だけを書く（→ Phase 5B の教訓）。

⚠ 列の追加ではなくコメントの更新なので、監査カラムを最終列に保つための
テーブル再作成は要らない。

Revision ID: f3c8b1a47d02
Revises: d4f2b81c60ae
Create Date: 2026-09-08 09:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f3c8b1a47d02"
down_revision: str | None = "d4f2b81c60ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ⚠ ORM（`db/models/masters.py`）の comment と**同じ文字列**にしてある。
#    片方だけ直すと、次に autogenerate を回した人が差分の意味を読み違える
NEW_COMMENTS = {
    "segment": (
        "区分。賃貸は正規化済みの間取り（1LDK・2DK…）、売買は単価の種類"
        "（AREA_SQM=マンション専有㎡ / LAND_SQM・FLOOR_SQM=戸建ての土地・延床㎡）。"
        "⚠ 賃貸は集計側と採点側で同じ normalize_layout を通す"
        "（別の規則を当てると突き合わせ0件の原因を切り分けられない）"
    ),
    "stat_basis": (
        "何の相場かを行が自己記述する。rent_listed_mansion / rent_listed_apart="
        "SUUMO の掲載賃料（建物種別）、trade_unit_price_01=不動産取引価格情報 / "
        "trade_unit_price_02=成約価格情報の㎡単価。"
        "⚠ SUUMO の相場ページには管理費の扱いも平均/中央値の別も**書かれていない**。"
        "取り違えると全掲載が一律「相場より高い」と出て例外にならないので、"
        "best/worst は 1.0 を中心と仮定せず実測した ratio 分布に合わせる。"
        "⚠ 取得元の中で母集団が混ざるのは避けられないので、どちらで測ったかを"
        "行に残して後から検証できるようにする（実測: 成約÷取引はマンション0.914・"
        "戸建て1.082 と種別で向きが逆）"
    ),
    "sample_count": (
        "集計に使った件数。⚠ **取得元によって埋まるかが違う**"
        "（SUUMO の家賃相場ページは母数を出さないので NULL、"
        "不動産情報ライブラリは明細から数えるので入る）。"
        "薄いセルを落とす根拠に使う（売買は 10件未満を CSV に書かない）"
    ),
}

OLD_COMMENTS = {
    "segment": (
        "区分。賃貸は正規化済みの間取り（1LDK・2DK…）。"
        "⚠ 集計側と採点側で同じ normalize_layout を通す"
        "（別の規則を当てると突き合わせ0件の原因を切り分けられない）"
    ),
    "stat_basis": (
        "何の相場かを行が自己記述する。rent_listed=掲載賃料。"
        "⚠ SUUMO の相場ページには管理費の扱いも平均/中央値の別も**書かれていない**。"
        "取り違えると全掲載が一律「相場より高い」と出て例外にならないので、"
        "best/worst は 1.0 を中心と仮定せず実測した ratio 分布に合わせる"
    ),
    "sample_count": (
        "集計に使った件数。⚠ **外部の相場では取れないので NULL になる**。"
        "自前集計に切り替えたときだけ入る（薄いセルを除外する根拠に使う）"
    ),
}


def _apply(comments: dict[str, str]) -> None:
    for column, comment in comments.items():
        # ⚠ COMMENT ON はDDLなのでバインド変数を使えない。素直にエスケープする
        escaped = comment.replace("'", "''")
        op.execute(f"COMMENT ON COLUMN m_market_rates.{column} IS '{escaped}'")


def upgrade() -> None:
    _apply(NEW_COMMENTS)


def downgrade() -> None:
    _apply(OLD_COMMENTS)
