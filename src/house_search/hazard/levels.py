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
HAZARD_TYPES = frozenset({"flood", "landslide", "landslide_special"})

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
            for aggregation in AGGREGATIONS:
                value = float(raw[aggregation])
                low, high = _VALUE_RANGE[aggregation]
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

    # ⚠ 恒等式の検査。対象キーのすべてに、全 hazard_type × aggregation の行が
    # 揃っていなければならない。欠けると「区域外」と「未解決」が混ざる。
    expected = len(keys) * len(types) * len(AGGREGATIONS)
    if len(rows) != expected:
        raise HazardLevelError(
            f"行数が恒等式と合いません: {len(rows):,} != {expected:,}"
            f"（キー {len(keys):,} × 種別 {len(types)} × 方式 {len(AGGREGATIONS)}）。"
            "照合できたキーに欠けている種別があると、"
            "「区域外（安全と確認した）」と「未解決（情報が無い）」が区別できなくなります"
        )

    return LoadResult(
        rows=tuple(rows), key_count=len(keys), hazard_types=tuple(sorted(types))
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
