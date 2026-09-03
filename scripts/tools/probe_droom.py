"""D-room（大和リビング）の取得仕様を実測する調査ツール（→ 課題#37・Phase 5H）。

課題#37 の実測チェックリストを、実装の前に測るためのもの。
結果は詳細設計書 §12 に書いてある。

⚠ **ホストは ``www.droom-daiwaliving.net``。** ``www.d-room.jp`` は
さくらインターネットの共有サーバで大和リビングのサイトではない（→ §12.1）。

⚠ **取得した応答は必ず保存する**（``data/probe/droom/``）。解析を直したくなったときに
取得をやり直さずに済む。実際、住戸単位の要素を ``room-list__card``（＝棟）と
取り違えて母集団が 334 → 98 に化けたが、保存があったので取得0本で直せた。

⚠ **フィルタが効いたかは「返る住戸の中身」で確かめる**（→ ADR 0015）。
D-room は総件数を出すが、件数だけでは不等号の向きまでは分からない。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/probe_droom.py --stage robots
    uv run python scripts/tools/probe_droom.py --stage list --pref tokyo --city 13121
    uv run python scripts/tools/probe_droom.py --stage list --pref saitama --band2
    uv run python scripts/tools/probe_droom.py --stage form --file <保存済みHTML>
    uv run python scripts/tools/probe_droom.py --stage dist --file <保存済みHTML>
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

BASE = "https://www.droom-daiwaliving.net"
CACHE = Path("data/probe/droom")

# MUST の間取り（configs/*.yaml の must.layouts）と D-room の rl[] コードの対応。
# ⚠ 一覧ページの検索フォームから採った実測値。推測で足さない。
LAYOUT_CODES = {"1LDK": "6", "2K": "7", "2DK": "8", "3K": "10", "3DK": "11", "3LDK": "12"}
MUST_LAYOUTS = {"1LDK", "2K", "2DK", "2LDK", "3DK", "3LDK"}

# 本番想定のリクエスト間隔（m_sites.min_interval_sec の既定と同じ）。
MIN_INTERVAL_SEC = 2.5
# 帯1（東京23区）の JIS5桁。連続取得の耐性を測るときに市区を変えながら叩く。
# ⚠ HOMES・ATHOME・NIFTY はいずれも1リクエスト目が正常に返るので、
#   単発の疎通確認では制限を再現できない（→ 課題#36）。
BAND1_CITY_JIS = [f"131{n:02d}" for n in range(1, 24)]

_LAYOUT_AREA = re.compile(r"([0-9A-Za-z]+)\(([\d.]+)m²\)")
_RENT = re.compile(r"([\d.]+)万円")
_MGMT = re.compile(r"([\d,]+)円")
_WALK = re.compile(r"徒歩(\d+)分")
_SPACES = re.compile(r"\s+")


def fetch_response(url: str, name: str) -> httpx.Response:
    """1本だけ取得して保存し、応答そのものを返す。

    ⚠ 連続で叩くときは呼び出し側で間隔を空けること。
    ⚠ 掲載終了の判定には最終URL（リダイレクト先）が要るので応答を返す。
    """
    ua = Settings().user_agent
    res = httpx.get(url, headers={"User-Agent": ua}, timeout=60, follow_redirects=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / name).write_bytes(res.content)
    print(f"{url}\n  -> HTTP {res.status_code} / {len(res.content):,} bytes -> {CACHE / name}")
    return res


def fetch(url: str, name: str) -> str:
    return fetch_response(url, name).text


def room_ids(html: str) -> list[str]:
    """一覧に載っている住戸のIDを拾う（ページ送りの重なりを見るため）。"""
    root = lxml.html.fromstring(html)
    return [e.get("value") or "" for e in root.cssselect('input[name="room_id[]"]')]


def detail_urls(html: str) -> list[str]:
    """一覧から住戸の詳細URLを拾う。"""
    root = lxml.html.fromstring(html)
    seen: list[str] = []
    for a in root.cssselect(".room-list__btn--rarrow a"):
        href = a.get("href") or ""
        if href and href not in seen:
            seen.append(href)
    return seen


def parse_rooms(html: str) -> tuple[list[str], list[dict]]:
    """棟カードを住戸へ展開する。

    ⚠ **住戸の単位は ``room-list__content``**（``room-list__card`` は「棟」）。
    取り違えると棟あたり1件しか拾えず、母集団が黙って減る（→ §12.3）。
    """
    root = lxml.html.fromstring(html)
    kensu = [_SPACES.sub(" ", e.text_content()).strip() for e in root.cssselect(".result__kensu")]
    rooms: list[dict] = []
    for card in root.cssselect(".result__card"):
        info: dict[str, str] = {}
        for item in card.cssselect(".result__item"):
            label = item.cssselect(".result__label")
            if label:
                key = _SPACES.sub("", label[0].text_content())
                info[key] = _SPACES.sub(" ", item.text_content()).replace(key, "", 1).strip()
        traffic = info.get("交通", "")
        # ⚠ バス経由の「徒歩N分」はバス停からの徒歩。駅徒歩に使うと walk_minutes_max を
        # 不当に通過する（UR と同じ罠 → 課題#37）。「バス」より前だけを見る。
        walks = [int(m) for m in _WALK.findall(traffic.split("バス")[0])]
        for unit in card.cssselect(".room-list__content"):
            named: dict[str, str] = {}
            rest: list[str] = []
            for dl in unit.cssselect(".room-list__dl"):
                terms = [x.text_content().strip() for x in dl.cssselect("dt")]
                values = [_SPACES.sub("", x.text_content()) for x in dl.cssselect("dd")]
                if terms:
                    named[terms[0]] = "".join(values)
                else:
                    rest.extend(values)
            rent = _RENT.search(named.get("賃料", ""))
            layout = _LAYOUT_AREA.search("".join(rest))
            if not (rent and layout):
                continue
            mgmt = _MGMT.search(named.get("管理費", ""))
            rooms.append(
                {
                    "rent": float(rent.group(1)) * 10000,
                    "mgmt": int(mgmt.group(1).replace(",", "")) if mgmt else None,
                    "layout": layout.group(1),
                    "area": float(layout.group(2)),
                    "walk": min(walks) if walks else None,
                    "traffic": traffic,
                    "built": info.get("築年月", ""),
                    "address": _SPACES.sub("", info.get("所在地", "")),
                }
            )
    return kensu, rooms


def show_form(html: str) -> None:
    """検索フォームのキーと選択肢を出す（取得を使わずに済む採り方 → §12.4）。"""
    root = lxml.html.fromstring(html)
    print("--- select ---")
    for sel in root.cssselect("select[name]"):
        options = [(o.get("value"), o.text_content().strip()) for o in sel.cssselect("option")]
        print(f"  {sel.get('name')}: {options[:14]}")
    print("--- checkbox / radio ---")
    seen: Counter[str] = Counter()
    for el in root.cssselect("input[name]"):
        name = el.get("name") or ""
        if name.startswith("room_id"):
            continue
        parent = el.getparent()
        text = ""
        if parent is not None:
            text = " ".join(t.strip() for t in parent.itertext() if t.strip())
        if seen[name] < 20:
            print(f"  {name}={el.get('value')!r:>10}  {text[:24]}")
        seen[name] += 1


def show_dist(html: str) -> None:
    """間取り・面積・rent_total の分布と MUST 充足を出す（採用可否の最速判定 → §11.8）。"""
    kensu, rooms = parse_rooms(html)
    print("該当:", kensu)
    if not rooms:
        print("⚠ 住戸が1件も取れなかった。セレクタが変わった疑い")
        return
    total = len(rooms)
    print(
        f"住戸 {total} 件 / 管理費あり {sum(1 for r in rooms if r['mgmt'] is not None)}"
        f" / 駅徒歩あり {sum(1 for r in rooms if r['walk'] is not None)}"
    )
    print("間取り:", dict(Counter(r["layout"] for r in rooms).most_common()))
    areas = sorted(r["area"] for r in rooms)
    totals = sorted(r["rent"] + (r["mgmt"] or 0) for r in rooms)
    ge30 = sum(1 for a in areas if a >= 30)
    in_must = sum(1 for r in rooms if r["layout"] in MUST_LAYOUTS)
    print(f"面積      : 中央 {areas[len(areas) // 2]}㎡ / 30㎡以上 {ge30} 件（{ge30 / total:.1%}）")
    print(f"間取り対象: {in_must} 件（{in_must / total:.1%}）")
    print(
        f"rent_total: 最小 {totals[0]:,.0f} / 中央 {totals[len(totals) // 2]:,.0f}"
        f" / 最大 {totals[-1]:,.0f}"
    )
    for name, cap in [("帯1 23区(10万円)", 100000), ("帯2 近郊(7万円)", 70000)]:
        ok = [
            r
            for r in rooms
            if r["layout"] in MUST_LAYOUTS
            and r["area"] >= 30
            and r["rent"] + (r["mgmt"] or 0) <= cap
            and r["walk"] is not None
            and r["walk"] <= 20
        ]
        print(f"  {name}: MUST 4条件 {len(ok)} 件")
        for room in ok[:6]:
            money = int(room["rent"] + (room["mgmt"] or 0))
            print(
                f"     {room['layout']} {room['area']}㎡ {money:,}円"
                f" 徒歩{room['walk']}分 {room['address'][:24]}"
            )


def band2_query() -> str:
    """帯2（rent_total 7万円以下・30㎡以上・MUST の間取り）のクエリ。

    ⚠ ``cff=Y``（共益費/管理費を含む）が要。無いと ``rcu`` は賃料だけに掛かり、
    管理費を足すと上限を超える住戸が混ざる。
    ⚠ 徒歩（``walk``）は送らない。選択肢が15分までで MUST の20分を表現できず、
    送ると ADR 0015 の不変条件（サイト側フィルタは MUST より厳しくしない）を破る。
    """
    codes = [LAYOUT_CODES[name] for name in sorted(MUST_LAYOUTS) if name in LAYOUT_CODES]
    layouts = "".join(f"&rl%5B%5D={code}" for code in codes)
    return f"?rcu=70000&cff=Y&sqml=30{layouts}&amount=100"


def probe_pager(pref: str, city: str | None) -> None:
    """ページ送りが ``page_num`` で効くかを確かめる（→ §12.6-1）。

    ⚠ **``page_num`` は 0 始まり**（``pager.js`` が ``onClick="pager(1)"`` の引数を
    そのまま hidden へ入れ、フォームは ``method="get"``）。1 始まりだと思って組むと
    **1ページ目を永久に取り逃す**（2ページ目が返るだけでエラーにならない）。
    """
    city_q = f"city%5B%5D={city}&" if city else ""
    pages: dict[int, list[str]] = {}
    for num in (0, 1):
        url = f"{BASE}/{pref}/list/?{city_q}page_num={num}"
        html = fetch(url, f"pager_{pref}_{city or 'all'}_p{num}.html")
        pages[num] = room_ids(html)
        time.sleep(MIN_INTERVAL_SEC)
    first, second = set(pages[0]), set(pages[1])
    print(f"\npage_num=0: 住戸 {len(pages[0])} 件  先頭 {pages[0][:3]}")
    print(f"page_num=1: 住戸 {len(pages[1])} 件  先頭 {pages[1][:3]}")
    print(f"重なり: {len(first & second)} 件")
    if not pages[1]:
        print("⚠ 2ページ目が空。page_num が効いていない疑い")
    elif first == second:
        print("⚠ 両ページが同一。page_num が黙って無視されている（APAMAN と同じ事故）")
    elif first & second:
        print("⚠ 一部が重なっている。0始まりの解釈が違う可能性")
    else:
        print("✓ page_num で別のページが返る（0始まりで妥当）")


def show_detail(html: str) -> None:
    """詳細ページの構造を出す（設備ブロックと宣伝文の見分け → 課題#19）。"""
    root = lxml.html.fromstring(html)
    title = root.cssselect("title")
    print("  title:", title[0].text_content().strip()[:60] if title else "(なし)")
    for sel in (".detail__label", "h2", "h3", "dt", "th"):
        texts = [_SPACES.sub(" ", e.text_content()).strip() for e in root.cssselect(sel)]
        texts = [t for t in texts if t][:18]
        if texts:
            print(f"  {sel}: {texts}")


