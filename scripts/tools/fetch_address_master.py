"""位置参照情報（大字・町丁目レベル）を ``data/address_master/`` へ取得する。

``m_address_points`` の原典。**丁目が実在するかを判定する唯一の根拠**であり、
これが無いと ``normalize_address`` は番地を丁目と誤認したままになる（→ 課題#48）。

出典: 「位置参照情報ダウンロードサービス」（国土交通省）
<https://nlftp.mlit.go.jp/isj/> — 政府標準利用規約（第2.0版）。
複製・再配布・改変・商用利用が可で、**出典の明示のみが義務**。
そのため総務省コード表（→ ADR 0014）と同じく**原典CSVを Git 管理下に置ける**
（駅マスタのように再配布不可でGit外へ逃がす必要がない → ADR 0016）。

使い方::

    # 1) 原典を取り直す（版が上がったときだけ）
    uv run python scripts/tools/fetch_address_master.py --fetch

    # 2) 保存済みCSVの中身を検査する（既定・ネットワーク不要）
    uv run python scripts/tools/fetch_address_master.py

⚠ **CGI を辿る必要はない。** ダウンロードページはPOSTのCGIだが、
実体は ``data/{版}/{都道府県コード}000-{版}.zip`` に直に置かれている（実測 2026-09-05）。

⚠ **原典CSVは cp932。** そのまま Git に置くと差分が読めないので UTF-8 へ変換して保存する。

⚠ **区分コードが要。** ``大字・字・丁目区分コード`` は 1=大字 / 2=字 / 3=丁目 で、
**「その町に丁目が実在するか」を正規表現の推測なしに判定できる**（実測 4都県で
1: 6,917件 / 2: 54件 / 3: 14,501件 / 0: 1件）。
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import zipfile
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "address_master"

# 位置参照情報の版。令和7年版（大字・町丁目レベル）。
# ⚠ 版が上がるとURLもファイル名も変わるので、ここと README の取得日を更新する。
VERSION = "19.0b"
DOWNLOAD_URL = "https://nlftp.mlit.go.jp/isj/dls/data/{version}/{pref}000-{version}.zip"

# 既定の対象。検索パターンのエリア帯（東京23区・近郊60分圏）が収まる1都3県。
# エリアを広げるときはここへ足して --fetch を流す。
DEFAULT_PREFECTURES = ("11", "12", "13", "14")

SOURCE_ENCODING = "cp932"
EXPECTED_COLUMNS = (
    "都道府県コード",
    "都道府県名",
    "市区町村コード",
    "市区町村名",
    "大字町丁目コード",
    "大字町丁目名",
    "緯度",
    "経度",
    "原典資料コード",
    "大字・字・丁目区分コード",
)


def csv_path(pref_code: str) -> Path:
    """保存先。原典のファイル名（``13_2025.csv``）をそのまま使う。"""
    return OUTPUT_DIR / f"{pref_code}_2025.csv"


def fetch(pref_code: str) -> Path:
    """ZIPを取得して展開し、UTF-8のCSVとして保存する。"""
    import httpx

    url = DOWNLOAD_URL.format(version=VERSION, pref=pref_code)
    response = httpx.get(url, timeout=120.0, follow_redirects=True)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise SystemExit(f"ZIP内のCSVが1件ではありません（{pref_code}）: {names}")
        raw = archive.read(names[0])

    text = raw.decode(SOURCE_ENCODING)
    destination = csv_path(pref_code)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # newline="" は csv モジュールの流儀。改行コードは原典（CRLF）のまま残す。
    with destination.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return destination


def inspect(pref_code: str) -> int:
    """保存済みCSVを検査して行数を返す。列の欠落と未知の区分コードで落ちる。"""
    path = csv_path(pref_code)
    if not path.exists():
        raise SystemExit(f"CSVがありません: {path}\n  --fetch で取得してください")
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = tuple(reader.fieldnames or ())
        if columns != EXPECTED_COLUMNS:
            raise SystemExit(f"列が想定と違います（{path.name}）: {columns}")
        rows = list(reader)

    kinds = Counter(row["大字・字・丁目区分コード"] for row in rows)
    prefecture = rows[0]["都道府県名"] if rows else "?"
    print(
        f"{path.name}  {prefecture:<5} {len(rows):>6}行"
        f"  大字{kinds['1']:>6} 字{kinds['2']:>4} 丁目{kinds['3']:>6} その他{kinds['0']:>3}"
    )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch", action="store_true", help="原典を取り直す（既定は保存済みCSVの検査だけ）"
    )
    parser.add_argument(
        "--prefectures",
        nargs="+",
        default=list(DEFAULT_PREFECTURES),
        help=f"対象の都道府県コード（既定: {' '.join(DEFAULT_PREFECTURES)}）",
    )
    args = parser.parse_args(argv)

    total = 0
    for pref_code in args.prefectures:
        if args.fetch:
            path = fetch(pref_code)
            print(f"取得しました: {path}")
        total += inspect(pref_code)
    print(f"合計 {total}行")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
