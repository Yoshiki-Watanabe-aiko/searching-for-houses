"""閲覧画面の防御（→ ADR 0026）。DB に触れずに確かめられるものだけを置く。

⚠ ローカル限定でも、DNS リバインディング・CSRF・XSS は現実の脅威になる
（悪意あるサイトを開いたブラウザが、127.0.0.1 の閲覧画面を読む・書く）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from house_search.web import create_app
from house_search.web.context import WebContext
from house_search.web.security import (
    BIND_HOST,
    SECURITY_HEADERS,
    is_allowed_host,
    is_same_origin_post,
    is_valid_csrf_token,
    safe_href,
    safe_next,
)

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "house_search" / "web"
PORT = 8765
TOKEN = "test-csrf-token"
BASE_URL = f"http://127.0.0.1:{PORT}"
# ⚠ 接続できない先。画面にこの文字列（パスワード・接続先）が出たら漏洩
_SECRET_URL = "postgresql+psycopg://leak_user:leak-password-123@127.0.0.1:1/leak_db"


@pytest.fixture
def client():
    engine = create_engine(_SECRET_URL, connect_args={"connect_timeout": 1})
    context = WebContext(
        engine=engine,
        patterns=(),
        condition_names={},
        site_names={},
        port=PORT,
        csrf_token=TOKEN,
    )
    app = create_app(context)
    app.logger.disabled = True
    with app.test_client() as test_client:
        yield test_client
    engine.dispose()


# ------------------------------------------------------------
# 単体
# ------------------------------------------------------------


def test_待ち受けはループバックに固定() -> None:
    assert BIND_HOST == "127.0.0.1"


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1:8765", True),
        ("localhost:8765", True),
        ("[::1]:8765", True),
        ("evil.example:8765", False),
        ("127.0.0.1:9999", False),
        ("127.0.0.1", False),
        ("", False),
        (None, False),
    ],
)
def test_Hostヘッダの許可リスト(host: str | None, allowed: bool) -> None:
    assert is_allowed_host(host, PORT) is allowed


@pytest.mark.parametrize(
    ("origin", "fetch_site", "allowed"),
    [
        (None, None, True),
        ("http://127.0.0.1:8765", "same-origin", True),
        ("http://evil.example", None, False),
        ("null", None, False),
        (None, "cross-site", False),
        (None, "same-site", False),
    ],
)
def test_書き込みの送信元(origin: str | None, fetch_site: str | None, allowed: bool) -> None:
    assert is_same_origin_post(origin, fetch_site, PORT) is allowed


def test_CSRFトークンの照合() -> None:
    assert is_valid_csrf_token(TOKEN, TOKEN)
    assert not is_valid_csrf_token("wrong", TOKEN)
    assert not is_valid_csrf_token(None, TOKEN)
    assert not is_valid_csrf_token("", "")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://suumo.jp/chintai/jc_1/", "https://suumo.jp/chintai/jc_1/"),
        ("http://example.com/a", "http://example.com/a"),
        ("javascript:alert(1)", None),
        (" JavaScript:alert(1)", None),
        ("data:text/html,<script>", None),
        ("//evil.example/", None),
        ("https:///no-host", None),
        (None, None),
    ],
)
def test_リンクにするのはhttpとhttpsだけ(url: str | None, expected: str | None) -> None:
    assert safe_href(url) == expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/patterns/chintai_23ku?page=2", "/patterns/chintai_23ku?page=2"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("patterns", "/"),
        (None, "/"),
    ],
)
def test_戻り先は自サイトの相対パスだけ(target: str | None, expected: str) -> None:
    assert safe_next(target) == expected


# ------------------------------------------------------------
# アプリ
# ------------------------------------------------------------


def test_別ホスト名での要求は拒否する(client) -> None:
    """⚠ DNS リバインディング。127.0.0.1 で待ち受けていても Host が別名なら通さない。"""
    response = client.get("/", base_url="http://evil.example:8765")
    assert response.status_code == 421
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_CSRFトークンの無い書き込みは拒否する(client) -> None:
    response = client.post("/listings/1/flags", base_url=BASE_URL, data={"flag": "excluded"})
    assert response.status_code == 403


def test_別サイトからの書き込みは拒否する(client) -> None:
    response = client.post(
        "/listings/1/flags",
        base_url=BASE_URL,
        data={"flag": "excluded", "value": "1", "csrf_token": TOKEN},
        headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_GETでは書き込めない(client) -> None:
    response = client.get(f"/listings/1/flags?csrf_token={TOKEN}", base_url=BASE_URL)
    assert response.status_code == 405


def test_応答にセキュリティヘッダが付く(client) -> None:
    response = client.get("/patterns/unknown", base_url=BASE_URL)
    assert response.status_code == 404
    assert "script-src" not in response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_参照元ポリシーは同じサイトへのPOSTでOriginを消さない() -> None:
    """⚠⚠ ``no-referrer`` だとブラウザは同じサイトへのフォーム POST に ``Origin: null`` を付け、
    送信元の検証（``null`` は拒否）で印が一切保存できなくなる（→ 課題#68・2026-09-15 本番で実測）。

    テストのクライアントは Origin を手で付けるので、ページ側の指定をここで固定する。
    ⚠ ヘッダと ``<meta name="referrer">`` の両方がページの方針になるので、両方を見る。
    """
    header = SECURITY_HEADERS["Referrer-Policy"]
    base_html = (WEB / "templates" / "base.html").read_text("utf-8")
    meta = re.search(r'<meta name="referrer" content="([^"]+)">', base_html)
    assert meta is not None
    assert header == meta.group(1) == "same-origin"
    # 仕様どおり null は拒否のまま（緩めて直さない）
    assert not is_same_origin_post("null", "same-origin", PORT)


def test_予期しないエラーで接続情報を出さない(client) -> None:
    """⚠ DB に繋がらないときの 500 ページに、接続先やパスワードを載せない。"""
    response = client.get("/", base_url=BASE_URL)
    body = response.get_data(as_text=True)
    assert response.status_code == 500
    assert "leak-password-123" not in body
    assert "leak_user" not in body
    assert "postgresql" not in body
    assert "Traceback" not in body


# ------------------------------------------------------------
# テンプレートとソースの静的検査
# ------------------------------------------------------------


def test_テンプレートで自動エスケープを外さない() -> None:
    """⚠ 物件名・設備原文はスクレイピング由来。``|safe`` を1つ足すだけで XSS になる。"""
    offenders: list[str] = []
    for path in sorted((WEB / "templates").glob("*.html")):
        source = path.read_text(encoding="utf-8")
        for pattern in (r"\|\s*safe\b", r"autoescape\s+false", r"\bMarkup\b"):
            if re.search(pattern, source):
                offenders.append(f"{path.name}: {pattern}")
    assert not offenders, offenders


def test_テンプレートにスクリプトとインラインスタイルを置かない() -> None:
    """CSP で script とインラインスタイルを禁止している（置いても動かず、緩めると穴になる）。"""
    offenders: list[str] = []
    for path in sorted((WEB / "templates").glob("*.html")):
        source = path.read_text(encoding="utf-8")
        for pattern in (r"<script", r"\sstyle\s*=", r"\son[a-z]+\s*="):
            if re.search(pattern, source, re.IGNORECASE):
                offenders.append(f"{path.name}: {pattern}")
    assert not offenders, offenders


def test_Pythonからもエスケープを外さない() -> None:
    offenders = [
        path.name
        for path in sorted(WEB.glob("*.py"))
        if re.search(r"\bMarkup\b|autoescape\s*=\s*False", path.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_定期タスクの経路はflaskをimportしない() -> None:
    """⚠ flask は閲覧画面だけの依存。main の .venv へ入れる前でも scan / digest が動くこと。"""
    offenders: list[str] = []
    package = REPO / "src" / "house_search"
    for path in sorted(package.rglob("*.py")):
        if WEB in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            # cli.py は web サブコマンドの中でだけ遅延 import する（関数の中なので許す）
            if any(name.split(".")[0] in {"flask", "werkzeug", "jinja2"} for name in names):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
            if (
                any(name.startswith("house_search.web") for name in names)
                and node.col_offset == 0
            ):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}（モジュール先頭）")
    assert not offenders, offenders
