"""HOMES / ATHOME のエリア索引から市区スラグを収集する（→ 課題#36・#21）。

この2サイトの市区の検索値は**サイト固有スラグ**で JIS コードからは導出できない。
``m_city_site_values`` に行が無い市区は ``resolve_areas`` が**黙って落とす**ため、
市区ローテーション（Phase 5E）を回す前に埋めておく必要がある。

⚠ **サイトのエリア索引から部分文字列一致で市区を当ててはいけない**（→ ADR 0014）。
Phase 2 で名古屋市に北名古屋市のコードが混入した原因がそれ。本スクリプトは
① 索引に埋まっている **JIS5桁**（HOMES の ``id="city-13101-label-part-1"``）で突き合わせ、
② それが無い場合だけ ``m_cities.canonical_name`` との**完全一致**で突き合わせる。
どちらでも当たらない行は捨て、件数と中身を必ず報告する。

⚠ **取得数に上限があるサイトなので、1回の実行で叩く本数を必ず絞る。**
HOMES は5リクエスト・ATHOME は4リクエストで頭打ちになる（実測 2026-09-03 → 課題#36）。
ATHOME は課題#21（47都道府県を一気に回してボット検知が発動）を繰り返さないよう
``--interval`` の既定を 15秒にしてある。

⚠ **取得したHTMLは必ず保存する**（``--cache-dir``）。解析を直したくなったときに
再取得すると貴重な予算を使うため、保存済みHTMLから作り直せるようにしてある
（``--from-cache``）。設備の ``re-extract``・経路の ``re-segment`` と同じ考え方。

使い方::

    uv run python scripts/tools/collect_city_slugs.py --site HOMES \
        --prefectures 東京都 埼玉県 千葉県 --cache-dir tmp/slugs
    uv run python scripts/tools/collect_city_slugs.py --site ATHOME \
        --prefectures 埼玉県 千葉県 神奈川県 --cache-dir tmp/slugs
    # 取得済みHTMLから作り直す（ネットワーク不要）
    uv run python scripts/tools/collect_city_slugs.py --site HOMES \
        --prefectures 東京都 --cache-dir tmp/slugs --from-cache
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.settings import Settings  # noqa: E402
from house_search.db.session import create_db_engine  # noqa: E402
from house_search.scrape.fetch import (  # noqa: E402
    BROWSER_USER_AGENT,
    RateLimit,
    SiteFetcher,
    build_client,
)
from house_search.scrape.prefectures import PREFECTURE_ROMAJI  # noqa: E402

# サイトごとの索引URLと、収集した値の持ち方。
#   value_with_pref=True  → 'tokyo/adachi-city' のように都道府県を含めて保存する
#                           （アダプタがそのままパスへ嵌める）
#   value_with_pref=False → 市区スラグだけを保存する（NIFTY 方式）
SITES: dict[str, dict[str, object]] = {
    "HOMES": {
        "index": "https://www.homes.co.jp/chintai/{pref}/city/",
        # 既定の自己申告UAだと robots.txt で許可されたパスでも 403 になる
        "user_agent": BROWSER_USER_AGENT,
        "interval": 2.5,
        "value_with_pref": True,
    },
    "ATHOME": {
        "index": "https://www.athome.co.jp/chintai/{pref}/city/",
        "user_agent": None,
        # 課題#21 の失敗（3秒間隔・47都道府県）を繰り返さない
        "interval": 15.0,
        "value_with_pref": True,
    },
    "SUUMO": {
        # ⚠ **売買だけがこの表を引く。** 賃貸の一覧は JIS5桁（``sc=13121``）で
        # 組み立てるので不要だが、売買は robots が ``/jj/bukken/ichiran/`` を
        # **明示的に禁じており**、SEOパス（``/ms/chuko/{pref}/sc_{slug}/``）でしか
        # 一覧を取れない（→ 課題#4）。スラグは種別によらず共通で、
        # 既存の賃貸由来23行（``sc_chiyoda`` 等）と一致することを実測で確認する。
        "index": "https://suumo.jp/ms/chuko/{pref}/city/",
        "user_agent": None,
        # 取得数の上限は無い。robots の Crawl-delay は bingbot 向けで `*` には無い
        "interval": 3.0,
        # 既存23行が ``sc_chiyoda``（都道府県を含まない）なので揃える
        "value_with_pref": False,
        "parser": "suumo",
    },
}

# 索引には駅（-st）・沿線（-line）・政令市まとめ（-locate）のリンクが同じURL形で
# 混ざる。市区は '-city' で終わる（政令市の行政区も '-city'）ので、それだけを拾う。
_CITY_HREF = re.compile(r"/chintai/(?P<pref>[a-z]+)/(?P<slug>[a-z0-9_\-]+-city)/")
# ⚠ **属性のクォートは `"` と `'` が混在する。** ATHOME の市区リンクは
# ``href='/chintai/saitama/fujimino-city/list/'`` と単一引用符で、`"` 決め打ちだと
# 71市区のうち10件しか拾えなかった（**エラーにならず件数が減るだけ**）。
_ANCHOR = re.compile(
    r"""<a\s(?P<attrs>[^>]*?)href=["'](?P<href>[^"']*)["'](?P<rest>[^>]*)>(?P<label>[^<]*)</a>""",
    re.IGNORECASE,
)
# 索引に埋まっている JIS5桁。名前照合より強い証拠なので優先して使う。
#   HOMES  … リンク自身の id（``id="city-13101-label-part-1"``）
#   ATHOME … 直前のチェックボックスの name（``name="areaList[11245]"``）
_JIS_IN_ANCHOR = re.compile(r"city-(?P<jis>\d{5})-")
_JIS_IN_CHECKBOX = re.compile(r"areaList\[(?P<jis>\d{5})\]")
# 直前のチェックボックスを探す窓（文字数）。実測で ``<input>`` と ``<a>`` の
# 間隔は約300文字なので、隣の項目まで届かない範囲に抑える。
_CHECKBOX_WINDOW = 600


def parse_index(html_text: str, *, pref_slug: str) -> list[tuple[str | None, str, str]]:
    """索引HTMLから ``(JIS5桁 or None, スラグ, リンク文字列)`` を取り出す。

    同じ市区へのリンクは索引に何度も現れる。JIS付き・ラベル付きの出現を優先して
    1件に畳む。
    """
    found: dict[str, tuple[str | None, str, str]] = {}
    for match in _ANCHOR.finditer(html_text):
        href_match = _CITY_HREF.search(match.group("href"))
        if not href_match or href_match.group("pref") != pref_slug:
            continue
        slug = href_match.group("slug")
        label = " ".join(match.group("label").split())
        attrs = match.group("attrs") + match.group("rest")
        if hit := _JIS_IN_ANCHOR.search(attrs):
            jis: str | None = hit.group("jis")
        else:
            window = html_text[max(0, match.start() - _CHECKBOX_WINDOW) : match.start()]
            checkbox = list(_JIS_IN_CHECKBOX.finditer(window))
            jis = checkbox[-1].group("jis") if checkbox else None
        current = found.get(slug)
        found[slug] = (
            jis or (current[0] if current else None),
            slug,
            label or (current[2] if current else ""),
        )
    return sorted(found.values(), key=lambda row: (row[0] or "zzzzz", row[1]))


# SUUMO の市区選択ページは HOMES/ATHOME と構造が違い、**JIS とスラグが別の要素**にある。
#   リンク   … <a href="/ms/chuko/tokyo/sc_chiyoda/" id="js-linkSc101">千代田区</a>
#   実体     … <input type="checkbox" name="sc" value="13101" id="sa01_sc101" ...>
# ⚠ **リンクの id に入るのは JIS の下3桁**（横浜市鶴見区なら 101 → 14101）。
# checkbox 側は JIS5桁そのものなので、下3桁で突き合わせて5桁を得る。
# ⚠ 課題#4 は「``<option value="13101">千代田区(585)</option>``」と記録していたが、
# 実測（2026-09-06）では **checkbox** で、ラベルに件数も付かない。
_SUUMO_LINK = re.compile(
    r"""<a\s[^>]*?href=["']/ms/chuko/(?P<pref>[a-z]+)/(?P<slug>sc_[a-z0-9_]+)/["']"""
    r"""[^>]*?id=["']js-linkSc(?P<tail>\d{3})["'][^>]*>(?P<label>[^<]*)</a>""",
    re.IGNORECASE,
)
_SUUMO_CHECKBOX = re.compile(
    r"""<input[^>]*?name=["']sc["'][^>]*?value=["'](?P<jis>\d{5})["']""", re.IGNORECASE
)


