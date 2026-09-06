"""ダイジェストの空送信を止めるテスト（→ 課題#28・要件定義書 §14.1）。

⚠ **実測（2026-09-06 20:00）で「中古マンション: 上位 0件 送信成功」が飛んでいた。**
順位の付いた掲載が1件も無いのに、見出しだけのメッセージを Discord へ送っていた。
「読まれない通知は、本物のエラーを見逃すという形で実害になる」（→ 課題#45）のと
同じ論点で、中身の無い便が定期的に届くとダイジェストそのものが読まれなくなる。

⚠⚠ **単に送信をやめるだけでは足りない。** CLI は ``not result.sent`` で
終了コード1を返すので、対象0件を「送信失敗」と同じ扱いにすると
**タスクスケジューラの「前回の結果」が本物の失敗と区別できなくなる**
（唯一の異常検知経路が潰れる）。``skipped`` で区別する。
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.pipeline.tasks import digest

_PATTERN_NAME = "ダイジェスト空送信テスト"


class _RecordingSender:
    """送信されたかどうかだけを記録するスタブ。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def send(self, url: str, message: object) -> bool:
        self.calls.append((url, message))
        return True


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _runtime(engine: Engine, sender: _RecordingSender) -> SimpleNamespace:
    settings = SimpleNamespace(webhook_url=lambda ref: f"https://example.invalid/{ref}")
    return SimpleNamespace(engine=engine, settings=settings, sender=sender)


def _pattern(name: str = _PATTERN_NAME) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        ranking=SimpleNamespace(top_n=15, digest_group=None),
        commute=None,
        want=SimpleNamespace(features=(), numeric=()),
        effective_digest_webhook_ref="MANSION_DIGEST",
    )


def _insert_scored_listing(conn: Connection, *, rank: int) -> int:
    """掲載とスコア行を1組作る（順位あり）。"""
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    listing_id = conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, area_sqm, layout, status,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id,
                'https://example.invalid/x', 'ダイジェスト空送信テスト',
                80000, 40.0, '2DK', 'active', now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "property_type_id": property_type_id,
            "external_id": f"digest-empty-{rank}",
        },
    ).scalar_one()
    conn.execute(
        text(
            """
            INSERT INTO t_listing_scores (
                listing_id, pattern_name, score, score_breakdown,
                must_result, rank_in_pattern, config_hash, created_at, updated_at
            ) VALUES (
                :listing_id, :pattern_name, 60.0, '[]'::jsonb,
                'pass', :rank, 'test-hash', now(), now()
            )
            """
        ),
        {"listing_id": listing_id, "pattern_name": _PATTERN_NAME, "rank": rank},
    )
    return listing_id


def _digest_count(conn: Connection) -> int:
    return conn.execute(
        text("SELECT count(*) FROM t_ranking_digests WHERE pattern_name = :name"),
        {"name": _PATTERN_NAME},
    ).scalar_one()


def test_対象が0件なら送信しない(test_engine: Engine, conn: Connection) -> None:
    """⚠ これが本題。空のダイジェストを Discord へ送らない。"""
    sender = _RecordingSender()
    result = digest(_runtime(test_engine, sender), _pattern())

    assert result.entries == 0
    assert sender.calls == []


def test_対象が0件のとき送信失敗と区別できる(test_engine: Engine, conn: Connection) -> None:
    """⚠ CLI が終了コード1を返すのは ``not sent`` のとき。

    対象0件を「送信失敗」と同じにすると、タスクの「前回の結果」で
    本物の失敗を見分けられなくなる。
    """
    result = digest(_runtime(test_engine, _RecordingSender()), _pattern())

    assert result.skipped is True
    assert result.sent is False


def test_対象が0件なら履歴を残さない(test_engine: Engine, conn: Connection) -> None:
    """送っていない便を送信履歴に残さない（追記専用テーブルを汚さない）。"""
    with test_engine.connect() as check:
        before = _digest_count(check)

    digest(_runtime(test_engine, _RecordingSender()), _pattern())

    with test_engine.connect() as check:
        assert _digest_count(check) == before


def test_対象があれば従来どおり送信する(test_engine: Engine) -> None:
    """現行挙動の固定。⚠ 空送信を止める修正が通常の送信まで止めていないこと。

    ⚠ ``digest`` は自分でトランザクションを開くので、ここではロールバックする
    ``conn`` フィクスチャを使わず、後始末を自分で行う。
    """
    sender = _RecordingSender()
    with test_engine.begin() as setup:
        listing_id = _insert_scored_listing(setup, rank=1)
    try:
        result = digest(_runtime(test_engine, sender), _pattern())

        assert result.entries == 1
        assert result.sent is True
        assert result.skipped is False
        assert len(sender.calls) == 1
        with test_engine.connect() as check:
            assert _digest_count(check) == 1
    finally:
        with test_engine.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM t_ranking_digests WHERE pattern_name = :name"),
                {"name": _PATTERN_NAME},
            )
            cleanup.execute(
                text("DELETE FROM t_listing_scores WHERE pattern_name = :name"),
                {"name": _PATTERN_NAME},
            )
            cleanup.execute(text("DELETE FROM t_listings WHERE id = :id"), {"id": listing_id})
