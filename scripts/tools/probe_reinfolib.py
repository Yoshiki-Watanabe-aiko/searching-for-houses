"""不動産情報ライブラリ API の対照実験（課題#49 Step 1）。

⚠ **推測でパラメータを書かない**（→ ADR 0015）。キー名・選択肢・件数の挙動は
このスクリプトで実測し、結果を課題#49 の表へ書き写してから実装に入る。

⚠ **判定方法の妥当性を先に担保する。** 「効かないはずのキー」（``zzz=1``）を送って
件数が変わらないことと、「効くはずのキー」（``year`` を1つ動かす）で件数が変わることを
**両方**確かめる。片方だけだと「どのパラメータも効いていない」状態を
「同一応答なので妥当」と読み違える（→ 課題#37 で実際に踏みかけた）。

⚠ **取得した生応答は必ず保存する。** 解析だけやり直せるようにしておかないと、
パーサの試行錯誤で貴重なリクエストを使い切る（→ 課題#36 の ATHOME で救われた形）。

使い方::

    uv run python scripts/tools/probe_reinfolib.py            # 取得して解析（最大12リクエスト）
    uv run python scripts/tools/probe_reinfolib.py --from-cache  # 保存済みから解析だけ
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from house_search.config.settings import Settings  # noqa: E402

BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"
CACHE_DIR = REPO_ROOT / "data" / "probe" / "reinfolib"
# ⚠ レート制限は非公開（「基準期間内に多数のリクエストがあった場合にはアクセス制限」）。
#    総量を12本に抑えたうえで10秒空ける。429/403 を受けたら即停止しリトライしない
#    （HOMES で「間隔を広げても上限は動かない」を実測済み → 課題#17）。
INTERVAL_SEC = 10.0

# 対象4都県（JIS2桁）。エリア帯ではなく売買パターンの対象。
PREFECTURES = {"13": "東京都", "11": "埼玉県", "12": "千葉県", "14": "神奈川県"}


class Budget:
    """リクエストの総量を数えて上限で止める。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> None:
        if self.used >= self.limit:
            raise RuntimeError(f"リクエスト上限 {self.limit} に達したので止めます")
        self.used += 1


def fetch(
    client: httpx.Client, api_id: str, params: dict[str, str], name: str, budget: Budget
) -> dict[str, Any]:
    """API を1回叩いて生応答を保存する。⚠ 失敗しても保存する（原因調査のため）。"""
    path = CACHE_DIR / f"{name}.json"
    meta_path = CACHE_DIR / f"{name}.meta.json"
    budget.take()
    url = f"{BASE_URL}/{api_id}"
    started = time.monotonic()
    response = client.get(url, params=params)
    elapsed = time.monotonic() - started
    raw = response.content
    meta = {
        "api_id": api_id,
        # ⚠ キーはヘッダで送るので params に載らない。ここに書き出しても漏れない
        "params": params,
        "status_code": response.status_code,
        "elapsed_sec": round(elapsed, 2),
        "bytes": len(raw),
        "content_encoding": response.headers.get("content-encoding"),
        # ⚠ gzip のマジックバイト。httpx が自動展開していれば 1f8b では始まらない
        "starts_with_gzip_magic": raw[:2] == b"\x1f\x8b",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    path.write_bytes(raw)

    if response.status_code in (429, 403):
        raise RuntimeError(
            f"HTTP {response.status_code}：レート制限とみられます。"
            "⚠ リトライせず時間を空けて再実行してください"
        )
    # ⚠⚠ **このAPIは「データが無い」を HTTP 404 ＋ {"message":"検索結果がありません。"}
    #     で返す**（2026-09-08 実測。空配列ではない）。例外にすると未公開の四半期で
    #     処理が止まり、握りつぶすとパス誤りの本物の404に気づけない。本文で判別する。
    if response.status_code == 404:
        body = json.loads(raw.decode("utf-8")) if raw else {}
        if "検索結果がありません" in str(body.get("message", "")):
            print(f"  取得 {name}: HTTP 404（データ無し）/ {elapsed:.1f}秒")
            time.sleep(INTERVAL_SEC)
            return {"status": "NOT_FOUND", "data": []}
        raise RuntimeError(f"HTTP 404（想定外）: {body}")
    response.raise_for_status()
    print(
        f"  取得 {name}: HTTP {response.status_code} / {len(raw):,}B / {elapsed:.1f}秒"
        f" / gzipマジック={meta['starts_with_gzip_magic']}"
    )
    time.sleep(INTERVAL_SEC)
    return json.loads(raw.decode("utf-8"))


def load_cached(name: str) -> dict[str, Any] | None:
    path = CACHE_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def records_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """応答から明細の配列を取り出す。⚠ 包み方が想定と違えば早めに気づけるようにする。"""
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"応答の data が配列ではありません: keys={list(payload)}")
    return data


