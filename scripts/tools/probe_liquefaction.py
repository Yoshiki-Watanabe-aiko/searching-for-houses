"""液状化（XKT025）の着手前実測（課題#49 Step 9）。

⚠ **目的は「本取得を払う価値があるか」の判定**であって、本採用の実装ではない。
``flood_rank_avg`` と強く相関するなら二重重みになるので、全域のタイル取得を
払わずに棄却できる（→ 課題#49 のゲート: 既存 metric との順位相関 |r| < 0.6）。

⚠ **推測でパラメータを書かない**（→ ADR 0015）。z の値域・応答形式・属性名は
マニュアルの記述をこのスクリプトで実測してから使う。

⚠ **判定方法の妥当性を先に担保する。** タイルAPIには「効かないはずのキー」に
相当する対照が無いので、代わりに **別のタイル座標では中身が変わること** と
**海上のタイルは空で返ること** を確かめる。これが無いと「全部同じ応答」を
「そういうデータ」と読み違える（→ 課題#37 で UR を踏みかけた形）。

⚠ **取得した生応答は必ず保存する**（→ 課題#36）。解析だけやり直せるようにしないと、
パーサの試行錯誤でリクエストを使い切る。

使い方::

    uv run python scripts/tools/probe_liquefaction.py --fetch      # 取得して解析
    uv run python scripts/tools/probe_liquefaction.py              # 保存済みから解析だけ
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from house_search.config.settings import Settings  # noqa: E402
from house_search.console import force_utf8_output  # noqa: E402

BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"
CACHE_DIR = REPO_ROOT / "data" / "probe" / "liquefaction"
# ⚠ レート制限は非公開。probe_reinfolib.py と同じく10秒空ける。
#    429/403 を受けたら即停止しリトライしない（→ 課題#17）。
INTERVAL_SEC = 10.0


def deg2tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """緯度経度を XYZ タイル座標へ（Web メルカトル）。"""
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_bounds(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    """タイルの (西, 南, 東, 北) を度で返す。"""
    n = 2.0**zoom

    def lon_of(xx: int) -> float:
        return xx / n * 360.0 - 180.0

    def lat_of(yy: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return lon_of(x), lat_of(y + 1), lon_of(x + 1), lat_of(y)


class Budget:
    """リクエストの総量を数えて上限で止める。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> None:
        if self.used >= self.limit:
            raise RuntimeError(f"リクエスト上限 {self.limit} に達したので止めます")
        self.used += 1