def parse_index_suumo(
    html_text: str, *, pref_slug: str
) -> list[tuple[str | None, str, str]]:
    """SUUMO の市区選択ページから ``(JIS5桁 or None, スラグ, リンク文字列)`` を取り出す。

    ⚠ **同定は JIS で行う**（→ ADR 0014）。部分文字列一致は使わない。
    ⚠ 掲載が無い市区にはリンクが無いので、``m_cities`` より少なくなるのが正常。
    """
    by_tail = {jis[2:]: jis for jis in _SUUMO_CHECKBOX.findall(html_text)}
    found: dict[str, tuple[str | None, str, str]] = {}
    for match in _SUUMO_LINK.finditer(html_text):
        if match.group("pref") != pref_slug:
            continue
        slug = match.group("slug")
        label = " ".join(match.group("label").split())
        found[slug] = (by_tail.get(match.group("tail")), slug, label)
    return sorted(found.values(), key=lambda row: (row[0] or "zzzzz", row[1]))


def match_cities(
    conn: Connection,
    *,
    prefecture: str,
    rows: list[tuple[str | None, str, str]],
    value_with_pref: bool,
) -> tuple[list[tuple[str, str, str]], list[tuple[str | None, str, str]]]:
    """索引の行を ``m_cities`` と突き合わせる。

    JIS5桁があればそれを、無ければ ``canonical_name`` との**完全一致**を使う。
    ⚠ 部分文字列一致は絶対に使わない（→ ADR 0014）。当たらない行は捨てて返す。
    ⚠ **JIS とラベルの両方が引けて食い違う行も捨てる。** 索引の並びから拾った
    JIS が隣の項目のものだった場合、黙って別の市の検索値が入る（Phase 2 で
    名古屋市に北名古屋市のコードが混入したのと同じ壊れ方）。
    """
    master = conn.execute(
        text("SELECT jis_code, canonical_name FROM m_cities WHERE prefecture = :pref"),
        {"pref": prefecture},
    ).all()
    by_jis = {jis: name for jis, name in master if jis}
    known_names = {name for _, name in master}

    pref_slug = PREFECTURE_ROMAJI[prefecture]
    matched: list[tuple[str, str, str]] = []
    unmatched: list[tuple[str | None, str, str]] = []
    for jis, slug, label in rows:
        from_jis = by_jis.get(jis) if jis else None
        from_label = label if label in known_names else None
        if from_jis and from_label and from_jis != from_label:
            unmatched.append((jis, slug, f"{label}（JISは{from_jis}）"))
            continue
        name = from_jis or from_label
        if name is None:
            unmatched.append((jis, slug, label))
            continue
        value = f"{pref_slug}/{slug}" if value_with_pref else slug
        matched.append((prefecture, name, value))
    return matched, unmatched


