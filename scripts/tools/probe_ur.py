"""UR賃貸住宅の取得仕様を実測する調査ツール（→ 課題#37・Phase 5F）。

実装の前に確かめておくことが7つある（課題#37 の実測チェックリスト）。
どれも**推測で書くと例外にならないまま静かに壊れる**たぐいのものなので、
実サイトで測ってから詳細設計書へ書く。

⚠ **取得した応答は必ず保存する**（``--cache-dir``）。解析を直したくなったときに
取得をやり直さずに済む（``--from-cache``）。ATHOME の市区リンクが単一引用符で
71市区中10市区しか拾えなかったとき、これがあったので取得予算を1回も使わずに直せた
（→ 課題#36）。設備の ``re-extract``・経路の ``re-segment`` と同じ考え方。

⚠ **「効いた」の判定方法そのものの妥当性を先に担保する。** 存在しないキー
（``zzz=1``）を送って結果が変わらないことを確かめてから各パラメータを測る。
これが無いと「絞れていないのに絞れたつもり」になる（→ 課題#29）。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/probe_ur.py --stage robots
    uv run python scripts/tools/probe_ur.py --stage search
    uv run python scripts/tools/probe_ur.py --stage rooms
    uv run python scripts/tools/probe_ur.py --stage areas
    uv run python scripts/tools/probe_ur.py --stage search --from-cache
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.settings import Settings  # noqa: E402

SEARCH_API = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken/search/list_bukken/"
ROOM_API = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken/detail/detail_bukken_room/"
LIST_HTML = "https://www.ur-net.go.jp/chintai/kanto/tokyo/list/"

# 検索APIの共通パラメータ。実測値は詳細設計書 §9.3 にある。
BASE_SEARCH: dict[str, str] = {
    "rent_low": "",
    "rent_high": "",
    "floorspace_low": "",
    "floorspace_high": "",
    "tdfk": "13",  # 東京都
    "area": "01",
    "block": "",
    "danchi": "",
    "shisya": "",
    "pageIndex": "0",
    "orderByField": "0",
    "orderBySort": "0",
}


def _interval_sleep(seconds: float) -> None:
    """本番と同じ ±30% のジッタを掛けて待つ（``SiteFetcher`` と同じ考え方）。"""

    time.sleep(seconds * random.uniform(0.7, 1.3))


def _cache_path(cache_dir: Path, label: str, suffix: str) -> Path:
    return cache_dir / f"{label}{suffix}"


def _post(
    client: httpx.Client,
    url: str,
    payload: dict[str, str],
    *,
    label: str,
    cache_dir: Path,
    interval: float,
) -> str:
    """POST して本文を保存する。⚠ 保存を省かない。"""

    _interval_sleep(interval)
    response = client.post(url, data=payload)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(cache_dir, label, ".json").write_text(response.text, encoding="utf-8")
    _cache_path(cache_dir, label, ".meta.json").write_text(
        json.dumps(
            {
                "url": url,
                "status": response.status_code,
                "bytes": len(response.content),
                "payload": payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  [{label}] HTTP {response.status_code} / {len(response.content)} bytes")
    return response.text


def _get(
    client: httpx.Client,
    url: str,
    *,
    label: str,
    cache_dir: Path,
    interval: float,
    suffix: str = ".html",
) -> httpx.Response:
    _interval_sleep(interval)
    response = client.get(url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(cache_dir, label, suffix).write_text(response.text, encoding="utf-8")
    print(f"  [{label}] HTTP {response.status_code} / {len(response.content)} bytes")
    return response


def _load(cache_dir: Path, label: str, suffix: str = ".json") -> str | None:
    path = _cache_path(cache_dir, label, suffix)
    return path.read_text(encoding="utf-8") if path.exists() else None


def _parse(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# stage: robots — APIホストの robots.txt が 403 を返すことの裏取り（→ ADR 新設）
# --------------------------------------------------------------------------
def stage_robots(client: httpx.Client, cache_dir: Path, interval: float) -> None:
    for label, url in (
        ("robots_www", "https://www.ur-net.go.jp/robots.txt"),
        ("robots_api", "https://chintai.r6.ur-net.go.jp/robots.txt"),
    ):
        response = _get(
            client, url, label=label, cache_dir=cache_dir, interval=interval, suffix=".txt"
        )
        head = response.text[:200].replace("\n", " / ")
        print(f"      本文の先頭: {head}")


# --------------------------------------------------------------------------
# stage: search — 検索APIのページ送り・vacancy・rent_high を測る
# --------------------------------------------------------------------------
SEARCH_PROBES: list[tuple[str, dict[str, str]]] = [
    # ① 基準
    ("search_base", {}),
    # ② 対照その1。存在しないキーが黙って無視されること（→ 課題#29）
    ("search_zzz", {"zzz": "1"}),
    # ⚠ ②だけでは判定方法を担保できない。**効くと分かっているキー**を動かして
    #    「差が出る経路が生きている」ことを同時に示す必要がある。実際、初回は
    #    pageIndex・vacancy・rent_high が揃って無視されていたのに zzz の対照が
    #    「同一＝妥当」と読めてしまい、危うく誤診するところだった。
    ("search_control_tdfk", {"tdfk": "11"}),
    # ③ vacancy。件数が変われば「空室のある団地に絞る」の裏取りになる
    ("search_vacancy", {"vacancy": "1"}),
    # ④ ページ送り。area=06 は117件を1応答で返したので「20件/ページ」は誤り
    ("search_page1", {"pageIndex": "1"}),
    ("search_page99", {"pageIndex": "99"}),
    # ⑤ rent_high の値域。端数で0件にならないか（SUUMO の ct と同じ事故の確認）
    ("search_rent_odd", {"rent_high": "99999"}),
    ("search_rent_round", {"rent_high": "100000"}),
    # ⑥ 面積下限。MUST をサイト側へ渡せるか（→ ADR 0015）
    ("search_area_min", {"floorspace_low": "30"}),
]


def stage_search(
    client: httpx.Client | None, cache_dir: Path, interval: float, *, from_cache: bool
) -> None:
    for label, extra in SEARCH_PROBES:
        if not from_cache:
            assert client is not None
            payload = dict(BASE_SEARCH)
            payload.update(extra)
            _post(
                client,
                SEARCH_API,
                payload,
                label=label,
                cache_dir=cache_dir,
                interval=interval,
            )
    _report_search(cache_dir)


def _rent_low_yen(row: dict[str, Any]) -> int | None:
    """``"84,900円～199,100円"`` の下限を円で返す。"""

    text = str(row.get("rent") or "")
    head = text.split("～")[0].replace(",", "").replace("円", "").strip()
    return int(head) if head.isdigit() else None


def _summarize_danchi(data: Any) -> dict[str, Any]:
    if not isinstance(data, list):
        return {"件数": 0, "型": type(data).__name__}
    rows = [row for row in data if isinstance(row, dict)]
    vacancies = [int(row.get("roomCount") or 0) for row in rows]
    rents = [yen for yen in (_rent_low_yen(row) for row in rows) if yen is not None]
    return {
        "件数": len(data),
        "空室のある団地": sum(1 for count in vacancies if count > 0),
        "空室数の合計": sum(vacancies),
        "賃料下限の範囲": (f"{min(rents):,}〜{max(rents):,}円" if rents else "—"),
        "先頭のid": (rows[0].get("id") if rows else None),
        "市区(skcs)": sorted({str(row.get("skcs")) for row in rows if row.get("skcs")}),
    }


def _report_search(cache_dir: Path) -> None:
    print("\n=== 検索API（list_bukken）の実測 ===")
    for label, extra in SEARCH_PROBES:
        data = _parse(_load(cache_dir, label))
        summary = _summarize_danchi(data)
        print(f"\n[{label}] 送った差分: {extra or '（なし）'}")
        for key, value in summary.items():
            if key == "市区(skcs)":
                print(f"    {key}: {len(value)}市区 {value[:8]}")
            else:
                print(f"    {key}: {value}")

    base_text = _load(cache_dir, "search_base")
    print("\n--- 基準との同一性（同一＝そのキーは黙って無視されている） ---")
    for label, _extra in SEARCH_PROBES[1:]:
        text = _load(cache_dir, label)
        if text is None or base_text is None:
            continue
        same = text == base_text
        print(f"    {label:22s}: {'同一（無視された）' if same else '差が出た（効いている）'}")
    control = _load(cache_dir, "search_control_tdfk")
    if control is not None and base_text is not None and control == base_text:
        print(
            "\n  ★ 対照（tdfk を変えた）でも差が出ていない。"
            "パラメータが届く経路そのものが死んでいる疑い。判定方法を先に直すこと"
        )


# --------------------------------------------------------------------------
# stage: rooms — 住戸API。id の一意性・floorspace の実体参照・設備テキストの有無
# --------------------------------------------------------------------------
def _split_danchi_id(danchi_id: str) -> dict[str, str]:
    """``"20_6310"`` を ``shisya=20 / danchi=631 / shikibetu=0`` へ分解する。"""

    shisya, rest = danchi_id.split("_", 1)
    return {"shisya": shisya, "danchi": rest[:-1], "shikibetu": rest[-1]}


def stage_rooms(
    client: httpx.Client | None, cache_dir: Path, interval: float, *, from_cache: bool
) -> None:
    base = _parse(_load(cache_dir, "search_base"))
    if not isinstance(base, list):
        print("★ 先に --stage search を実行すること（search_base.json が無い）")
        return

    targets = [
        row
        for row in base
        if isinstance(row, dict) and int(row.get("roomCount") or 0) > 0
    ][:3]
    if not targets:
        print("★ 空室のある団地が search_base に無い。area を変えて取り直すこと")
        return

    for index, row in enumerate(targets):
        label = f"rooms_{index}"
        if not from_cache:
            assert client is not None
            payload = _split_danchi_id(str(row["id"]))
            payload.update({"orderByField": "0", "orderBySort": "0", "pageIndex": "0"})
            print(f"  団地 {row.get('name')} ({row.get('id')}) → {payload}")
            _post(
                client,
                ROOM_API,
                payload,
                label=label,
                cache_dir=cache_dir,
                interval=interval,
            )
    _report_rooms(cache_dir, targets)


def _report_rooms(cache_dir: Path, targets: list[dict[str, Any]]) -> None:
    print("\n=== 住戸API（detail_bukken_room）の実測 ===")
    seen_ids: dict[str, str] = {}
    for index, row in enumerate(targets):
        data = _parse(_load(cache_dir, f"rooms_{index}"))
        print(f"\n[rooms_{index}] {row.get('name')} ({row.get('id')})")
        if not isinstance(data, list):
            print(f"    ★ JSON配列でない: {type(data).__name__}")
            continue
        print(f"    住戸数: {len(data)}（検索APIの roomCount は {row.get('roomCount')}）")
        for room in data:
            if not isinstance(room, dict):
                continue
            room_id = str(room.get("id"))
            if room_id in seen_ids and seen_ids[room_id] != str(row.get("id")):
                print(f"    ★ 住戸id {room_id} が団地をまたいで重複（{seen_ids[room_id]}）")
            seen_ids.setdefault(room_id, str(row.get("id")))
        first = next((r for r in data if isinstance(r, dict)), None)
        if first:
            print("    1件目のキー: " + ", ".join(sorted(first)))
            for key in (
                "id",
                "name",
                "rent",
                "commonfee",
                "type",
                "floorspace",
                "floor",
                "floorAll",
                "madori",
                "year",
                "kouzou",
                "traffic",
                "place",
                "feature",
                "urlDetail",
            ):
                if key in first:
                    value = str(first[key])
                    print(f"      {key}: {value[:120]}")
    print(f"\n  住戸idの延べ数 {len(seen_ids)}（団地をまたぐ重複の有無は上の★で判定）")


# --------------------------------------------------------------------------
# stage: areas — area=01〜06 と市区（skcs）の対応。帯の市区をどう指定するか
# --------------------------------------------------------------------------
def stage_areas(
    client: httpx.Client | None, cache_dir: Path, interval: float, *, from_cache: bool
) -> None:
    for area in ("01", "02", "03", "04", "05", "06"):
        label = f"area_{area}"
        if not from_cache:
            assert client is not None
            payload = dict(BASE_SEARCH)
            payload.update({"area": area, "vacancy": "1"})
            _post(
                client,
                SEARCH_API,
                payload,
                label=label,
                cache_dir=cache_dir,
                interval=interval,
            )
    print("\n=== area 区分と市区（skcs）の対応 ===")
    for area in ("01", "02", "03", "04", "05", "06"):
        data = _parse(_load(cache_dir, f"area_{area}"))
        summary = _summarize_danchi(data)
        cities = summary.get("市区(skcs)", [])
        print(f"\n[area={area}] 団地 {summary['件数']} / 空室のある団地 {summary.get('空室のある団地')}")
        print(f"    市区: {cities}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        required=True,
        choices=("robots", "search", "rooms", "areas"),
        help="測る対象",
    )
    parser.add_argument("--cache-dir", default=Path("tmp/ur"), type=Path)
    parser.add_argument("--interval", default=2.5, type=float, help="リクエスト間隔（秒）")
    parser.add_argument("--tdfk", default="13", help="都道府県コード（13=東京都）")
    parser.add_argument("--area", default="06", help="URのエリア区分（01〜06）")
    parser.add_argument(
        "--from-cache", action="store_true", help="取得せず保存済みの応答から解析し直す"
    )
    args = parser.parse_args()
    BASE_SEARCH["tdfk"] = args.tdfk
    BASE_SEARCH["area"] = args.area

    settings = Settings()
    client: httpx.Client | None = None
    if not args.from_cache:
        client = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en;q=0.8",
            },
            timeout=settings.request_timeout_sec,
            follow_redirects=True,
        )

    try:
        if args.stage == "robots":
            assert client is not None, "robots は --from-cache に対応しない"
            stage_robots(client, args.cache_dir, args.interval)
        elif args.stage == "search":
            stage_search(client, args.cache_dir, args.interval, from_cache=args.from_cache)
        elif args.stage == "rooms":
            stage_rooms(client, args.cache_dir, args.interval, from_cache=args.from_cache)
        elif args.stage == "areas":
            stage_areas(client, args.cache_dir, args.interval, from_cache=args.from_cache)
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
