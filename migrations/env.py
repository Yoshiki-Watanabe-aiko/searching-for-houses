"""Alembic の実行環境設定。

設定の意図はここ（Pythonソース = UTF-8で読まれる）に書く。
``alembic.ini`` に日本語を書くと、日本語Windows（cp932）では
``alembic upgrade`` コマンドそのものが UnicodeDecodeError で落ちるため
``alembic.ini`` は ASCII のみに保つこと。

接続URLは ``alembic.ini`` に書かず ``.env`` から読む（Git管理下にパスワードを置かないため）。
テストDBへ適用するときは x-argument で切り替える::

    uv run alembic -x test=true upgrade head
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from house_search.config.settings import load_settings
from house_search.db.models import Base  # noqa: F401  全モデルを metadata へ登録する

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """適用先DBの接続URLを決める。``-x test=true`` でテストDBへ向ける。"""
    settings = load_settings()
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("test", "").lower() in {"1", "true", "yes"}:
        if not settings.database_test_url:
            raise RuntimeError(
                "DATABASE_TEST_URL が .env に設定されていないためテストDBへ適用できません"
            )
        return settings.database_test_url
    return settings.database_url


def run_migrations_offline() -> None:
    """SQLを出力するだけのオフラインモード。"""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """実DBへ接続して適用するオンラインモード。"""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            # 稼働中プロセスとのロック競合ではfail-fastさせる（DB規約）。
            connection.exec_driver_sql("SET lock_timeout = '10s'")
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
