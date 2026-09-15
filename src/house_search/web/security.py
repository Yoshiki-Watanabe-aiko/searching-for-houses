"""閲覧画面の防御（→ ADR 0026）。

ローカル限定でも次の3つは現実の脅威になる。

- **DNS リバインディング**: 悪意あるサイトが自分のドメインを 127.0.0.1 へ向け直し、
  ブラウザ経由で閲覧画面を読む。⚠ 127.0.0.1 で待ち受けるだけでは防げない
  → ``Host`` ヘッダを許可リストで検証する
- **CSRF**: 悪意あるサイトがフォームを自動送信して、ブラウザ経由で印を書き換える
  → 起動ごとのトークン＋``Origin``／``Sec-Fetch-Site`` の検証
- **XSS**: 物件名・設備原文・URL は**スクレイピング由来で信頼できない**
  → テンプレートの自動エスケープ（``|safe`` 禁止をテストで固定）＋
  リンクは http/https だけ＋JavaScript を使わない CSP
"""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

# 待ち受けるアドレス。⚠ 引数で変えられないようにする（LAN へ公開する経路を作らない）
BIND_HOST = "127.0.0.1"

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
#: 同じサイト内にだけ参照元を送る。外部リンクは ``rel="noreferrer"`` も付けてある
REFERRER_POLICY = "same-origin"
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # 掲載サイトへのリンクを踏んだとき、閲覧画面の URL（掲載ID入り）を送らない
    # ⚠⚠ ``no-referrer`` にしない（→ 課題#68）。ブラウザは同じサイトへのフォーム POST でも
    # ``Origin: null`` を送るので、``is_same_origin_post`` が弾いて印が一切保存できなくなる
    # （本番の Chrome 系で実測。テストのクライアントは Origin を手で付けるので気づけない）。
    # ``base.html`` の ``<meta name="referrer">`` も同じ値にそろえる（テストで固定）
    "Referrer-Policy": REFERRER_POLICY,
    "Cache-Control": "no-store",
}

_SAFE_SCHEMES = frozenset({"http", "https"})
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})


def allowed_hosts(port: int) -> frozenset[str]:
    """``Host`` ヘッダとして受け付ける値。"""
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})


def allowed_origins(port: int) -> frozenset[str]:
    """``Origin`` ヘッダとして受け付ける値。"""
    return frozenset(f"http://{host}" for host in allowed_hosts(port))


def is_allowed_host(host: str | None, port: int) -> bool:
    """``Host`` ヘッダが閲覧画面自身を指しているか（DNS リバインディング対策）。"""
    return bool(host) and host.lower() in allowed_hosts(port)


def is_same_origin_post(origin: str | None, fetch_site: str | None, port: int) -> bool:
    """書き込み要求が閲覧画面自身のページから来たか。

    ⚠ ヘッダが無いこと自体は拒否しない（古いブラウザ・テストクライアント）。
    その場合の防御は CSRF トークンが担う。付いていれば必ず一致を求める。
    """
    if origin is not None and origin.lower() not in allowed_origins(port):
        return False
    return not (fetch_site is not None and fetch_site.lower() not in _ALLOWED_FETCH_SITES)


def is_valid_csrf_token(submitted: str | None, expected: str) -> bool:
    """CSRF トークンの照合。⚠ 時間差で推測されないよう定数時間で比べる。"""
    if not submitted or not expected:
        return False
    return hmac.compare_digest(submitted.encode("utf-8"), expected.encode("utf-8"))


def safe_href(url: str | None) -> str | None:
    """リンクにしてよい URL だけを返す（http/https で、ホストがあるもの）。

    ⚠ ``javascript:`` や ``data:`` をリンクにしない。自動エスケープは属性値の
    引用符を守るだけで、スキームまでは止めない。
    """
    if not url:
        return None
    candidate = url.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in _SAFE_SCHEMES or not parts.netloc:
        return None
    return candidate


def safe_next(target: str | None, default: str = "/") -> str:
    """書き込み後の戻り先。自分のサイト内の相対パスだけを許す（オープンリダイレクト対策）。"""
    if not target or not target.startswith("/") or target.startswith("//"):
        return default
    if "\\" in target or any(ord(ch) < 0x20 for ch in target):
        return default
    return target
