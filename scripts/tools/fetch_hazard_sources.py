"""ハザード評価の原典（洪水・土砂災害・丁目境界）を取得して検査する。

出典:
- 「国土数値情報（洪水浸水想定区域データ A31）」（国土交通省）
  <https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A31-v4_0.html>
- 「国土数値情報（土砂災害警戒区域データ A33）」（国土交通省）
  <https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A33-v1_4.html>
  — 対象4都県はいずれも「オープンデータとしての利用可（商用利用可・再配信可）」
- 「令和2年国勢調査 町丁・字等別境界データ」（総務省統計局・e-Stat）
  <https://www.e-stat.go.jp/gis> — 政府標準利用規約（第2.0版）

⚠ **原典は Git 管理外**（``data/hazard_sources/``）。A31 だけで約1GB あり、
リポジトリを恒久的に太らせるため。再配布が禁じられているからではない
（住所マスタ → ADR 0020 とは事情が違う）。「いつ時点の版か」は本スクリプトが
書く ``manifest.json`` の SHA256 と取得日で担保する。

⚠ **3データセットとも直リンクで取れる**（CGI を辿らない）。位置参照情報と同じ。

⚠ **A31 は 1次メッシュ単位**（都道府県別ではない）。対象82市区に必要なのは
**5339・5340 の2つだけ**で、``10``（洪水予報河川・水位周知河川）と
``20``（その他の河川）の**両方**が要る。片方だけでも件数は出て正常終了するため、
欠けていることに気づけない。

⚠ **A31 は SHP 版を使う**。GML 版（232MB）の方が小さいが ``gml:Curve`` →
``Surface`` → Feature の3段参照で、自前パースの誤りが静かに入る。
GeoJSON 版は 692MB とかえって大きい。

引数なしで実行すると**検査だけ**を行う（ネットワーク不要）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "hazard_sources"

# 対象82市区が必要とする1次メッシュ（丁目代表点の緯度経度から算出。→ 課題#46）。
# ⚠ 5439・5440 は不要。増やすときは m_address_points から測り直すこと。
A31_MESHES = ("5339", "5340")
# 10=洪水予報河川・水位周知河川 / 20=その他の河川。⚠ 両方を読んで worst をマージする。
A31_RIVER_KINDS = ("10", "20")
A31_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/A31/A31-22/A31-22_{kind}_{mesh}_SHP.zip"

# 対象4都県。⚠ A33 は都道府県別（A31 と違いメッシュではない）。
PREFECTURES = ("11", "12", "13", "14")
A33_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/A33/A33-23/A33-23_{pref}_GML.zip"

# e-Stat の境界データ。datum=2011 で JGD2011（A31・A33 と同じ測地系）になる。
# ⚠ ここを 2000 にすると測地系が混ざるが、ずれは数メートルなので**気づけない**。
BOUNDARY_URL = (
    "https://www.e-stat.go.jp/gis/statmap-search/data"
    "?dlserveyId=A002005212020&code={pref}&coordSys=1&format=shape"
    "&downloadType=5&datum=2011"
)

MANIFEST = DATA_DIR / "manifest.json"


def a31_path(kind: str, mesh: str) -> Path:
    return DATA_DIR / f"A31-22_{kind}_{mesh}_SHP.zip"


def a33_path(pref: str) -> Path:
    return DATA_DIR / f"A33-23_{pref}_GML.zip"


def boundary_path(pref: str) -> Path:
    return DATA_DIR / f"estat_r2ka{pref}.zip"


def _targets() -> list[tuple[str, Path, str]]:
    """(種別, 保存先, URL) の一覧。"""
    items: list[tuple[str, Path, str]] = []
    for kind in A31_RIVER_KINDS:
        for mesh in A31_MESHES:
            items.append(("A31", a31_path(kind, mesh), A31_URL.format(kind=kind, mesh=mesh)))
    for pref in PREFECTURES:
        items.append(("A33", a33_path(pref), A33_URL.format(pref=pref)))
    for pref in PREFECTURES:
        items.append(("境界", boundary_path(pref), BOUNDARY_URL.format(pref=pref)))
    return items


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(path: Path, url: str, *, force: bool) -> bool:
    """未取得なら取得する。取得したら True。"""
    if path.exists() and not force:
        return False
    import httpx

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"取得中: {path.name} … ", end="", flush=True)
    with httpx.stream("GET", url, timeout=1800.0, follow_redirects=True) as response:
        response.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in response.iter_bytes(1 << 20):
                fh.write(chunk)
        tmp.replace(path)
    print(f"{path.stat().st_size:,} bytes")
    return True


def inspect(kind: str, path: Path) -> dict[str, object]:
    """ZIP の中身が想定どおりかを検査する。

    ⚠ **サイズと存在だけでは足りない。** 原典の版が上がって形式が変わっても
    ダウンロードは成功するので、**中に入っているファイルの種類まで確かめる**。
    """
    if not path.exists():
        raise SystemExit(f"未取得です: {path.name}（--fetch で取得してください）")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    if kind == "A31":
        # 想定最大規模の Shapefile が要る。計画規模しか無ければ版が変わっている。
        needed = [n for n in names if "20_想定最大規模" in n and n.endswith(".shp")]
        if not needed:
            raise SystemExit(
                f"{path.name}: 想定最大規模の .shp が見つかりません（版が変わった疑い）"
            )
    elif kind == "A33":
        if not any(n.endswith(".geojson") for n in names):
            raise SystemExit(f"{path.name}: GeoJSON が入っていません（版が変わった疑い）")
    else:
        if not any(n.endswith(".shp") for n in names):
            raise SystemExit(f"{path.name}: Shapefile が入っていません（版が変わった疑い）")
    size = path.stat().st_size
    print(f"  {kind:<4} {path.name:<32} {size:>12,} bytes  ({len(names)} ファイル)")
    return {"name": path.name, "kind": kind, "size": size, "sha256": _sha256(path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch", action="store_true", help="原典を取得する（既定は保存済みZIPの検査だけ）"
    )
    parser.add_argument("--force", action="store_true", help="取得済みでも取り直す")
    args = parser.parse_args(argv)

    targets = _targets()
    if args.fetch:
        for _, path, url in targets:
            if not fetch(path, url, force=args.force):
                print(f"取得済み: {path.name}")

    print(f"\n=== 検査（{DATA_DIR}）===")
    entries = [inspect(kind, path) for kind, path, _ in targets]
    total = sum(int(e["size"]) for e in entries)
    print(f"合計 {len(entries)} ファイル / {total / 1024 / 1024:,.0f} MB")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {"acquired_on": date.today().isoformat(), "files": entries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"版を記録しました: {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
