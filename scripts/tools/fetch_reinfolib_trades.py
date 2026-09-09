"""国交省「不動産情報ライブラリ」XIT001（不動産取引価格情報）の生応答を取得する。

課題#49 Step 2。売買の相場（㎡単価）を作る材料で、集計は
``build_buy_market_rates.py`` が保存済みの応答から行う。

    uv run python scripts/tools/fetch_reinfolib_trades.py --fetch   # 取得（約3分）
    uv run python scripts/tools/fetch_reinfolib_trades.py           # 保存済みを検査

⚠ **取得と集計を分けてある**（`build_market_rates.py` と同じ形）。集計の
試行錯誤で API を叩き直さないため。生応答は Git 管理外（`data/market_rates/raw/`）、
集計結果のCSVだけを Git へ入れる。

⚠ **ライセンスは公共データ利用規約 第1.0版**（出典表示のみ・商用可・再配布可）。
出典: 「不動産情報ライブラリ」（国土交通省）

⚠ **APIキーはヘッダでのみ送る。** URL・ログ・例外文・manifest のいずれにも出さない
（約款が第三者提供を禁じている）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from house_search.config.settings import Settings  # noqa: E402
from house_search.console import force_utf8_output  # noqa: E402
from house_search.scrape.prefectures import PREFECTURE_JIS  # noqa: E402

BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"
API_ID = "XIT001"
RAW = REPO / "data" / "market_rates" / "raw" / "reinfolib"
MANIFEST = REPO / "data" / "market_rates" / "reinfolib_manifest.json"

# 対象は全国47都道府県（JIS2桁 → 都道府県名）。
# ⚠ **2026-09-09 に4都県から全国へ広げた**（ユーザー判断）。API は都道府県単位で
# その四半期の全件を1リクエストで返すので市区ごとに叩く必要がなく、
# 47都道府県 × 4四半期 = 188リクエスト（10秒間隔で約31分）で済む。
# ⚠ **いまの掲載は4都県にしかないので相場比には効かない。** 効くのは検索パターンを
# 他県へ広げたときで、そのとき相場が無いと `market_rate_ratio` が未解決のまま
# 順位に効かない（例外にならない → 課題#49）
AREAS = {jis: name for name, jis in PREFECTURE_JIS.items()}

# 窓の長さ（四半期）。⚠ Step 1 の実測では1四半期でも 47/48 市区が n>=10 を
# 満たすが、不動産取引価格情報（priceClassification=01）は全体の22%しかない。
# 01 だけを採る判断（Step 3）に耐えるよう4四半期ぶん取る
WINDOW_QUARTERS = 4

# ⚠ レート制限は非公開（「基準期間内に多数のリクエストがあった場合にはアクセス制限」）。
# 10秒空け、429/403 を受けたら**即停止しリトライしない**
# （HOMES で「間隔を広げても上限は動かない」を実測済み → 課題#17）
INTERVAL_SEC = 10.0

# 起点（最新の公開四半期）を探すときに遡る上限。今日の四半期は未公開なのが普通で、
# 実測（2026-09-08）では 2026Q2 が 404・2026Q1 が最新だった
MAX_LOOKBACK = 6


class Budget:
    """リクエストの総量を数えて上限で止める。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> None:
        if self.used >= self.limit:
            raise SystemExit(f"リクエスト上限 {self.limit} に達したので止めます")
        self.used += 1


class RateLimited(SystemExit):
    """レート制限とみられる応答。⚠ リトライしない。"""


def raw_path(year: int, quarter: int, area: str) -> Path:
    return RAW / f"xit001_{year}q{quarter}_{area}.json"


def previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def quarter_of(date: dt.date) -> tuple[int, int]:
    return date.year, (date.month - 1) // 3 + 1


def window(year: int, quarter: int, count: int) -> list[tuple[int, int]]:
    """``(year, quarter)`` を最新として ``count`` 四半期ぶん遡った並びを返す。"""
    out = [(year, quarter)]
    while len(out) < count:
        out.append(previous_quarter(*out[-1]))
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """応答から明細の配列を取り出す。⚠ 包み方が変わったら早めに気づけるようにする。"""
    data = payload.get("data")
    if not isinstance(data, list):
        raise SystemExit(f"応答の data が配列ではありません: keys={sorted(payload)}")
    return data