def _cache_path(cache_dir: Path, site: str, pref: str) -> Path:
    return cache_dir / f"{site}_{pref}.html"


def fetch_index(
    site: str,
    prefectures: list[str],
    *,
    cache_dir: Path,
    interval: float,
    from_cache: bool,
    user_agent: str,
) -> dict[str, str]:
    """都道府県ごとの索引HTMLを取る（``from_cache`` なら保存済みを読むだけ）。"""
    pages: dict[str, str] = {}
    if from_cache:
        for pref in prefectures:
            pages[pref] = _cache_path(cache_dir, site, pref).read_text(encoding="utf-8")
        return pages

    index_template = str(SITES[site]["index"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = build_client(user_agent=user_agent, timeout_sec=30.0)
    fetcher = SiteFetcher(
        site_code=site, client=client, rate_limit=RateLimit(min_interval_sec=interval)
    )
    try:
        for pref in prefectures:
            url = index_template.format(pref=PREFECTURE_ROMAJI[pref])
            if not fetcher.is_allowed(url):
                raise SystemExit(f"robots.txt が禁止しています: {url}")
            response = fetcher.get(url)
            print(f"  取得 {pref}: HTTP {response.status_code} / {len(response.text):,}文字")
            _cache_path(cache_dir, site, pref).write_text(response.text, encoding="utf-8")
            pages[pref] = response.text
    finally:
        client.close()
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description="エリア索引から市区スラグを収集する")
    parser.add_argument("--site", required=True, choices=sorted(SITES))
    parser.add_argument("--prefectures", nargs="+", required=True)
    parser.add_argument("--cache-dir", default=Path("tmp/slugs"), type=Path)
    parser.add_argument("--interval", type=float, default=None, help="取得間隔（秒）")
    parser.add_argument("--from-cache", action="store_true", help="保存済みHTMLから作り直す")
    parser.add_argument("--out", type=Path, help="SQLのVALUES行を書き出すファイル")
    args = parser.parse_args()

    settings = Settings()
    spec = SITES[args.site]
    interval = args.interval if args.interval is not None else float(spec["interval"])  # type: ignore[arg-type]
    print(f"{args.site}: {len(args.prefectures)}リクエスト / 間隔{interval}秒")
    pages = fetch_index(
        args.site,
        list(args.prefectures),
        cache_dir=args.cache_dir,
        interval=interval,
        from_cache=args.from_cache,
        user_agent=str(spec["user_agent"] or settings.user_agent),
    )

    engine = create_db_engine(settings.database_url)
    lines: list[str] = []
    with engine.connect() as conn:
        for pref in args.prefectures:
            parser = (
                parse_index_suumo
                if SITES[args.site].get("parser") == "suumo"
                else parse_index
            )
            rows = parser(pages[pref], pref_slug=PREFECTURE_ROMAJI[pref])
            matched, unmatched = match_cities(
                conn,
                prefecture=pref,
                rows=rows,
                value_with_pref=bool(spec["value_with_pref"]),
            )
            print(f"{pref}: リンク{len(rows)} → 一致{len(matched)} / 不一致{len(unmatched)}")
            for jis, slug, label in unmatched:
                print(f"    捨てた: jis={jis} slug={slug} label={label}")
            for prefecture, name, value in matched:
                lines.append(f"    ('{prefecture}', '{name}', '{args.site}', '{value}'),")

    body = "\n".join(lines)
    if args.out:
        args.out.write_text(body + "\n", encoding="utf-8")
        print(f"書き出し: {args.out}（{len(lines)}行）")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
