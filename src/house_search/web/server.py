"""閲覧画面を起動する（``house-search web``）。

⚠ **待ち受けは 127.0.0.1 に固定する**（引数で変えられない）。LAN の他端末から
見えないようにするため。
⚠ デバッガ・自動リロードは使わない（デバッガは任意コード実行の入口になる）。
⚠ 常駐はさせない。見るときに前面で起動し、Ctrl+C で止める（ユーザー判断 2026-09-15）。
"""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

from sqlalchemy.exc import OperationalError

from house_search.config.settings import load_settings
from house_search.db.session import create_web_engine
from house_search.web import create_app
from house_search.web.context import build_context
from house_search.web.security import BIND_HOST

#: 使えるポートの範囲（特権ポートは使わない。Host ヘッダの検証が「ポート付き」を前提にする）
MIN_PORT = 1024
MAX_PORT = 65535
#: ブラウザを開くまでの待ち（秒）。サーバが待ち受けを始める前に開くと接続失敗の画面になる
_OPEN_DELAY_SEC = 1.0


def port_is_free(port: int) -> bool:
    """そのポートで待ち受けられるか（別の起動が残っていないか）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((BIND_HOST, port))
        except OSError:
            return False
    return True


def run_server(*, port: int, use_test_db: bool, open_browser: bool) -> int:
    """閲覧画面を起動し、止められるまで待つ。終了コードを返す。"""
    if not MIN_PORT <= port <= MAX_PORT:
        print(f"ポートは {MIN_PORT}〜{MAX_PORT} で指定してください（{port}）", file=sys.stderr)
        return 1
    settings = load_settings()
    url = settings.database_test_url if use_test_db else settings.database_url
    if not url:
        print("DATABASE_TEST_URL が未設定です", file=sys.stderr)
        return 1
    if not port_is_free(port):
        print(
            f"ポート {port} は使用中です（閲覧画面がすでに起動していないか確認してください。"
            "別のポートは --port で指定できます）",
            file=sys.stderr,
        )
        return 1

    engine = create_web_engine(url)
    try:
        try:
            context = build_context(engine, configs_dir=settings.configs_dir, port=port)
        except OperationalError as exc:
            # ⚠ 例外の文言は接続先を含みうるので、種類だけを出す
            print(f"DBへ接続できません（{type(exc.orig).__name__}）", file=sys.stderr)
            return 1
        app = create_app(context)
        address = f"http://{BIND_HOST}:{port}/"
        db_note = "（テストDB）" if use_test_db else ""
        print(f"閲覧画面を起動しました{db_note}: {address}  ― 終了は Ctrl+C")
        if open_browser:
            threading.Timer(_OPEN_DELAY_SEC, webbrowser.open, args=(address,)).start()
        app.run(host=BIND_HOST, port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        engine.dispose()
    return 0
