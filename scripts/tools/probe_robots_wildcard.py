"""robots.txt のワイルドカード非対応の影響範囲を測る調査ツール（→ 課題#52）。

⚠ **これは測るためのもので、修正ではない。** 標準の
``urllib.robotparser.RobotFileParser`` は ``Disallow`` の ``*`` を展開せず
（``RuleLine.applies_to`` が単純な前方一致）、RFC 9309 §2.2.3 に反する。
誤判定は**許可側に倒れる**ので、**禁止パスを叩いていても取得が成功して気づけない**
（課題#43 とまったく同型）。

測るのは2つ:

1. 各サイトの robots.txt に**ワイルドカードを含む規則がいくつあるか**
2. **こちらが実際に組み立てるURL**が、そのうちどれに当たるか
   （アダプタの ``list_urls`` / ``page_url`` に組み立てさせる。推測しない）

⚠ **robots.txt はサイトごとに1本しか取らない。** 定期スキャンと並走させないこと。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/probe_robots_wildcard.py --fetch
    uv run python scripts/tools/probe_robots_wildcard.py
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.settings import Settings  # noqa: E402
from house_search.scrape import SCRAPERS  # noqa: E402
from house_search.scrape.area import AreaTarget  # noqa: E402
from house_search.scrape.fetch import merge_robots_groups  # noqa: E402

CACHE = Path("data/probe/robots")
MIN_INTERVAL_SEC = 3.0


# ---------------------------------------------------------------- 判定の2実装


def _wildcard_pattern(path: str) -> re.Pattern[str]:
    """robots の Disallow/Allow の値を正規表現へ変換する（RFC 9309 §2.2.3）。

    ``*`` は任意長、``$`` は末尾。それ以外は素の文字として扱う。
    """
    parts = []
    for chunk in path.split("*"):
        parts.append(re.escape(chunk))
    body = ".*".join(parts)
    if body.endswith(re.escape("$")):
        body = body[: -len(re.escape("$"))] + r"\Z"
    return re.compile("^" + body)


@dataclass(frozen=True)
class Rule:
    allow: bool
    path: str
    pattern: re.Pattern[str]


def _parse_rules(text: str) -> list[Rule]:
    """``User-agent: *`` のグループから Allow/Disallow を宣言順に集める。"""
    rules: list[Rule] = []
    applies = False
    for raw in merge_robots_groups(text) if isinstance(merge_robots_groups(text), list) else []:
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            applies = value == "*"
        elif applies and key in {"allow", "disallow"} and value:
            rules.append(Rule(key == "allow", value, _wildcard_pattern(value)))
    return rules


def wildcard_allows(rules: list[Rule], path: str) -> bool:
    """宣言順に最初に当たった規則を採る（標準の RobotFileParser と同じ順序規則）。

    ⚠ RFC 9309 は「最長一致優先」だが、ここでは**標準と同じ順序規則のまま
    ワイルドカードだけを足す**ことで、差分が `*` の展開だけになるようにしてある。
    """
    for rule in rules:
        if rule.pattern.match(path):
            return rule.allow
    return True


def standard_allows(text: str, ua: str, url: str) -> bool:
    merged = merge_robots_groups(text)
    parser = RobotFileParser()
    parser.parse(merged if isinstance(merged, list) else merged.splitlines())
    return parser.can_fetch(ua, url)


# ---------------------------------------------------------------- URL の組み立て


class _Search:
    prefectures = ("東京都",)
    cities: tuple[str, ...] = ()
    price_max_hint = 100_000
    site_filters = None


class _Pattern:
    search = _Search()
    property_type = "CHINTAI"


def sample_urls(site_code: str) -> list[str]:
    """アダプタ自身に一覧URLを組み立てさせる（推測しない）。"""
    factory = SCRAPERS.get(site_code)
    if factory is None:
        return []
    scraper = factory()
    areas = [AreaTarget(prefecture="東京都", city_name="足立区", jis_code="13121", value="13121")]
    urls: list[str] = []
    try:
        urls = list(scraper.list_urls(_Pattern(), areas))
    except Exception as exc:  # noqa: BLE001 - 調査ツールなので理由を出して続ける
        print(f"    ! {site_code}: list_urls が失敗しました: {exc}")
        return []
    extra: list[str] = []
    for url in urls[:1]:
        try:
            extra.append(scraper.page_url(url, 2))
        except Exception:  # noqa: BLE001, S110 - ページ送りが無いサイトもある
            pass
    return urls[:3] + extra


# ---------------------------------------------------------------- ステージ


def site_origins() -> dict[str, str]:
    """m_sites.base_url からサイトごとのオリジンを引く。"""
    from sqlalchemy import text as sql_text

    from house_search.db.session import get_engine

    origins: dict[str, str] = {}
    with get_engine().connect() as conn:
        rows = conn.execute(
            sql_text("SELECT code, base_url FROM m_sites WHERE base_url IS NOT NULL ORDER BY code")
        ).fetchall()
    for code, base_url in rows:
        parts = urlsplit(base_url)
        if parts.scheme and parts.netloc:
            origins[code] = f"{parts.scheme}://{parts.netloc}"
    return origins


def fetch_all(origins: dict[str, str]) -> None:
    settings = Settings()
    CACHE.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        timeout=settings.request_timeout_sec,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as client:
        for i, (code, origin) in enumerate(sorted(origins.items())):
            if i:
                time.sleep(MIN_INTERVAL_SEC * random.uniform(0.9, 1.3))
            url = f"{origin}/robots.txt"
            try:
                response = client.get(url)
                body = response.text if response.status_code < 400 else ""
                status = response.status_code
            except Exception as exc:  # noqa: BLE001 - 取れないサイトも記録して続ける
                body, status = "", f"error: {exc}"
            (CACHE / f"{code}.txt").write_text(body, encoding="utf-8")
            (CACHE / f"{code}.meta.json").write_text(
                json.dumps({"url": url, "status": str(status), "bytes": len(body)},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  {code:<14} HTTP {status} / {len(body):,} bytes")


def report(origins: dict[str, str]) -> int:
    settings = Settings()
    ua = settings.user_agent
    total_diff = 0
    print(f"  User-agent: {ua}\n")
    print(f"  {'サイト':<14} {'*規則':>5} {'URL':>4} {'差分':>4}  内容")
    print("  " + "-" * 76)
    details: list[str] = []
    for code in sorted(origins):
        path = CACHE / f"{code}.txt"
        if not path.exists():
            print(f"  {code:<14} （robots.txt 未取得）")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            print(f"  {code:<14} （空。取得できていない）")
            continue
        rules = _parse_rules(text)
        wild = [r for r in rules if "*" in r.path or "$" in r.path]
        urls = sample_urls(code)
        diffs = []
        for url in urls:
            std = standard_allows(text, ua, url)
            wc = wildcard_allows(rules, urlsplit(url).path + (
                "?" + urlsplit(url).query if urlsplit(url).query else ""
            ))
            if std != wc:
                diffs.append((url, std, wc))
        total_diff += len(diffs)
        mark = "  ← 差分あり" if diffs else ""
        print(f"  {code:<14} {len(wild):>5} {len(urls):>4} {len(diffs):>4}{mark}")
        for url, std, wc in diffs:
            hit = next((r.path for r in rules if r.pattern.match(
                urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")
            )), "?")
            details.append(f"    [{code}] 標準={'OK' if std else 'NG'} / 展開後={'OK' if wc else 'NG'}"
                           f"\n        規則: {hit}\n        URL : {url}")
    if details:
        print("\n  === 差分の内訳 ===")
        for line in details:
            print(line)
    print(f"\n  差分の合計: {total_diff} 本")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="robots.txt を取り直す")
    args = parser.parse_args()

    origins = site_origins()
    print(f"  対象サイト: {len(origins)} 件\n")
    if args.fetch:
        fetch_all(origins)
        print()
    return report(origins)


if __name__ == "__main__":
    raise SystemExit(main())
