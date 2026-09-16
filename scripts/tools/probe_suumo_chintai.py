"""SUUMO 賃貸の「網羅取得」に要る仕様を実測する調査ツール。

賃貸アダプタは v1（Go）から引き継いだ ``&pc=30&pn=N`` でページを送っているが、
実HTML（``tests/fixtures/suumo/list_page1.html``）のページ送りリンクは ``&page=N`` で、
SUUMO の売買4アダプタも ``page=N`` を使っている。**``pn`` が無視されていれば
2ページ目以降が1ページ目と同じ内容**になり、``--full`` の5ページが全部1ページ目
になる。⚠ 例外にも件数の減少にもならない（同じ ``external_id`` が返るので
upsert が吸収する）ため、実データを見るまで気づけない。

同じ実HTMLから、並び替えが ``<select name="po1">``（``09``＝新着順 /
``17``＝住所別 / ``25``＝おすすめ順）で、表示建物数 ``<select name="pc">`` の選択肢が
10/20/30/50 であることも分かっている。robots が禁じるのは ``/*?*sort=`` だけで
（→ 課題#52）、``po1`` を禁じる規則は無い。

⚠ **「効いた」の判定方法そのものの妥当性を先に担保する。** 存在しないキー
（``zzz=1``）を送って総件数と掲載IDが変わらないことを確かめてから各パラメータを測る
（→ ADR 0015・課題#29。UR で7通り全部が同一応答になったとき、対照が「効かないキー」
だけだったために「どのパラメータも効いていない」ことを見落とした）。

⚠ **取得を伴う段は取得ロック（``scraping_lock``）を取り、取れなければ1本も叩かずに
非0で終わる。** このツールは本体の ``SiteFetcher`` を通らないので、定期スキャンと
並走すると SUUMO への実効間隔が半分になる。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/probe_suumo_chintai.py --stage robots
    uv run python scripts/tools/probe_suumo_chintai.py --stage paging --city 13121
    uv run python scripts/tools/probe_suumo_chintai.py --stage analyze
    uv run python scripts/tools/probe_suumo_chintai.py --stage counts --pattern 東京23区賃貸
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
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.pattern import load_patterns  # noqa: E402
from house_search.config.settings import Settings  # noqa: E402
from house_search.config.site_params import load_from_db  # noqa: E402
from house_search.console import force_utf8_output  # noqa: E402
from house_search.db.session import scraping_lock  # noqa: E402
from house_search.pipeline.scan import site_filter_query  # noqa: E402
from house_search.scrape.area import resolve_areas  # noqa: E402
from house_search.scrape.fetch import RobotsRules  # noqa: E402
from house_search.scrape.suumo import SuumoScraper  # noqa: E402

BASE_URL = "https://suumo.jp"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
DEFAULT_CACHE = Path("data/probe/suumo_chintai")
# SUUMO の m_sites.min_interval_sec は 2.5秒。調査でもそれを下回らない
MIN_INTERVAL_SEC = 3.0

# robots で許可を確かめたいパス。⚠ 2本目は**禁止されるべき対照**で、
# ここが OK と出たら判定器が `*` を展開していない＝判定そのものが信用できない
_LIST = "/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13&sc=13121"
ROBOTS_CHECK_PATHS = (
    _LIST,
    f"{_LIST}&sort=2",
    f"{_LIST}&page=2",
    f"{_LIST}&pc=30&pn=2",
    f"{_LIST}&pc=50",
    f"{_LIST}&po1=09",
    f"{_LIST}&po1=17&page=3",
)

# ページ送り・並び順・表示件数を1回で測るための変種。
# ⚠ **1本目は素のURL（対照）、2本目は存在しないキー**の順を崩さない
PAGING_VARIANTS: tuple[tuple[str, str], ...] = (
    ("base", ""),
    ("zzz", "zzz=1"),
    ("pn2", "pc=30&pn=2"),
    ("page2", "page=2"),
    ("page3", "page=3"),
    ("pc50", "pc=50"),
    ("po1_shinchaku", "po1=09"),
    ("po1_jusho", "po1=17"),
)


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
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  {label}: HTTP {response.status_code} / {len(response.content):,} bytes")
    return response


def _hit_count(doc) -> int | None:
    """総件数（``div.paginate_set-hit``）。見出しの本文全体から拾わない。"""
    for node in doc.cssselect("div.paginate_set-hit"):
        match = re.search(r"([0-9,]+)", node.text_content())
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _room_ids(doc) -> list[str]:
    """住戸の物件ID（一覧のチェックボックス value）。順序を保って返す。"""
    ids: list[str] = []
    for node in doc.cssselect("div.cassetteitem input[name='bc']"):
        value = (node.get("value") or "").strip()
        if value:
            ids.append(value)
    return ids


def _summarize(doc, raw: str) -> dict[str, object]:
    title = doc.cssselect("title")
    ids = _room_ids(doc)
    return {
        "総件数": _hit_count(doc),
        "建物": len(doc.cssselect("div.cassetteitem")),
        "住戸": len(ids),
        "ids": ids,
        "title": title[0].text_content().strip()[:40] if title else "",
        "エラーページ": bool(title and "エラー" in title[0].text_content()),
        "bytes": len(raw),
    }


def _base_url(pattern_name: str, jis_code: str, configs_dir: Path) -> str:
    """本番の ``scan`` と同じ一覧URL（1ページ目・サイト側フィルタ込み）を作る。

    ⚠ **URLを手で組み立てない。** 本番と1文字でも違うと、測った結果が
    本番の取得に当てはまらない。
    """
    patterns = [p for p in load_patterns(configs_dir) if p.name == pattern_name]
    if not patterns:
        raise SystemExit(f"検索パターンが見つかりません: {pattern_name}")
    pattern = patterns[0]
    scraper = SuumoScraper()

    settings = Settings()
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        areas = resolve_areas(
            conn,
            site_code=scraper.site_code,
            prefectures=pattern.search.prefectures,
            cities=pattern.search.cities,
            requires_city=scraper.requires_city,
            city_value_source=scraper.city_value_source,
        )
    targets = [a for a in areas if a.jis_code == jis_code] if jis_code else areas
    if not targets:
        raise SystemExit(f"市区が対象に含まれていません: {jis_code}")

    url = scraper.list_urls(pattern, targets[:1])[0]
    query = site_filter_query(scraper, pattern, load_from_db(engine))
    if query:
        suffix = "&".join(f"{key}={value}" for key, values in query.items() for value in values)
        url = f"{url}{'&' if '?' in url else '?'}{suffix}"
    return url


def stage_robots(client: httpx.Client | None, cache_dir: Path, *, reuse: bool) -> None:
    """robots.txt を取得し、ページ送り・並び順のパスが許可されているかを見る。"""
    if reuse:
        text = (cache_dir / "robots.html").read_text(encoding="utf-8")
    else:
        text = _fetch(client, ROBOTS_URL, cache_dir, "robots").text
    # ⚠ 本体と同じ RobotsRules で判定する（標準の RobotFileParser は
    # `*` を展開せず `/*?*sort=` を黙って許可にする → 課題#52）
    rules = RobotsRules.parse(text)
    ua = client.headers.get("User-Agent", "*") if client else Settings().user_agent
    print(f"\n  User-agent: {ua}")
    for path in ROBOTS_CHECK_PATHS:
        allowed = rules.can_fetch(ua, urljoin(BASE_URL, path))
        print(f"    {'OK ' if allowed else 'NG '} {path}")


def stage_paging(client: httpx.Client, cache_dir: Path, base: str) -> None:
    """1市区へ変種を順に投げる（ページ送り・並び順・表示件数を1回で測る）。"""
    print(f"  ベースURL: {base}")
    for index, (label, param) in enumerate(PAGING_VARIANTS):
        url = base if not param else f"{base}{'&' if '?' in base else '?'}{param}"
        if index:
            _sleep(MIN_INTERVAL_SEC)
        _fetch(client, url, cache_dir, label)


def stage_analyze(cache_dir: Path) -> None:
    """保存済みHTMLを突き合わせる（ネットワーク不要）。

    判定基準:
    * ``zzz`` が base と同じ → 「同じ＝効いていない」と言ってよい対照が取れている
    * ``pn2`` の住戸IDが base と**完全一致**なら ``pn`` は効いていない
    * ``page2`` の住戸IDが base と重ならなければ ``page`` が効いている
    """
    results: dict[str, dict[str, object]] = {}
    for label, _ in PAGING_VARIANTS:
        path = cache_dir / f"{label}.html"
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        results[label] = _summarize(lxml_html.fromstring(raw), raw)

    if "base" not in results:
        raise SystemExit("base の保存がありません（--stage paging を先に流してください）")
    base_ids = list(results["base"]["ids"])  # type: ignore[arg-type]
    print(f"\n  {'ラベル':<16}{'総件数':>10}{'建物':>6}{'住戸':>6}{'baseと同じ':>12}  先頭ID")
    for label, info in results.items():
        ids = list(info["ids"])  # type: ignore[arg-type]
        same = "—" if label == "base" else f"{len(set(ids) & set(base_ids))}/{len(ids)}"
        head = ids[0] if ids else "(なし)"
        mark = " ⚠エラーページ" if info["エラーページ"] else ""
        print(
            f"  {label:<16}{str(info['総件数']):>10}{info['建物']:>6}"
            f"{info['住戸']:>6}{same:>12}  {head}{mark}"
        )

    print("\n  === 判定 ===")
    if "zzz" in results:
        same = set(results["zzz"]["ids"]) == set(base_ids)  # type: ignore[arg-type]
        print(f"  対照(zzz=1) が base と同一: {'はい（判定方法は妥当）' if same else 'いいえ ⚠'}")
    for label, key in (("pn2", "pn"), ("page2", "page"), ("page3", "page(3)")):
        if label not in results:
            continue
        ids = set(results[label]["ids"])  # type: ignore[arg-type]
        overlap = len(ids & set(base_ids))
        verdict = "効いていない（1ページ目と同じ）" if overlap == len(ids) else "効いている"
        print(f"  {key:<10} 重なり {overlap}/{len(ids)} → {verdict}")
    for label in ("pc50", "po1_shinchaku", "po1_jusho"):
        if label not in results:
            continue
        info = results[label]
        ids = set(info["ids"])  # type: ignore[arg-type]
        print(
            f"  {label:<14} 建物 {info['建物']}件 / 総件数 {info['総件数']} / "
            f"base との重なり {len(ids & set(base_ids))}/{len(ids)}"
        )


def stage_counts(
    client: httpx.Client, cache_dir: Path, pattern_name: str, configs_dir: Path
) -> None:
    """パターンの全市区について1ページ目を取り、総件数から網羅の規模を見積もる。"""
    patterns = [p for p in load_patterns(configs_dir) if p.name == pattern_name]
    if not patterns:
        raise SystemExit(f"検索パターンが見つかりません: {pattern_name}")
    pattern = patterns[0]
    scraper = SuumoScraper()
    settings = Settings()
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        areas = resolve_areas(
            conn,
            site_code=scraper.site_code,
            prefectures=pattern.search.prefectures,
            cities=pattern.search.cities,
            requires_city=scraper.requires_city,
            city_value_source=scraper.city_value_source,
        )
    urls = scraper.list_urls(pattern, areas)
    query = site_filter_query(scraper, pattern, load_from_db(engine))
    suffix = "&".join(f"{key}={value}" for key, values in query.items() for value in values)
    if suffix:
        urls = [f"{u}{'&' if '?' in u else '?'}{suffix}" for u in urls]

    total = 0
    rows: list[tuple[str, int | None, int]] = []
    for index, (area, url) in enumerate(zip(areas, urls, strict=True)):
        if index:
            _sleep(MIN_INTERVAL_SEC)
        label = f"count_{area.jis_code or area.prefecture}"
        response = _fetch(client, url, cache_dir, label)
        doc = lxml_html.fromstring(response.text)
        hits = _hit_count(doc)
        buildings = len(doc.cssselect("div.cassetteitem"))
        rows.append((area.city_name or area.prefecture, hits, buildings))
        total += hits or 0

    print(f"\n  {'市区':<14}{'該当件数':>10}{'1ページ目の建物':>16}")
    for name, hits, buildings in rows:
        print(f"  {name:<14}{str(hits):>10}{buildings:>16}")
    print(f"\n  合計 {total:,} 件")
    for page_size in (30, 50):
        pages = sum(-(-(h or 0) // page_size) for _, h, _ in rows)
        minutes = pages * MIN_INTERVAL_SEC / 60
        print(f"  1ページ{page_size}建物なら {pages:,} ページ ≒ {minutes:,.0f} 分（3.0秒間隔）")


def main() -> int:
    # ⚠ 先に UTF-8 へ付け替える。cp932 のままだと ⚠ を含む報告の print が
    # UnicodeEncodeError になり、調べた結果ごと失われる（→ 課題#49）
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=["robots", "paging", "analyze", "counts"])
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--configs-dir", type=Path, default=Path("configs"))
    parser.add_argument("--city", default="13121", help="paging: 測る市区のJIS5桁（既定は足立区）")
    parser.add_argument("--pattern", default="東京23区賃貸")
    parser.add_argument("--reuse", action="store_true", help="保存済みの応答を使う")
    args = parser.parse_args()

    if args.stage == "analyze":
        stage_analyze(args.cache_dir)
        return 0
    if args.stage == "robots" and args.reuse:
        stage_robots(None, args.cache_dir, reuse=True)
        return 0

    # ⚠ ここから先は SUUMO を叩く。定期スキャンと並走させないため取得ロックを取り、
    # 取れなければ1本も叩かずに非0で終わる
    with scraping_lock() as acquired:
        if not acquired:
            print(
                "他の取得処理（scan・掃き出し等）が実行中のため中止しました。"
                "1本も取得していません。終わってから流し直してください",
                file=sys.stderr,
            )
            return 1
        settings = Settings()
        with httpx.Client(
            timeout=settings.request_timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        ) as client:
            if args.stage == "robots":
                stage_robots(client, args.cache_dir, reuse=False)
            elif args.stage == "paging":
                stage_paging(
                    client, args.cache_dir, _base_url(args.pattern, args.city, args.configs_dir)
                )
            else:
                stage_counts(client, args.cache_dir, args.pattern, args.configs_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
