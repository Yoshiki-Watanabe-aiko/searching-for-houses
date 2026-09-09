"""液状化（XKT025）のベクトルタイルを取得する（課題#59）。

国土交通省都市局「地形区分に基づく液状化の発生傾向図」を、不動産情報ライブラリ API の
XKT025（ベクトルタイル）から取る。⚠ **国土数値情報には無く、都市局のページにも
GISデータのリンクが無い**ので、この API が唯一の取得経路である。

⚠ **タイル集合は丁目境界ポリゴンの bbox から導く。矩形で決め打ちしない。**
4都県の外接矩形（138.85〜141.00 / 34.85〜36.35）には**島嶼部が入らない**
（小笠原村・大島町・八丈町など404丁目が外にある）。矩形で取ると恒等式
（対象キーすべてに行がある → 課題#46）を満たせないまま「取れたつもり」になる。

⚠ **z11 で十分**（実測 2026-09-10）。250mメッシュの矩形ポリゴンがそのまま返り、
z13 と比べて件数が面積比と一致する＝間引かれていない。ズームを上げる理由が無い。

⚠ **レート制限は非公開**なので10秒空ける。429/403 はリトライせず即停止する
（→ 課題#17「間隔を広げても上限は動かない」）。

使い方::

    uv run python scripts/tools/fetch_liquefaction_tiles.py --plan   # 必要なタイルを数える
    uv run python scripts/tools/fetch_liquefaction_tiles.py --fetch  # 未取得だけ取得
    uv run python scripts/tools/fetch_liquefaction_tiles.py          # 検査して manifest を書く
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "tools"))

from house_search.config.settings import Settings  # noqa: E402
from house_search.console import force_utf8_output  # noqa: E402

BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external/XKT025"
OUT_DIR = REPO_ROOT / "data" / "hazard_sources" / "liquefaction"
# ⚠ ゲート測定（課題#49 Step 9）で取った分を再利用する。同じタイルを取り直さない
PROBE_DIR = REPO_ROOT / "data" / "probe" / "liquefaction"
ZOOM = 11
INTERVAL_SEC = 10.0
# 250mメッシュの1辺（度）。JIS X 0410 の5次メッシュ（経度 11.25″ / 緯度 7.5″）
MESH_LON_DEG = 11.25 / 3600
MESH_LAT_DEG = 7.5 / 3600
# ⚠ ベクトルタイルの量子化ステップ（z11 の経度幅 632.8″ ÷ 4096 ≒ 0.155″）。
#    満寸のメッシュでも幅が 11.2782″ / 11.1237″ と2種類に振れるのはこのため。
QUANTIZE_TOLERANCE_DEG = 0.2 / 3600


class RateLimited(RuntimeError):
    """429/403。⚠ リトライしない（→ 課題#17）。"""


def deg2tile(lat: float, lon: float, zoom: int = ZOOM) -> tuple[int, int]:
    """緯度経度を XYZ タイル座標へ（Web メルカトル）。"""
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def plan_tiles() -> list[tuple[int, int]]:
    """丁目境界ポリゴンの bbox を覆う z11 タイルを列挙する。

    ⚠ **矩形の決め打ちをしない。** 島嶼部（小笠原・大島・八丈島ほか404丁目）は
    4都県の外接矩形の外にあり、矩形で取ると恒等式を満たせない。
    """
    from build_hazard_levels import load_chome_polygons

    chome, _towns, _total, _coverage = load_chome_polygons()
    tiles: set[tuple[int, int]] = set()
    for polygon in chome.values():
        west, south, east, north = polygon.bounds
        x0, y0 = deg2tile(north, west)
        x1, y1 = deg2tile(south, east)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                tiles.add((x, y))
    return sorted(tiles)


def tile_path(x: int, y: int) -> Path:
    return OUT_DIR / f"z{ZOOM}_x{x}_y{y}.json"


