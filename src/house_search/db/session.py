"""DBエンジン・セッションの生成。

engine は遅延生成し接続タイムアウトを必ず設ける（DB規約）。
これを怠るとDB停止時にジョブが接続待ちでハングし、時間制約のある処理では
機会そのものを失う。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from house_search.config.settings import load_settings

# 到達不能なホストで無限に待たないための接続タイムアウト（秒）。
CONNECT_TIMEOUT_SEC = 5

# 取得を伴う処理（scan / sweep / check-sold）を排他するアドバイザリロックのキー。
# 値そのものに意味はないが、変えると別プロセスと排他されなくなる。
SCRAPING_LOCK_KEY = 8290421001


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    """接続URLからエンジンを作る。"""
    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SEC},
    )


# ブラウザ閲覧画面（→ 課題#68）の1文あたりの上限（ミリ秒）。
# ⚠ 定期スキャンと同じDBを読むので、重い絞り込みが居座って取得の upsert を
#   待たせないよう、画面側を先に諦めさせる。
WEB_STATEMENT_TIMEOUT_MS = 10_000
# 開いたまま放置されたトランザクションを切る上限（ミリ秒）
WEB_IDLE_IN_TRANSACTION_TIMEOUT_MS = 30_000
# pg_stat_activity で定期スキャンのロック保持と見分けるための名前
WEB_APPLICATION_NAME = "house-search-web"


def create_web_engine(url: str) -> Engine:
    """ブラウザ閲覧画面専用のエンジン。

    ⚠ **取得ロック（``scraping_lock``）は取らない。** 閲覧は取得と排他しない。
    ⚠ 接続数は小さく抑える（1人がローカルで見るだけ。定期スキャンの接続を圧迫しない）。
    """
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_timeout=CONNECT_TIMEOUT_SEC,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_SEC,
            "application_name": WEB_APPLICATION_NAME,
            "options": (
                f"-c statement_timeout={WEB_STATEMENT_TIMEOUT_MS} "
                f"-c idle_in_transaction_session_timeout={WEB_IDLE_IN_TRANSACTION_TIMEOUT_MS}"
            ),
        },
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


@contextmanager
def scraping_lock() -> Iterator[bool]:
    """取得を伴う処理を排他する。取れたかどうかを返す。

    **レート制御は ``SiteFetcher`` のプロセス内にしかない。** 別プロセスの
    ``scan`` と ``check-sold``、あるいは増分スキャンと週次の全件スキャンが
    並走すると、同一サイトへの実効間隔が半分になる。トリガー時刻を分ける
    という約束だけでは、実行が延びたときや手動実行したときに破れる。

    セッションレベルのロックなのでトランザクションとは独立に保たれる。
    接続がプールへ戻ってもロックは残るため、``finally`` で必ず解放する
    （プロセスごと落ちた場合はセッション終了で自動的に解放される）。
    """
    with get_engine().connect() as conn:
        acquired = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": SCRAPING_LOCK_KEY}
            ).scalar()
        )
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": SCRAPING_LOCK_KEY}
                )