def fetch_one(client: httpx.Client, year: int, quarter: int, area: str, budget: Budget) -> str:
    """1都県×1四半期を取得して保存する。戻り値は ``ok`` / ``not_found`` / ``cached``。

    ⚠ **404「検索結果がありません」は失敗に数えない**（2026-09-08 実測。
    未公開の四半期がこの形で返る）。例外にすると処理が止まり、握りつぶすと
    パス誤りの本物の404に気づけないので、本文で判別する（→ 課題#25 と同型）。
    """
    path = raw_path(year, quarter, area)
    if path.exists():
        print(f"  スキップ（取得済み）: {path.name}")
        return "cached"

    budget.take()
    params = {"year": str(year), "quarter": str(quarter), "area": area}
    started = time.monotonic()
    response = client.get(f"{BASE_URL}/{API_ID}", params=params)
    elapsed = time.monotonic() - started
    body = response.content

    if response.status_code in (429, 403):
        raise RateLimited(
            f"HTTP {response.status_code}：レート制限とみられます。"
            "⚠ リトライせず時間を空けて再実行してください（取得済みの分は残ります）"
        )
    if response.status_code == 404:
        message = ""
        if body:
            try:
                message = str(json.loads(body.decode("utf-8")).get("message", ""))
            except json.JSONDecodeError:
                message = body[:200].decode("utf-8", errors="replace")
        if "検索結果がありません" not in message:
            raise SystemExit(f"HTTP 404（想定外）: {message!r}")
        # ⚠ ファイルは書かない。空ファイルを置くと次回「取得済み」と誤認する
        print(f"  {AREAS[area]} {year}Q{quarter}: データ無し（未公開）")
        time.sleep(INTERVAL_SEC)
        return "not_found"

    response.raise_for_status()
    payload = json.loads(body.decode("utf-8"))
    count = len(_records(payload))
    # ⚠ 都県単位なら公開済みの四半期は必ず数千件ある（実測: 東京都1四半期 11,993件）。
    # 200 で 0件が返るのは想定外なので、黙って空ファイルを残さない
    if count == 0:
        print(f"  ⚠ {AREAS[area]} {year}Q{quarter}: HTTP 200 だが0件（保存しません）")
        time.sleep(INTERVAL_SEC)
        return "not_found"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    print(
        f"  {AREAS[area]} {year}Q{quarter}: {count:,}件 / "
        f"{len(body):,}B / {elapsed:.1f}秒 → {path.name}"
    )
    time.sleep(INTERVAL_SEC)
    return "ok"


def find_latest(client: httpx.Client, budget: Budget) -> tuple[int, int]:
    """最新の公開四半期を探す（東京都を代表に、今日の四半期から遡る）。

    ⚠ **固定値にしない。** 四半期ごとの定期実行（Step 8）でそのまま動くようにする。
    """
    year, quarter = quarter_of(dt.date.today())
    for _ in range(MAX_LOOKBACK):
        status = fetch_one(client, year, quarter, "13", budget)
        if status in ("ok", "cached"):
            return year, quarter
        year, quarter = previous_quarter(year, quarter)
    raise SystemExit(
        f"直近 {MAX_LOOKBACK} 四半期のいずれにもデータがありません。"
        "APIの仕様変更かキーの権限を疑ってください"
    )


