"""ODPT の実レスポンスを取得して構造を報告する（Phase 5D 第0歩・使い捨てのスパイク）。

⚠ **これは調査用スクリプトであって本実装ではない。** 通勤時間を実ダイヤから算出する
（→ Phase 5D）にあたり、パーサとDDLを書く前に**実データでスキーマを確定させる**ために使う。
このプロジェクトは推測でパラメータ名を書いて「HTTP 200 のまま0件」を4回踏んでいるので、
先に実物を見る手順を挟む（→ 課題#29・ADR 0015・ADR 0016）。

答えを出したい不確実性:

1. ``odpt:TrainTimetable`` の実キー名と構造（``odpt:arrivalTime`` は**任意項目**なので欠落率）
2. ``odpt:Station`` に緯度経度が入っているか（駅の照合のタイブレークに使えるか）
3. ``odpt:calendar`` の値体系（平日／土休日の表し方が事業者共通か）
4. 直通列車の表現（``odpt:previousTrainTimetable`` / ``odpt:nextTrainTimetable``）
5. 駅時刻表しか無い5社の ``odpt:StationTimetable`` に種別・行先・列車IDがどれだけあるか
6. レートリミット・1回の最大件数・ページングの有無（レスポンスヘッダを出す）
7. 東京メトロが同じトークン・同じホストで取れるか
8. JR東日本の路線分割の粒度（駅データ.jp の ``line_cd`` との対応の作りやすさ）

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/odpt_spike.py --operator Toei
    uv run python scripts/tools/odpt_spike.py --operator Toei --railway odpt.Railway:Toei.Mita
    uv run python scripts/tools/odpt_spike.py --operator TokyoMetro
    uv run python scripts/tools/odpt_spike.py --operator JR-East --skip-timetable

取得した生データは ``data/odpt/raw/`` へ保存する（Git管理外。基本ライセンスが
第三者への生データ再配布を制限しているため）。
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "odpt" / "raw"

# ⚠ ホストは2つある可能性がある。旧「東京公共交通オープンデータチャレンジ」の
# api-tokyochallenge.odpt.org から api.odpt.org へ移行した経緯があり、
# 事業者によって片方にしか無いことがある。既定で両方試して、どちらが返すかを記録する。
API_HOSTS = (
    "https://api.odpt.org/api/v4",
    "https://api-tokyochallenge.odpt.org/api/v4",
)

# 取得間隔（秒）。レートリミットが公開されていないので保守的に置く。
REQUEST_INTERVAL_SEC = 2.0


class SpikeError(RuntimeError):
    """調査を続けられない状態。**握りつぶさずに落とす**ためのもの。"""


def load_consumer_key() -> str:
    """``.env`` から ODPT のトークンを読む。

    ⚠ トークンは実キーなので**引数で渡さない**（プロセス一覧とシェル履歴に残る）。
    """
    values = dotenv_values(PROJECT_ROOT / ".env")
    key = (values.get("ODPT_CONSUMER_KEY") or "").strip()
    if not key or key == "your-consumer-key-here":
        raise SpikeError(
            "ODPT_CONSUMER_KEY が .env に設定されていません。\n"
            "https://developer.odpt.org/ で登録してトークンを発行し、.env へ転記してください"
        )
    return key


def fetch(
    data_type: str, params: dict[str, str], key: str, hosts: tuple[str, ...] = API_HOSTS
) -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    """1つのデータ種別を取得する。

    返すのは ``(レコード列, 使えたホスト, レスポンスヘッダ)``。

    ⚠ **0件を正常として返さない。** ODPT は条件に合うものが無いと ``[]`` を
    HTTP 200 で返すため、そのまま通すと「取れているつもり」になる（このプロジェクトが
    繰り返し踏んでいる形）。呼び出し側が件数を必ず見られるようにヘッダごと返す。
    """
    last_error: str | None = None
    for host in hosts:
        url = f"{host}/{data_type}"
        query = {**params, "acl:consumerKey": key}
        try:
            response = httpx.get(url, params=query, timeout=60.0)
        except httpx.HTTPError as exc:  # 接続そのものが失敗した
            last_error = f"{host}: {exc}"
            continue
        if response.status_code == 200:
            payload = response.json()
            if not isinstance(payload, list):
                raise SpikeError(
                    f"{url} の応答が配列ではありません（型={type(payload).__name__}）。"
                    f"先頭200字: {response.text[:200]}"
                )
            return payload, host, dict(response.headers)
        last_error = f"{host}: HTTP {response.status_code} {response.text[:200]}"
        time.sleep(REQUEST_INTERVAL_SEC)
    raise SpikeError(f"{data_type} を取得できませんでした。最後のエラー: {last_error}")


def key_coverage(records: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    """レコード群のキーごとの出現数と値の例を返す。

    **任意項目がどれだけ欠けるか**を見るのが目的。``odpt:arrivalTime`` のように
    仕様上は任意でも実データではほぼ入っている、という判断をここで行う。
    """
    counter: collections.Counter[str] = collections.Counter()
    samples: dict[str, str] = {}
    for record in records:
        for field, value in record.items():
            counter[field] += 1
            if field not in samples:
                samples[field] = json.dumps(value, ensure_ascii=False)[:80]
    return [(field, count, samples[field]) for field, count in counter.most_common()]


def report_keys(title: str, records: list[dict[str, Any]]) -> None:
    """キーの充足率を表にして出す。"""
    total = len(records)
    print(f"\n--- {title}（{total}件）---")
    if total == 0:
        print("  ⚠ 0件。条件が合っていないか、この事業者は当該データを提供していない")
        return
    for field, count, sample in key_coverage(records):
        rate = count / total * 100
        flag = "" if count == total else "  ← 欠落あり"
        print(f"  {rate:5.1f}%  {field:<40} 例: {sample}{flag}")


def value_distribution(records: list[dict[str, Any]], field: str, limit: int = 10) -> None:
    """特定キーの値の分布を出す（``odpt:calendar`` の値体系を知るため）。"""
    counter = collections.Counter(
        json.dumps(record.get(field), ensure_ascii=False) for record in records
    )
    print(f"\n  [{field}] の値の分布（上位{limit}）")
    for value, count in counter.most_common(limit):
        print(f"    {count:6d}  {value}")


def save(records: list[dict[str, Any]], operator: str, name: str) -> Path:
    """生データを保存する。一時ファイルへ書いてから置換する。

    ⚠ 直接上書きすると、取得に失敗したとき**既にある正常なデータを空で壊す**。
    """
    directory = RAW_DIR / operator
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def inspect_train_timetable(records: list[dict[str, Any]]) -> None:
    """列車時刻表の停車イベント（``odpt:trainTimetableObject``）を掘る。

    ⚠ **ここが Phase 5D 第1段の素材そのもの。** 連続する停車駅のペアを辺にすると、
    各停は隣接駅の辺、急行は駅を飛ばす辺になり、種別を持たずに優等列車を表現できる。
    そのために「出発時刻・到着時刻・駅がどれだけ揃っているか」を確かめる。
    """
    object_key = None
    for candidate in ("odpt:trainTimetableObject", "odpt:TrainTimetableObject"):
        if any(candidate in record for record in records):
            object_key = candidate
            break
    if object_key is None:
        print("\n  ⚠ 停車イベントの配列キーが見つからない。上のキー一覧から実名を確認すること")
        return

    stops = [stop for record in records for stop in record.get(object_key, [])]
    report_keys(f"停車イベント {object_key}", stops)

    # 区間（連続する2停車）を作れる列車がどれだけあるかを数える。
    usable = 0
    pairs = 0
    for record in records:
        sequence = record.get(object_key, [])
        times = [
            stop.get("odpt:departureTime") or stop.get("odpt:arrivalTime") for stop in sequence
        ]
        stations = [
            stop.get("odpt:departureStation") or stop.get("odpt:arrivalStation")
            for stop in sequence
        ]
        valid = sum(
            1
            for i in range(len(sequence) - 1)
            if times[i] and times[i + 1] and stations[i] and stations[i + 1]
        )
        pairs += valid
        if valid:
            usable += 1
    print(f"\n  区間を作れる列車: {usable}/{len(records)}本 / 取り出せる区間: {pairs}件")
    print("  ※ 区間が0なら第1段は成立しない。キー名を実データで確認し直すこと")


def main() -> int:
    parser = argparse.ArgumentParser(description="ODPT の実レスポンスを調べる（Phase 5D 第0歩）")
    parser.add_argument("--operator", default="Toei", help="事業者（例: Toei / TokyoMetro / JR-East）")
    parser.add_argument("--railway", default=None, help="列車時刻表を絞る路線ID（既定は先頭の路線）")
    parser.add_argument("--skip-timetable", action="store_true", help="時刻表を取らず駅と路線だけ見る")
    args = parser.parse_args()

    key = load_consumer_key()
    operator_id = f"odpt.Operator:{args.operator}"
    print(f"事業者: {operator_id}")

    railways, host, headers = fetch("odpt:Railway", {"odpt:operator": operator_id}, key)
    print(f"応答したホスト: {host}")
    interesting = {
        k: v
        for k, v in headers.items()
        if "rate" in k.lower() or "limit" in k.lower() or k.lower() in {"date", "content-length"}
    }
    print(f"レスポンスヘッダ（レート制限の手がかり）: {interesting}")
    save(railways, args.operator, "railway")
    report_keys("路線 odpt:Railway", railways)
    if not railways:
        raise SpikeError("路線が0件。事業者IDが違うか、この事業者はデータを提供していない")

    time.sleep(REQUEST_INTERVAL_SEC)
    stations, _, _ = fetch("odpt:Station", {"odpt:operator": operator_id}, key)
    save(stations, args.operator, "station")
    report_keys("駅 odpt:Station", stations)

    if args.skip_timetable:
        return 0

    railway_id = args.railway or railways[0].get("owl:sameAs")
    print(f"\n列車時刻表を取る路線: {railway_id}")

    time.sleep(REQUEST_INTERVAL_SEC)
    try:
        timetables, _, _ = fetch("odpt:TrainTimetable", {"odpt:railway": railway_id}, key)
    except SpikeError as exc:
        print(f"  ⚠ 列車時刻表を取得できなかった: {exc}")
        timetables = []
    if timetables:
        save(timetables, args.operator, "train_timetable")
        report_keys("列車時刻表 odpt:TrainTimetable", timetables)
        value_distribution(timetables, "odpt:calendar")
        value_distribution(timetables, "odpt:trainType")
        inspect_train_timetable(timetables)

    time.sleep(REQUEST_INTERVAL_SEC)
    try:
        station_timetables, _, _ = fetch(
            "odpt:StationTimetable", {"odpt:railway": railway_id}, key
        )
    except SpikeError as exc:
        print(f"  ⚠ 駅時刻表を取得できなかった: {exc}")
        station_timetables = []
    if station_timetables:
        save(station_timetables, args.operator, "station_timetable")
        report_keys("駅時刻表 odpt:StationTimetable", station_timetables)
        entries = [
            entry
            for record in station_timetables
            for entry in record.get("odpt:stationTimetableObject", [])
        ]
        report_keys("駅時刻表の発車 odpt:stationTimetableObject", entries)

    print(f"\n生データの保存先: {RAW_DIR / args.operator}")
    return 0


if __name__ == "__main__":
    # ⚠ 日本語Windowsのコンソールは cp932 なので、stdout と stderr の**両方**を
    # UTF-8 にしないとエラーメッセージが文字化けして読めない（実際に化けた）。
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except SpikeError as error:
        print(f"\nエラー: {error}", file=sys.stderr)
        raise SystemExit(1) from error
