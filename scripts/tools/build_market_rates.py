"""SUUMO の家賃相場ページから「市区 × 間取り」の相場CSVを作る（オフライン）。

    # 取得（全国47都道府県・1,277市区。3秒間隔で約90分）→ data/market_rates/raw/{YYYY-MM}/
    uv run python scripts/tools/build_market_rates.py --fetch

    # 保存済みHTMLから生成だけやり直す（ネットワーク不要）
    uv run python scripts/tools/build_market_rates.py

⚠ **取得と解析を分けてある。** 解析の試行錯誤で取得を繰り返さないため
（ATHOME の市区リンクを単一引用符で取りこぼした件と同じ備え → 課題#36）。

⚠⚠ **保存先は月ごとのディレクトリにする。** 旧実装は `raw/{jis}.html` という
月を含まない名前だったため、`if path.exists(): continue` が毎回効いて
**毎月1日のタスクが走っても1本も取得しなかった**。それでいて `period` は
生成時の年月になるので、**古い相場が新しい月のラベルでDBに入っていた**
（例外にもならず、CSV も「更新されました」と出る → 課題#49）。

⚠ **取得は `scraping_lock` を取る。** 全国だと約90分かかり、その間に
2時間ごとの定期スキャンが起動する。レート制御は `SiteFetcher` のプロセス内に
しかないので、並走すると SUUMO への実効間隔が半分になる（→ ADR 0013 決定8）。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import shutil
import sys
import time
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from house_search.scrape.prefectures import (  # noqa: E402
    PREFECTURE_JIS,
    PREFECTURE_ROMAJI,
)

DATA = REPO / "data" / "market_rates"
RAW = DATA / "raw"
SLUGS = DATA / "city_slugs.json"
OUT = DATA / "rent_rates.csv"

# ⚠⚠ 相場ページだけ綴りが違う県（実測 2026-09-09・47都道府県を1本ずつ確認）。
#   北海道 `hokkaido_`（末尾アンダースコア）・群馬 `gumma`。
# ⚠ **`hokkaido` は 404 にならない。** HTTP 200 で「北海道の都道府県から賃貸家賃相場を
#   調べる」という空の案内ページ（39KB・市区リンク0件）が返るので、
#   **209市区を落としたまま「取れたつもり」になる**（群馬は素直に 404 だった）。
# ⚠ `PREFECTURE_ROMAJI` 側は直さない。他アダプタが使う一覧用のスラグであり、
#   サイト固有の綴り違いは使う側で上書きする（`prefectures.py` の方針）。
_SLUG_OVERRIDES: dict[str, str] = {"01": "hokkaido_", "10": "gumma"}

# 都道府県コード（JIS2桁）→ 相場ページのスラグ。
# ⚠ 綴りが違うと空ページか 404 になり、その県の市区が0件のまま通り過ぎるので、
# `fetch_index` が索引0件の県を集めて最後に非0で終わる。
PREF_SLUG: dict[str, str] = {
    jis: _SLUG_OVERRIDES.get(jis, PREFECTURE_ROMAJI[name]) for name, jis in PREFECTURE_JIS.items()
}

INTERVAL = 3.0
SOURCE = "suumo_soba"
# 建物種別（ts）。既定の 1=マンションだけでは 2DK の相場が出ない市区が多いため、
# 2=アパートで補完する（→ soba.py の docstring・課題#49）
TS_APART = "2"
APART_SUFFIX = "_ts2"

# raw を残す月数（当月を含む）。
# ⚠ 売買（`fetch_reinfolib_trades.py`）は窓4四半期ぶんを集計に使うので消さないが、
# 賃貸は**当月ぶんしか集計に使わない**ので、先月ぶんを比較用に1つ残せば足りる。
# 全国だと1ヶ月あたり約100MBになるため、残し続けると年1.2GBになる。
KEEP_MONTHS = 2

# 「相場表が無いページ」の許容割合。これを超えたら止める。
# ⚠ 実測は 8/1,277＝0.6%（掲載の少ない郡部）。構造が変われば全件がこれになるので、
# 低すぎる閾値にすると正常な郡部で止まり、高すぎると構造変更に気づけない
MISSING_LIMIT = 0.10

_MONTH_DIR = re.compile(r"\d{4}-\d{2}")

# 索引の JSON: "121":{"name":"足立区","url":"/chintai/tokyo/sc_adachi/..."}
_INDEX_ENTRY = re.compile(
    r'"(\d{3})":\{"name":"([^"]+)","url":"/chintai/(\w+)/(sc_[a-z0-9_]+)/[^"]*"'
)


def _user_agent() -> str:
    from house_search.config.settings import load_settings

    return load_settings().user_agent


def safe_parse_soba(text: str) -> list:
    """相場ページを解析する。⚠ **相場表が無いページは空リストにする**。

    ⚠⚠ **全国化して初めて出た**（実測 2026-09-09・1,277市区中8件）。掲載の少ない
    郡部（積丹郡・土佐郡・土佐清水市など）は**タイトルは正常なのに相場表が無い**。
    4都県では全市区に表があったため表面化しなかった。
    ⚠ `parse_soba` の例外文言は「ページ構造が変わった可能性」だが、実態は
    **その市区に相場が無い**だけ。ここで吸収し、件数は呼び出し側が報告する
    （握りつぶすと構造変更に気づけなくなるので、`build` が割合で見張る）。
    """
    from house_search.market.soba import SobaParseError, parse_soba

    try:
        return parse_soba(text)
    except SobaParseError:
        return []


def month_dir(period: str) -> Path:
    """その月の取得結果を置くディレクトリ（`raw/2026-10`）。"""
    return RAW / period


def _must_layouts() -> set[str]:
    """検索パターンが MUST で使う間取り（帯の和集合）。

    ⚠ **アパート補完の対象をここから決める。** 「相場が1つでも欠けている市区」に
    すると 4LDK・5K以上まで拾って全市区が対象になり、取得が無駄に増える。
    """
    layouts: set[str] = set()
    for path in sorted((REPO / "configs").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        layouts |= set(data["must"].get("layouts") or [])
    return layouts


def prune_old_months(period: str, keep: int = KEEP_MONTHS) -> list[str]:
    """当月を含めて `keep` ヶ月ぶんを残し、古い月のディレクトリを消す。"""
    if not RAW.exists():
        return []
    months = sorted(
        (p.name for p in RAW.iterdir() if p.is_dir() and _MONTH_DIR.fullmatch(p.name)),
        reverse=True,
    )
    if period in months:
        months.remove(period)
    stale = months[max(0, keep - 1) :]
    for name in stale:
        shutil.rmtree(RAW / name)
    if stale:
        print(f"古い取得結果を削除: {', '.join(sorted(stale))}")
    return stale


def parse_index(text: str, pref_code: str, pref_slug: str) -> dict[str, dict[str, str]]:
    """索引HTMLから ``{JIS5桁: {name, slug}}`` を作る（ネットワーク不要）。

    ⚠ **市区の同定は索引に埋まっている JIS コードで行う。**
    部分文字列一致で推測すると他市のコードが混入する（→ ADR 0014）。

    ⚠⚠ **キーは市区名ではなく JIS コードにする。** 全国では同名の市区が7組ある
    （東京都府中市13206 と広島県府中市34208、伊達市01233/07213 ほか）。
    名前をキーにすると**後に読んだ県で上書きされて片方が黙って消える**
    （実測 2026-09-09。1,277 → 1,270件になり、東京都府中市が落ちていた）。

    ⚠ **URL の都道府県部分が要求したスラグと一致する行だけ採る。** 索引には
    他県へのリンクも混じる。
    """
    out: dict[str, dict[str, str]] = {}
    for code3, name, url_pref, slug in _INDEX_ENTRY.findall(text):
        if url_pref == pref_slug:
            out[pref_code + code3] = {"name": name, "slug": slug}
    return out


def fetch_index(ua: str, base: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """47都道府県の索引から市区スラグと JIS の対応を採る。

    戻り値は (JIS5桁 → {name, slug}, 索引が1件も採れなかった都道府県スラグ)。
    """
    index_dir = base / "_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict[str, str]] = {}
    empty: list[str] = []
    for pref_code, pref_slug in sorted(PREF_SLUG.items()):
        path = index_dir / f"{pref_slug}.html"
        if not path.exists():
            url = f"https://suumo.jp/chintai/soba/{pref_slug}/"
            resp = httpx.get(url, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=True)
            if resp.status_code != 200:
                print(f"  NG 索引 {pref_slug}: HTTP {resp.status_code}")
                empty.append(pref_slug)
                time.sleep(INTERVAL)
                continue
            # ⚠⚠ **200 でも中身が空の案内ページのことがある**（北海道を `hokkaido` で
            #   引くと市区リンク0件のページが 200 で返る）。保存すると次回以降
            #   「取得済み」と見なされて**永久に0件のまま**になるので、
            #   市区リンクが取れたときだけ保存する
            if not _INDEX_ENTRY.search(resp.text):
                print(f"  NG 索引 {pref_slug}: 市区リンクが0件（スラグの綴りを疑う）")
                empty.append(pref_slug)
                time.sleep(INTERVAL)
                continue
            path.write_text(resp.text, encoding="utf-8")
            time.sleep(INTERVAL)
        found = parse_index(
            path.read_text(encoding="utf-8", errors="replace"), pref_code, pref_slug
        )
        out.update(found)
        if not found:
            print(f"  NG 索引 {pref_slug}: 市区リンクが0件（スラグの綴りを疑う）")
            empty.append(pref_slug)
    SLUGS.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"索引: {len(PREF_SLUG) - len(empty)}/{len(PREF_SLUG)}都道府県 / {len(out):,}市区")
    return out, empty


def pending_cities(slugs: dict[str, dict[str, str]], base: Path) -> list[tuple[str, str, str]]:
    """その月にまだ取っていない市区を返す（JIS, 市区名, スラグ）。

    ⚠ **検索パターンの市区に絞らない**（2026-09-09 ユーザー判断で全国化）。
    絞ると帯を広げたときにその市区の相場が無く、`market_rate_ratio` が
    未解決のまま順位に効かない（例外にならない）。

    ⚠⚠ **既取得の判定は月ディレクトリの中で行う。** これが「毎月の取り直し」の
    実体で、月をまたげば同じ市区がふたたび対象になる。旧実装は月を含まない
    `raw/{jis}.html` を見ていたため、**タスクが毎月走っても1本も取得しなかった**。
    """
    targets = sorted((jis, v["name"], v["slug"]) for jis, v in slugs.items())
    return [t for t in targets if not (base / f"{t[0]}.html").exists()]


def fetch_cities(slugs: dict[str, dict[str, str]], ua: str, base: Path) -> None:
    """索引に載っている全市区の相場ページを取る。"""
    base.mkdir(parents=True, exist_ok=True)
    todo = pending_cities(slugs, base)
    print(f"市区ページを取得: {len(todo)}件（対象 {len(slugs)}件）/ 間隔 {INTERVAL}秒")
    for jis, name, slug in todo:
        path = base / f"{jis}.html"
        url = f"https://suumo.jp/chintai/soba/{PREF_SLUG[jis[:2]]}/{slug}/"
        resp = httpx.get(url, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=True)
        if resp.status_code != 200 or "家賃相場" not in resp.text:
            print(f"  NG {name}: HTTP {resp.status_code}")
        else:
            path.write_text(resp.text, encoding="utf-8")
        time.sleep(INTERVAL)


def fetch_apartments(slugs: dict[str, dict[str, str]], ua: str, base: Path) -> None:
    """マンション相場に MUST の間取りが欠けている市区だけ ``ts=2`` を取る。"""
    want = _must_layouts()
    by_jis = {jis: (v["name"], v["slug"]) for jis, v in slugs.items()}
    targets: list[str] = []
    for path in sorted(base.glob("*.html")):
        if path.stem.endswith(APART_SUFFIX):
            continue
        have = {
            r.layout for r in safe_parse_soba(path.read_text(encoding="utf-8", errors="replace"))
        }
        if want - have:
            targets.append(path.stem)

    todo = [j for j in targets if j in by_jis and not (base / f"{j}{APART_SUFFIX}.html").exists()]
    print(f"アパート相場を取得: {len(todo)}件（MUST の間取りが欠けている市区 {len(targets)}件）")
    for jis in todo:
        out = base / f"{jis}{APART_SUFFIX}.html"
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


def build(base: Path, period: str, acquired_on: str) -> int:
    from house_search.market.soba import STAT_BASIS_APART, merge_rates

    if not base.exists():
        raise SystemExit(f"取得結果がありません: {base.relative_to(REPO)}（--fetch を先に実行）")
    slugs = json.loads(SLUGS.read_text(encoding="utf-8"))
    by_jis = {jis: v["name"] for jis, v in slugs.items()}

    def _rates(path: Path) -> list:
        return safe_parse_soba(path.read_text(encoding="utf-8", errors="replace"))

    rows: list[dict[str, object]] = []
    missing: list[str] = []
    filled = 0
    for path in sorted(base.glob("*.html")):
        if path.stem.endswith(APART_SUFFIX):
            continue  # ⚠ 補完側は主ループで回さない（city_jis が壊れる）
        jis = path.stem
        name = by_jis.get(jis, "?")
        mansion = _rates(path)
        if not mansion:
            missing.append(name)
            continue
        apart_path = base / f"{jis}{APART_SUFFIX}.html"
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

    # ⚠ 生成できた市区が想定より少なければ報告する。0件のCSVを黙って書くと
    # 「相場が無いまま採点が続く」状態になり、例外にならない
    cities = {r["city_jis"] for r in rows}
    if missing:
        print(f"⚠ 相場表が無かった市区: {len(missing)}件 {sorted(missing)[:10]}")
    if not rows:
        raise SystemExit("相場が1件も作れませんでした（ページ構造の変更を疑う）")
    # ⚠ 相場表が無い市区は正常にありうる（掲載の少ない郡部。実測 8/1,277＝0.6%）が、
    # **ページ構造が変わると全件がこれになる**。割合で見張って区別する
    ratio = len(missing) / (len(missing) + len(cities))
    if ratio > MISSING_LIMIT:
        raise SystemExit(
            f"相場表が無いページが多すぎます（{len(missing)}/{len(missing) + len(cities)}"
            f"＝{ratio:.1%} > {MISSING_LIMIT:.0%}）。ページ構造の変更を疑う"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"生成: {OUT.relative_to(REPO)} / {len(rows):,}行 / {len(cities):,}市区 / period={period}"
    )
    # ⚠ 補完を黙って行わない。件数が急に増減したら建物種別の扱いを疑う材料になる
    print(f"  うちアパート相場で補完: {filled}行")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="SUUMO から取得し直す")
    parser.add_argument("--period", default=dt.date.today().strftime("%Y-%m"))
    parser.add_argument(
        "--keep-months", type=int, default=KEEP_MONTHS, help="raw を残す月数（当月を含む）"
    )
    args = parser.parse_args()

    base = month_dir(args.period)
    failures: list[str] = []
    if args.fetch:
        from house_search.db.session import scraping_lock

        # ⚠ 全国だと約90分かかり、その間に定期スキャンが起動する。並走すると
        # SUUMO への実効間隔が半分になるので、取得ロックで構造的に排他する。
        # ⚠ 取れなければ**非0で終わる**。月1回の処理なので、黙って飛ばすと
        # 1ヶ月ぶん古い相場のまま気づけない（scan は2時間後に再実行されるので 0 でよい）
        with scraping_lock() as acquired:
            if not acquired:
                raise SystemExit("他の取得処理が実行中のため中止しました（相場の更新は未実施）")
            ua = _user_agent()
            slugs, failures = fetch_index(ua, base)
            fetch_cities(slugs, ua, base)
            fetch_apartments(slugs, ua, base)
        prune_old_months(args.period, args.keep_months)

    build(base, args.period, dt.date.today().isoformat())
    if failures:
        raise SystemExit(f"索引を取れなかった都道府県: {sorted(failures)} — スラグの綴りを疑う")


if __name__ == "__main__":
    main()
