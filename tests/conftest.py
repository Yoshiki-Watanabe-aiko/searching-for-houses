"""pytest 共通フィクスチャ。

DBを触るテストは ``DATABASE_TEST_URL`` が設定されているときだけ動かす
（未設定時は安全側に倒してスキップ。本番DBへは絶対に向けない）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from house_search.config.settings import load_settings
from house_search.db.session import create_db_engine


@pytest.fixture(scope="session")
def test_engine() -> Engine:
    """テストDBのエンジン。未設定ならテストをスキップする。"""
    settings = load_settings()
    if not settings.database_test_url:
        pytest.skip("DATABASE_TEST_URL が未設定のためDB統合テストをスキップします")
    engine = create_db_engine(settings.database_test_url)
    try:
        with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - 環境依存
        pytest.skip(f"テストDBへ接続できません: {exc}")
    return engine