def fetch_tile(
    client: httpx.Client, zoom: int, x: int, y: int, budget: Budget
) -> dict[str, Any] | None:
    """タイルを1枚取って生応答を保存する。⚠ 失敗しても保存する（原因調査のため）。"""
    name = f"z{zoom}_x{x}_y{y}"
    path = CACHE_DIR / f"{name}.json"
    meta_path = CACHE_DIR / f"{name}.meta.json"
    budget.take()
    params = {"response_format": "geojson", "z": str(zoom), "x": str(x), "y": str(y)}
    started = time.monotonic()
    response = client.get(f"{BASE_URL}/XKT025", params=params)
    elapsed = time.monotonic() - started
    raw = response.content
    meta = {
        "params": params,
        "status_code": response.status_code,
        "elapsed_sec": round(elapsed, 2),
        "bytes": len(raw),
        "content_type": response.headers.get("content-type"),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    path.write_bytes(raw)

    if response.status_code in (429, 403):
        raise RuntimeError(
            f"HTTP {response.status_code}：レート制限とみられます。"
            "⚠ リトライせず時間を空けて再実行してください"
        )
    if response.status_code == 404:
        # ⚠ このAPIは「データが無い」を 404 で返しうる（XIT001 で実測済み → 課題#49）
        print(f"  取得 {name}: HTTP 404（データ無し）/ {elapsed:.1f}秒")
        time.sleep(INTERVAL_SEC)
        return None
    response.raise_for_status()
    print(f"  取得 {name}: HTTP {response.status_code} / {len(raw):,}B / {elapsed:.1f}秒")
    time.sleep(INTERVAL_SEC)
    return json.loads(raw.decode("utf-8"))


def load_cached(zoom: int, x: int, y: int) -> dict[str, Any] | None:
    path = CACHE_DIR / f"z{zoom}_x{x}_y{y}.json"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    payload = json.loads(text)
    if isinstance(payload, dict) and "features" not in payload:
        return None  # 404 のメッセージ本文など
    return payload


def describe(payload: dict[str, Any] | None, label: str) -> dict[str, Any]:
    """応答の中身を要約する。⚠ 属性名は決め打ちせず実際のキーを列挙する。"""
    if payload is None:
        print(f"\n[{label}] データ無し")
        return {"features": 0}
    features = payload.get("features", [])
    print(f"\n[{label}] features={len(features):,}")
    if not features:
        return {"features": 0}
    keys: Counter[str] = Counter()
    for feat in features:
        keys.update((feat.get("properties") or {}).keys())
    print(f"  属性キー: {dict(keys)}")
    sample = features[0]
    props = sample.get("properties") or {}
    print(f"  1件目の属性: {json.dumps(props, ensure_ascii=False)}")
    geom = sample.get("geometry") or {}
    coords = geom.get("coordinates")
    ring_len = None
    if geom.get("type") == "Polygon" and coords:
        ring_len = len(coords[0])
    elif geom.get("type") == "MultiPolygon" and coords:
        ring_len = len(coords[0][0])
    print(f"  1件目のジオメトリ: type={geom.get('type')} 外周の頂点数={ring_len}")
    return {"features": len(features), "keys": dict(keys)}


def covering_tiles(zoom: int) -> list[tuple[int, int, int]]:
    """掲載のある丁目の代表点を覆うタイルを、掲載数の多い順に返す。

    ⚠ 相関の判定に使うサンプルなので **掲載のある丁目だけ**でよい。
    本採用するなら恒等式（対象キー全部に行がある → 課題#46）を満たすため
    4都県を穴なく覆う必要があり、そのときは矩形で総なめする。
    """
    from sqlalchemy import create_engine, text

    from house_search.config.settings import load_settings

    engine = create_engine(load_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ap.lat, ap.lon, count(DISTINCT l.id) AS listings
                  FROM m_address_points ap
                  JOIN t_listings l ON l.address_normalized = ap.normalized_key
                 WHERE l.status = 'active' AND ap.lat IS NOT NULL
                 GROUP BY 1, 2
                """
            )
        ).fetchall()
    tiles: Counter[tuple[int, int]] = Counter()
    for row in rows:
        tiles[deg2tile(float(row.lat), float(row.lon), zoom)] += row.listings
    return [(zoom, x, y) for (x, y), _ in tiles.most_common()]


def _bbox(feature: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """フィーチャの外接矩形を返す。⚠ 250mメッシュは矩形なので bbox 判定が厳密になる。"""
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates")
    if not coords:
        return None
    if geom.get("type") == "Polygon":
        ring = coords[0]
    elif geom.get("type") == "MultiPolygon":
        ring = coords[0][0]
    else:
        return None
    lons = [pt[0] for pt in ring]
    lats = [pt[1] for pt in ring]
    return min(lons), min(lats), max(lons), max(lats)


def correlate() -> int:
    """保存済みタイルから丁目ごとの液状化レベルを引き、既存 metric との相関を測る。

    ⚠ **これはゲートの判定**（→ 課題#49）。解決率 >= 80% かつ既存 metric との
    順位相関 |r| < 0.6 を満たさなければ、二重重みになるので配点しない。

    ⚠ 丁目の代表点で引く。課題#46 は「代表点方式は12分の1しか拾えない」として
    棄却したが、あれは**面積の小さい警戒区域に代表点が入る確率**の話。
    液状化は250mメッシュで**全面を覆う**ので、代表点は必ずどれかのメッシュに入る。
    """
    from sqlalchemy import create_engine, text

    from house_search.config.settings import load_settings

    engine = create_engine(load_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ap.normalized_key AS key, ap.lat, ap.lon,
                       max(CASE WHEN h.hazard_type = 'flood' AND h.aggregation = 'rank_avg'
                                THEN h.value END) AS flood_rank_avg,
                       max(CASE WHEN h.hazard_type = 'landslide' AND h.aggregation = 'area_ratio'
                                THEN h.value END) AS landslide_area_ratio
                  FROM m_address_points ap
                  JOIN t_listings l ON l.address_normalized = ap.normalized_key
                  LEFT JOIN m_hazard_levels h ON h.normalized_key = ap.normalized_key
                 WHERE l.status = 'active' AND ap.lat IS NOT NULL
                 GROUP BY 1, 2, 3
                """
            )
        ).fetchall()

    print(f"対象の丁目: {len(rows):,}件")

    # タイルごとに丁目をまとめる（1タイル内だけを線形探索する）
    by_tile: dict[tuple[int, int], list[Any]] = {}
    for row in rows:
        by_tile.setdefault(deg2tile(float(row.lat), float(row.lon), 11), []).append(row)

    levels: dict[str, int] = {}
    missing_tiles = 0
    for (x, y), members in sorted(by_tile.items()):
        payload = load_cached(11, x, y)
        if payload is None:
            missing_tiles += 1
            continue
        meshes = []
        for feat in payload.get("features", []):
            box = _bbox(feat)
            if box is None:
                continue
            level = (feat.get("properties") or {}).get("liquefaction_tendency_level")
            if level is None:
                continue
            meshes.append((box, int(level)))
        for row in members:
            lon, lat = float(row.lon), float(row.lat)
            for (west, south, east, north), level in meshes:
                if west <= lon <= east and south <= lat <= north:
                    levels[row.key] = level
                    break

    resolved = len(levels)
    print(f"タイル未取得で飛ばした: {missing_tiles}枚")
    print(f"液状化を引けた丁目: {resolved:,} / {len(rows):,}（{resolved / len(rows):.1%}）")
    counts = Counter(levels.values())
    print("  レベルの分布（小さいほど液状化しやすい）:")
    for level in sorted(counts):
        print(f"    {level}: {counts[level]:,}件（{counts[level] / resolved:.1%}）")

    # 既存 metric との順位相関（Spearman）
    def spearman(pairs: list[tuple[float, float]]) -> float:
        if len(pairs) < 3:
            return float("nan")

        def ranks(values: list[float]) -> list[float]:
            order = sorted(range(len(values)), key=lambda i: values[i])
            out = [0.0] * len(values)
            i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                    j += 1
                avg = (i + j) / 2 + 1
                for k in range(i, j + 1):
                    out[order[k]] = avg
                i = j + 1
            return out

        xs = ranks([p[0] for p in pairs])
        ys = ranks([p[1] for p in pairs])
        n = len(pairs)
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
        dx = sum((a - mx) ** 2 for a in xs) ** 0.5
        dy = sum((b - my) ** 2 for b in ys) ** 0.5
        return num / (dx * dy) if dx and dy else float("nan")

    print("\n既存 metric との順位相関（ゲート: |r| < 0.6）:")
    for label, attr in (
        ("flood_rank_avg", "flood_rank_avg"),
        ("landslide_area_ratio", "landslide_area_ratio"),
    ):
        pairs = [
            (float(levels[row.key]), float(getattr(row, attr)))
            for row in rows
            if row.key in levels and getattr(row, attr) is not None
        ]
        r = spearman(pairs)
        verdict = "通過" if abs(r) < 0.6 else "⚠ 不通過"
        print(f"  {label:22s} n={len(pairs):>6,}  r={r:+.3f}  {verdict}")

    # ⚠ 相関係数は「どれだけ重なるか」しか言わない。配点の判断には
    #    **洪水では捉えられない危険が何件あるか**が要る。
    print("\n洪水 × 液状化のクロス集計（丁目数）:")

    def flood_band(value: float) -> str:
        if value <= 0.0:
            return "洪水 区域外"
        if value < 1.0:
            return "洪水 弱(0〜1)"
        return "洪水 強(1以上)"

    def liq_band(level: int) -> str:
        if level <= 2:
            return "液状化しやすい(1-2)"
        if level <= 4:
            return "中間(3-4)"
        return "しにくい(5-6)"

    cross: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if row.key not in levels or row.flood_rank_avg is None:
            continue
        cross[(flood_band(float(row.flood_rank_avg)), liq_band(levels[row.key]))] += 1
    fl_keys = ["洪水 区域外", "洪水 弱(0〜1)", "洪水 強(1以上)"]
    lq_keys = ["液状化しやすい(1-2)", "中間(3-4)", "しにくい(5-6)"]
    header = "".join(f"{k:>22s}" for k in lq_keys)
    print(f"  {'':16s}{header}")
    for fk in fl_keys:
        cells = "".join(f"{cross[(fk, lk)]:>22,}" for lk in lq_keys)
        print(f"  {fk:16s}{cells}")
    only_liq = cross[("洪水 区域外", "液状化しやすい(1-2)")]
    total_cross = sum(cross.values())
    print(
        f"  ⚠ 洪水は区域外なのに液状化しやすい丁目: {only_liq:,}件"
        f"（{only_liq / total_cross:.1%}）＝ 洪水では捉えられない危険"
    )

    # 地形区分の内訳（レベルの意味を実データで確かめる）
    print("\n液状化レベルと地形区分の対応:")
    detail: dict[int, Counter[str]] = {}
    notes: dict[int, Counter[str]] = {}
    for x, y in sorted(by_tile):
        payload = load_cached(11, x, y)
        if payload is None:
            continue
        for feat in payload.get("features", []):
            props = feat.get("properties") or {}
            raw_level = props.get("liquefaction_tendency_level")
            if raw_level is None:
                continue
            level = int(raw_level)
            detail.setdefault(level, Counter())[
                str(props.get("topographic_classification_name_ja"))
            ] += 1
            notes.setdefault(level, Counter())[str(props.get("note"))] += 1
    for level in sorted(detail):
        note = notes[level].most_common(1)[0][0]
        tops = "、".join(name for name, _ in detail[level].most_common(3))
        print(f"  レベル{level}: 「{note}」 / 地形: {tops}")
    return 0