def fetch_tile(client: httpx.Client, x: int, y: int) -> int:
    """タイルを1枚取って保存し、feature 件数を返す。"""
    params = {"response_format": "geojson", "z": str(ZOOM), "x": str(x), "y": str(y)}
    response = client.get(BASE_URL, params=params)
    if response.status_code in (429, 403):
        raise RateLimited(
            f"HTTP {response.status_code}：レート制限とみられます。"
            "⚠ リトライせず時間を空けて再実行してください"
        )
    response.raise_for_status()
    raw = response.content
    # ⚠ features が空でも保存する。「取りに行った」記録が無いと次回また取りに行く
    payload = json.loads(raw.decode("utf-8"))
    tile_path(x, y).write_bytes(raw)
    return len(payload.get("features", []))


def reuse_from_probe(x: int, y: int) -> bool:
    """ゲート測定で取ったタイルがあれば流用する。⚠ 同じものを取り直さない。"""
    src = PROBE_DIR / f"z{ZOOM}_x{x}_y{y}.json"
    if not src.exists():
        return False
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or "features" not in payload:
        return False
    tile_path(x, y).write_bytes(src.read_bytes())
    return True


def inspect(tiles: list[tuple[int, int]]) -> dict[str, Any]:
    """保存済みタイルを検査して manifest を組み立てる。

    ⚠⚠ **「切り取られていないこと」は確かめられない。** 実測（2026-09-10）で
    geometry は**タイル境界で切り取られ、しかもバッファで隣接タイルへはみ出す**
    （断片 3.4%・最大はみ出し 0.7725″）。そのため集計は geometry ではなく
    **mesh_code から再構成した矩形**を使う（→ ADR 0023・課題#59）。
    ここで確かめるのは **その再構成が満寸の geometry と一致すること**である。
    ⚠ **mesh_code の重複でレベルが食い違わないこと**も見る（隣接タイルに同じ
    メッシュが出るのは正常。食い違うなら版が混ざっている）。
    """
    from build_hazard_levels import mesh_bounds

    levels_by_mesh: dict[str, int] = {}
    conflicts = 0
    indexes: Counter[str] = Counter()
    level_counts: Counter[int] = Counter()
    fragments = 0
    full_meshes = 0
    mesh_mismatch = 0
    worst_diff = 0.0
    empty_tiles = 0
    total_features = 0
    files: list[dict[str, Any]] = []

    for x, y in tiles:
        path = tile_path(x, y)
        if not path.exists():
            raise RuntimeError(f"未取得のタイルがあります: {path.name}（--fetch を先に）")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        features = payload.get("features", [])
        if not features:
            empty_tiles += 1
        total_features += len(features)
        for feature in features:
            props = feature.get("properties") or {}
            mesh = str(props.get("mesh_code"))
            raw_level = props.get("liquefaction_tendency_level")
            if raw_level is None:
                continue
            level = int(raw_level)
            level_counts[level] += 1
            indexes[str(props.get("_index"))] += 1
            previous = levels_by_mesh.get(mesh)
            if previous is None:
                levels_by_mesh[mesh] = level
            elif previous != level:
                conflicts += 1
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates")
            ring = None
            if geometry.get("type") == "Polygon" and coords:
                ring = coords[0]
            elif geometry.get("type") == "MultiPolygon" and coords:
                ring = coords[0][0]
            if ring is None or len(ring) != 5:
                mesh_mismatch += 1
                continue
            lons = [pt[0] for pt in ring]
            lats = [pt[1] for pt in ring]
            # ⚠ 断片（タイル境界で切られたもの）は再構成と比べない。形が違って当然
            if (
                abs((max(lons) - min(lons)) - MESH_LON_DEG) > QUANTIZE_TOLERANCE_DEG
                or abs((max(lats) - min(lats)) - MESH_LAT_DEG) > QUANTIZE_TOLERANCE_DEG
            ):
                fragments += 1
                continue
            full_meshes += 1
            west, south, east, north = mesh_bounds(mesh)
            diff = max(
                abs(west - min(lons)),
                abs(south - min(lats)),
                abs(east - max(lons)),
                abs(north - max(lats)),
            )
            worst_diff = max(worst_diff, diff)
            if diff > QUANTIZE_TOLERANCE_DEG:
                mesh_mismatch += 1
        files.append(
            {
                "tile": f"z{ZOOM}_x{x}_y{y}",
                "bytes": len(raw),
                "features": len(features),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    print(f"タイル: {len(tiles):,}枚（うち features が空: {empty_tiles}枚）")
    print(f"feature: {total_features:,}件 / ユニークな mesh_code: {len(levels_by_mesh):,}件")
    print(f"  レベルの分布: {dict(sorted(level_counts.items()))}")
    print(f"  _index: {dict(indexes)}")
    if conflicts:
        raise RuntimeError(
            f"⚠ 同じ mesh_code でレベルが食い違う feature が {conflicts:,}件あります。"
            "版が混ざっている疑いがあるので取り直してください"
        )
    print(
        f"  満寸 {full_meshes:,} / 断片 {fragments:,}"
        f"（⚠ タイル境界で切られたもの。集計は mesh_code から再構成するので影響しない）"
    )
    print(f"  再構成との最大のずれ: {worst_diff * 3600:.4f}″")
    if mesh_mismatch:
        raise RuntimeError(
            f"⚠⚠ mesh_code から再構成した矩形と食い違う feature が {mesh_mismatch:,}件あります。"
            "集計はこの再構成を正典にするので、食い違うと面積加重が狂います"
        )
    # ⚠ _index の日付が複数あれば版ずれ。source 列に何を書くか決められない
    stamps = {name.rsplit("_", 1)[-1][:8] for name in indexes}
    if len(stamps) != 1:
        raise RuntimeError(f"⚠ _index の日付が複数あります: {sorted(stamps)}")
    stamp = stamps.pop()
    print(f"  出典の版: {stamp}")

    return {
        "source": f"mlit_xkt025_{stamp}",
        "acquired_on": date.today().isoformat(),
        "zoom": ZOOM,
        "tiles": len(tiles),
        "empty_tiles": empty_tiles,
        "features": total_features,
        "full_meshes": full_meshes,
        "fragments": fragments,
        "unique_meshes": len(levels_by_mesh),
        "level_counts": dict(sorted(level_counts.items())),
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(description="液状化（XKT025）のタイル取得")
    parser.add_argument("--plan", action="store_true", help="必要なタイルを数えるだけ")
    parser.add_argument("--fetch", action="store_true", help="未取得のタイルを取得する")
    parser.add_argument("--limit", type=int, default=300, help="リクエストの上限")
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tiles = plan_tiles()
    print(f"丁目境界を覆う z{ZOOM} タイル: {len(tiles):,}枚")

    if args.plan:
        have = sum(1 for x, y in tiles if tile_path(x, y).exists())
        reusable = sum(
            1
            for x, y in tiles
            if not tile_path(x, y).exists() and (PROBE_DIR / f"z{ZOOM}_x{x}_y{y}.json").exists()
        )
        need = len(tiles) - have - reusable
        print(f"  取得済み {have:,} / 流用可 {reusable:,} / 要取得 {need:,}")
        print(f"  取得の見込み: {need * INTERVAL_SEC / 60:.1f}分")
        return 0

    if args.fetch:
        missing = [(x, y) for x, y in tiles if not tile_path(x, y).exists()]
        reused = 0
        for x, y in list(missing):
            if reuse_from_probe(x, y):
                reused += 1
                missing.remove((x, y))
        print(f"ゲート測定から流用: {reused:,}枚 / これから取得: {len(missing):,}枚")
        if len(missing) > args.limit:
            raise RuntimeError(f"要取得 {len(missing)} 枚が上限 {args.limit} を超えています")
        key = Settings().require_reinfolib_api_key()
        with httpx.Client(
            timeout=60.0,
            headers={"Ocp-Apim-Subscription-Key": key},
            follow_redirects=False,
        ) as client:
            for index, (x, y) in enumerate(missing, 1):
                count = fetch_tile(client, x, y)
                print(f"  [{index}/{len(missing)}] z{ZOOM}_x{x}_y{y}: {count:,}件", flush=True)
                if index < len(missing):
                    time.sleep(INTERVAL_SEC)

    manifest = inspect(tiles)
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"manifest: {OUT_DIR / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
