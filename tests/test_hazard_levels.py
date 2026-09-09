"""ハザード評価CSVの読み込みと検証（DB不要）。

⚠ **このテストが守っているのは「区域外」と「未解決」の区別**である。
照合できた丁目には区域外でも ``value = 0`` の行が入り、行が無いことが
「照合できなかった」を意味する。片方の災害種別だけ行が欠けると両者が混ざり、
**危険な丁目が黙って減点されなくなる**（例外にならない）。
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from house_search.hazard.levels import (
    AGGREGATIONS,
    HazardLevelError,
    hazard_levels_path,
    load_hazard_rows,
)

_COLUMNS = (
    "normalized_key",
    "level",
    "hazard_type",
    "area_ratio",
    "rank_avg",
    "rank_max",
    "source",
    "acquired_on",
)


def _row(
    key: str,
    hazard_type: str,
    *,
    level: str = "chome",
    area_ratio: float = 0.0,
    rank_avg: float = 0.0,
    rank_max: float = 0.0,
) -> dict[str, object]:
    return {
        "normalized_key": key,
        "level": level,
        "hazard_type": hazard_type,
        "area_ratio": area_ratio,
        "rank_avg": rank_avg,
        "rank_max": rank_max,
        "source": "mlit_a33-23",
        "acquired_on": "2026-09-05",
    }


def _write(tmp_path: Path, rows: list[dict[str, object]], *, columns=_COLUMNS) -> Path:
    path = hazard_levels_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path


def test_横持ちCSVを縦持ちの行へ展開する(tmp_path: Path) -> None:
    data_dir = _write(
        tmp_path,
        [
            _row("東京都北区浮間5丁目", "landslide", area_ratio=0.25, rank_avg=0.5, rank_max=2.0),
            _row(
                "東京都北区浮間5丁目",
                "landslide_special",
                area_ratio=0.1,
                rank_avg=0.2,
                rank_max=2.0,
            ),
        ],
    )
    loaded = load_hazard_rows(data_dir)

    assert len(loaded.rows) == 2 * len(AGGREGATIONS)
    assert loaded.key_count == 1
    assert loaded.hazard_types == ("landslide", "landslide_special")
    values = {(r.hazard_type, r.aggregation): r.value for r in loaded.rows}
    assert values[("landslide", "area_ratio")] == 0.25
    assert values[("landslide_special", "rank_max")] == 2.0
    assert loaded.rows[0].acquired_on == date(2026, 9, 5)


def test_区域外はvalue0の行として保持される(tmp_path: Path) -> None:
    """⚠ 0 は「安全だと確認した」の証拠。行ごと落としてはいけない。"""
    data_dir = _write(
        tmp_path,
        [
            _row("東京都千代田区丸の内1丁目", "landslide"),
            _row("東京都千代田区丸の内1丁目", "landslide_special"),
        ],
    )
    loaded = load_hazard_rows(data_dir)

    assert len(loaded.rows) == 2 * len(AGGREGATIONS)
    assert all(row.value == 0.0 for row in loaded.rows)


def test_災害種別が欠けたら例外にする(tmp_path: Path) -> None:
    """⚠ これを通すと「区域外」と「未解決」が混ざる（本モジュール最大のリスク）。"""
    data_dir = _write(
        tmp_path,
        [
            _row("東京都北区浮間5丁目", "landslide", area_ratio=0.25),
            _row("東京都北区浮間5丁目", "landslide_special"),
            # ⚠ この丁目には landslide_special の行が無い
            _row("東京都北区志茂4丁目", "landslide", area_ratio=0.4),
        ],
    )
    # ⚠ 文言は「その種別に行の無いキーが N 件」（種別ごとの検査になったため → 課題#59）
    with pytest.raises(HazardLevelError, match="行の無いキー"):
        load_hazard_rows(data_dir)


def test_液状化だけは欠けてよい(tmp_path: Path) -> None:
    """⚠ 原典が評価していない丁目（湖沼・河道だけ）に値を作るのは捏造（→ ADR 0023 決定4）。

    洪水・土砂が全キーに揃っていれば、液状化が一部で欠けても読める。
    ⚠ 逆に洪水が欠けたら例外（上のテスト）。この非対称が要点。
    """
    keys = [f"東京都北区浮間{i}丁目" for i in range(1, 201)]
    rows = []
    for index, key in enumerate(keys):
        rows.append(_row(key, "landslide"))
        rows.append(_row(key, "landslide_special"))
        rows.append(_row(key, "flood"))
        # 200件中1件（0.5%）だけ液状化を欠けさせる
        if index > 0:
            rows.append(_row(key, "liquefaction", rank_avg=1.0, rank_max=1.0))
    loaded = load_hazard_rows(_write(tmp_path, rows))
    assert dict(loaded.missing_by_type) == {"liquefaction": 1}


def test_液状化の欠落が多すぎたら例外にする(tmp_path: Path) -> None:
    """⚠ 島嶼タイルの取り忘れ（1.9%）と原典の未評価（0.1%未満）を分ける線。"""
    keys = [f"東京都北区浮間{i}丁目" for i in range(1, 101)]
    rows = []
    for index, key in enumerate(keys):
        rows.append(_row(key, "flood"))
        # 100件中2件（2%）を欠けさせる＝上限 0.5% 超
        if index > 1:
            rows.append(_row(key, "liquefaction", rank_avg=1.0, rank_max=1.0))
    with pytest.raises(HazardLevelError, match="欠落が多すぎます"):
        load_hazard_rows(_write(tmp_path, rows))


def test_液状化のランクは1未満を弾く(tmp_path: Path) -> None:
    """⚠⚠ 0 は「評価対象外（原典のレベル6）が 6−6=0 として漏れている」印。

    そのまま入れると水域の多い丁目が満点を取る（→ ADR 0023 決定2）。
    """
    rows = [_row("東京都北区浮間5丁目", "liquefaction", rank_avg=0.0, rank_max=0.0)]
    with pytest.raises(HazardLevelError, match="rank_avg が域外"):
        load_hazard_rows(_write(tmp_path, rows))


def test_未知の災害種別を弾く(tmp_path: Path) -> None:
    data_dir = _write(tmp_path, [_row("東京都北区浮間5丁目", "tsunami")])
    with pytest.raises(HazardLevelError, match="未知の災害種別"):
        load_hazard_rows(data_dir)


def test_未知の粒度を弾く(tmp_path: Path) -> None:
    data_dir = _write(
        tmp_path, [_row("東京都北区浮間5丁目", "landslide", level="banchi")]
    )
    with pytest.raises(HazardLevelError, match="未知の粒度"):
        load_hazard_rows(data_dir)


@pytest.mark.parametrize(
    ("column", "value"),
    [("area_ratio", 1.5), ("area_ratio", -0.1), ("rank_avg", 6.5), ("rank_max", 7.0)],
)
def test_値域の外を弾く(tmp_path: Path, column: str, value: float) -> None:
    """⚠ DB側に CHECK 制約を張っていないぶん、ここで止める。"""
    row = _row("東京都北区浮間5丁目", "landslide")
    row[column] = value
    data_dir = _write(tmp_path, [row])
    with pytest.raises(HazardLevelError, match="域外"):
        load_hazard_rows(data_dir)


def test_列が違えば例外にする(tmp_path: Path) -> None:
    """⚠ 生成スクリプトの出力形式が変わったのを黙って受け入れない。"""
    columns = _COLUMNS[:-1]
    row = _row("東京都北区浮間5丁目", "landslide")
    row.pop("acquired_on")
    data_dir = _write(tmp_path, [row], columns=columns)
    with pytest.raises(HazardLevelError, match="列が想定と違います"):
        load_hazard_rows(data_dir)


def test_取得日が読めなければ例外にする(tmp_path: Path) -> None:
    row = _row("東京都北区浮間5丁目", "landslide")
    row["acquired_on"] = "2026/09/05"
    data_dir = _write(tmp_path, [row])
    with pytest.raises(HazardLevelError, match="取得日"):
        load_hazard_rows(data_dir)


def test_CSVが無ければ生成方法を案内する(tmp_path: Path) -> None:
    with pytest.raises(HazardLevelError, match="build_hazard_levels"):
        load_hazard_rows(tmp_path)


def test_空のCSVを弾く(tmp_path: Path) -> None:
    data_dir = _write(tmp_path, [])
    with pytest.raises(HazardLevelError, match="行が1件もありません"):
        load_hazard_rows(data_dir)
