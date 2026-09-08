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


def test_不動産情報ライブラリのキーは未設定ならNone(tmp_path: Path) -> None:
    """キーは相場の取得スクリプトしか使わないので、無くても他の機能は動く。"""
    settings = _settings(tmp_path, "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\n")
    assert settings.mlit_reinfolib_api_key is None


def test_不動産情報ライブラリのキーが空値なら要求時に落とす(tmp_path: Path) -> None:
    """⚠ ``KEY=`` だけの行は「設定した」ように見えるが実質未設定。

    ここで落とさないと、キー無しのまま API を叩いて 401 を受け、
    「キーが誤っている」のか「そもそも送っていない」のか区別できなくなる。
    """
    settings = _settings(
        tmp_path,
        "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\nMLIT_REINFOLIB_API_KEY=\n",
    )
    assert settings.mlit_reinfolib_api_key is None
    with pytest.raises(RuntimeError, match="MLIT_REINFOLIB_API_KEY"):
        settings.require_reinfolib_api_key()


def test_不動産情報ライブラリのキーは前後の空白を落として返す(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\nMLIT_REINFOLIB_API_KEY= abc123 \n",
    )
    assert settings.require_reinfolib_api_key() == "abc123"


def test_例外にキーの値を含めない(tmp_path: Path) -> None:
    """⚠ 約款がキーの第三者提供を禁じているので、値をメッセージへ入れない。

    空白だけのキーは「未設定」として弾かれるが、そのとき値が漏れないことも固定する。
    """
    settings = _settings(
        tmp_path,
        "DATABASE_URL=postgresql+psycopg://u:p@h:5432/db\nMLIT_REINFOLIB_API_KEY=   \n",
    )
    with pytest.raises(RuntimeError) as exc:
        settings.require_reinfolib_api_key()
    assert "   " not in str(exc.value).replace("MLIT_REINFOLIB_API_KEY が未設定です。", "")


def test_env_exampleの空値行にインラインコメントを書かない() -> None:
    """⚠ ``KEY=  # コメント`` と書くと python-dotenv が「# コメント」を値として読む。

    実際に別プロジェクトで踏んだ事故なので、雛形の側で機械的に止める。
    """
    example = Path(__file__).resolve().parents[1] / ".env.example"
    offenders = [
        line
        for line in example.read_text(encoding="utf-8").splitlines()
        if "=" in line
        and not line.lstrip().startswith("#")
        and not line.split("=", 1)[1].split("#", 1)[0].strip()
        and "#" in line.split("=", 1)[1]
    ]
    assert not offenders, f".env.example の空値行にインラインコメントがある: {offenders}"
