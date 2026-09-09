"""売買の相場CSV（市区 × 単価区分）を保存済みの XIT001 応答から作る（課題#49 Step 3）。

    uv run python scripts/tools/build_buy_market_rates.py

⚠ **ネットワークに出ない。** 取得は `fetch_reinfolib_trades.py --fetch` の担当で、
集計の試行錯誤で API を叩き直さないよう分けてある（`build_market_rates.py` と同じ形）。

出典: 「不動産情報ライブラリ」（国土交通省）／公共データ利用規約（第1.0版）

決めたこと（いずれも 2026-09-08 の実測に基づく。→ 課題#49 Step 3）:

* **統計は中央値。** マンション㎡単価は最大 1億円/㎡ という外れ値を含むので、
  平均だと1件で市区の相場が壊れる。
* **窓は保存済みの全四半期（4期）。** 1四半期だと市区×種別で n>=10 を満たすのが
  中古マンションで 151/245 しかない。4期で 174 になる。
* ⚠⚠ **01（不動産取引価格情報）と 02（成約価格情報）を混ぜない。** 同一市区で
  比べても水準が系統的に違い、しかも**種別で向きが逆**（成約÷取引の中央は
  マンション 0.914 / 戸建て 1.082）。混ぜると市区ごとに目盛りが変わる
  （→ ADR 0022 決定2 と同じ理由）。
* **主は種別ごとに「n>=10 を満たす市区が多い方」**を採り、無いセルだけ他方で補う
  （マンション=02 で170市区 / 戸建て=01 で219市区）。⚠ 市区の**相対順位**は
  01/02 で r=0.957（マンション）・0.982（戸建て）とほぼ一致するので、
  どちらを主にしても順位への影響は小さい。決め手はカバレッジのほう。
  ⚠ **補完はほとんど効かない**（実測でマンション0件・戸建て2件）。
  「01+02 の和で n>=10 になる市区」と「他方が単独で n>=10 の市区」は別物で、
  和で届くセルは**どちらも単独では薄い**ことが多いため。
* **どちらの区分で測った相場かは `stat_basis` が持つ**（行が自己記述する）。
* ⚠ **n<10 のセルは書かない。** 0 で埋めると「相場ちょうど」と区別がつかない
  （→ ADR 0021 決定4）。書かなければ NULL＝未解決として metric から外れる。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from house_search.console import force_utf8_output  # noqa: E402

RAW = REPO / "data" / "market_rates" / "raw" / "reinfolib"
OUT = REPO / "data" / "market_rates" / "buy_rates.csv"
SOURCE = "mlit_library"

# 応答の Type → 種別ファミリ。⚠ 「宅地(土地)」は土地（Phase 9）の相場なので今は使わない
TYPE_MANSION = "中古マンション等"
TYPE_KODATE = "宅地(土地と建物)"

# 応答の PriceCategory → stat_basis。⚠ 自由文字列にしない（どちらで測ったかを
# 後から集計できないと、水準差の検証そのものができなくなる → ADR 0022 決定2）
CATEGORY_01 = "不動産取引価格情報"
CATEGORY_02 = "成約価格情報"
STAT_01 = "trade_unit_price_01"
STAT_02 = "trade_unit_price_02"
STAT_OF = {CATEGORY_01: STAT_01, CATEGORY_02: STAT_02}

# 主に使う価格区分（種別ごと）。⚠ 実測で「補完が最小になる方」を選んである
PRIMARY_STAT = {"MANSION_BUY": STAT_02, "KODATE_BUY": STAT_01}

# 単価の区分。マンションは専有面積、戸建ては土地と延床の両方を作る。
# ⚠ 戸建ての土地と延床は市区順位が r=0.985 で一致するが値域の安定は延床が上
# （土地は 7,447〜5,692,308 円/㎡ で765倍、延床は132倍）。どちらを採点に使うかは
# 判別力を測ってから決めるので、両方書いておく（segment が違うので併存できる）。
SEG_AREA = "AREA_SQM"
SEG_LAND = "LAND_SQM"
SEG_FLOOR = "FLOOR_SQM"

# 窓の長さ（四半期）。⚠ `fetch_reinfolib_trades.py` と同じ値にする。
# 片方だけ変えると、取っていない四半期を待つか古い期を混ぜるかになる
# （`tests/test_buy_market_window.py` が一致を固定している）
WINDOW_QUARTERS = 4

MIN_SAMPLES = 10
# ⚠ 相場としてありえない㎡単価を弾く。桁を1つ間違えたときに気づけるのはここだけ。
# 実測の市区中央値は 7,447〜5,692,308 円/㎡ に収まっていた
MIN_RATE = 5_000
MAX_RATE = 20_000_000
# ⚠ 数値化できない行が増えたら止める。応答の形が変わった合図
MAX_EXCLUDED_RATIO = 0.05


def _num(value: object) -> float | None:
    """数値化する。⚠ 応答の値はすべて文字列なので float に通す。"""
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _iter_records(quarters: int = WINDOW_QUARTERS) -> tuple[list[dict[str, Any]], list[str]]:
    """保存済みの応答を読む。戻り値は明細と、読んだ四半期の並び。

    ⚠⚠ **新しい方から `quarters` 期だけ読む。** 定期実行（四半期ごと）に載せると
    保存済みの四半期は増え続ける（`fetch --fetch` は「既存は飛ばす」ので古い
    ファイルが残る）。絞らないと窓が単調に広がり、**古い相場が混ざったまま
    行数と sample_count だけが増える**。⚠ 例外にならず「たくさんデータが取れた」
    ようにしか見えないので、水準のずれに気づく手立てがない。

    ⚠ **窓の外のファイルは消さない。** 原典を残しておけば過去の窓で作り直せる
    （ハザード・家賃相場と同じ「原典は残し、生成物だけ作り直す」）。
    """
    files = sorted(RAW.glob("xit001_*.json"))
    if not files:
        raise SystemExit(
            f"保存済みの応答がありません: {RAW}\n"
            "  先に `fetch_reinfolib_trades.py --fetch` を実行してください"
        )
    by_period: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        by_period[path.stem.removeprefix("xit001_").split("_")[0]].append(path)

    ordered = sorted(by_period)
    periods = ordered[-quarters:]
    dropped = ordered[: -len(periods)] if len(ordered) > len(periods) else []
    if dropped:
        # ⚠ 黙って落とさない。窓の外に何期あるかは、公開の遅れや窓の設定ミスに
        #    気づく材料になる（消してはいないので `--quarters` で読み直せる）
        print(f"  窓の外の四半期を読み飛ばしました: {', '.join(dropped)}")

    rows: list[dict[str, Any]] = []
    for period in periods:
        for path in sorted(by_period[period]):
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = payload.get("data")
            if not isinstance(data, list):
                raise SystemExit(f"{path.name}: 応答の data が配列ではありません")
            rows.extend(data)
    return rows, periods


def _period_label(periods: list[str]) -> str:
    """窓を表す期間ラベル（``2025Q2-2026Q1``）。

    ⚠ **採点は `ORDER BY period DESC` で最新を1つ採る**（→ persist.py）ので、
    次の窓が辞書順で大きくなる形にしておく。
    """

    def fmt(raw: str) -> str:
        return f"{raw[:4]}Q{raw[-1]}"

    return f"{fmt(periods[0])}-{fmt(periods[-1])}" if len(periods) > 1 else fmt(periods[0])


def collect(
    quarters: int = WINDOW_QUARTERS,
) -> tuple[dict[tuple[str, str, str, str], list[float]], dict[str, str], list[str]]:
    """明細を「ファミリ×市区×区分×統計基準」のセルへ畳む。"""
    rows, periods = _iter_records(quarters)
    cells: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    names: dict[str, str] = {}
    seen: Counter[str] = Counter()
    excluded: Counter[str] = Counter()

    for row in rows:
        type_name = str(row.get("Type"))
        if type_name == TYPE_MANSION:
            family, segments = "MANSION_BUY", ((SEG_AREA, "Area"),)
        elif type_name == TYPE_KODATE:
            family, segments = "KODATE_BUY", ((SEG_LAND, "Area"), (SEG_FLOOR, "TotalFloorArea"))
        else:
            continue

        stat = STAT_OF.get(str(row.get("PriceCategory")))
        if stat is None:
            excluded[f"{family}/価格区分が未知"] += 1
            continue
        city = str(row.get("MunicipalityCode") or "")
        name = str(row.get("Municipality") or "")
        if len(city) != 5:
            excluded[f"{family}/市区コード不正"] += 1
            continue
        names.setdefault(city, name)
        price = _num(row.get("TradePrice"))

        for segment, area_key in segments:
            seen[f"{family}/{segment}"] += 1
            area = _num(row.get(area_key))
            if price is None or area is None:
                excluded[f"{family}/{segment}"] += 1
                continue
            cells[(family, city, segment, stat)].append(price / area)

    print("=== 明細 ===")
    print(f"  読み込み: {len(rows):,}行 / 四半期 {', '.join(periods)}")
    for key in sorted(seen):
        ex = excluded.get(key, 0)
        ratio = ex / seen[key] if seen[key] else 0.0
        print(f"  {key}: 対象 {seen[key]:,} / 単価を出せず {ex:,} ({ratio:.2%})")
        # ⚠ 除外が増えたら止める。黙って薄い相場を作ると、その市区だけ
        #    採点軸が1本減った状態で順位が出る（例外にならない）
        if ratio > MAX_EXCLUDED_RATIO:
            raise SystemExit(
                f"{key} の除外率が {ratio:.1%} で想定（{MAX_EXCLUDED_RATIO:.0%}）を超えました。"
                "応答の形が変わった疑いがあります"
            )
    segments = {SEG_AREA, SEG_LAND, SEG_FLOOR}
    for key in sorted(k for k in excluded if k.split("/")[-1] not in segments):
        print(f"  ⚠ {key}: {excluded[key]:,}件を捨てました")
    return cells, names, periods


def build(
    period: str | None, acquired_on: str, quarters: int = WINDOW_QUARTERS
) -> tuple[list[dict[str, object]], str]:
    """セルから相場の行を作る。主の区分で足りなければ他方で補完する。

    戻り値は行と、実際に読んだ窓から作った期間ラベル。
    ⚠ **ラベルは `collect()` が返す窓から作る。** 保存済みの全ファイルから作ると、
    読んでいない古い四半期までラベルに含まれて「どの窓の相場か」が嘘になる。
    """
    cells, names, periods = collect(quarters)
    label = period or _period_label(periods)
    out: list[dict[str, object]] = []
    stats: Counter[str] = Counter()

    keys = {(family, city, segment) for (family, city, segment, _) in cells}
    for family, city, segment in sorted(keys):
        primary = PRIMARY_STAT[family]
        fallback = STAT_01 if primary == STAT_02 else STAT_02
        chosen: tuple[str, list[float]] | None = None
        for stat in (primary, fallback):
            values = cells.get((family, city, segment, stat), [])
            if len(values) >= MIN_SAMPLES:
                chosen = (stat, values)
                break
        if chosen is None:
            # ⚠ 薄いセルは書かない（NULL＝未解決。0 で埋めると「相場ちょうど」と混ざる）
            stats[f"{family}/{segment}/薄いので不採用"] += 1
            continue
        stat, values = chosen
        rate = round(statistics.median(values))
        if not (MIN_RATE <= rate <= MAX_RATE):
            raise SystemExit(
                f"{names.get(city, city)}({city}) {family}/{segment} の相場が想定外です: "
                f"{rate:,}円/㎡（{MIN_RATE:,}〜{MAX_RATE:,} を想定）"
            )
        stats[f"{family}/{segment}/{stat}"] += 1
        out.append(
            {
                "family": family,
                "city_jis": city,
                "city_name": names.get(city, ""),
                "segment": segment,
                "rate_value": rate,
                "sample_count": len(values),
                "source": SOURCE,
                "stat_basis": stat,
                "period": label,
                "acquired_on": acquired_on,
            }
        )

    print("\n=== 採用したセル ===")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")
    # ⚠ 恒等式。採用したセルの数と書き出す行数が食い違ったら数え方が壊れている
    adopted = sum(v for k, v in stats.items() if not k.endswith("薄いので不採用"))
    if adopted != len(out):
        raise SystemExit(f"採用 {adopted} と行数 {len(out)} が一致しません")
    return out, label


def main(argv: list[str] | None = None) -> int:
    # ⚠ 先に UTF-8 へ付け替える。cp932 のままだと ⚠ を含む警告の print が
    # UnicodeEncodeError になり、**取得は成功しているのに処理全体が落ちる**
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", default=None, help="期間ラベル（既定は窓から作る）")
    parser.add_argument(
        "--quarters",
        type=int,
        default=WINDOW_QUARTERS,
        help="窓の長さ（四半期・既定4）。⚠ fetch 側と揃えること",
    )
    args = parser.parse_args(argv)

    # ⚠ 期間ラベルは build() が「実際に読んだ窓」から返す。保存済みの全ファイルから
    #    作ると、読んでいない古い四半期までラベルに載って嘘になる（JSON の二度読みも
    #    起きない。collect() は build() の中で1回だけ呼ばれる）
    rows, period = build(args.period, dt.date.today().isoformat(), args.quarters)
    if not rows:
        raise SystemExit("相場が1件も作れませんでした（応答の形の変更を疑う）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    cities = {r["city_jis"] for r in rows}
    print(f"\n生成: {OUT.relative_to(REPO)} / {len(rows):,}行 / {len(cities)}市区 / 期間 {period}")
    print("  ⚠ Git 管理下の生成物です。差分を見てからコミットしてください")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
