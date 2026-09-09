"""ハザード評価（``m_hazard_levels``）の読み込みと同期。

正典は ``data/hazard_levels/hazard_levels.csv`` で、
``scripts/tools/build_hazard_levels.py`` が丁目境界とハザードのポリゴンを
交差して生成する（→ 課題#46）。

⚠ **ここに幾何ライブラリは入らない。** ポリゴンの計算は生成スクリプト側で
終わっており、この層が扱うのは集計済みの数値だけ。``scan`` / ``rescore`` が
shapely に依存しないことは ``tests/test_no_geo_runtime_deps.py`` が固定している。

⚠⚠ **「区域外」と「未解決」を区別する。** 照合できた丁目には、区域に掛からなくても
``value = 0`` の行が入っている（安全だと確認した証拠）。行が無い＝そもそも
照合できなかった、という意味。混ぜると「危険なのに情報が無いから減点されない」
掲載が「安全」と同じ扱いになり、**例外にならないまま順位が狂う**。

⚠ **CSVは横持ち・テーブルは縦持ち。** CSVは1行に3方式（``area_ratio`` /
``rank_avg`` / ``rank_max``）をまとめてある。縦持ちのまま置くと行数が3倍・
17MBになりリポジトリを太らせるため。展開はこのモジュールが行う。
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

HAZARD_LEVELS_DIRNAME = "hazard_levels"
HAZARD_LEVELS_FILENAME = "hazard_levels.csv"

LEVEL_CHOME = "chome"
LEVEL_TOWN = "town"

# 集計方式。CSVの列名でもある。
AGGREGATIONS = ("area_ratio", "rank_avg", "rank_max")

# 想定する災害種別。⚠ ここに無い値は綴り間違いとして弾く。
# 第2弾で高潮・津波を足すときはここへ追加する（DDL 変更は要らない）。
HAZARD_TYPES = frozenset({"flood", "landslide", "landslide_special", "liquefaction"})

# ⚠⚠ **全キーに行があることを要求する種別。** 液状化だけは部分カバーを許す
# （→ ADR 0023 決定4）。原典が評価していない丁目（湖沼・河道だけの丁目）に
# 値を作るのは捏造であり、「区域外(0)と未解決(None)を混ぜない」に反する。
FULL_COVERAGE_TYPES = frozenset({"flood", "landslide", "landslide_special"})

# ⚠ 部分カバーを許す種別で、欠落がこの割合を超えたら異常として止める。
# 取り忘れ（島嶼タイルを落とすと 1.9%）と原典の未評価（0.1%未満）を分ける線。
PARTIAL_MAX_MISSING_RATIO = 0.005

EXPECTED_COLUMNS = (
    "normalized_key",
    "level",
    "hazard_type",
    "area_ratio",
    "rank_avg",
    "rank_max",
    "source",
    "acquired_on",
)

# 値域。⚠ 面積比は 0〜1、ランクは 0〜6（A31 の浸水深ランクが最大6）。
# 域外の値は生成スクリプトの不具合を意味するので、DBへ入れる前に止める。
_VALUE_RANGE = {
    "area_ratio": (0.0, 1.0),
    "rank_avg": (0.0, 6.0),
    "rank_max": (0.0, 6.0),
}

# ⚠⚠ **種別ごとの値域**。液状化のランクは **1〜5**（0 は出ない）。
# 0 が来たら「評価対象外（原典のレベル6）が 6−6=0 として漏れている」ので止める
# （→ ADR 0023 決定2。例外にしないと水域の多い丁目が満点を取る）。
_TYPE_VALUE_RANGE: dict[str, dict[str, tuple[float, float]]] = {
    "liquefaction": {"rank_avg": (1.0, 5.0), "rank_max": (1.0, 5.0)},
}

# 入れ替えで件数がこの割合を下回ったら警告する。
# ⚠ 全置換なのでデータが静かに痩せうる。生成スクリプトが一部のデータセットしか
# 読まなかった場合など、エラーにならないまま行が減る。
SHRINK_WARN_RATIO = 0.8


class HazardLevelError(ValueError):
    """ハザード評価CSVの読み込みに失敗した。"""


@dataclass(frozen=True, slots=True)
class HazardLevelRow:
    """``m_hazard_levels`` の1行（縦持ち）。"""

    normalized_key: str
    level: str
    hazard_type: str
    aggregation: str
    value: float
    source: str
    acquired_on: date


@dataclass(frozen=True, slots=True)
class LoadResult:
    """CSVから読んだ結果。``sync-hazards`` の出力に使う。"""

    rows: tuple[HazardLevelRow, ...]
    key_count: int
    hazard_types: tuple[str, ...]
    # 種別 → 行が無かったキーの数。⚠ 液状化だけが非0になりうる（→ ADR 0023 決定4）
    missing_by_type: tuple[tuple[str, int], ...] = ()

    @property
    def chome_count(self) -> int:
        return sum(1 for row in self.rows if row.level == LEVEL_CHOME)

    @property
    def town_count(self) -> int:
        return sum(1 for row in self.rows if row.level == LEVEL_TOWN)


def hazard_levels_path(data_dir: Path) -> Path:
    return data_dir / HAZARD_LEVELS_DIRNAME / HAZARD_LEVELS_FILENAME


def load_hazard_rows(data_dir: Path) -> LoadResult:
    """``data/hazard_levels/hazard_levels.csv`` を読んで縦持ちの行へ展開する。

    ⚠ **値域と種別をここで検証する。** DB側に CHECK 制約を張っていないのは、
    第2弾で災害種別を足すときに DDL 変更を伴わせないため。そのぶん
    綴り間違いや域外の値はこの関数が止める。
    """
    path = hazard_levels_path(data_dir)
    if not path.is_file():
        raise HazardLevelError(
            f"ハザード評価のCSVがありません: {path}\n"
            "`uv run python scripts/tools/build_hazard_levels.py` で生成してください"
        )

    rows: list[HazardLevelRow] = []
    keys: set[tuple[str, str]] = set()
    types: set[str] = set()
    keys_by_type: dict[str, set[tuple[str, str]]] = defaultdict(set)
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = tuple(reader.fieldnames or ())
        if columns != EXPECTED_COLUMNS:
            raise HazardLevelError(
                f"列が想定と違います: {columns}\n想定: {EXPECTED_COLUMNS}"
            )
        for line, raw in enumerate(reader, start=2):
            hazard_type = raw["hazard_type"]
            if hazard_type not in HAZARD_TYPES:
                raise HazardLevelError(
                    f"{path.name}:{line} 未知の災害種別です: {hazard_type!r}"
                    f"（想定: {sorted(HAZARD_TYPES)}）"
                )
            level = raw["level"]
            if level not in (LEVEL_CHOME, LEVEL_TOWN):
                raise HazardLevelError(f"{path.name}:{line} 未知の粒度です: {level!r}")
            try:
                acquired_on = date.fromisoformat(raw["acquired_on"])
            except ValueError as exc:
                raise HazardLevelError(
                    f"{path.name}:{line} 取得日が読めません: {raw['acquired_on']!r}"
                ) from exc

            keys.add((raw["normalized_key"], level))
            types.add(hazard_type)
            keys_by_type[hazard_type].add((raw["normalized_key"], level))
            for aggregation in AGGREGATIONS:
                value = float(raw[aggregation])
                low, high = _TYPE_VALUE_RANGE.get(hazard_type, {}).get(
                    aggregation, _VALUE_RANGE[aggregation]
                )
                if not low <= value <= high:
                    raise HazardLevelError(
                        f"{path.name}:{line} {aggregation} が域外です: {value}"
                        f"（想定 {low}〜{high}）"
                    )
                rows.append(
                    HazardLevelRow(
                        normalized_key=raw["normalized_key"],
                        level=level,
                        hazard_type=hazard_type,
                        aggregation=aggregation,
                        value=value,
                        source=raw["source"],
                        acquired_on=acquired_on,
                    )
                )

    if not rows:
        raise HazardLevelError(f"{path.name} に行が1件もありません")

    # ⚠ 恒等式の検査。対象キーのすべてに、全 aggregation の行が揃っていなければ
    # ならない。欠けると「区域外」と「未解決」が混ざる。
    # ⚠⚠ **種別ごとに検査する**（→ ADR 0023 決定4）。液状化だけは欠けてよいが、
    #    欠落が多ければ取り忘れなので止める。全体一致で見ると、液状化の欠落1件で
    #    洪水・土砂まで巻き添えになる。
    missing: list[tuple[str, int]] = []
    for hazard_type in sorted(types):
        have = keys_by_type[hazard_type]
        rows_of_type = sum(1 for row in rows if row.hazard_type == hazard_type)
        if rows_of_type != len(have) * len(AGGREGATIONS):
            raise HazardLevelError(
                f"{hazard_type} の行数が恒等式と合いません: "
                f"{rows_of_type:,} != {len(have) * len(AGGREGATIONS):,}"
                f"（キー {len(have):,} × 方式 {len(AGGREGATIONS)}）"
            )
        absent = len(keys) - len(have)
        if hazard_type in FULL_COVERAGE_TYPES:
            if absent:
                raise HazardLevelError(
                    f"{hazard_type} に行の無いキーが {absent:,} 件あります"
                    "（この種別は全キーに行が要ります）。"
                    "「区域外（安全と確認した）」と「未解決（情報が無い）」が"
                    "区別できなくなります"
                )
        elif absent:
            ratio = absent / len(keys)
            if ratio > PARTIAL_MAX_MISSING_RATIO:
                raise HazardLevelError(
                    f"{hazard_type} の欠落が多すぎます: {absent:,} / {len(keys):,}"
                    f"（{ratio:.2%}・上限 {PARTIAL_MAX_MISSING_RATIO:.1%}）。"
                    "生成時のタイルの取り忘れを疑ってください"
                )
            missing.append((hazard_type, absent))

    return LoadResult(
        rows=tuple(rows),
        key_count=len(keys),
        hazard_types=tuple(sorted(types)),
        missing_by_type=tuple(missing),
    )


_INSERT = text(
    """
    INSERT INTO m_hazard_levels (
        normalized_key, level, hazard_type, aggregation, value, source, acquired_on
    ) VALUES (
        :normalized_key, :level, :hazard_type, :aggregation, :value, :source, :acquired_on
    )
    """
)


def sync_hazard_levels(engine: Engine, rows: tuple[HazardLevelRow, ...]) -> tuple[int, int]:
    """ハザード評価をDBへ同期する。``(投入件数, 削除件数)`` を返す。

    ⚠ **差分ではなく全置換にしてある**（``sync_address_points`` と同じ理由）。
    自然キーが正規化規則に依存するので、規則を直したときに古い行が残ると
    「直したのに一致しない」状態が生まれる。

    ⚠ **``id`` を外部から参照しない前提**（全置換で振り直される）。
    掲載との紐付けは ``address_normalized`` からの JOIN で引く。
    """
    if not rows:
        raise HazardLevelError("ハザード評価が1件も読めませんでした")

    params = [
        {
            "normalized_key": row.normalized_key,
            "level": row.level,
            "hazard_type": row.hazard_type,
            "aggregation": row.aggregation,
            "value": row.value,
            "source": row.source,
            "acquired_on": row.acquired_on,
        }
        for row in rows
    ]
    with engine.begin() as conn:
        deleted = conn.execute(text("DELETE FROM m_hazard_levels")).rowcount
        conn.execute(_INSERT, params)
    return len(params), deleted