def describe_trades(name: str, payload: dict[str, Any]) -> None:
    """XIT001 の応答の中身を実測する（課題#49 の「未測定」を埋める材料）。"""
    rows = records_of(payload)
    print(f"\n--- {name}: {len(rows):,}件 ---")
    if not rows:
        return
    print(f"  列（{len(rows[0])}個）: {', '.join(sorted(rows[0]))}")

    types = Counter(r.get("Type") for r in rows)
    print(f"  Type: {dict(types)}")
    for key in ("PriceCategory", "Period", "Renovation"):
        print(f"  {key}: {dict(Counter(r.get(key) for r in rows).most_common(4))}")

    # ⚠ 数値なのか文字列なのか、レンジ表記（「10㎡未満」等）が混じるのかを見る
    for key in ("TradePrice", "Area", "UnitPrice", "PricePerUnit", "TotalFloorArea"):
        values = [r.get(key) for r in rows]
        filled = [v for v in values if v not in (None, "")]
        kinds = Counter(type(v).__name__ for v in filled)
        non_numeric = sorted({str(v) for v in filled if not str(v).replace(".", "", 1).isdigit()})
        print(
            f"  {key}: 充足 {len(filled)}/{len(rows)} ({len(filled) / len(rows):.0%})"
            f" 型={dict(kinds)} 非数値={non_numeric[:5]}"
        )

    for key in ("BuildingYear", "FloorPlan", "Structure", "Use", "MunicipalityCode"):
        vals = Counter(str(r.get(key)) for r in rows)
        print(f"  {key}: {dict(vals.most_common(5))}")


