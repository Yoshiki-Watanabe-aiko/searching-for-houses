"""DBエンジン・セッションの生成。

engine は遅延生成し接続タイムアウトを必ず設ける（DB規約）。
これを怠るとDB停止時にジョブが接続待ちでハングし、時間制約のある処理では
機会そのものを失う。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from house_search.config.settings import load_settings

# 到達不能なホストで無限に待たないための接続タイムアウト（秒）。
CONNECT_TIMEOUT_SEC = 5


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    """接続URLからエンジンを作る。"""
    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SEC},
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """本番DBのエンジンを遅延生成して使い回す。"""
    return create_db_engine(load_settings().database_url)


@contextmanager
def session_scope() -> Iterator[Session]:
    """トランザクション境界を持つセッションを供給する。"""
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
