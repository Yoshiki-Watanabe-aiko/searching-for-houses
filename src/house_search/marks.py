"""掲載への手動の印（お気に入り・除外・メモ → 課題#68・ADR 0026）。

印は**掲載ごとに保存**し、名寄せグループへの効果は**読み出し時に導く**
（同じグループのどれか1件に印があれば、その物件全体に効かせる）。

⚠ **グループIDに印を付けない。** ``sync_groups`` はメンバー0件のグループを消し、
組み直しでIDが振り直されうる。グループに付けると印が黙って外れる。

⚠ 「同じグループのどれか1件」の判定はここの ``mark_exists_sql`` の1箇所に置く。
閲覧画面の一覧と日次ダイジェストの双方がこれを使う（片方だけ規則を変えると、
画面では隠れているのにダイジェストに載る、という食い違いが例外にならずに起きる）。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from house_search.db.models.transactions import MARK_MEMO_MAX_CHARS

MarkFlag = Literal["is_excluded", "is_favorite"]
_FLAGS: frozenset[str] = frozenset({"is_excluded", "is_favorite"})
# SQL の別名に使ってよい文字（呼び出し側の固定値だけを受ける。利用者の入力は渡さない）
_ALIAS_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz_")


class MemoTooLongError(ValueError):
    """メモが上限を超えた（黙って切り詰めない）。"""


def mark_exists_sql(flag: MarkFlag, listing_alias: str = "p") -> str:
    """「掲載 ``listing_alias`` と同じグループ（未グループなら自身）に印がある」の SQL 片。

    ⚠ ``listing_alias`` は ``t_listings`` の別名。未グループ（``group_id IS NULL``）のとき
    ``mp.group_id = p.group_id`` は NULL＝偽になるので、自身の印だけを見る。
    """
    if flag not in _FLAGS:
        raise ValueError(f"未知の印: {flag}")
    if not listing_alias or not set(listing_alias) <= _ALIAS_CHARS:
        raise ValueError(f"別名に使えない文字が含まれています: {listing_alias!r}")
    alias = listing_alias
    return (
        "EXISTS ("
        " SELECT 1 FROM t_listing_marks m"
        " JOIN t_listings mp ON mp.id = m.listing_id"
        f" WHERE m.{flag}"
        f" AND (mp.id = {alias}.id OR mp.group_id = {alias}.group_id)"
        ")"
    )


@dataclass(frozen=True, slots=True)
class GroupMarks:
    """物件1件（グループ全体）から見た印。"""

    is_favorite: bool = False
    is_excluded: bool = False
    memo_count: int = 0


NO_MARKS = GroupMarks()


_GROUP_MARKS = text(
    """
    SELECT target.id AS listing_id,
           bool_or(m.is_favorite) AS is_favorite,
           bool_or(m.is_excluded) AS is_excluded,
           count(m.memo) AS memo_count
    FROM t_listings target
    JOIN t_listings member
      ON member.id = target.id OR member.group_id = target.group_id
    JOIN t_listing_marks m ON m.listing_id = member.id
    WHERE target.id = ANY(:ids)
    GROUP BY target.id
    """
)


def group_marks(conn: Connection, listing_ids: Sequence[int]) -> dict[int, GroupMarks]:
    """掲載IDごとに、グループ全体へ効く印を返す。印の無い掲載も既定値で埋める。"""
    ids = list(listing_ids)
    result: dict[int, GroupMarks] = dict.fromkeys(ids, NO_MARKS)
    if not ids:
        return result
    for row in conn.execute(_GROUP_MARKS, {"ids": ids}):
        result[row.listing_id] = GroupMarks(
            is_favorite=bool(row.is_favorite),
            is_excluded=bool(row.is_excluded),
            memo_count=int(row.memo_count),
        )
    return result


@dataclass(frozen=True, slots=True)
class ListingMarkRow:
    """掲載1件に保存された印そのもの。"""

    listing_id: int
    is_favorite: bool
    is_excluded: bool
    memo: str | None


def marks_of(conn: Connection, listing_ids: Sequence[int]) -> dict[int, ListingMarkRow]:
    """掲載ごとに保存された印（グループへ畳まない）。印の無い掲載はキー自体が無い。"""
    ids = list(listing_ids)
    if not ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT listing_id, is_favorite, is_excluded, memo "
            "FROM t_listing_marks WHERE listing_id = ANY(:ids)"
        ),
        {"ids": ids},
    )
    return {
        row.listing_id: ListingMarkRow(
            listing_id=row.listing_id,
            is_favorite=row.is_favorite,
            is_excluded=row.is_excluded,
            memo=row.memo,
        )
        for row in rows
    }


def normalize_memo(memo: str | None) -> str | None:
    """メモを保存する形へ整える。空なら None。上限超えは例外にする。

    ⚠ **黙って切り詰めない**（書いた内容が消えたことに気づけない）。
    ⚠ 改行・タブ以外の制御文字は落とす（画面の表示を崩すだけで意味を持たない）。
    ⚠ 改行は CRLF を LF に揃えてから数える（ブラウザは CRLF で送るので、
    揃えないと画面の文字数表示と上限の判定がずれる）。
    """
    if memo is None:
        return None
    value = memo.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) != "Cc").strip()
    if not value:
        return None
    if len(value) > MARK_MEMO_MAX_CHARS:
        raise MemoTooLongError(
            f"メモは{MARK_MEMO_MAX_CHARS}字までです（{len(value)}字あります）"
        )
    return value


def save_group_mark(
    conn: Connection,
    *,
    listing_id: int,
    is_favorite: bool,
    is_excluded: bool,
    memo: str | None,
) -> None:
    """閲覧画面のフォームの内容を保存する。

    - 印とメモは ``listing_id`` の行に書く
    - **チェックを外した印は、同じグループの他の掲載からも外す。**
      画面はグループ全体の印を表示しているので、外したのに別の掲載の印で
      残り続けると「外せない」ように見える
    - 3項目とも空になった行は消す（表を「印のある掲載」だけに保つ）
    """
    cleaned = normalize_memo(memo)
    conn.execute(
        text(
            """
            INSERT INTO t_listing_marks (
                listing_id, is_favorite, is_excluded, memo, created_at, updated_at
            ) VALUES (:listing_id, :is_favorite, :is_excluded, :memo, now(), now())
            ON CONFLICT (listing_id) DO UPDATE SET
                is_favorite = EXCLUDED.is_favorite,
                is_excluded = EXCLUDED.is_excluded,
                memo = EXCLUDED.memo,
                updated_at = now()
            """
        ),
        {
            "listing_id": listing_id,
            "is_favorite": is_favorite,
            "is_excluded": is_excluded,
            "memo": cleaned,
        },
    )
    for flag, on in (("is_favorite", is_favorite), ("is_excluded", is_excluded)):
        if not on:
            _clear_group_flag(conn, listing_id=listing_id, flag=flag)
    _delete_empty_marks(conn)


def set_group_flag(conn: Connection, *, listing_id: int, flag: MarkFlag, on: bool) -> None:
    """印を1つだけ付ける／外す（一覧の行のボタン用）。メモと他方の印は変えない。

    ⚠ 外すときは同じグループの他の掲載からも外す（``save_group_mark`` と同じ理由）。
    """
    if flag not in _FLAGS:
        raise ValueError(f"未知の印: {flag}")
    if on:
        # flag は _FLAGS の固定値だけ（利用者の入力は SQL に入らない）
        conn.execute(
            text(
                f"""
                INSERT INTO t_listing_marks (listing_id, {flag}, created_at, updated_at)
                VALUES (:listing_id, true, now(), now())
                ON CONFLICT (listing_id) DO UPDATE SET {flag} = true, updated_at = now()
                """
            ),
            {"listing_id": listing_id},
        )
        return
    _clear_group_flag(conn, listing_id=listing_id, flag=flag)
    _delete_empty_marks(conn)


def _clear_group_flag(conn: Connection, *, listing_id: int, flag: str) -> None:
    """``listing_id`` 自身と同じグループの全掲載から印を外す。"""
    if flag not in _FLAGS:
        raise ValueError(f"未知の印: {flag}")
    conn.execute(
        text(
            f"""
            UPDATE t_listing_marks m SET {flag} = false, updated_at = now()
            FROM t_listings member, t_listings target
            WHERE target.id = :listing_id
              AND (member.id = target.id OR member.group_id = target.group_id)
              AND m.listing_id = member.id
              AND m.{flag}
            """
        ),
        {"listing_id": listing_id},
    )


def _delete_empty_marks(conn: Connection) -> None:
    """印もメモも無くなった行を消す（表を「印のある掲載」だけに保つ）。"""
    conn.execute(
        text(
            "DELETE FROM t_listing_marks WHERE NOT is_favorite AND NOT is_excluded AND memo IS NULL"
        )
    )
