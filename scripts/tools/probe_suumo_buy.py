"""SUUMO 売買の取得仕様を実測する調査ツール（→ 課題#4・Phase 6 手順3）。

売買のクエリキー名と選択肢の値は**まだ1つも測っていない**。調査資料
（``資料_サイト別検索条件一覧.md`` §1.2〜§1.5）にあるのは**フォームのラベルだけ**で、
URLのキーは載っていない。推測で書くと「0件になる／黙って無視される／向きが逆」の
いずれかになり、**どれも例外にならない**（→ ADR 0015・課題#29）。

⚠ **取得した応答は必ず保存する**（``--cache-dir``）。解析を直したくなったときに
取得をやり直さずに済む。ATHOME の市区リンクが単一引用符で71市区中10市区しか
拾えなかったとき、これがあったので取得予算を1回も使わずに直せた（→ 課題#36）。

⚠ **キーと選択肢は、まず一覧ページに埋まっている検索フォームから採る。**
HOMES・GOO・APAMAN・ATHOME はすべてこれで確定でき、実サイトは検証だけに使えた。

⚠ **「効いた」の判定方法そのものの妥当性を先に担保する。** 存在しないキー
（``zzz=1``）を送って総件数が変わらないことを確かめてから各パラメータを測る。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/probe_suumo_buy.py --stage robots
    uv run python scripts/tools/probe_suumo_buy.py --stage city
    uv run python scripts/tools/probe_suumo_buy.py --stage fetch --url "..." --label list_chuko_m
    uv run python scripts/tools/probe_suumo_buy.py --stage forms --label list_chuko_m
    uv run python scripts/tools/probe_suumo_buy.py --stage links --label city_chuko_m
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.settings import Settings  # noqa: E402
from house_search.scrape.fetch import merge_robots_groups  # noqa: E402

BASE_URL = "https://suumo.jp"
ROBOTS_URL = f"{BASE_URL}/robots.txt"

# 種別ごとの市区選択ページ（調査資料 §1.2〜§1.5）。
# ⚠ **これは一覧ページではない**。ここから一覧へのリンクを辿ってURLの形を確かめる。
CITY_PAGES: dict[str, str] = {
    "chuko_m": f"{BASE_URL}/ms/chuko/tokyo/city/",
    "shinchiku_m": f"{BASE_URL}/ms/shinchiku/tokyo/city/",
    "shinchiku_k": f"{BASE_URL}/ikkodate/tokyo/city/",
    "chuko_k": f"{BASE_URL}/chukoikkodate/tokyo/city/",
}

# 許可を確かめたいパス。賃貸で使っている一覧パスも対照として入れる。
ROBOTS_CHECK_PATHS = (
    "/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13",
    "/ms/chuko/tokyo/city/",
    "/ms/shinchiku/tokyo/city/",
    "/ikkodate/tokyo/city/",
    "/chukoikkodate/tokyo/city/",
    "/jj/bukken/ichiran/JJ012FC001/?ar=030&bs=011",
    "/ms/chuko/tokyo/sc_chiyoda/",
)

DEFAULT_CACHE = Path("data/probe/suumo_buy")
# SUUMO の m_sites.min_interval_sec は 2.5秒。調査でもそれを下回らない
MIN_INTERVAL_SEC = 3.0


def _sleep(seconds: float) -> None:
    time.sleep(seconds * random.uniform(0.9, 1.3))


def _fetch(client: httpx.Client, url: str, cache_dir: Path, label: str) -> httpx.Response:
    """1本取得して必ず保存する。"""
    response = client.get(url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{label}.html").write_text(response.text, encoding="utf-8")
    (cache_dir / f"{label}.meta.json").write_text(
        json.dumps(
            {
                "url": url,
                "status": response.status_code,
                "bytes": len(response.content),
                "content_type": response.headers.get("content-type"),
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  {label}: HTTP {response.status_code} / {len(response.content):,} bytes / {url}")
    return response


def stage_robots(client: httpx.Client | None, cache_dir: Path, *, reuse: bool = False) -> None:
    """robots.txt を取得し、売買のパスが許可されているかを判定する。"""
    from urllib.robotparser import RobotFileParser

    if reuse:
        text = (cache_dir / "robots.html").read_text(encoding="utf-8")
    else:
        text = _fetch(client, ROBOTS_URL, cache_dir, "robots").text
    # ⚠ 標準の RobotFileParser は同じ User-agent のグループが2つ以上あると
    # 2つ目以降を丸ごと落とす（RFC 9309 §2.2.1 違反 → 課題#43）。
    # 本体と同じく merge_robots_groups を通してから解析する
    merged = merge_robots_groups(text)
    parser = RobotFileParser()
    parser.parse(merged if isinstance(merged, list) else merged.splitlines())

    ua = client.headers.get("User-Agent", "*") if client else Settings().user_agent
    groups = len(re.findall(r"(?mi)^\s*user-agent\s*:\s*\*", text))
    print(f"\n  User-agent: {ua}")
    print(f"  `User-agent: *` のグループ数: {groups}（2以上なら統合が効いている）")
    for path in ROBOTS_CHECK_PATHS:
        allowed = parser.can_fetch(ua, urljoin(BASE_URL, path))
        print(f"    {'OK ' if allowed else 'NG '} {path}")


def stage_city(client: httpx.Client, cache_dir: Path, kinds: list[str]) -> None:
    """種別ごとの市区選択ページを取得して保存する。"""
    for i, kind in enumerate(kinds):
        url = CITY_PAGES[kind]
        if i:
            _sleep(MIN_INTERVAL_SEC)
        _fetch(client, url, cache_dir, f"city_{kind}")


def stage_fetch(client: httpx.Client, cache_dir: Path, url: str, label: str) -> None:
    _fetch(client, url, cache_dir, label)


def stage_measure(
    client: httpx.Client, cache_dir: Path, base: str, params: list[str], label: str
) -> None:
    """同じベースURLへパラメータを変えて取り、間隔を空けて保存する。

    ⚠ **1本目は必ず素のURL（対照）にする。** 存在しないキー（``zzz=1``）で
    結果が変わらないことを先に確かめないと「絞れていないのに絞れたつもり」になる。
    """
    for i, param in enumerate(params):
        url = base if not param else f"{base}{'&' if '?' in base else '?'}{param}"
        slug = "base" if not param else re.sub(r"[^0-9a-zA-Z]+", "_", param).strip("_")
        if i:
            _sleep(MIN_INTERVAL_SEC)
        _fetch(client, url, cache_dir, f"{label}_{slug}")


def stage_links(cache_dir: Path, label: str, pattern: str | None) -> None:
    """保存済みHTMLからリンクを抜き出す（ネットワーク不要）。

    ⚠ **市区選択ページから一覧へ辿るのが目的**。一覧URLの形は推測しない。
    """
    doc = lxml_html.fromstring((cache_dir / f"{label}.html").read_text(encoding="utf-8"))
    seen: dict[str, str] = {}
    for anchor in doc.cssselect("a[href]"):
        href = urljoin(BASE_URL, anchor.get("href") or "")
        text = " ".join(anchor.text_content().split())
        if pattern and pattern not in href:
            continue
        seen.setdefault(href, text)
    print(f"  リンク {len(seen)} 本（pattern={pattern!r}）")
    for href, text in list(seen.items())[:80]:
        print(f"    {text[:28]:<30} {href}")


def stage_forms(cache_dir: Path, label: str) -> None:
    """保存済みHTMLから検索フォームのキーと選択肢を抜き出す（ネットワーク不要）。

    ⚠ **これが本命**。HOMES・GOO・APAMAN・ATHOME はすべてここでキー名と選択肢を
    確定でき、実サイトは検証だけに使えた（取得予算を使わずに済む）。
    """
    doc = lxml_html.fromstring((cache_dir / f"{label}.html").read_text(encoding="utf-8"))

    print("\n  === form ===")
    for form in doc.cssselect("form"):
        action = form.get("action") or "(なし)"
        method = (form.get("method") or "get").lower()
        print(f"    action={action}  method={method}  id={form.get('id')}")

    print("\n  === select ===")
    for select in doc.cssselect("select[name]"):
        name = select.get("name")
        options = [
            (opt.get("value"), " ".join(opt.text_content().split()))
            for opt in select.cssselect("option")
        ]
        print(f"    {name}  ({len(options)} 択)")
        for value, text in options[:40]:
            print(f"        {value!r:<14} {text}")

    print("\n  === checkbox / radio ===")
    grouped: dict[str, list[tuple[str, str]]] = {}
    for node in doc.cssselect("input[type=checkbox][name], input[type=radio][name]"):
        name = node.get("name") or ""
        label_text = ""
        node_id = node.get("id")
        if node_id:
            labels = doc.cssselect(f"label[for='{node_id}']")
            if labels:
                label_text = " ".join(labels[0].text_content().split())
        grouped.setdefault(name, []).append((node.get("value") or "", label_text))
    for name, values in grouped.items():
        print(f"    {name}  ({len(values)} 個)")
        for value, text in values[:40]:
            print(f"        {value!r:<14} {text}")

    print("\n  === hidden ===")
    for node in doc.cssselect("input[type=hidden][name]"):
        print(f"    {node.get('name')!r} = {node.get('value')!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        required=True,
        choices=["robots", "city", "fetch", "links", "forms", "measure"],
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--kind", action="append", choices=sorted(CITY_PAGES))
    parser.add_argument("--url")
    parser.add_argument("--base", help="measure: 共通のベースURL")
    parser.add_argument(
        "--param", action="append", default=[], help="measure: 付けるクエリ（空文字で対照）"
    )
    parser.add_argument("--label")
    parser.add_argument("--pattern", help="links: このパターンを含むhrefだけ出す")
    parser.add_argument(
        "--reuse", action="store_true", help="保存済みの応答を使い、取得をやり直さない"
    )
    args = parser.parse_args()

    if args.stage == "robots" and args.reuse:
        stage_robots(None, args.cache_dir, reuse=True)
        return 0

    if args.stage in {"links", "forms"}:
        if not args.label:
            parser.error("--label が要ります")
        if args.stage == "links":
            stage_links(args.cache_dir, args.label, args.pattern)
        else:
            stage_forms(args.cache_dir, args.label)
        return 0

    settings = Settings()
    with httpx.Client(
        timeout=settings.request_timeout_sec,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    ) as client:
        if args.stage == "robots":
            stage_robots(client, args.cache_dir)
        elif args.stage == "city":
            stage_city(client, args.cache_dir, args.kind or ["chuko_m"])
        elif args.stage == "measure":
            if not (args.base and args.label):
                parser.error("--base と --label が要ります")
            stage_measure(client, args.cache_dir, args.base, args.param or [""], args.label)
        else:
            if not (args.url and args.label):
                parser.error("--url と --label が要ります")
            stage_fetch(client, args.cache_dir, args.url, args.label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
