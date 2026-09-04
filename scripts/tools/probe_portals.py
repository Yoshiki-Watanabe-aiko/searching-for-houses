"""ポータル系3サイトの取得仕様を実測する（→ 課題#37・Phase 5H）。

ハウスコム・ホームメイト・CHINTAI.net が対象。

課題#37 の実測チェックリストを、実装の前に測るためのもの。結果は詳細設計書 §13 に書く。

⚠ **§9.2 の候補表にホスト名が書かれていない。** D-room で
``www.d-room.jp``（さくらインターネットの共有サーバ）を叩いた失敗（→ §12.1）を
繰り返さないよう、**まず robots.txt の内容が §9.2 の記録と一致するか**で
ホストを同定する。一致しなければそのホストは別サイトである。

⚠ **取得した応答は必ず保存する**（``data/probe/portals/``）。解析を直したくなったときに
取得をやり直さずに済む（ATHOME の市区リンク・D-room の住戸単位で実際に効いた）。

⚠ **ポータル系は「分布を先に測る」判定が効かない**（→ §12.5 で D-room に効いた手）。
普通の賃貸ポータルなので間取り・面積の条件を満たす住戸は必ずある。
判定材料になるのは**既存DBとの重複**で、これは実装しないと `dedup-stats` で測れない。
そのため ``--stage overlap`` で「一覧の建物名・賃料・面積が既存DBにあるか」を
突き合わせ、実装前に重複率を一次見積もりする。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/probe_portals.py --stage robots
    uv run python scripts/tools/probe_portals.py --stage top
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.settings import Settings  # noqa: E402

CACHE = Path("data/probe/portals")

# 候補ホスト。⚠ **推測である**。robots.txt の内容が §9.2 の記録と一致して初めて確定する。
CANDIDATES = {
    "HOUSECOM": "https://www.housecom.jp",
    "HOMEMATE": "https://www.homemate.co.jp",
    "CHINTAI_NET": "https://www.chintai.net",
}

# §9.2 に記録されている Disallow の一部。ホスト同定の照合に使う。
EXPECTED_DISALLOW = {
    "HOUSECOM": ["/js/", "/faq/readmore/", "/here/map/"],
    "HOMEMATE": ["/ad", "/rent/qr.asp", "/rent/dtlprint.asp"],
    "CHINTAI_NET": ["/api/"],
}

MIN_INTERVAL_SEC = 2.5


def fetch(url: str, name: str) -> httpx.Response:
    ua = Settings().user_agent
    res = httpx.get(url, headers={"User-Agent": ua}, timeout=60, follow_redirects=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / name).write_bytes(res.content)
    print(f"{url}\n  -> HTTP {res.status_code} / {len(res.content):,} bytes"
          f" / final={res.url} -> {CACHE / name}")
    return res


def stage_robots() -> None:
    for code, base in CANDIDATES.items():
        print(f"\n===== {code} =====")
        res = fetch(f"{base}/robots.txt", f"{code}_robots.txt")
        text = res.text
        if res.status_code != 200:
            print(f"  ⚠ robots.txt が HTTP {res.status_code}（UR と同じく 403 なら拒否）")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        print(f"  行数 {len(lines)}")
        for ln in lines[:40]:
            print(f"    {ln}")
        if len(lines) > 40:
            print(f"    ...（残り {len(lines) - 40} 行）")
        hit = [p for p in EXPECTED_DISALLOW[code] if p in text]
        miss = [p for p in EXPECTED_DISALLOW[code] if p not in text]
        print(f"  §9.2 の記録との照合: 一致 {hit} / 不一致 {miss}")
        if miss:
            print("  ⚠⚠ 不一致がある。ホストが違う可能性を疑うこと（→ §12.1 の D-room の失敗）")
        time.sleep(MIN_INTERVAL_SEC)


def stage_top() -> None:
    """トップページを取り、サイト名が名乗りどおりかを確かめる（ホスト同定の裏取り）。"""
    import lxml.html

    for code, base in CANDIDATES.items():
        print(f"\n===== {code} =====")
        res = fetch(base + "/", f"{code}_top.html")
        root = lxml.html.fromstring(res.content)
        title = (root.cssselect("title")[0].text_content().strip()
                 if root.cssselect("title") else "(no title)")
        print(f"  title: {title}")
        time.sleep(MIN_INTERVAL_SEC)


def stage_fetch(site: str, path: str, name: str | None = None) -> None:
    """任意のパスを1本だけ取って保存する（汎用）。

    ⚠ **推測でURLを組まない。** 索引ページのリンクから採った実在のパスだけを渡すこと。
    """
    base = CANDIDATES[site]
    fetch(base + path, name or f"{site}{path.replace('/', '_')}.html")


def stage_links(site: str, name: str, pattern: str, limit: int = 25) -> None:
    """保存済みHTMLからリンクを抜く（取得0本）。"""
    import re

    import lxml.html

    root = lxml.html.fromstring((CACHE / name).read_bytes())
    rx = re.compile(pattern)
    seen: list[tuple[str, str]] = []
    for a in root.cssselect("a[href]"):
        href = a.get("href") or ""
        txt = re.sub(r"\s+", " ", a.text_content()).strip()
        if rx.search(href) and (href, txt) not in seen:
            seen.append((href, txt))
    for href, txt in seen[:limit]:
        print(f"  {txt[:22]:<24} {href}")
    print(f"  （該当 {len(seen)} 本）")


# ---------------------------------------------------------------------------
# 実測で確定したこと（詳細は詳細設計書 §13）
# ---------------------------------------------------------------------------
# ⚠ ハウスコムの市区スラグは政令市の行政区が **アンダースコア区切り**
#    （saitamashi_minamiku-city）。``[a-z0-9\-]+`` だと27市区が黙って落ちる。
HOUSECOM_CITY_RE = r"^/{pref}/([a-z0-9_\-]+)-city/$"

# ⚠ ハウスコムの間取りコード（パス形式 cc_mdr-N）。MUST の5種は 4/5/6/9/10。
HOUSECOM_LAYOUT_CODES = {
    "1R": 1, "1K": 2, "1DK": 3, "1LDK": 4, "2K": 5, "2DK": 6,
    "2LDK": 7, "3K": 8, "3DK": 9, "3LDK": 10, "4K": 11,
}
MUST_LAYOUTS = {"1LDK", "2K", "2DK", "2LDK", "3DK", "3LDK"}


def stage_slugs() -> None:
    """ハウスコムの市区スラグを都道府県索引から集める（1都道府県1リクエスト）。

    ⚠ **アンダースコアを含めること**（→ §13.8）。含めないと政令市の行政区が
    黙って落ち、``resolve_areas`` がその市区を対象から外す（→ 課題#36）。
    """
    import json
    import re

    import lxml.html

    out: dict[str, dict[str, str]] = {}
    for pref, slug in [("東京都", "tokyo"), ("埼玉県", "saitama"),
                       ("千葉県", "chiba"), ("神奈川県", "kanagawa")]:
        res = fetch(f"{CANDIDATES['HOUSECOM']}/{slug}/", f"HC_area_{slug}.html")
        root = lxml.html.fromstring(res.content)
        rx = re.compile(HOUSECOM_CITY_RE.format(pref=slug))
        found = {}
        for a in root.cssselect("a[href]"):
            m = rx.match(a.get("href") or "")
            name = re.sub(r"\s+", "", a.text_content()).strip()
            if m and re.search(r"[区市町村]$", name):
                found[name] = m.group(1)
        out[pref] = found
        print(f"  {pref}: {len(found)} 市区")
        time.sleep(MIN_INTERVAL_SEC)
    path = CACHE / "housecom_slugs.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  -> {path}（合計 {sum(len(v) for v in out.values())} 市区）")


def stage_endurance(site: str, urls: list[str]) -> None:
    """本番想定間隔で連続取得し、上限・検知の発火位置を観測する。

    ⚠ **1リクエスト目は必ず正常に返る**（HOMES・ATHOME・NIFTY で実証済み）。
    単発の疎通確認を「取得できる」の根拠にしてはいけない（→ 課題#36）。
    """
    import lxml.html

    ng = 0
    for i, url in enumerate(urls, 1):
        started = time.time()
        try:
            res = fetch(url, f"{site}_endurance_{i:02d}.html")
            root = lxml.html.fromstring(res.content)
            rooms = len(root.cssselect(".property_room" if site == "HOUSECOM"
                                       else ".m_prpty_list_room"))
            ok = res.status_code == 200
            ng += 0 if ok else 1
            print(f"  {i:>2} HTTP {res.status_code} / {len(res.content):>7,}B / 住戸 {rooms:>3}"
                  f"{'' if ok else ' ⚠'}")
        except Exception as exc:  # noqa: BLE001
            ng += 1
            print(f"  {i:>2} 失敗 {type(exc).__name__}")
        time.sleep(max(0.0, MIN_INTERVAL_SEC - (time.time() - started)))
    print(f"  異常 {ng}/{len(urls)} 件")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["robots", "top", "fetch", "links", "slugs"])
    ap.add_argument("--site", choices=sorted(CANDIDATES))
    ap.add_argument("--path")
    ap.add_argument("--name")
    ap.add_argument("--pattern")
    args = ap.parse_args()
    if args.stage == "robots":
        stage_robots()
    elif args.stage == "top":
        stage_top()
    elif args.stage == "slugs":
        stage_slugs()
    elif args.stage == "fetch":
        stage_fetch(args.site, args.path, args.name)
    else:
        stage_links(args.site, args.name, args.pattern)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
