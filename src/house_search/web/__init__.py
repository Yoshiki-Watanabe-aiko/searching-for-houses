"""ローカル限定のブラウザ閲覧画面（→ 課題#68・ADR 0026）。

検索パターン別のランキングと物件の詳細を見て、お気に入り・除外・メモを付ける。

⚠ **flask はこのパッケージだけの依存。** 定期タスク（scan / digest）の経路から
import しない（``cli._cmd_web`` が遅延 import している）。
"""

from __future__ import annotations

from typing import Any

from flask import Flask, abort, render_template, request
from werkzeug.exceptions import HTTPException, MisdirectedRequest

from house_search.web.context import WebContext
from house_search.web.errors import UserInputError
from house_search.web.security import (
    SECURITY_HEADERS,
    is_allowed_host,
    is_same_origin_post,
    is_valid_csrf_token,
)

#: フォームの受信サイズの上限（バイト）。メモ2,000字をURLエンコードしても収まる大きさ
MAX_FORM_BYTES = 64 * 1024

EXTENSION_KEY = "house_search"

_ERROR_MESSAGES: dict[int, str] = {
    400: "要求の内容が正しくありません。",
    403: "この操作は受け付けられません（画面を開き直してからやり直してください）。",
    404: "ページが見つかりません。",
    405: "この方法では操作できません。",
    413: "送信した内容が大きすぎます。",
    421: "このアドレスでは開けません。http://127.0.0.1 から開いてください。",
}


def create_app(context: WebContext) -> Flask:
    """閲覧画面のアプリを作る。"""
    from house_search.web.views import bp

    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_FORM_BYTES, TEMPLATES_AUTO_RELOAD=False)
    # ⚠ 拡張子に関係なく必ず自動エスケープする（物件名・設備原文はスクレイピング由来）
    app.jinja_env.autoescape = True
    app.extensions[EXTENSION_KEY] = context

    @app.before_request
    def _guard() -> None:
        # ⚠ DNS リバインディング対策。127.0.0.1 で待ち受けるだけでは防げない
        if not is_allowed_host(request.host, context.port):
            raise MisdirectedRequest()
        if request.method == "POST":
            if not is_same_origin_post(
                request.headers.get("Origin"), request.headers.get("Sec-Fetch-Site"), context.port
            ):
                abort(403)
            if not is_valid_csrf_token(request.form.get("csrf_token"), context.csrf_token):
                abort(403)

    @app.after_request
    def _headers(response: Any) -> Any:
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.context_processor
    def _globals() -> dict[str, Any]:
        return {
            "csrf_token": context.csrf_token,
            "nav_patterns": [(entry.slug, entry.pattern.name) for entry in context.patterns],
        }

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException) -> tuple[str, int]:
        code = exc.code or 500
        message = _ERROR_MESSAGES.get(code, "エラーが発生しました。")
        # 入力の誤りだけは原因（絞り込み条件・メモの上限）を出す。⚠ 自前で書いた文言に限る
        detail = exc.description if isinstance(exc, UserInputError) else None
        return render_template("error.html", code=code, message=message, detail=detail), code

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception) -> tuple[str, int]:
        # ⚠ トレースバックは画面に出さない（接続文字列などが載りうる）。ログにだけ残す
        app.logger.exception("閲覧画面で予期しないエラー")
        return (
            render_template(
                "error.html", code=500, message="エラーが発生しました（ログを確認してください）。",
                detail=None,
            ),
            500,
        )

    app.register_blueprint(bp)
    return app