def unit_prices(rows: list[dict[str, Any]], type_name: str, area_key: str) -> list[float]:
    """㎡単価（総額 ÷ 面積）を出す。⚠ 数値化できない行は捨てて件数を返す側で数える。"""
    out: list[float] = []
    for row in rows:
        if row.get("Type") != type_name:
            continue
        try:
            price = float(str(row.get("TradePrice", "")))
            area = float(str(row.get(area_key, "")))
        except (TypeError, ValueError):
            continue
        if area > 0:
            out.append(price / area)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-cache", action="store_true", help="保存済み応答から解析だけ行う")
    parser.add_argument("--limit", type=int, default=12, help="リクエストの上限（既定12）")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.limit)

    client: httpx.Client | None = None
    if not args.from_cache:
        key = Settings().require_reinfolib_api_key()
        client = httpx.Client(
            # ⚠ キーはヘッダでのみ送る（URLに載せない・ログに出さない）
            headers={"Ocp-Apim-Subscription-Key": key},
            timeout=60.0,
        )

    def get(api_id: str, params: dict[str, str], name: str) -> dict[str, Any] | None:
        if args.from_cache:
            return load_cached(name)
        assert client is not None
        return fetch(client, api_id, params, name, budget)

    try:
        # === (a) XIT002: 市区町村一覧と m_cities.jis_code の突き合わせ =============
        print("=== (a) XIT002 市区町村一覧 ===")
        api_cities: dict[str, list[dict[str, Any]]] = {}
        for area, pref in PREFECTURES.items():
            payload = get("XIT002", {"area": area}, f"xit002_{area}")
            if payload is None:
                continue
            rows = records_of(payload)
            api_cities[area] = rows
            print(f"  {pref}: {len(rows)}件  例={rows[:2]}")

        # === (d)(c) XIT001: 最新の非空四半期を探索しつつ都県単位の重さを測る =======
        print("\n=== (c)(d) XIT001 都県単位・最新四半期の探索 ===")
        latest: tuple[int, int] | None = None
        tokyo_all: dict[str, Any] | None = None
        # ⚠ 2026Q2 は 2026-09-08 時点で 404（未公開）と実測済みなので Q1 から始める
        for year, quarter in ((2026, 1), (2025, 4), (2025, 3)):
            name = f"xit001_area13_{year}q{quarter}"
            payload = get(
                "XIT001", {"year": str(year), "quarter": str(quarter), "area": "13"}, name
            )
            if payload is None:
                continue
            n = len(records_of(payload))
            print(f"  東京都 {year}Q{quarter}: {n:,}件")
            if n > 0:
                latest, tokyo_all = (year, quarter), payload
                break
        if latest is None:
            print("  ⚠ どの四半期も0件でした。パラメータかキーを疑ってください")
            return 1
        year, quarter = latest
        print(f"  → 最新の非空四半期は {year}Q{quarter}")

        # === (b) 対照実験: zzz=1 と priceClassification ============================
        print(f"\n=== (b) 対照実験（世田谷区 13112・{year}Q{quarter}）===")
        base = {"year": str(year), "quarter": str(quarter), "city": "13112"}
        counts: dict[str, int] = {}
        variants = {
            "base": base,
            "zzz": {**base, "zzz": "1"},
            "pc01": {**base, "priceClassification": "01"},
            "pc02": {**base, "priceClassification": "02"},
        }
        payloads: dict[str, dict[str, Any]] = {}
        for label, params in variants.items():
            payload = get("XIT001", params, f"xit001_13112_{year}q{quarter}_{label}")
            if payload is None:
                continue
            payloads[label] = payload
            counts[label] = len(records_of(payload))
            print(f"  {label:<6}: {counts[label]:,}件")

        if "base" in counts and "zzz" in counts:
            ok = counts["base"] == counts["zzz"]
            print(f"  判定方法の妥当性（zzz=1 が基準と同一）: {'OK' if ok else '⚠ 不一致'}")
        if all(k in counts for k in ("base", "pc01", "pc02")):
            total = counts["pc01"] + counts["pc02"]
            print(
                f"  01+02={total:,} と 未指定={counts['base']:,}: "
                f"{'一致（排他）' if total == counts['base'] else '⚠ 不一致（重複か別母集団）'}"
            )

        # === (e) 応答の中身 ========================================================
        print("\n=== (e) 応答の中身 ===")
        if tokyo_all is not None:
            describe_trades(f"東京都 {year}Q{quarter}（全種別）", tokyo_all)
        for label in ("pc01", "pc02"):
            if label in payloads:
                describe_trades(f"世田谷区 {label}", payloads[label])

        # === (f) 01 と 02 の水準差 ==================================================
        print("\n=== (f) 取引価格(01) と 成約価格(02) の㎡単価の水準差 ===")
        for type_name, area_key in (("中古マンション等", "Area"), ("宅地(土地と建物)", "Area")):
            line = [f"  {type_name}:"]
            medians: dict[str, float] = {}
            for label in ("pc01", "pc02"):
                if label not in payloads:
                    continue
                prices = unit_prices(records_of(payloads[label]), type_name, area_key)
                if prices:
                    medians[label] = statistics.median(prices)
                    line.append(f"{label} 中央 {medians[label]:,.0f}円/㎡（n={len(prices)}）")
                else:
                    line.append(f"{label} 該当なし")
            if len(medians) == 2:
                line.append(f"→ 02/01 = {medians['pc02'] / medians['pc01']:.2f}")
            print(" ".join(line))

        # === (a) の突き合わせ ======================================================
        if api_cities:
            print("\n=== (a) 市区コードの突き合わせ ===")
            from sqlalchemy import text

            from house_search.db.session import get_engine

            with get_engine().connect() as conn:
                db_cities = {
                    row[0]: row[1]
                    for row in conn.execute(
                        text(
                            "SELECT jis_code, canonical_name FROM m_cities "
                            "WHERE jis_code IS NOT NULL AND left(jis_code, 2) = ANY(:prefs)"
                        ),
                        {"prefs": list(PREFECTURES)},
                    )
                }
            for area, rows in api_cities.items():
                api_ids = {str(r.get("id")): str(r.get("name")) for r in rows}
                missing = sorted(set(api_ids) - set(db_cities))
                name_diff = [
                    (jis, api_ids[jis], db_cities[jis])
                    for jis in sorted(set(api_ids) & set(db_cities))
                    if api_ids[jis] != db_cities[jis]
                ]
                print(
                    f"  {PREFECTURES[area]}: API {len(api_ids)}件 / "
                    f"DBに無いコード {len(missing)}件 {missing[:5]} / "
                    f"名称の食い違い {len(name_diff)}件 {name_diff[:3]}"
                )
    finally:
        if client is not None:
            client.close()
        if not args.from_cache:
            print(f"\n使用したリクエスト: {budget.used} / {budget.limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