def inspect(*, write_manifest: bool) -> int:
    """保存済みの応答を検査する（ネットワーク不要）。

    ⚠ **窓が埋まっていることを機械的に確かめる。** 1ファイル欠けても集計は
    走ってしまい、その都県の相場だけが薄いまま気づけない（→ 課題#36 と同型）。
    """
    files = sorted(RAW.glob("xit001_*.json"))
    if not files:
        print(f"⚠ 保存済みの応答がありません: {RAW}")
        print("  `--fetch` で取得してください")
        return 1

    entries: list[dict[str, Any]] = []
    by_quarter: dict[tuple[int, int], set[str]] = {}
    for path in files:
        stem = path.stem.removeprefix("xit001_")
        period, area = stem.split("_")
        year, quarter = int(period[:4]), int(period[-1])
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = _records(payload)
        by_quarter.setdefault((year, quarter), set()).add(area)
        entries.append(
            {
                "name": path.name,
                "area": area,
                "pref": AREAS.get(area, "?"),
                "year": year,
                "quarter": quarter,
                "records": len(rows),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )

    total = sum(int(e["records"]) for e in entries)
    print(f"保存済み: {len(entries)}ファイル / 明細 {total:,}件")
    for (year, quarter), areas in sorted(by_quarter.items(), reverse=True):
        rows = sum(
            int(e["records"]) for e in entries if (e["year"], e["quarter"]) == (year, quarter)
        )
        missing = sorted(set(AREAS) - areas)
        mark = "" if not missing else f"  ⚠ 欠け: {[AREAS[a] for a in missing]}"
        print(f"  {year}Q{quarter}: {len(areas)}/{len(AREAS)}都道府県 / {rows:,}件{mark}")

    complete = [q for q, areas in by_quarter.items() if areas == set(AREAS)]
    print(f"全都道府県そろっている四半期: {len(complete)} / 窓の想定 {WINDOW_QUARTERS}")

    if write_manifest:
        MANIFEST.write_text(
            json.dumps(
                {
                    "source": "mlit_reinfolib_XIT001",
                    "license": "公共データ利用規約（第1.0版）",
                    "attribution": "「不動産情報ライブラリ」（国土交通省）",
                    "inspected_on": dt.date.today().isoformat(),
                    "files": sorted(entries, key=lambda e: e["name"]),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"版を記録しました: {MANIFEST.relative_to(REPO)}")

    if len(complete) < WINDOW_QUARTERS:
        print(f"⚠ 窓が {WINDOW_QUARTERS} 四半期に足りません。`--fetch` を実行してください")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    # ⚠ 先に UTF-8 へ付け替える。cp932 のままだと ⚠ を含む警告の print が
    # UnicodeEncodeError になり、**取得は成功しているのに処理全体が落ちる**
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="APIから取得する（既存は飛ばす）")
    parser.add_argument(
        "--quarters", type=int, default=WINDOW_QUARTERS, help="窓の長さ（四半期・既定4）"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=len(AREAS) * WINDOW_QUARTERS + MAX_LOOKBACK,
        help="リクエストの上限",
    )
    args = parser.parse_args(argv)

    if not args.fetch:
        return inspect(write_manifest=False)

    RAW.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.limit)
    key = Settings().require_reinfolib_api_key()
    client = httpx.Client(
        # ⚠ キーはヘッダでのみ送る（URLに載せない・ログに出さない）
        headers={"Ocp-Apim-Subscription-Key": key},
        timeout=120.0,
    )
    try:
        print("最新の公開四半期を探しています")
        latest = find_latest(client, budget)
        print(f"  → 最新は {latest[0]}Q{latest[1]}")
        today_q = quarter_of(dt.date.today())
        if window(*today_q, 4)[-1] > latest:
            # ⚠ 3四半期以上さかのぼるのは公開が止まっている疑い。黙って古い相場を作らない
            print(
                f"  ⚠ 今日の四半期 {today_q[0]}Q{today_q[1]} から離れています。"
                "公開状況を確認してください"
            )

        targets = window(*latest, args.quarters)
        print(f"\n取得: {len(AREAS)}都道府県 × {len(targets)}四半期")
        failures: list[str] = []
        for year, quarter in targets:
            for area in AREAS:
                status = fetch_one(client, year, quarter, area, budget)
                if status == "not_found":
                    failures.append(f"{AREAS[area]} {year}Q{quarter}")
        if failures:
            # ⚠ 「データ無し」は HTTP 失敗ではないが、窓の中で起きたら集計が薄くなる
            print(f"\n⚠ データが無かった組み合わせ {len(failures)}件: {', '.join(failures)}")
    finally:
        client.close()
        print(f"\n使用したリクエスト: {budget.used} / {args.limit}")

    print()
    return inspect(write_manifest=True)


if __name__ == "__main__":
    raise SystemExit(main())
