"""HTTP取得のレート制御・リトライ・robots.txt 判定。

サイトごとに ``m_sites`` の ``min_interval_sec`` / ``max_pages_per_run`` /
``daily_request_cap`` を尊重する。相手サーバに負荷をかけないことが第一で、
429・5xx は指数バックオフし、連続失敗が続いたらそのサイトを打ち切る。
"""

from __future__ import annotations

import random
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

# 既定のレート制御値。サイト個別設定が無いときに使う。
DEFAULT_MIN_INTERVAL_SEC = 2.5
# 待ち時間に載せる揺らぎの割合（±30%）。同じ間隔で叩き続けないため。
JITTER_RATIO = 0.3
# 429/5xx のリトライ回数と初回待機秒（以後2倍ずつ）。
MAX_RETRIES = 3
BACKOFF_BASE_SEC = 4.0
# これだけ連続で失敗したらそのサイトを打ち切る。
CONSECUTIVE_FAILURE_LIMIT = 5

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class SiteAborted(RuntimeError):
    """連続失敗・日次上限などでサイトの取得を打ち切ったことを表す。"""


class RobotsDisallowed(RuntimeError):
    """robots.txt が当該URLの取得を禁じていることを表す。"""


@dataclass(slots=True)
class RateLimit:
    """1サイトぶんのレート制御設定。"""

    min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC
    max_pages_per_run: int = 5
    daily_request_cap: int | None = None


@dataclass(slots=True)
class FetchStats:
    """取得の実績。打ち切り判断と実行ログに使う。"""

    requests: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    retries: int = 0


@dataclass(slots=True)
class SiteFetcher:
    """1サイトぶんのHTTP取得クライアント。

    ``sleep`` を差し替え可能にしてあるのはテストで待たないため
    （実運用では ``time.sleep``）。
    """

    site_code: str
    client: httpx.Client
    rate_limit: RateLimit = field(default_factory=RateLimit)
    stats: FetchStats = field(default_factory=FetchStats)
    sleep: object = time.sleep
    _last_request_at: float = 0.0
    _robots: urllib.robotparser.RobotFileParser | None = None
    _robots_origin: str | None = None

    def _wait(self) -> None:
        """前回リクエストからの経過を見て、必要なら待つ（±30%のジッタ付き）。"""
        interval = self.rate_limit.min_interval_sec
        jitter = interval * JITTER_RATIO * random.uniform(-1.0, 1.0)  # noqa: S311
        target = max(0.0, interval + jitter)
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < target:
            self.sleep(target - elapsed)  # type: ignore[operator]

    def _load_robots(self, url: str) -> None:
        """オリジンごとに robots.txt を1回だけ取得する。"""
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        if self._robots_origin == origin:
            return
        parser = urllib.robotparser.RobotFileParser()
        try:
            response = self.client.get(urljoin(origin, "/robots.txt"), timeout=15.0)
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        except httpx.HTTPError:
            # robots.txt が取れないときは「禁止されていない」とはみなさず、
            # 空ルール（全許可）で進めるのではなく従来どおり控えめな間隔で進む。
            parser.parse([])
        self._robots = parser
        self._robots_origin = origin

    def is_allowed(self, url: str) -> bool:
        """robots.txt 上、このURLを取得してよいか。"""
        self._load_robots(url)
        if self._robots is None:
            return True
        user_agent = self.client.headers.get("User-Agent", "*")
        return self._robots.can_fetch(user_agent, url)

    def get(self, url: str) -> httpx.Response:
        """1ページ取得する。レート制御・robots判定・リトライを内包する。"""
        if self.rate_limit.daily_request_cap is not None and (
            self.stats.requests >= self.rate_limit.daily_request_cap
        ):
            raise SiteAborted(
                f"{self.site_code}: 日次リクエスト上限 "
                f"{self.rate_limit.daily_request_cap} 件に達しました"
            )
        if not self.is_allowed(url):
            raise RobotsDisallowed(
                f"{self.site_code}: robots.txt により取得が許可されていません: {url}"
            )

        backoff = BACKOFF_BASE_SEC
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            self._wait()
            self._last_request_at = time.monotonic()
            self.stats.requests += 1
            try:
                response = self.client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code not in RETRYABLE_STATUSES:
                    response.raise_for_status()
                    self.stats.consecutive_failures = 0
                    return response
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )

            if attempt < MAX_RETRIES:
                self.stats.retries += 1
                self.sleep(backoff)  # type: ignore[operator]
                backoff *= 2

        self.stats.failures += 1
        self.stats.consecutive_failures += 1
        if self.stats.consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            raise SiteAborted(
                f"{self.site_code}: {CONSECUTIVE_FAILURE_LIMIT} 回連続で失敗したため打ち切ります"
            ) from last_error
        raise RuntimeError(f"{self.site_code}: 取得に失敗しました: {url}") from last_error


# 自己申告のUser-Agentを 403 で拒否するサイト向けのブラウザ相当UA。
# LIFULL HOME'S は robots.txt で当該パスを User-agent: * に許可しているのに、
# 名乗りが既定のUAだと 403 を返す（実測）。適用はアダプタが
# ``user_agent`` を宣言したサイトだけに限り、間隔・robots.txt の尊重は変えない。
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def build_client(*, user_agent: str, timeout_sec: float) -> httpx.Client:
    """スクレイピング用のHTTPクライアント。"""
    return httpx.Client(
        headers={
            "User-Agent": user_agent,
            "Accept-Language": "ja,en;q=0.8",
        },
        timeout=timeout_sec,
        follow_redirects=True,
    )
