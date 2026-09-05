"""丁目境界とハザードのポリゴンを交差し、``m_hazard_levels`` の入力CSVを作る。

⚠ **このスクリプトは実行時（``scan`` / ``rescore``）から呼ばれない。**
年1回オフラインで回し、生成物 ``data/hazard_levels/hazard_levels.csv`` を
``sync-hazards`` でDBへ入れる。幾何ライブラリ（shapely / pyshp）は
**dev 依存に隔離**してあり、``src/house_search/`` からは import しない
（``tests/test_no_geo_runtime_deps.py`` が固定 → 課題#46）。

処理は3段:
  1. 丁目境界（e-Stat）を読み、``normalize_base`` で住所マスタと突き合わせる
  2. ハザードのポリゴンと交差し、**交差面積**を中間キャッシュへ書く
  3. キャッシュから ``hazard_type`` × ``aggregation`` の値を作ってCSVへ

⚠ **``--from-cache`` で段2を飛ばせる。** 交差面積の計算は洪水で約200秒かかるので、
集計方法を変えるたびに払わずに済む（設備の ``re-extract``・経路の ``re-segment`` と
同じ考え方）。

⚠⚠ **突き合わせが0件でも「全件 unknown」で正常終了してしまう。**
だからカバー率を必ず出し、閾値を下回ったら**終了コードを非0にする**。
実測（2026-09-05）の基準線は **90.5%**（19,428 / 21,471 丁目。対象82市区に絞れば98.3%）。

⚠⚠ **「区域外」と「未解決」を区別する。** 照合できた丁目には、区域に掛からなくても
``value = 0`` の行を必ず書く。行が無い＝照合できなかった、という意味にする。
混ぜると「危険なのに情報が無いから減点されない」掲載が「安全」と同じ扱いになる。
段3の最後で **行数の恒等式**（対象キー数 × 災害種別）を検査している。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from house_search.dedup.address import normalize_base  # noqa: E402
from house_search.dedup.address_master import load_address_rows  # noqa: E402

SOURCES = ROOT / "data" / "hazard_sources"
OUT_DIR = ROOT / "data" / "hazard_levels"
OUT_CSV = OUT_DIR / "hazard_levels.csv"
CACHE_DIR = ROOT / "tmp" / "hazard_cache"

PREFECTURES = ("11", "12", "13", "14")
A31_MESHES = ("5339", "5340")
A31_RIVER_KINDS = ("10", "20")

# 住所マスタの丁目のうち、境界データが見つかった割合の下限。
# これを下回ったら異常として終了コードを非0にする。
# ⚠ **実測（2026-09-05）の基準線は 4都県全体で 90.5%**（19,428 / 21,471）。
# 対象82市区に絞れば 98.3% だが、生成は検索パターンに依存させたくないので
# 4都県全体で回す。差は多摩・房総の山間部などで境界データ側の町丁名が
# 位置参照情報と食い違うぶん（版のずれ・町名改称）。
# ⚠ **これは網羅性の指標ではなく、正規化がずれていないかの指標**である。
# 突き合わせが壊れると一気に落ちるので、そこを捕まえるための線。
MIN_COVERAGE = 0.88

# ⚠ **CSVは横持ち**（1行に3方式）。テーブルは縦持ちなので ``sync-hazards`` が展開する。
# 縦持ちのまま書くと行数が3倍・17MBになり、リポジトリを太らせるため。
# 「区域外は0を明示する」という設計はそのまま保たれる（行はあり、値が0になる）。
CSV_COLUMNS = (
    "normalized_key",
    "level",
    "hazard_type",
    "area_ratio",
    "rank_avg",
    "rank_max",
    "source",
    "acquired_on",
)

# 集計方式。⚠ 洪水と土砂で効く方式が違う（洪水は面積比で階調をつけないと
# 72.9%が該当して判別力が出ない。土砂は該当が18.9%で有無だけでも効く → 課題#46）。
AGGREGATIONS = ("area_ratio", "rank_avg", "rank_max")

# hazard_type → (出典ラベル, ((キャッシュのデータセット, 絞り込むランク), ...))。
# landslide_special は A33 の区分2（特別警戒区域＝レッドゾーン）だけを見る。
#
# ⚠⚠ **洪水は 10（洪水予報河川）と 20（その他の河川）を別々に集計して max を採る。**
# 同じ場所が両方の浸水想定に含まれることがあり、交差面積を単純に足すと
# **二重計上**になる（実測で rank_avg が 6.32 とランクの上限6を超えた）。
# ⚠ max は逆に過小評価になりうる（重ならない部分は合算が正しい）が、
# 100万ポリゴンの union は現実的でないうえ、値域を外れる方が害が大きい。
HAZARD_SPECS: dict[str, tuple[str, tuple[tuple[str, int | None], ...]]] = {
    "landslide": ("mlit_a33-23", (("a33", None),)),
    "landslide_special": ("mlit_a33-23", (("a33", 2),)),
    "flood": ("mlit_a31-22", (("a31_10", None), ("a31_20", None))),
}
# 引数のデータセット → 作る hazard_type。
DATASET_TYPES = {"a33": ("landslide", "landslide_special"), "a31": ("flood",)}


_CHOME_SUFFIX = re.compile(r"\d+丁目$")


def _town_key(normalized_key: str) -> str:
    """正規化キーから丁目を落として町キーにする。

    ⚠ **住所マスタに載っている町では、マスタの ``town_key`` を優先する**
    （物理列で持つという ADR 0020 の方針）。ここは**マスタに無い町**のための
    後詰めで、境界データ側の町丁名からその場で導く。
    """
    return _CHOME_SUFFIX.sub("", normalized_key)


def _geom(obj):
    """shapely のジオメトリへ変換し、壊れていれば修復する。"""
    from shapely.geometry import shape

    geometry = shape(obj)
    return geometry if geometry.is_valid else geometry.buffer(0)


def _extract(zip_path: Path, member_stem: str, work: Path) -> Path:
    """Shapefile の3点セットを作業ディレクトリへ展開して基底パスを返す。"""
    work.mkdir(parents=True, exist_ok=True)
    stem = member_stem.split("/")[-1]
    with zipfile.ZipFile(zip_path) as zf:
        for ext in (".shp", ".shx", ".dbf"):
            target = work / f"{stem}{ext}"
            if not target.exists():
                target.write_bytes(zf.read(f"{member_stem}{ext}"))
    return work / stem


def load_chome_polygons() -> tuple[dict[str, object], dict[str, list[str]], int, float]:
    """丁目境界を読み、住所マスタのキーへ対応づける。

    戻り値は (丁目キー→ポリゴン, 町キー→配下の丁目キー, マスタの丁目総数, カバー率)。

    ⚠ **``normalize_base`` を共用する。** 独自の正規化を書くと、突き合わせが
    0件になったとき「マスタに無い」のか「正規化がずれている」のかを区別できない
    （→ ADR 0020 決定2 と同じ理由）。
    """
    import shapefile
    from shapely.ops import unary_union

    master = load_address_rows(ROOT / "data")
    town_of = {row.normalized_key: row.town_key for row in master.rows}

    parts: dict[str, list[object]] = defaultdict(list)
    unmatched: set[str] = set()
    for pref in PREFECTURES:
        zip_path = SOURCES / f"estat_r2ka{pref}.zip"
        with zipfile.ZipFile(zip_path) as zf:
            member = next(n for n in zf.namelist() if n.endswith(".shp"))[:-4]
        base = _extract(zip_path, member, CACHE_DIR / "boundary")
        reader = shapefile.Reader(str(base), encoding="cp932")
        for record in reader.iterShapeRecords():
            attrs = record.record.as_dict()
            name = (attrs.get("S_NAME") or "").strip()
            # 「水面調査区」や「‐」は統計上の区分で実在の町ではない。
            if not name or name in ("‐", "-"):
                continue
            # 先頭の 'x' は normalize_base に都道府県の切り出しをさせないための番人。
            key = normalize_base("x" + attrs["PREF_NAME"] + attrs["CITY_NAME"] + name)
            if not key:
                continue
            key = key[1:]
            # ⚠ **マスタに無くても捨てない。** 位置参照情報（大字・町丁目レベル）は
            # すべての町を収録しているわけではなく（実測でいすみ市などが欠ける）、
            # 境界データの方が網羅的。捨てるとその町の掲載が
            # 「未解決」のまま永久にハザード評価を持てなくなる。
            if key not in town_of:
                unmatched.add(key)
            parts[key].append(_geom(record.shape.__geo_interface__))

    chome = {
        key: (geoms[0] if len(geoms) == 1 else unary_union(geoms))
        for key, geoms in parts.items()
    }
    towns: dict[str, list[str]] = defaultdict(list)
    for key in chome:
        towns[town_of.get(key) or _town_key(key)].append(key)

    matched = sum(1 for key in chome if key in town_of)
    coverage = matched / len(town_of) if town_of else 0.0
    print(
        f"丁目境界: {len(chome):,}（うちマスタ照合 {matched:,} / マスタ {len(town_of):,}"
        f"  カバー率 {coverage:.1%}）"
    )
    if unmatched:
        print(
            f"  ※ 位置参照情報に無い町丁 {len(unmatched):,} 件も対象に含めた"
            f"（例: {sorted(unmatched)[:3]}）"
        )
    return chome, dict(towns), len(town_of), coverage


# A33 の区域区分コード。⚠ **これ以外の値は原典の誤り**として捨てる。
# 実測（2026-09-05）で神奈川県に 3 が1件・4 が2件あった。データの注記にも
# 「原典の有するデータ欠損、誤り等はそのまま反映されます」とある。
# ⚠ 黙って通すと ``rank_max`` が 4 になり、コードリストが変わったときに気づけない。
A33_ZONE_CODES = frozenset({1, 2})


def _iter_a33():
    """A33（土砂災害）のポリゴンと区分を返す。区分は 1=警戒 / 2=特別警戒。"""
    dropped: Counter[int] = Counter()
    for pref in PREFECTURES:
        with zipfile.ZipFile(SOURCES / f"A33-23_{pref}_GML.zip") as zf:
            name = next(n for n in zf.namelist() if n.endswith(".geojson"))
            payload = json.loads(zf.read(name).decode("utf-8"))
        for feature in payload["features"]:
            zone = int(feature["properties"]["A33_002"])
            if zone not in A33_ZONE_CODES:
                dropped[zone] += 1
                continue
            yield zone, _geom(feature["geometry"])
    if dropped:
        print(f"  ⚠ 想定外の区域区分を捨てた: {dict(sorted(dropped.items()))}", flush=True)


def _iter_a31(kind: str):
    """A31（洪水・想定最大規模）のポリゴンと浸水深ランク（1〜6）を返す。

    ⚠ ``10``（洪水予報河川・水位周知河川）と ``20``（その他の河川）は
    **呼び分けて別々に集計する**。同じ場所が両方に含まれることがあり、
    交差面積を足すと二重計上になるため（→ ``HAZARD_SPECS`` の注記）。
    ⚠ 片方だけでも件数は出て正常終了するので、欠けていても気づけない。
    """
    import shapefile

    for mesh in A31_MESHES:
        zip_path = SOURCES / f"A31-22_{kind}_{mesh}_SHP.zip"
        with zipfile.ZipFile(zip_path) as zf:
            member = next(
                n
                for n in zf.namelist()
                if "20_想定最大規模" in n and n.endswith(".shp")
            )[:-4]
        base = _extract(zip_path, member, CACHE_DIR / "a31" / f"{kind}_{mesh}")
        reader = shapefile.Reader(str(base))
        for record in reader.iterShapeRecords():
            yield int(record.record.as_dict()["A31_201"]), _geom(
                record.shape.__geo_interface__
            )


def _cache_datasets(datasets: tuple[str, ...]) -> tuple[str, ...]:
    """引数のデータセットから、中間キャッシュに現れる名前を導く。"""
    names: list[str] = []
    for dataset in datasets:
        for hazard_type in DATASET_TYPES[dataset]:
            for cache_name, _ in HAZARD_SPECS[hazard_type][1]:
                if cache_name not in names:
                    names.append(cache_name)
    return tuple(names)


def intersect(chome: dict[str, object], datasets: tuple[str, ...]) -> None:
    """丁目 × ハザードの交差面積を中間キャッシュへ書く。"""
    from shapely.strtree import STRtree

    keys = list(chome)
    polys = [chome[key] for key in keys]
    tree = STRtree(polys)

    acc: dict[tuple[str, str, int], float] = defaultdict(float)
    for dataset in _cache_datasets(datasets):
        source = _iter_a33() if dataset == "a33" else _iter_a31(dataset.split("_")[1])
        count = 0
        for rank, geometry in source:
            count += 1
            if count % 50_000 == 0:
                print(f"  {dataset}: {count:,} ポリゴン", flush=True)
            for index in tree.query(geometry):
                target = polys[index]
                if not target.intersects(geometry):
                    continue
                try:
                    area = target.intersection(geometry).area
                except Exception:  # noqa: BLE001 - 壊れたポリゴンは捨てる
                    continue
                if area > 0:
                    acc[(keys[index], dataset, rank)] += area
        print(f"  {dataset}: {count:,} ポリゴン（完了）", flush=True)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with (CACHE_DIR / "intersections.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(("normalized_key", "dataset", "rank", "area"))
        for (key, dataset, rank), area in sorted(acc.items()):
            writer.writerow((key, dataset, rank, f"{area:.12g}"))
    with (CACHE_DIR / "chome_area.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(("normalized_key", "area"))
        for key in keys:
            writer.writerow((key, f"{chome[key].area:.12g}"))
    print(f"中間キャッシュ: {CACHE_DIR}（交差 {len(acc):,} 行）")


def _load_cache() -> tuple[dict[str, float], dict[tuple[str, str], dict[int, float]]]:
    areas: dict[str, float] = {}
    with (CACHE_DIR / "chome_area.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            areas[row["normalized_key"]] = float(row["area"])
    inter: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    dropped: Counter[int] = Counter()
    with (CACHE_DIR / "intersections.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rank = int(row["rank"])
            # ⚠ **キャッシュ側にも想定外の区分が残りうる**（--from-cache で
            #    取り込み時のフィルタを通らないため）。ここでも弾く。
            if row["dataset"] == "a33" and rank not in A33_ZONE_CODES:
                dropped[rank] += 1
                continue
            inter[(row["normalized_key"], row["dataset"])][rank] = float(row["area"])
    if dropped:
        print(f"  ⚠ キャッシュ内の想定外の区域区分を捨てた: {dict(sorted(dropped.items()))}")
    return areas, inter


def compute_values(total_area: float, by_rank: dict[int, float]) -> dict[str, float]:
    """1つの丁目・1つの災害種別について3方式の値を出す。

    ⚠ ``rank_avg`` は**丁目の全面積**で加重する（区域外を0として含む）。
    区域内だけで平均すると、端に少し掛かっただけの丁目が全域が深い丁目と同じ値になる。
    """
    covered = sum(by_rank.values())
    ratio = min(covered / total_area, 1.0) if total_area > 0 else 0.0
    weighted = sum(rank * area for rank, area in by_rank.items())
    cap = float(max(by_rank)) if by_rank else 0.0
    # ⚠ **平均は最大を超えない。** ランクの違う区域どうしが地理的に重なると
    #    （土砂の警戒／特別警戒、洪水の別水系）交差面積が二重計上され、
    #    加重平均が上限を超える（実測で洪水 6.32・土砂 4.99）。
    #    正しくは union を取るべきだが100万ポリゴンでは現実的でないので、
    #    **上限でクリップして値域だけは保証する**（過大評価は残る）。
    average = min(weighted / total_area, cap) if total_area > 0 else 0.0
    return {
        "area_ratio": round(ratio, 4),
        "rank_avg": round(average, 4),
        "rank_max": cap,
    }


def aggregate(
    chome_keys: list[str],
    towns: dict[str, list[str]],
    datasets: tuple[str, ...],
    acquired_on: str,
) -> tuple[list[dict[str, object]], int]:
    """中間キャッシュから出力行を組み立てる。戻り値は (行, 対象キー数)。"""
    areas, inter = _load_cache()
    town_keys = sorted(
        k for k, members in towns.items() if any(areas.get(m, 0.0) > 0 for m in members)
    )

    rows: list[dict[str, object]] = []
    for dataset in datasets:
        for hazard_type in DATASET_TYPES[dataset]:
            label, specs = HAZARD_SPECS[hazard_type]
            per_chome: dict[str, dict[str, float]] = {}
            for key in chome_keys:
                total_area = areas.get(key, 0.0)
                # ⚠ 複数のソース（洪水の 10 / 20）は**足さずに max を採る**。
                #    同じ場所が両方に含まれると交差面積が二重計上され、
                #    rank_avg がランクの上限6を超える（実測 6.32）。
                candidates = []
                for cache_name, only_rank in specs:
                    by_rank = dict(inter.get((key, cache_name), {}))
                    if only_rank is not None:
                        by_rank = {r: a for r, a in by_rank.items() if r == only_rank}
                    candidates.append(compute_values(total_area, by_rank))
                values = {
                    aggregation: max(c[aggregation] for c in candidates)
                    for aggregation in AGGREGATIONS
                }
                per_chome[key] = values
                rows.append(
                    {
                        "normalized_key": key,
                        "level": "chome",
                        "hazard_type": hazard_type,
                        **{a: values[a] for a in AGGREGATIONS},
                        "source": label,
                        "acquired_on": acquired_on,
                    }
                )
            # 町行は配下の丁目から作る。
            # ⚠ 面積比と rank_avg は面積加重、rank_max は最大を採る
            # （町名までしか出さないサイトの掲載が落ちる先なので、粗いのは承知のうえ）。
            for town_key in town_keys:
                members = towns[town_key]
                total = sum(areas.get(k, 0.0) for k in members)
                if total <= 0:
                    continue
                merged = {
                    "area_ratio": round(
                        sum(per_chome[k]["area_ratio"] * areas.get(k, 0.0) for k in members)
                        / total,
                        4,
                    ),
                    "rank_avg": round(
                        sum(per_chome[k]["rank_avg"] * areas.get(k, 0.0) for k in members)
                        / total,
                        4,
                    ),
                    "rank_max": max(per_chome[k]["rank_max"] for k in members),
                }
                rows.append(
                    {
                        "normalized_key": town_key,
                        "level": "town",
                        "hazard_type": hazard_type,
                        **{a: merged[a] for a in AGGREGATIONS},
                        "source": label,
                        "acquired_on": acquired_on,
                    }
                )
    return rows, len(chome_keys) + len(town_keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=["a33"],
        choices=["a33", "a31"],
        help="対象データセット（既定: a33。洪水を含めるなら a33 a31）",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="交差計算を飛ばして中間キャッシュから集計だけやり直す",
    )
    args = parser.parse_args(argv)
    datasets = tuple(dict.fromkeys(args.datasets))

    manifest = json.loads((SOURCES / "manifest.json").read_text(encoding="utf-8"))
    acquired_on = manifest["acquired_on"]

    chome, towns, _master_total, coverage = load_chome_polygons()
    if coverage < MIN_COVERAGE:
        print(
            f"✗ カバー率 {coverage:.1%} が下限 {MIN_COVERAGE:.0%} を下回りました。"
            "正規化のずれか境界データの版替わりを疑ってください。",
            file=sys.stderr,
        )
        return 1

    if args.from_cache:
        if not (CACHE_DIR / "intersections.csv").exists():
            print(
                "✗ 中間キャッシュがありません（--from-cache を外して実行してください）",
                file=sys.stderr,
            )
            return 1
    else:
        intersect(chome, datasets)

    rows, key_count = aggregate(sorted(chome), towns, datasets, acquired_on)

    # ⚠ 恒等式の検査。対象キーのすべてに、全 hazard_type × aggregation の行が
    # 揃っていなければならない。1つでも欠けると「区域外（安全と確認した）」と
    # 「未解決（情報が無い）」が混ざり、危険な丁目が黙って減点されなくなる。
    type_count = sum(len(DATASET_TYPES[d]) for d in datasets)
    expected = key_count * type_count
    if len(rows) != expected:
        print(f"✗ 行数が恒等式と合いません: {len(rows):,} != {expected:,}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n書き出しました: {OUT_CSV}  {len(rows):,} 行（対象キー {key_count:,}）")
    for hazard_type in sorted({str(r["hazard_type"]) for r in rows}):
        hit = [
            r
            for r in rows
            if r["hazard_type"] == hazard_type
            and r["level"] == "chome"
            and float(r["area_ratio"]) > 0
        ]
        print(f"  {hazard_type:<18} 区域に掛かる丁目 {len(hit):,} / {len(chome):,}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
