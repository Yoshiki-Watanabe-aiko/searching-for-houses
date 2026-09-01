"""Discord Webhook への送信。

Webhook URL は ``.env`` に集約してあり（YAMLは ``webhook_ref`` で論理名を参照）、
このモジュールは解決済みのURLだけを受け取る。URLをログへ出さないのは、
実行ログが漏れたときに通知先を乗っ取られないようにするため。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

# 送信間隔（秒）。Discord のレート制限に踏み込まないための下限。
SEND_INTERVAL_SEC = 2.0
# 429 を受けたときの最大リトライ回数。
MAX_RETRIES = 3
# 1メッセージあたりの Discord 側の上限。
MAX_EMBEDS_PER_MESSAGE = 10


@dataclass(slots=True)
class DiscordSender:
    """Webhook送信クライアント。

    ``sleep`` を差し替えられるのはテストで待たないため。
    """

    client: httpx.Client
    interval_sec: float = SEND_INTERVAL_SEC
    sleep: object = time.sleep
    _last_sent_at: float = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_sent_at
        if self._last_sent_at and elapsed < self.interval_sec:
            self.sleep(self.interval_sec - elapsed)  # type: ignore[operator]

    def send(self, webhook_url: str, payload: dict[str, Any]) -> bool:
        """1メッセージ送る。成功したら True。

        429 は ``retry_after`` に従って待ち直す。それ以外の失敗は
        呼び出し側が ``t_notifications.status='failed'`` として記録できるよう
        False を返す（例外にしないのは、1件の失敗で実行全体を止めないため）。
        """
        embeds = payload.get("embeds") or []
        if len(embeds) > MAX_EMBEDS_PER_MESSAGE:
            raise ValueError(
                f"1メッセージのembedは{MAX_EMBEDS_PER_MESSAGE}個までです（{len(embeds)}個）"
            )

        for _ in range(MAX_RETRIES):
            self._wait()
            self._last_sent_at = time.monotonic()
            try:
                response = self.client.post(webhook_url, json=payload, timeout=20.0)
            except httpx.HTTPError:
                return False
            if response.status_code == 429:
                retry_after = _retry_after_sec(response)
                self.sleep(retry_after)  # type: ignore[operator]
                continue
            return response.is_success
        return False


def _retry_after_sec(response: httpx.Response) -> float:
    """429 レスポンスから待機秒を取り出す。取れなければ既定間隔。"""
    try:
        body = response.json()
    except ValueError:
        body = {}
    value = body.get("retry_after") if isinstance(body, dict) else None
    if value is None:
        value = response.headers.get("Retry-After")
    try:
        return max(float(value), SEND_INTERVAL_SEC)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return SEND_INTERVAL_SEC


def build_sender(*, user_agent: str) -> DiscordSender:
    return DiscordSender(client=httpx.Client(headers={"User-Agent": user_agent}))