def probe_detail(list_html: str, limit: int) -> None:
    """詳細ページを取得して構造を見る（→ §12.6-3）。"""
    urls = detail_urls(list_html)
    print(f"一覧から詳細URL {len(urls)} 件。先頭 {limit} 件を取得する")
    for i, url in enumerate(urls[:limit]):
        html = fetch(url, f"detail_{i}.html")
        show_detail(html)
        time.sleep(MIN_INTERVAL_SEC)


def probe_gone(url: str) -> None:
    """掲載終了（存在しない住戸）の判定材料を見る（→ §12.6-4）。

    ⚠ レオパレスは **404 にならなかった**（HTTP 200 で検索トップ相当）。
    404 を前提にすると ``is_sold`` が永久に発火しない。
    """
    res = fetch_response(url, "detail_gone.html")
    print(f"  最終URL: {res.url}")
    print(f"  リダイレクト: {[str(h.url) for h in res.history]}")
    root = lxml.html.fromstring(res.text)
    title = root.cssselect("title")
    print("  title:", title[0].text_content().strip()[:80] if title else "(なし)")
    if res.status_code == 404:
        print("✓ 404 を返すので is_sold の判定に使える")
    else:
        print(f"⚠ HTTP {res.status_code}。404 では判定できないので本文の文言で見分ける")