def main() -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(description="液状化（XKT025）の着手前実測")
    parser.add_argument(
        "--fetch", action="store_true", help="実際に取得する（既定は保存済みから解析）"
    )
    parser.add_argument("--limit", type=int, default=6, help="リクエストの上限（既定6）")
    parser.add_argument(
        "--cover",
        action="store_true",
        help="掲載のある丁目を覆う z11 タイルを取る（相関測定のサンプル）",
    )
    parser.add_argument(
        "--correlate",
        action="store_true",
        help="保存済みタイルから既存 metric との相関を測る（ゲートの判定）",
    )
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.correlate:
        return correlate()

    # 測る対象。⚠ 対照を2つ置く（別座標で中身が変わる／海上は空）。
    targets = [
        # 東京駅（低地・液状化の想定がある想定）
        ("z11_東京駅", 11, *deg2tile(35.681, 139.767, 11)),
        # 八王子（丘陵・別の座標で中身が変わることの対照）
        ("z11_八王子", 11, *deg2tile(35.656, 139.339, 11)),
        # 同じ東京駅を z13 で（ズームによる粒度の違いを見る）
        ("z13_東京駅", 13, *deg2tile(35.681, 139.767, 13)),
        # 太平洋上（データが無いことの対照）
        ("z11_海上", 11, *deg2tile(34.2, 141.5, 11)),
    ]

    if args.cover:
        tiles = covering_tiles(11)
        print(f"掲載のある丁目を覆う z11 タイル: {len(tiles)}枚")
        targets = [(f"cover_{i:03d}", z, x, y) for i, (z, x, y) in enumerate(tiles, 1)]

    client: httpx.Client | None = None
    if args.fetch:
        key = Settings().require_reinfolib_api_key()
        client = httpx.Client(
            timeout=60.0,
            headers={"Ocp-Apim-Subscription-Key": key},
            follow_redirects=False,
        )
    budget = Budget(args.limit)

    results: dict[str, Any] = {}
    try:
        for label, zoom, x, y in targets:
            west, south, east, north = tile_bounds(x, y, zoom)
            print(f"\n=== {label} z={zoom} x={x} y={y} ===")
            print(f"  範囲: 経度 {west:.4f}〜{east:.4f} / 緯度 {south:.4f}〜{north:.4f}")
            payload = load_cached(zoom, x, y)
            if payload is None and args.fetch and client is not None:
                payload = fetch_tile(client, zoom, x, y, budget)
            elif payload is not None:
                print("  （保存済みを使用）")
            elif not args.fetch:
                print("  ⚠ 未取得。--fetch を付けてください")
                continue
            results[label] = describe(payload, label)
    finally:
        if client is not None:
            client.close()

    # 4都県を覆うタイル数の見積もり。⚠ 実測した z ごとに出す
    print("\n=== 4都県を覆うタイル数（見積もり） ===")
    # 4都県のおおよその外接矩形
    west, south, east, north = 138.85, 34.85, 141.00, 36.35
    for zoom in (11, 12, 13, 14, 15):
        x0, y0 = deg2tile(north, west, zoom)
        x1, y1 = deg2tile(south, east, zoom)
        cols = x1 - x0 + 1
        rows = y1 - y0 + 1
        total = cols * rows
        hours = total * INTERVAL_SEC / 3600
        print(f"  z{zoom}: {cols}列 × {rows}行 = {total:,}タイル（10秒間隔で {hours:.1f}時間）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
