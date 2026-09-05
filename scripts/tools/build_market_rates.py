"""SUUMO の家賃相場ページから「市区 × 間取り」の相場CSVを作る（オフライン）。

    # 取得（robots 許可済み・82市区で約4分）→ data/market_rates/raw/ へ保存
    uv run python scripts/tools/build_market_rates.py --fetch

    # 保存済みHTMLから生成だけやり直す（ネットワーク不要）
    uv run python scripts/tools/build_market_rates.py

⚠ **取得と解析を分けてある。** 解析の試行錯誤で取得を繰り返さないため
（ATHOME の市区リンクを単一引用符で取りこぼした件と同じ備え → 課題#36）。

⚠ **定期スキャンと並走させない。** SUUMO は既にスクレイピング対象で、
レート制御は `SiteFetcher` のプロセス内にしかない（→ ADR 0013 決定8）。
取得は定期スキャンが止まっている窓で行う。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "market_rates"
RAW = DATA / "raw"
SLUGS = DATA / "city_slugs.json"
OUT = DATA / "rent_rates.csv"
INDEX_DIR = RAW / "_index"

PREF_SLUG = {"13": "tokyo", "11": "saitama", "12": "chiba", "14": "kanagawa"}
INTERVAL = 3.0
SOURCE = "suumo_soba"
# 建物種別（ts）。既定の 1=マンションだけでは 2DK の相場が 82市区中26市区で
# 出ないため、2=アパートで補完する（→ soba.py の docstring・課題#49）
TS_APART = "2"
APART_SUFFIX = "_ts2"

# 索引の JSON: "121":{"name":"足立区","url":"/chintai/tokyo/sc_adachi/..."}
_INDEX_ENTRY = re.compile(
    r'"(\d{3})":\{"name":"([^"]+)","url":"/chintai/(\w+)/(sc_[a-z0-9_]+)/[^"]*"'
)


def _user_agent() -> str:
    sys.path.insert(0, str(REPO / "src"))
    from house_search.config.settings import load_settings

    return load_settings().user_agent


def _target_cities() -> set[str]:
    """検索パターンが対象にしている市区（帯の和集合）。"""
    cities: set[str] = set()
    for path in sorted((REPO / "configs").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cities |= set(data["search"]["cities"])
    return cities


def _must_layouts() -> set[str]:
    """検索パターンが MUST で使う間取り（帯の和集合）。

    ⚠ **補完の対象をここから決める。** 「相場が1つでも欠けている市区」にすると
    4LDK・5K以上まで拾って全市区が対象になり、取得が無駄に増える。
    """
    layouts: set[str] = set()
    for path in sorted((REPO / "configs").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        layouts |= set(data["must"].get("layouts") or [])
    return layouts


def fetch_index(ua: str) -> dict[str, dict[str, str]]:
    """都県索引から市区スラグと JIS の対応を採る（4リクエスト）。

    ⚠ **市区の同定は索引に埋まっている JIS コードで行う。**
    部分文字列一致で推測すると他市のコードが混入する（→ ADR 0014）。
    """
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict[str, str]] = {}
    for pref_code, pref_slug in PREF_SLUG.items():
        path = INDEX_DIR / f"{pref_slug}.html"
        if not path.exists():
            url = f"https://suumo.jp/chintai/soba/{pref_slug}/"
            resp = httpx.get(url, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=True)
            print(f"  索引 HTTP {resp.status_code} {url}")
            resp.raise_for_status()
            path.write_text(resp.text, encoding="utf-8")
            time.sleep(INTERVAL)
        text = path.read_text(encoding="utf-8", errors="replace")
        for code3, name, url_pref, slug in _INDEX_ENTRY.findall(text):
            if url_pref == pref_slug:
                out[name] = {"jis": pref_code + code3, "slug": slug}
    SLUGS.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return out


def fetch_cities(slugs: dict[str, dict[str, str]], ua: str) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    targets = sorted(
        (slugs[c]["jis"], c, slugs[c]["slug"]) for c in _target_cities() if c in slugs
    )
    print(f"市区ページを取得: {len(targets)}件 / 間隔 {INTERVAL}秒")
    for jis, name, slug in targets:
        path = RAW / f"{jis}.html"
        if path.exists():
            continue
        url = f"https://suumo.jp/chintai/soba/{PREF_SLUG[jis[:2]]}/{slug}/"
        resp = httpx.get(url, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=True)
        if resp.status_code != 200 or "家賃相場" not in resp.text:
            print(f"  NG {name}: HTTP {resp.status_code}")
        else:
            path.write_text(resp.text, encoding="utf-8")
        time.sleep(INTERVAL)


def fetch_apartments(slugs: dict[str, dict[str, str]], ua: str) -> None:
    """マンション相場に MUST の間取りが欠けている市区だけ ``ts=2`` を取る。

    ⚠ **取得は既存ファイルがあれば飛ばす。** 解析の試行錯誤で取得を繰り返さない。
    """
    sys.path.insert(0, str(REPO / "src"))
    from house_search.market.soba import parse_soba

    want = _must_layouts()
    by_jis = {v["jis"]: (name, v["slug"]) for name, v in slugs.items()}
    targets: list[str] = []
    for path in sorted(RAW.glob("*.html")):
        if path.stem.endswith(APART_SUFFIX):
            continue
        have = {r.layout for r in parse_soba(path.read_text(encoding="utf-8", errors="replace"))}
        if want - have:
            targets.append(path.stem)

    print(f"アパート相場を取得: {len(targets)}件（MUST の間取りが欠けている市区）")
    for jis in targets:
        out = RAW / f"{jis}{APART_SUFFIX}.html"
        if out.exists() or jis not in by_jis:
            continue
        name, slug = by_jis[jis]
        url = f"https://suumo.jp/chintai/soba/{PREF_SLUG[jis[:2]]}/{slug}/?ts={TS_APART}"
        resp = httpx.get(url, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=True)
        title = re.search(r"<title>(.*?)</title>", resp.text, re.S)
        title_text = re.sub(r"\s+", "", title.group(1)) if title else ""
        # ⚠ 取り違えを黙って通さない。ts が効かなくなればマンション相場が
        # アパート相場として保存され、例外にならないまま値だけがずれる
        if resp.status_code != 200 or "アパート" not in title_text:
            print(f"  NG {name}: HTTP {resp.status_code} title={title_text!r}")
        else:
            out.write_text(resp.text, encoding="utf-8")
        time.sleep(INTERVAL)


def build(period: str, acquired_on: str) -> int:
    sys.path.insert(0, str(REPO / "src"))
    from house_search.market.soba import STAT_BASIS_APART, merge_rates, parse_soba

    slugs = json.loads(SLUGS.read_text(encoding="utf-8"))
    by_jis = {v["jis"]: name for name, v in slugs.items()}

    def _rates(path: Path) -> list:
        return parse_soba(path.read_text(encoding="utf-8", errors="replace"))

    rows: list[dict[str, object]] = []
    missing: list[str] = []
    filled = 0
    for path in sorted(RAW.glob("*.html")):
        if path.stem.endswith(APART_SUFFIX):
            continue  # ⚠ 補完側は主ループで回さない（city_jis が壊れる）
        jis = path.stem
        name = by_jis.get(jis, "?")
        mansion = _rates(path)
        if not mansion:
            missing.append(name)
            continue
        apart_path = RAW / f"{jis}{APART_SUFFIX}.html"
        apart = _rates(apart_path) if apart_path.exists() else []
        for rate in merge_rates(mansion, apart):
            filled += rate.stat_basis == STAT_BASIS_APART
            rows.append(
                {
                    "city_jis": jis,
                    "city_name": name,
                    "segment": rate.layout,
                    "rate_value": rate.rent_yen,
                    "source": SOURCE,
                    "stat_basis": rate.stat_basis,
                    "period": period,
                    "acquired_on": acquired_on,
                }
            )

    # ⚠ 生成できた市区が想定より少なければ止める。0件のCSVを黙って書くと
    # 「相場が無いまま採点が続く」状態になり、例外にならない
    cities = {r["city_jis"] for r in rows}
    expected = len([p for p in RAW.glob("*.html") if not p.stem.endswith(APART_SUFFIX)])
    if expected and len(cities) < expected:
        print(f"⚠ 相場を取れなかった市区: {sorted(missing)}")
    if not rows:
        raise SystemExit("相場が1件も作れませんでした（ページ構造の変更を疑う）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"生成: {OUT.relative_to(REPO)} / {len(rows):,}行 / {len(cities)}市区")
    # ⚠ 補完を黙って行わない。件数が急に増減したら建物種別の扱いを疑う材料になる
    print(f"  うちアパート相場で補完: {filled}行")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="SUUMO から取得し直す")
    parser.add_argument("--period", default=dt.date.today().strftime("%Y-%m"))
    args = parser.parse_args()

    if args.fetch:
        ua = _user_agent()
        slugs = fetch_index(ua)
        fetch_cities(slugs, ua)
        fetch_apartments(slugs, ua)
    build(args.period, dt.date.today().isoformat())


if __name__ == "__main__":
    main()
