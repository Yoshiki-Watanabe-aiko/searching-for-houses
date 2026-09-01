"""``.env`` から読み込むアプリケーション設定。

Discord の Webhook URL は v2 ではすべて ``.env`` に集約し、検索パターンYAMLからは
``webhook_ref`` で論理名を参照する。これにより ``configs/*.yaml`` をGit管理に戻せる
（検索条件の変更履歴が残る）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 検索パターンYAML の webhook_ref を解決するときの環境変数名の接頭辞。
WEBHOOK_PREFIX = "DISCORD_WEBHOOK_"


class Settings(BaseSettings):
    """環境変数・``.env`` 由来の設定値。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        description="本番DBの接続URL（postgresql+psycopg://user:pass@host:port/dbname）"
    )
    database_test_url: str | None = Field(
        default=None,
        description="テストDBの接続URL。未設定ならDB統合テストはスキップする",
    )
    configs_dir: Path = Field(
        default=PROJECT_ROOT / "configs",
        description="検索パターンYAMLを置くディレクトリ",
    )
    data_dir: Path = Field(
        default=PROJECT_ROOT / "data",
        description="設備抽出辞書などGit管理データの置き場",
    )
    log_dir: Path = Field(
        default=PROJECT_ROOT / "logs",
        description="実行ログの出力先ディレクトリ",
    )
    discord_webhook_errors: str | None = Field(
        default=None,
        description="サイト失敗・YAML読込失敗を流すグローバルエラーチャンネルのWebhook URL",
    )
    default_min_interval_sec: float = Field(
        default=2.5,
        description="サイト個別設定が無い場合のリクエスト間隔（秒）",
    )
    request_timeout_sec: float = Field(
        default=30.0, description="HTTPリクエストのタイムアウト（秒）"
    )
    user_agent: str = Field(
        default="house-search/2.0 (personal property watcher)",
        description="スクレイピング時に名乗るUser-Agent",
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_empty_values(cls, data: Any) -> Any:
        """``.env`` の ``KEY=``（空値）を「未設定」として扱い、既定値を効かせる。

        空文字のまま ``Path`` へ渡すとカレントディレクトリを指してしまい、
        「configs が見つからない」という無関係な症状になる。
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data

    @field_validator("database_url", "database_test_url", mode="after")
    @classmethod
    def _require_psycopg_driver(cls, value: str | None) -> str | None:
        """SQLAlchemy が psycopg3 を使うようドライバ指定を正規化する。

        v1（Go）から引き継いだ ``postgres://`` 形式をそのまま渡すと
        SQLAlchemy が psycopg2 を探しに行って ImportError になるため、
        ここで ``postgresql+psycopg://`` へ寄せる。
        """
        if value is None:
            return None
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    def webhooks(self) -> dict[str, str]:
        """``DISCORD_WEBHOOK_*`` を集めて返す。

        pydantic-settings は宣言したフィールドしか読まないため、任意個の
        ``DISCORD_WEBHOOK_{論理名}`` は ``.env`` を直接読んで拾う。
        プロセス環境変数のほうが ``.env`` より優先される（CI・一時上書き用）。
        """
        merged: dict[str, str] = {}
        env_file = self.model_config.get("env_file")
        if env_file and Path(env_file).exists():
            merged.update({k: v for k, v in dotenv_values(env_file).items() if v})
        merged.update(os.environ)
        return {
            key: value for key, value in merged.items() if key.startswith(WEBHOOK_PREFIX) and value
        }

    def webhook_url(self, ref: str) -> str:
        """YAML の ``webhook_ref`` から実際のWebhook URLを解決する。

        ``CHINTAI_ALONE`` → ``DISCORD_WEBHOOK_CHINTAI_ALONE``。
        未定義の参照は起動時バリデーションで落とすため、ここでは例外を投げる。
        """
        key = f"{WEBHOOK_PREFIX}{ref.upper()}"
        url = self.webhooks().get(key)
        if not url:
            raise ValueError(
                f"webhook_ref '{ref}' に対応する環境変数 {key} が .env に定義されていません"
            )
        return url


def load_settings() -> Settings:
    """設定を読み込む。``.env`` が無い環境では環境変数のみで解決する。"""
    return Settings()  # type: ignore[call-arg]
