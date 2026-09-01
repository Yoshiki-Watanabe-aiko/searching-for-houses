"""マスタデータのシード投入。

DDLは Alembic、マスタデータは ``db/seed/*.sql`` という分担にしている。
マスタは運用中に育つ（サイト追加・条件追加・辞書更新）ため、
すべての文を ``ON CONFLICT`` 付きの冪等な形で書き、何度流しても壊れないようにする。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, text

from house_search.config.settings import PROJECT_ROOT

SEED_DIR = PROJECT_ROOT / "db" / "seed"

# 件数検証の対象。シード後にこの行数以上あることを確認する。
EXPECTED_MIN_ROWS = {
    "m_property_types": 5,
    "m_sites": 12,
    "m_condition_categories": 19,
    "m_conditions": 148,
    "m_condition_property_types": 487,
    "m_cities": 947,
    # 07（サイト固有マッピング931行）＋ 08（ATHOME/NIFTY のスラグ902行）
    "m_city_site_values": 1833,
}


@dataclass(frozen=True, slots=True)
class SeedResult:
    """シード投入の結果。"""

    applied_files: list[str]
    row_counts: dict[str, int]

    def shortfalls(self) -> dict[str, tuple[int, int]]:
        """期待行数に満たなかったテーブルを ``{表名: (実測, 期待)}`` で返す。"""
        return {
            table: (self.row_counts[table], expected)
            for table, expected in EXPECTED_MIN_ROWS.items()
            if self.row_counts.get(table, 0) < expected
        }


def seed_files(seed_dir: Path = SEED_DIR) -> list[Path]:
    """適用対象のSQLファイルをファイル名順に返す（先頭の連番が適用順）。"""
    return sorted(seed_dir.glob("*.sql"))


def apply_seed(engine: Engine, seed_dir: Path = SEED_DIR) -> SeedResult:
    """シードSQLを順に適用し、投入後の行数を数える。"""
    files = seed_files(seed_dir)
    if not files:
        raise FileNotFoundError(f"シードSQLが見つかりません: {seed_dir}")

    applied: list[str] = []
    with engine.begin() as conn:
        # psycopg3 はパラメータを渡すと SQL 中の '%' をプレースホルダとして解釈する。
        # シードSQLには「建ぺい率（%）」のように % を含む日本語が入るため、
        # パラメータ無しで DBAPI カーソルへ直接流してプレースホルダ解析を回避する。
        raw_cursor = conn.connection.cursor()
        for path in files:
            raw_cursor.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
        raw_cursor.close()

    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in EXPECTED_MIN_ROWS:
            counts[table] = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()

    return SeedResult(applied_files=applied, row_counts=counts)
