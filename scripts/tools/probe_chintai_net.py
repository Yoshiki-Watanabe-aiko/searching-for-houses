"""CHINTAI.net の取得仕様を実測する（→ 課題#37・#43・Phase 5H）。

課題#37 の実測チェックリストのうち、``probe_portals.py`` で測れなかったものを扱う。
結果は詳細設計書 §14 に書く。

⚠ **前セッションのキャッシュにあった ``*_adachi_base/filtered`` は「家賃相場」ページ**
（``cassette_item`` が0件）だった。つまり §14.1 の「絞り込み」欄はキー名を挙げただけで、
**効くことを確かめていない**。ここで測り直す。

⚠ **対照を先に置く。** 存在しないキー ``zzz=1`` を送って結果が変わらないことを
確かめてから各軸を測る。これが無いと「効いていないのに効いたつもり」になる
（→ ADR 0015・課題#29）。

⚠ **取得した応答は必ず保存する**（``data/probe/chintai_net/``）。解析を直したくなったときに
取得をやり直さずに済む（ATHOME の市区リンク・D-room の住戸単位で実際に効いた）。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）::

    uv run python scripts/tools/probe_chintai_net.py --stage filters
    uv run python scripts/tools/probe_chintai_net.py --stage paging
    uv run python scripts/tools/probe_chintai_net.py --stage endurance
    uv run python scripts/tools/probe_chintai_net.py --stage detail
    uv run python scripts/tools/probe_chintai_net.py --stage sold
    uv run python scripts/tools/probe_chintai_net.py --stage overlap
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import lxml.html

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.settings import Settings  # noqa: E402

BASE = "https://www.chintai.net"
CACHE = Path("data/probe/chintai_net")
MIN_INTERVAL_SEC = 2.5

# 足立区（13121）を基準にする。他サイトの実測と揃えて比較できるため。
ADACHI = "/tokyo/area/13121/list/"

_last_at = 0.0


def fetch(path: str, name: str, *, force: bool = False) -> str:
    """1ページ取る。⚠ 間隔を守り、必ず保存する。"""
    global _last_at
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{name}.html"
    if dest.exists() and not force:
        print(f"  [cache] {name}")
        return dest.read_text(encoding="utf-8")
    wait = MIN_INTERVAL_SEC - (time.monotonic() - _last_at)
    if _last_at and wait > 0:
        time.sleep(wait)
    ua = Settings().user_agent
    response = httpx.get(
        BASE + path, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=True
    )
    _last_at = time.monotonic()
    dest.write_text(response.text, encoding="utf-8", newline="")
    print(f"  HTTP {response.status_code} {len(response.text):>7}B  {name}  {path}")
    return response.text


def parse_rooms(html: str) -> list[dict]:
    """一覧から住戸を取り出す。

    ⚠ **``.cassette_item`` は「棟」で、住戸は棟内の hidden input の組**
    （D-room §12.3・ハウスコム §13.8 と同じ二層構造）。棟だけ数えると
    23件になるが実際の住戸は51件で、**エラーにならず母集団が半分以下に化ける**。
    """
    doc = lxml.html.fromstring(html)
    rooms: list[dict] = []
    for item in doc.cssselect(".cassette_item"):
        is_pr = bool(item.cssselect(".cassette_detail_pr"))
        names = item.cssselect("input.bkName")
        rents = item.cssselect("input.chinRyo")
        layouts = item.cssselect("input.madori")
        areas = item.cssselect("input.senMenseki")
        stations = item.cssselect("input.ekiName")
        walks = item.cssselect("input.ekiToho")
        for idx in range(len(names)):

            def val(seq, i=idx):
                return seq[i].get("value") if i < len(seq) else None

            rent = val(rents)
            area = val(areas)
            walk = val(walks)
            rooms.append(
                {
                    "name": val(names),
                    "rent": int(rent) if rent and rent.isdigit() else None,
                    "layout": val(layouts),
                    "area": float(area) if area else None,
                    "station": val(stations),
                    "walk": int(walk) if walk and walk.isdigit() else None,
                    "pr": is_pr,
                }
            )
    return rooms


def summarize(label: str, html: str) -> list[dict]:
    doc = lxml.html.fromstring(html)
    title = (doc.findtext(".//title") or "").strip()
    rooms = parse_rooms(html)
    areas = [r["area"] for r in rooms if r["area"]]
    rents = [r["rent"] for r in rooms if r["rent"]]
    walks = [r["walk"] for r in rooms if r["walk"] is not None]
    layouts = Counter(r["layout"] for r in rooms)
    buildings = len(doc.cssselect(".cassette_item"))
    pr = sum(1 for r in rooms if r["pr"])
    print(f"--- {label} ---")
    print(f"  title  : {title[:60]}")
    print(f"  棟 {buildings} / 住戸 {len(rooms)}（うちPR {pr}）")
    if areas:
        under30 = sum(1 for a in areas if a < 30)
        print(f"  面積   : {min(areas):.1f}〜{max(areas):.1f}㎡（30㎡未満 {under30}件）")
    if rents:
        print(f"  賃料   : {min(rents):,}〜{max(rents):,}円")
    if walks:
        over20 = sum(1 for w in walks if w > 20)
        print(f"  徒歩   : {min(walks)}〜{max(walks)}分（20分超 {over20}件）")
    print(f"  間取り : {dict(layouts.most_common(8))}")
    return rooms


def total_count(html: str) -> int | None:
    """一覧の総件数。

    ⚠⚠ **一覧に返る住戸は実行のたびに入れ替わる**（おすすめ順が既定で、
    同じURLでも中身が違う）。そのため GOO・APAMAN・ATHOME で使った
    「返る掲載の中身で確かめる」（→ §13.9）が**このサイトでは使えない**。
    総件数だけが安定した判定材料になる。
    """
    text = lxml.html.fromstring(html).text_content()
    hits = re.findall(r"([0-9][0-9,]{2,})\s*件", text)
    return int(hits[0].replace(",", "")) if hits else None


def stage_filters() -> None:
    """⚠ 対照 → 基準 → 各軸の順に測る。判定は総件数で行う。"""
    print("## サイト側フィルタ（→ ADR 0015）\n")
    # ⚠ キー名と選択肢は**一覧ページの検索フォームから採った**（取得を1本も使わない）。
    # §14.1 の初版は ct=130000・j=20 と書いていたが、どちらも選択肢の形式が違い
    # **黙って無視されていた**（ct は万円・j はコード）。
    axes = [
        ("base", "", "基準（絞り込みなし）"),
        ("zzz", "?zzz=1", "対照 zzz=1（効かないはずのキー）"),
        ("sf", "?sf=30", "面積下限 sf=30（㎡）"),
        ("ct2", "?ct=130", "賃料上限 ct=130（万円・選択肢にある値）"),
        ("j2", "?j=7", "駅徒歩 j=7（=20分以内のコード）"),
        ("m2", "?m=3&m=4&m=5&m=6&m=8&m=9", "間取り 6種（⚠ 3DK=m8 を含む）"),
        ("all", "?sf=30&ct=130&j=7&m=3&m=4&m=5&m=6&m=8&m=9", "4軸すべて"),
    ]
    for key, path, label in axes:
        html = fetch(ADACHI + path, f"f_{key}")
        rooms = summarize(label, html)
        print(f"  総件数 : {total_count(html)}")
        del rooms


def stage_paging() -> None:
    """ページ送りと並び順。⚠ 分布は並び順とセットで測る（→ §13.4）。"""
    print("## ページ送り・並び順\n")
    summarize("2ページ目（パス形式）", fetch(ADACHI + "page2/", "p_page2"))
    # ⚠ **既定（おすすめ順）は実行のたびに中身が入れ替わる。** ページ送りで
    # 網羅するには決定的な並び順が要る。o の選択肢は 10=おすすめ / 2=安い順 /
    # 3=高い順 / 7=新着順 / 4=面積が広い順 / 6=築年月が新しい順 / 8 / 9。
    orders = [
        ("o1", "?o=1", "並び順 o=1（選択肢に無い値）"),
        ("o2", "?o=2", "並び順 o=2（家賃が安い順）"),
        ("o7", "?o=7", "並び順 o=7（新着順）"),
        ("o7p2", "?o=7&x=1", "新着順・同じURLを2回目（揺れるか）"),
    ]
    for key, path, label in orders:
        summarize(label, fetch(ADACHI + path, f"p_{key}"))


def stage_endurance() -> None:
    """⚠ 本番想定間隔で20市区。1リクエスト目が通っても上限があるサイトがある。"""
    print("## 連続取得の耐性（2.5秒間隔・20市区）\n")
    jis = [
        13101, 13102, 13103, 13104, 13105, 13106, 13107, 13108, 13109, 13110,
        13111, 13112, 13113, 13114, 13115, 13116, 13117, 13118, 13119, 13120,
    ]
    ok = 0
    for i, code in enumerate(jis, 1):
        html = fetch(f"/tokyo/area/{code}/list/", f"e_{code}")
        rooms = parse_rooms(html)
        doc = lxml.html.fromstring(html)
        title = (doc.findtext(".//title") or "").strip()
        status = "OK" if rooms else "⚠ 住戸0件"
        if rooms:
            ok += 1
        print(f"  {i:>2}/20 {code} 住戸{len(rooms):>3}件 {status}  {title[:34]}")
    print(f"\n  正常 {ok}/20")


def stage_detail() -> None:
    """詳細ページ。⚠ 設備でないもの（諸費用・他物件）が同居していないか見る。"""
    print("## 詳細ページ\n")
    doc = lxml.html.fromstring(fetch(ADACHI, "f_base"))
    hrefs: list[str] = []
    for anchor in doc.cssselect("a"):
        href = anchor.get("href") or ""
        if re.search(r"/detail/|/room", href) and href not in hrefs:
            hrefs.append(href)
    print(f"  詳細候補リンク {len(hrefs)}件（先頭5件）")
    for href in hrefs[:5]:
        print(f"    {href}")
    for i, href in enumerate(hrefs[:2]):
        path = href if href.startswith("/") else "/" + href.split("chintai.net/", 1)[-1]
        ddoc = lxml.html.fromstring(fetch(path, f"d_{i}"))
        title = (ddoc.findtext(".//title") or "").strip()
        heads = [h.text_content().strip()[:24] for h in ddoc.cssselect("h2, h3, th, dt")]
        print(f"  --- 詳細{i}: {title[:56]} ---")
        print(f"    見出し {len(heads)}個: {heads[:20]}")


def stage_sold() -> None:
    """⚠ 掲載終了が404か文言か。D-room は404にならなかった（→ §12.9）。"""
    print("## 掲載終了の判定\n")
    ua = Settings().user_agent
    for name, path in [("存在しないID", "/detail/000000000000000000000000/")]:
        try:
            response = httpx.get(
                BASE + path, headers={"User-Agent": ua}, timeout=30.0, follow_redirects=False
            )
        except httpx.HTTPError as exc:
            print(f"  {name}: {type(exc).__name__}")
            continue
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / f"s_{name}.html").write_text(response.text, encoding="utf-8", newline="")
        title = ""
        if response.text.strip():
            title = (lxml.html.fromstring(response.text).findtext(".//title") or "").strip()
        print(f"  {name}: HTTP {response.status_code} {len(response.text)}B")
        print(f"    title={title[:60]}")
        if response.headers.get("location"):
            print(f"    location={response.headers['location']}")


def stage_overlap() -> None:
    """既存DBとの重複を一次見積もりする。

    ⚠ **この見積もりは桁を外さない用途に留める。** Phase 5H でハウスコムは
    22%→実測19%（近い）だがホームメイトは12%→実測51%と外れた（→ §13.5・§13.10）。
    """
    from sqlalchemy import text

    from house_search.db.session import session_scope

    # ⚠ **既存DBには MUST 1段目を通った掲載しかない。** 条件外の住戸まで対象にすると
    # 必ず「重複なし」と出るので**構造的に過小評価する**（→ §13.5）。
    # 4軸フィルタ済みの応答（＝MUST に近い母集団）で測る。
    html = fetch(ADACHI + "?sf=30&ct=130&j=7&m=3&m=4&m=5&m=6&m=8&m=9", "f_all")
    rooms = [r for r in parse_rooms(html) if r["area"] and r["rent"]]
    print(f"  4軸フィルタ済みの住戸 {len(rooms)}件（面積・賃料あり）")
    hit = 0
    with session_scope() as session:
        for room in rooms:
            found = session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM t_listings l
                    JOIN m_cities c ON c.id = l.city_id
                    WHERE c.jis_code = '13121'
                      AND ABS(l.area_sqm - :area) < 0.06
                      AND l.price = :rent
                    """
                ),
                {"area": room["area"], "rent": room["rent"]},
            ).scalar_one()
            if found:
                hit += 1
    ratio = hit / max(len(rooms), 1)
    print(f"  既存DBに面積+賃料が一致 {hit}/{len(rooms)}件 = {ratio:.0%}")
    print("  ⚠ 上限の見積もり。実際のユニーク率はこれより高い（→ §13.5）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        required=True,
        choices=["filters", "paging", "endurance", "detail", "sold", "overlap"],
    )
    args = parser.parse_args()
    stages = {
        "filters": stage_filters,
        "paging": stage_paging,
        "endurance": stage_endurance,
        "detail": stage_detail,
        "sold": stage_sold,
        "overlap": stage_overlap,
    }
    stages[args.stage]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
