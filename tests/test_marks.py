"""掲載への手動の印（お気に入り・除外・メモ → 課題#68・ADR 0026）。

⚠⚠ **印は掲載ごとに持ち、グループへの効果は読み出し時に導く。** グループIDに付けると
``sync_groups`` の組み直しでIDが振り直されたとき、印が例外にならずに黙って外れる。
⚠ チェックを外した印は同じグループの他の掲載からも外す（画面はグループ全体の印を
出しているので、外したのに残ると「外せない」ように見える）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search import marks
from house_search.db.models.transactions import MARK_MEMO_MAX_CHARS

# ============================================================
# DB不要
# ============================================================


def test_未知の印はSQLに入れない() -> None:
    with pytest.raises(ValueError):
        marks.mark_exists_sql("is_deleted")  # type: ignore[arg-type]


@pytest.mark.parametrize("alias", ["p; DROP TABLE t_listings", "P", "", "p1"])
def test_別名に使えない文字は弾く(alias: str) -> None:
    with pytest.raises(ValueError):
        marks.mark_exists_sql("is_excluded", alias)


def test_メモは空なら保存しない() -> None:
    assert marks.normalize_memo(None) is None
    assert marks.normalize_memo("  \r\n ") is None


def test_メモの改行はLFに揃え制御文字を落とす() -> None:
    assert marks.normalize_memo("一行目\r\n二行目\x07\t") == "一行目\n二行目"


def test_メモが上限を超えたら黙って切り詰めない() -> None:
    assert marks.normalize_memo("あ" * MARK_MEMO_MAX_CHARS) == "あ" * MARK_MEMO_MAX_CHARS
    with pytest.raises(marks.MemoTooLongError):
        marks.normalize_memo("あ" * (MARK_MEMO_MAX_CHARS + 1))


def test_上限はCRLFをLFに揃えてから数える() -> None:
    """ブラウザは改行を CRLF で送る。揃えずに数えると画面の maxlength と食い違う。"""
    memo = "\r\n".join(["あ"] * (MARK_MEMO_MAX_CHARS // 2))
    assert marks.normalize_memo(memo) is not None


# ============================================================
# DB統合
# ============================================================


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _listing(conn: Connection, *, group_id: int | None = None) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title, status, group_id,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :type_id, :external_id, 'https://example.invalid/m', '印テスト',
                'active', :group_id, now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "type_id": type_id,
            "external_id": f"marks-{uuid.uuid4().hex}",
            "group_id": group_id,
        },
    ).scalar_one()


def _group(conn: Connection) -> int:
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    return conn.execute(
        text(
            "INSERT INTO t_listing_groups (dedup_key, property_type_id, member_count) "
            "VALUES (:key, :type_id, 2) RETURNING id"
        ),
        {"key": uuid.uuid4().hex, "type_id": type_id},
    ).scalar_one()


def _excluded_ids(conn: Connection, ids: list[int]) -> set[int]:
    rows = conn.execute(
        text(
            f"SELECT p.id FROM t_listings p WHERE p.id = ANY(:ids) "
            f"AND {marks.mark_exists_sql('is_excluded')}"
        ),
        {"ids": ids},
    )
    return {row.id for row in rows}


@pytest.mark.db
def test_グループの1件に付けた除外がグループ全体に効く(conn: Connection) -> None:
    group_id = _group(conn)
    representative = _listing(conn, group_id=group_id)
    member = _listing(conn, group_id=group_id)
    alone = _listing(conn)

    marks.save_group_mark(conn, listing_id=member, is_favorite=False, is_excluded=True, memo=None)

    assert _excluded_ids(conn, [representative, member, alone]) == {representative, member}
    summary = marks.group_marks(conn, [representative, alone])
    assert summary[representative].is_excluded is True
    assert summary[alone] == marks.NO_MARKS


@pytest.mark.db
def test_未グループの掲載は自分の印だけを見る(conn: Connection) -> None:
    """⚠ group_id が NULL どうしを「同じグループ」と数えない（NULL = NULL は偽）。"""
    first = _listing(conn)
    second = _listing(conn)

    marks.set_group_flag(conn, listing_id=first, flag="is_excluded", on=True)

    assert _excluded_ids(conn, [first, second]) == {first}


@pytest.mark.db
def test_グループの組み直しで印が消えない(conn: Connection) -> None:
    """グループIDが振り直されても、印は掲載に付いているので残る。"""
    old_group = _group(conn)
    representative = _listing(conn, group_id=old_group)
    member = _listing(conn, group_id=old_group)
    marks.set_group_flag(conn, listing_id=member, flag="is_favorite", on=True)

    new_group = _group(conn)
    conn.execute(
        text("UPDATE t_listings SET group_id = :new WHERE id = ANY(:ids)"),
        {"new": new_group, "ids": [representative, member]},
    )
    conn.execute(text("DELETE FROM t_listing_groups WHERE id = :old"), {"old": old_group})

    assert marks.group_marks(conn, [representative])[representative].is_favorite is True


@pytest.mark.db
def test_外した印は同じグループの他の掲載からも外れる(conn: Connection) -> None:
    group_id = _group(conn)
    representative = _listing(conn, group_id=group_id)
    member = _listing(conn, group_id=group_id)
    marks.set_group_flag(conn, listing_id=member, flag="is_excluded", on=True)

    # 画面は代表の詳細からチェックを外す
    marks.save_group_mark(
        conn, listing_id=representative, is_favorite=False, is_excluded=False, memo=None
    )

    assert _excluded_ids(conn, [representative, member]) == set()


@pytest.mark.db
def test_印の切り替えはメモと他方の印を変えない(conn: Connection) -> None:
    listing_id = _listing(conn)
    marks.save_group_mark(
        conn, listing_id=listing_id, is_favorite=True, is_excluded=False, memo="駐輪場を確認"
    )

    marks.set_group_flag(conn, listing_id=listing_id, flag="is_excluded", on=True)
    marks.set_group_flag(conn, listing_id=listing_id, flag="is_excluded", on=False)

    row = marks.marks_of(conn, [listing_id])[listing_id]
    assert (row.is_favorite, row.is_excluded, row.memo) == (True, False, "駐輪場を確認")


@pytest.mark.db
def test_印もメモも無くなった行は消す(conn: Connection) -> None:
    listing_id = _listing(conn)
    marks.set_group_flag(conn, listing_id=listing_id, flag="is_favorite", on=True)

    marks.set_group_flag(conn, listing_id=listing_id, flag="is_favorite", on=False)

    assert marks.marks_of(conn, [listing_id]) == {}


@pytest.mark.db
def test_DBもメモの上限を守る(conn: Connection) -> None:
    """画面の検証を通り抜けた経路があっても、CHECK 制約が止める。"""
    listing_id = _listing(conn)
    with pytest.raises(Exception, match="memo_length"):
        conn.execute(
            text("INSERT INTO t_listing_marks (listing_id, memo) VALUES (:id, :memo)"),
            {"id": listing_id, "memo": "あ" * (MARK_MEMO_MAX_CHARS + 1)},
        )