def probe_burst(pref: str, cities: list[str]) -> None:
    """本番想定の間隔で市区を変えながら連続取得し、検知・絞りの発火位置を見る（→ §12.6-2）。"""
    print(f"{len(cities)} 市区を {MIN_INTERVAL_SEC} 秒間隔で連続取得する")
    started = time.monotonic()
    for i, city in enumerate(cities, start=1):
        url = f"{BASE}/{pref}/list/?city%5B%5D={city}&amount=10"
        res = fetch_response(url, f"burst_{i:02d}_{city}.html")
        rooms = len(room_ids(res.text)) if res.text else 0
        elapsed = time.monotonic() - started
        flag = ""
        if res.status_code != 200 or not res.content:
            flag = "  ⚠ 異常（ステータスまたは空ボディ）"
        elif rooms == 0:
            flag = "  ⚠ 住戸0件（検知ページの疑い）"
        print(f"  {i:2}本目 {city} {elapsed:6.1f}秒  HTTP {res.status_code} 住戸{rooms:3}{flag}")
        if i < len(cities):
            time.sleep(MIN_INTERVAL_SEC)


def show_order(html: str) -> None:
    """``odr=1``（賃料昇順）が住戸の並びに効いているかを見る（→ §12.6-5）。"""
    _, rooms = parse_rooms(html)
    totals = [int(r["rent"] + (r["mgmt"] or 0)) for r in rooms]
    print(f"住戸 {len(totals)} 件の並び（先頭20件）: {totals[:20]}")
    if totals == sorted(totals):
        print("✓ rent_total の昇順になっている")
    else:
        rents = [int(r["rent"]) for r in rooms]
        print("⚠ rent_total では昇順でない")
        print(f"  賃料のみ: {'昇順' if rents == sorted(rents) else '昇順でない'}")
        for label, values in _card_rents(html).items():
            inversions = sum(1 for a, b in zip(values, values[1:], strict=False) if a > b)
            order = "昇順" if inversions == 0 else f"逆転 {inversions}/{len(values) - 1} 箇所"
            print(f"  棟ごとの{label}: {order}  {values[:12]}")


