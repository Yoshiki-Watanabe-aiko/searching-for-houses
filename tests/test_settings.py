"""``.env`` 由来の設定読み込みのテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.config.settings import Settings


def _settings(tmp_path: Path, body: str) -> Settings:
    env_file = tmp_path / ".env"
    env_file.write_text(body, encoding="utf-8")

    class _S(Settings):
        model_config = Settings.model_config | {"env_file": env_file}

    return _S()  # type: ignore[call-arg]


def test_v1形式のpostgres_urlをpsycopgドライバへ寄せる(tmp_path: Path) -> None:
    """v1（Go）の接続文字列をそのまま渡すと psycopg2 を探して ImportError になる。"""
    settings = _settings(tmp_path, "DATABASE_URL=postgres://u:p@localhost:5432/db\n")
    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/db"


def test_既にpsycopg指定なら変えない(tmp_path: Path) -> None:
    url = "postgresql+psycopg://u:p@localhost:5432/db"
    assert _settings(tmp_path, f"DATABASE_URL={url}\n").database_url == url


def test_空値の項目は未設定として既定値に倒す(tmp_path: Path) -> None:
    """``CONFIGS_DIR=`` を空文字のまま Path にすると "." を指してしまう。"""
    settings = _settings(
        tmp_path, "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\nCONFIGS_DIR=\n"
    )
    assert settings.configs_dir.name == "configs"
    assert settings.configs_dir != Path(".")


def test_webhook_refから環境変数を解決する(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\n"
        "DISCORD_WEBHOOK_CHINTAI_ALONE=https://discord.com/api/webhooks/1/a\n",
    )
    assert settings.webhook_url("CHINTAI_ALONE") == "https://discord.com/api/webhooks/1/a"
    # 小文字で書いても解決できる
    assert settings.webhook_url("chintai_alone") == "https://discord.com/api/webhooks/1/a"


def test_未定義のwebhook_refはエラーにする(tmp_path: Path) -> None:
    """YAML の参照ミスを黙って「通知先なし」にしない。"""
    settings = _settings(tmp_path, "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\n")
    with pytest.raises(ValueError, match="DISCORD_WEBHOOK_NOPE"):
        settings.webhook_url("NOPE")


def test_プロセス環境変数がenvファイルより優先される(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_CHINTAI_ALONE", "https://discord.com/api/webhooks/2/b")
    settings = _settings(
        tmp_path,
        "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\n"
        "DISCORD_WEBHOOK_CHINTAI_ALONE=https://discord.com/api/webhooks/1/a\n",
    )
    assert settings.webhook_url("CHINTAI_ALONE") == "https://discord.com/api/webhooks/2/b"
