"""日次ダイジェストで除外の印を飛ばして繰り上げる（→ 課題#68・ADR 0026）。

⚠⚠ **除外は LIMIT の前で抜く。** 後から捨てると上位 N 件が N 未満に減る。
⚠ 順位（``rank_in_pattern``）そのものは変えず、表示する番号も DB の順位のまま（欠番が見える）。
⚠ 同じ名寄せグループの非代表に付いた除外でも、代表が飛ぶ（閲覧画面と同じ規則）。

⚠ ``digest`` は自分で接続を開くので、ロールバックするフィクスチャは使えない。
テストが作った行は ``finally`` で消す。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search import marks
from house_search.pipeline.tasks import digest

pytestmark = pytest.mark.db

_PATTERN_NAME = "ダイジェスト除外テスト"


class _RecordingSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def send(self, url: str, message: object) -> bool:
        self.calls.append((url, message))
        return True


def _runtime(engine: Engine, sender: _RecordingSender) -> SimpleNamespace:
    settings = SimpleNamespace(webhook_url=lambda ref: f"https://example.invalid/{ref}")
    return SimpleNamespace(engine=engine, settings=settings, sender=sender, condition_names={})


def _pattern(top_n: int) -> SimpleNamespace:
    return SimpleNamespace(
        name=_PATTERN_NAME,
        ranking=SimpleNamespace(top_n=top_n, digest_group=None),
        commute=None,
        want=SimpleNamespace(features=(), numeric=()),
        effective_digest_webhook_ref="CHINTAI_DIGEST",
    )


def _listing(conn: Connection, *, rank: int | None, group_id: int | None = None) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    listing_id = conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title, price, area_sqm, layout,
                status, group_id, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :type_id, :external_id, 'https://example.invalid/d', 'ダイジェスト除外',
                80000, 40.0, '2DK', 'active', :group_id, now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "type_id": type_id,
            "external_id": f"digest-exclusion-{uuid.uuid4().hex}",
            "group_id": group_id,
        },
    ).scalar_one()
    conn.execute(
        text(
            """
            INSERT INTO t_listing_scores (
                listing_id, pattern_name, score, score_breakdown, must_result,
                rank_in_pattern, config_hash, created_at, updated_at
            ) VALUES (
                :listing_id, :pattern_name, :score, '[]'::jsonb, 'pass', :rank, 'test-hash',
                now(), now()
            )
            """
        ),
        {
            "listing_id": listing_id,
            "pattern_name": _PATTERN_NAME,
            "score": 90 - (rank or 50),
            "rank": rank,
        },
    )
    return listing_id


def _cleanup(engine: Engine, listing_ids: list[int], group_ids: list[int]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM t_ranking_digests WHERE pattern_name = :name"),
            {"name": _PATTERN_NAME},
        )
        conn.execute(
            text("DELETE FROM t_listing_scores WHERE pattern_name = :name"),
            {"name": _PATTERN_NAME},
        )
        # t_listing_marks は ON DELETE CASCADE で消える
        conn.execute(text("DELETE FROM t_listings WHERE id = ANY(:ids)"), {"ids": listing_ids})
        conn.execute(text("DELETE FROM t_listing_groups WHERE id = ANY(:ids)"), {"ids": group_ids})


def _ranks(result_sender: _RecordingSender) -> str:
    _url, message = result_sender.calls[-1]
    return message["embeds"][0]["description"]  # type: ignore[index]


def test_除外を飛ばして上位N件を保つ(test_engine: Engine) -> None:
    with test_engine.begin() as setup:
        ids = [_listing(setup, rank=rank) for rank in (1, 2, 3)]
        marks.set_group_flag(setup, listing_id=ids[1], flag="is_excluded", on=True)
    try:
        sender = _RecordingSender()
        result = digest(_runtime(test_engine, sender), _pattern(top_n=2), dry_run=False)

        assert result.entries == 2
        assert result.excluded == 1
        description = _ranks(sender)
        # ⚠ 表示する番号は DB の順位のまま（1位・3位。2位は欠番）
        assert "**1. " in description
        assert "**3. " in description
        assert "**2. " not in description
    finally:
        _cleanup(test_engine, ids, [])


def test_非代表に付いた除外で代表が飛ぶ(test_engine: Engine) -> None:
    with test_engine.begin() as setup:
        type_id = setup.execute(
            text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
        ).scalar_one()
        group_id = setup.execute(
            text(
                "INSERT INTO t_listing_groups (dedup_key, property_type_id, member_count) "
                "VALUES (:key, :type_id, 2) RETURNING id"
            ),
            {"key": uuid.uuid4().hex, "type_id": type_id},
        ).scalar_one()
        representative = _listing(setup, rank=1, group_id=group_id)
        member = _listing(setup, rank=None, group_id=group_id)
        other = _listing(setup, rank=2)
        setup.execute(
            text("UPDATE t_listing_groups SET representative_listing_id = :rep WHERE id = :gid"),
            {"rep": representative, "gid": group_id},
        )
        marks.set_group_flag(setup, listing_id=member, flag="is_excluded", on=True)
    try:
        result = digest(_runtime(test_engine, _RecordingSender()), _pattern(top_n=15), dry_run=True)

        assert result.entries == 1
        assert result.excluded == 1
    finally:
        _cleanup(test_engine, [representative, member, other], [group_id])


def test_印が無ければ従来どおり(test_engine: Engine) -> None:
    with test_engine.begin() as setup:
        ids = [_listing(setup, rank=rank) for rank in (1, 2)]
    try:
        result = digest(_runtime(test_engine, _RecordingSender()), _pattern(top_n=15), dry_run=True)

        assert result.entries == 2
        assert result.excluded == 0
    finally:
        _cleanup(test_engine, ids, [])


def test_全件が除外なら対象0件として送らない(test_engine: Engine) -> None:
    """⚠ 除外で空になった便も送らない（→ 課題#28）。件数は出す。"""
    with test_engine.begin() as setup:
        ids = [_listing(setup, rank=1)]
        marks.set_group_flag(setup, listing_id=ids[0], flag="is_excluded", on=True)
    try:
        sender = _RecordingSender()
        result = digest(_runtime(test_engine, sender), _pattern(top_n=15))

        assert result.skipped is True
        assert result.excluded == 1
        assert sender.calls == []
    finally:
        _cleanup(test_engine, ids, [])
