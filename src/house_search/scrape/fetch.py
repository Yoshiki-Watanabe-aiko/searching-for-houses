"""HTTP取得のレート制御・リトライ・robots.txt 判定。

サイトごとに ``m_sites`` の ``min_interval_sec`` / ``max_pages_per_run`` /
``daily_request_cap`` を尊重する。相手サーバに負荷をかけないことが第一で、
429・5xx は指数バックオフし、連続失敗が続いたらそのサイトを打ち切る。
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import NoReturn
from urllib.parse import urljoin, urlsplit

import httpx

logger = logging.getLogger(__name__)

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
# 404 は「その掲載が無い」という正常な状態変化なので例外にせず response を返す。
# 呼び出し側（``is_sold``）が掲載終了の判定に使う。
NOT_FOUND_STATUS = 404

# robots.txt でグループ（User-agent 行に紐づく規則）の一部になるフィールド。
# これ以外（Sitemap 等）はどの User-agent にも属さないのでそのまま残す。
ROBOTS_GROUP_FIELDS = frozenset({"allow", "disallow", "crawl-delay", "request-rate"})


def merge_robots_groups(text: str) -> list[str]:
    """robots.txt の同じ User-agent のグループを1つにまとめて行のリストを返す。

    ⚠ **標準の ``RobotFileParser`` は同じ User-agent のグループが2つ以上あると
    2つ目以降を丸ごと落とす**（``_add_entry`` が "the first default entry wins"
    として最初の ``*`` だけを採るため）。RFC 9309 §2.2.1 は
    「繰り返された user-agent のグループは統合しなければならない」と定めており、
    これは標準ライブラリ側の仕様違反である（→ 課題#43）。

    ⚠ **黙って禁止パスを叩き始める**類の欠陥なので、パースの前にここで直す。
    CHINTAI.net の robots.txt が実際に ``User-agent: *`` を2グループ持ち、
    そのままでは ``/api/`` や ``/list/?b=`` を許可と誤判定する。

    ⚠ **グループが1つ以下の robots.txt では出力の意味を変えない**
    （既存15サイトはすべてこれに当たる）。
    """
    rules: dict[str, list[str]] = {}
    labels: dict[str, str] = {}  # 小文字キー → 最初に現れた表記
    order: list[str] = []
    outside: list[str] = []  # Sitemap など、どのグループにも属さない行
    agents: list[str] = []  # いま規則を紐づける相手（連続する User-agent 行）
    in_rules = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()
        if field_name == "user-agent":
            # 規則の直後の User-agent 行は新しいグループの始まり。
            if in_rules:
                agents = []
                in_rules = False
            key = value.lower()
            if key not in rules:
                rules[key] = []
                labels[key] = value
                order.append(key)
            if key not in agents:
                agents.append(key)
        elif field_name in ROBOTS_GROUP_FIELDS:
            # ⚠ User-agent 行より前の規則は標準と同じく捨てる。
            if not agents:
                continue
            in_rules = True
            for key in agents:
                rules[key].append(f"{field_name}: {value}")
        else:
            outside.append(line)

    merged: list[str] = []
    for key in order:
        merged.append(f"User-agent: {labels[key]}")
        merged.extend(rules[key])
        merged.append("")  # 空行がグループの区切りになる
    merged.extend(outside)
    return merged


def robots_path_pattern(value: str) -> re.Pattern[str]:
    """Allow/Disallow の値を正規表現へ変換する（RFC 9309 §2.2.3）。

    ``*`` は任意長、``$`` は末尾を表す。⚠ **標準の ``RobotFileParser`` は
    これを展開せず単純な前方一致で見る**ため、``/*?*sort=`` のような規則は
    「そういう名前のパス」として扱われ**どのURLにも当たらない**（→ 課題#52）。
    """
    body = ".*".join(re.escape(chunk) for chunk in value.split("*"))
    tail = re.escape("$")
    if body.endswith(tail):
        body = body[: -len(tail)] + r"\Z"
    return re.compile("^" + body)


@dataclass(frozen=True)
class _RobotsRule:
    allow: bool
    value: str
    pattern: re.Pattern[str]


class RobotsRules:
    """robots.txt の Allow/Disallow を、ワイルドカード込みで判定する。

    ⚠ 標準の ``urllib.robotparser`` には**許可側に倒れる**欠陥が2つある。
    ①同じ User-agent のグループが2つ以上あると2つ目以降を丸ごと落とす
    （→ 課題#43。こちらは ``merge_robots_groups`` が前処理で直す）
    ②``Disallow`` の値の ``*`` / ``$`` を展開しない（→ 課題#52。本クラス）。
    どちらも robots を尊重するつもりのコードが**禁止パスを叩く**形になり、
    ⚠ **取得は成功するので例外にもログにも現れない。**

    ⚠ **RFC 9309 §2.2.2 の「最長一致優先」は採らない。** 標準と同じ
    「宣言順で最初に当たった規則」のままにしてあり、標準との差は
    ``*`` / ``$`` の展開だけに閉じている。そうすることで
    「既存16サイトの判定が変わらない」ことを実測で担保できる
    （2026-09-06 実測。差分は SUUMO の2本のみ → 課題#52）。

    ⚠ **判定にはクエリまで含める。** ``/*?*sort=`` はクエリを見る規則なので、
    パスだけで判定すると展開しても当たらない。
    """

    def __init__(self, lines: Iterable[str]) -> None:
        groups: dict[str, list[_RobotsRule]] = {}
        order: list[str] = []
        agents: list[str] = []
        in_rules = False
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field_name, _, value = line.partition(":")
            field_name = field_name.strip().lower()
            value = value.strip()
            if field_name == "user-agent":
                # 規則の直後の User-agent 行は新しいグループの始まり。
                if in_rules:
                    agents = []
                    in_rules = False
                key = value.lower()
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                if key not in agents:
                    agents.append(key)
            elif field_name in {"allow", "disallow"}:
                # ⚠ User-agent 行より前の規則は標準と同じく捨てる。
                if not agents:
                    continue
                in_rules = True
                # ⚠ 値が空の Disallow は「全許可」の意味（標準と同じ扱い）。
                rule = _RobotsRule(
                    allow=field_name == "allow" or not value,
                    value=value,
                    pattern=robots_path_pattern(value),
                )
                for key in agents:
                    groups[key].append(rule)
        self._groups: list[tuple[str, list[_RobotsRule]]] = [
            (key, groups[key]) for key in order
        ]

    @classmethod
    def parse(cls, text: str) -> RobotsRules:
        """robots.txt の本文から作る。⚠ **グループ統合を必ず通す**（→ 課題#43）。"""
        return cls(merge_robots_groups(text))

    def can_fetch(self, user_agent: str, url: str) -> bool:
        """このUAでこのURLを取得してよいか。規則が無ければ全許可。"""
        rules = self._rules_for(user_agent)
        if not rules:
            return True
        target = self._target(url)
        for rule in rules:
            if rule.pattern.match(target):
                return rule.allow
        return True

    def matched_rule(self, user_agent: str, url: str) -> str | None:
        """判定に効いた規則の値（診断用）。当たる規則が無ければ ``None``。"""
        target = self._target(url)
        for rule in self._rules_for(user_agent):
            if rule.pattern.match(target):
                return rule.value
        return None

    def _rules_for(self, user_agent: str) -> list[_RobotsRule]:
        """適用するグループを選ぶ（標準の ``Entry.applies_to`` と同じ規則）。

        名乗るUAの製品トークンに、robots 側のトークンが部分文字列として
        含まれれば適用する。名指しが1つも当たらなければ ``*`` を使う。
        """
        agent = user_agent.split("/")[0].lower()
        default: list[_RobotsRule] | None = None
        for token, rules in self._groups:
            if token == "*":
                if default is None:
                    default = rules
                continue
            if token in agent:
                return rules
        return default or []

    @staticmethod
    def _target(url: str) -> str:
        """判定に当てる文字列。⚠ **クエリまで含める**（→ 課題#52）。"""
        split = urlsplit(url)
        target = split.path or "/"
        return f"{target}?{split.query}" if split.query else target


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
    # robots.txt を無視するか。**既定は False で、既定を変えてはいけない。**
    # アパマンショップだけ `User-agent: * / Disallow: /` に対してユーザーが
    # 明示的に取得を選んだため、そのサイトのアダプタだけが True を宣言する
    # （→ ADR 0011）。取得間隔・日次上限はこのフラグでも一切緩めない
    ignore_robots: bool = False
    _last_request_at: float = 0.0
    _robots: RobotsRules | None = None
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
        """オリジンごとに robots.txt を1回だけ取得する。

        ⚠ **パースの前に ``merge_robots_groups`` を通す。** 標準の
        ``RobotFileParser`` は同じ User-agent のグループが2つ以上あると
        2つ目以降を落とし、**禁止パスを黙って許可と誤判定する**（→ 課題#43）。

        ⚠ **取得できなかったときは空ルール（＝全許可）で進む。**
        標準の ``RobotFileParser.read()`` は 401/403 を ``disallow_all`` に
        するので、ここは**標準より許可側に倒れている**。

        UR賃貸のAPIホストが実際に **HTTP 403** を返す（不在ではない）ため、
        黙って全許可になるのを避けて**記録されたうえでの全許可**にする
        （→ ADR 0019）。取得間隔・日次上限・打ち切りは緩めない。
        """
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        if self._robots_origin == origin:
            return
        rules = RobotsRules([])
        try:
            response = self.client.get(urljoin(origin, "/robots.txt"), timeout=15.0)
            if response.status_code == 200:
                rules = RobotsRules.parse(response.text)
            else:
                self._warn_robots_unavailable(origin, f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            self._warn_robots_unavailable(origin, type(exc).__name__)
        self._robots = rules
        self._robots_origin = origin

    def _warn_robots_unavailable(self, origin: str, reason: str) -> None:
        """robots.txt を読めなかったことをオリジンごとに1回だけ記録する。"""
        logger.warning(
            "%s: robots.txt を取得できませんでした（%s）。%s は全許可として進みます",
            self.site_code,
            reason,
            origin,
        )

    def is_allowed(self, url: str) -> bool:
        """robots.txt 上、このURLを取得してよいか。"""
        if self.ignore_robots:
            return True
        self._load_robots(url)
        if self._robots is None:
            return True
        user_agent = self.client.headers.get("User-Agent", "*")
        return self._robots.can_fetch(user_agent, url)

    def get(self, url: str) -> httpx.Response:
        """1ページ取得する。レート制御・robots判定・リトライを内包する。"""
        return self.request("GET", url)

    def post(self, url: str, data: dict[str, str]) -> httpx.Response:
        """フォームPOSTで取得する。``get`` と同じ制御を通る。

        UR賃貸は一覧・詳細ともJSON APIへの POST でしか取れない（→ ADR 0019）。
        ⚠ **レート制御・robots判定・バックオフ・打ち切り・日次上限は
        ``get`` と共有する。** メソッドが違うだけで相手にかける負荷は同じなので、
        ここに別経路を作ってはいけない。
        """
        return self.request("POST", url, data=data)

    def request(
        self, method: str, url: str, *, data: dict[str, str] | None = None
    ) -> httpx.Response:
        """取得の本体。レート制御・robots判定・リトライ・打ち切りを内包する。"""
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
                response = self.client.request(method, url, data=data)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                status = response.status_code
                if status not in RETRYABLE_STATUSES:
                    if status < 400 or status == NOT_FOUND_STATUS:
                        self.stats.consecutive_failures = 0
                        return response
                    # 403・405 のように相手が明示的に拒否している。再試行せず
                    # 失敗として数え、連続すれば打ち切る。ここを例外の素通しに
                    # すると失敗が1件も数えられず、打ち切りが永久に発火しない
                    return self._fail(
                        url,
                        httpx.HTTPStatusError(
                            f"HTTP {status}", request=response.request, response=response
                        ),
                    )
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )

            if attempt < MAX_RETRIES:
                self.stats.retries += 1
                self.sleep(backoff)  # type: ignore[operator]
                backoff *= 2

        return self._fail(url, last_error)

    def _fail(self, url: str, last_error: Exception | None) -> NoReturn:
        """失敗を数えて例外にする。連続が続けばサイトごと打ち切る。

        ⚠ **理由をメッセージ本文に入れる。** ``raise ... from`` の連鎖は
        ``t_scrape_logs`` へ ``str(exc)`` で書かれる時点で落ちるため、
        入れないとログが「取得に失敗しました」だけになり、
        **405（ボット検知）とタイムアウトと 403 を区別できない**
        （NIFTY の 405 を調べるのに実サイトを叩き直す羽目になった）。
        """
        self.stats.failures += 1
        self.stats.consecutive_failures += 1
        reason = f"（{last_error}）" if last_error is not None else ""
        if self.stats.consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            raise SiteAborted(
                f"{self.site_code}: {CONSECUTIVE_FAILURE_LIMIT} 回連続で失敗したため"
                f"打ち切ります{reason}"
            ) from last_error
        raise RuntimeError(
            f"{self.site_code}: 取得に失敗しました: {url}{reason}"
        ) from last_error


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