def _card_rents(html: str) -> dict[str, list[int]]:
    """棟ごとの「先頭住戸」と「最安住戸」の賃料。並びが棟単位かを見分けるため。"""
    root = lxml.html.fromstring(html)
    first: list[int] = []
    cheapest: list[int] = []
    for card in root.cssselect(".result__card"):
        rents: list[int] = []
        for unit in card.cssselect(".room-list__content"):
            match = _RENT.search(_SPACES.sub("", unit.text_content()))
            if match:
                rents.append(int(float(match.group(1)) * 10000))
        if rents:
            first.append(rents[0])
            cheapest.append(min(rents))
    return {"先頭賃料": first, "最安賃料": cheapest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        required=True,
        choices=["robots", "list", "form", "dist", "pager", "detail", "gone", "burst", "order"],
    )
    parser.add_argument("--pref", default="tokyo", help="都道府県スラグ（tokyo / saitama …）")
    parser.add_argument("--city", help="市区の JIS5桁。省略すると都道府県全域")
    parser.add_argument("--band2", action="store_true", help="帯2の条件で絞る")
    parser.add_argument(
        "--file", help="解析するローカルHTML（--stage form / dist / detail / order）"
    )
    parser.add_argument("--url", help="取得するURL（--stage gone）")
    parser.add_argument("--limit", type=int, default=2, help="詳細ページの取得件数")
    parser.add_argument("--count", type=int, default=20, help="連続取得の本数（--stage burst）")
    args = parser.parse_args()

    if args.stage == "robots":
        fetch(f"{BASE}/robots.txt", "robots.txt")
        return 0
    if args.stage == "pager":
        probe_pager(args.pref, args.city)
        return 0
    if args.stage == "burst":
        probe_burst(args.pref, BAND1_CITY_JIS[: args.count])
        return 0
    if args.stage == "gone":
        if not args.url:
            print("--url を指定してください", file=sys.stderr)
            return 2
        probe_gone(args.url)
        return 0
    if args.stage == "list":
        if args.band2:
            query = band2_query()
            if args.city:
                query += f"&city%5B%5D={args.city}"
            name = f"list_{args.pref}_band2.html"
        else:
            city = f"city%5B%5D={args.city}&" if args.city else ""
            query = f"?{city}odr=1&amount=100"
            name = f"list_{args.pref}_{args.city or 'all'}.html"
        show_dist(fetch(f"{BASE}/{args.pref}/list/{query}", name))
        return 0
    if not args.file:
        print("--file を指定してください", file=sys.stderr)
        return 2
    html = Path(args.file).read_text(encoding="utf-8", errors="replace")
    if args.stage == "detail":
        probe_detail(html, args.limit)
    elif args.stage == "order":
        show_order(html)
    else:
        (show_form if args.stage == "form" else show_dist)(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
