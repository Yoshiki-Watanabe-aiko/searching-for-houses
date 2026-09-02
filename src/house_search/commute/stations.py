"""駅マスタ（駅データ.jp 無料版）の読み込みとDB同期。

正典は ``data/train_master/`` の CSV。``sync-stations`` で ``m_stations`` へ同期し、
実行時はDBから読む（``m_condition_synonyms`` / ``m_site_search_params`` と同じ構成）。

⚠ **CSV は再配布不可のため Git 管理外**にしてある。設備抽出辞書やサイト検索パラメータは
Git管理YAMLを正典にできたが、この表だけはライセンス上そうできない（→ ADR 0016）。
ファイル名に取得日が入る（``station20260731free.csv``）ので、日付部分は glob で吸収する。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, text

from house_search.commute.normalize import normalize_key

TRAIN_MASTER_DIRNAME = "train_master"

# 駅データ.jp の e_status。0=営業中 / 1=未開業 / 2=廃止。
STATUS_ACTIVE = "0"


class StationMasterError(ValueError):
    """駅マスタの読み込みに失敗した。"""


@dataclass(frozen=True)
class StationRow:
    """``m_stations`` の1行。"""

    station_cd: int
    station_g_cd: int
    station_name: str
    station_name_key: str
    line_cd: int
    line_name: str
    company_name: str | None
    pref_cd: int
    lon: Decimal
    lat: Decimal


@dataclass(frozen=True)
class LoadResult:
    """CSVから読んだ結果。件数は ``sync-stations`` の出力に使う。"""

    rows: tuple[StationRow, ...]
    skipped_closed: int
    skipped_no_line: int


def _pick(directory: Path, pattern: str) -> Path:
    """取得日つきのファイル名を glob で拾う。複数あれば新しい方（名前順の末尾）。"""
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise StationMasterError(
            f"駅マスタのCSVが見つかりません: {directory / pattern}\n"
            "data/train_master/README.md の手順で駅データ.jp から取得してください"
        )
    return matches[-1]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_station_rows(data_dir: Path) -> LoadResult:
    """駅・路線・事業者のCSVを突き合わせて ``m_stations`` の行を組み立てる。

    営業中（``e_status=0``）の駅だけを対象にする。廃止駅を入れても掲載側には
    現れず、同名の営業駅との衝突（``ambiguous``）を増やすだけになる。
    路線側も営業中に限る（未開業路線の駅は掲載に出ない）。
    """
    directory = data_dir / TRAIN_MASTER_DIRNAME
    if not directory.is_dir():
        raise StationMasterError(
            f"駅マスタのディレクトリがありません: {directory}\n"
            "data/train_master/README.md の手順で駅データ.jp から取得してください"
        )

    companies = {
        row["company_cd"]: row["company_name"]
        for row in _read_csv(_pick(directory, "company*.csv"))
    }
    lines = {
        row["line_cd"]: row
        for row in _read_csv(_pick(directory, "line*.csv"))
        if row["e_status"] == STATUS_ACTIVE
    }

    rows: list[StationRow] = []
    skipped_closed = 0
    skipped_no_line = 0
    for raw in _read_csv(_pick(directory, "station*.csv")):
        if raw["e_status"] != STATUS_ACTIVE:
            skipped_closed += 1
            continue
        line = lines.get(raw["line_cd"])
        if line is None:
            skipped_no_line += 1
            continue
        name = raw["station_name"].strip()
        rows.append(
            StationRow(
                station_cd=int(raw["station_cd"]),
                station_g_cd=int(raw["station_g_cd"]),
                station_name=name,
                station_name_key=normalize_key(name),
                line_cd=int(raw["line_cd"]),
                line_name=line["line_name"].strip(),
                company_name=companies.get(line["company_cd"]),
                pref_cd=int(raw["pref_cd"]),
                lon=Decimal(raw["lon"]),
                lat=Decimal(raw["lat"]),
            )
        )
    return LoadResult(
        rows=tuple(rows), skipped_closed=skipped_closed, skipped_no_line=skipped_no_line
    )


_UPSERT = text(
    """
    INSERT INTO m_stations (
        station_cd, station_g_cd, station_name, station_name_key,
        line_cd, line_name, company_name, pref_cd, lon, lat,
        created_at, updated_at
    )
    VALUES (
        :station_cd, :station_g_cd, :station_name, :station_name_key,
        :line_cd, :line_name, :company_name, :pref_cd, :lon, :lat,
        now(), now()
    )
    ON CONFLICT (station_cd) DO UPDATE SET
        station_g_cd     = EXCLUDED.station_g_cd,
        station_name     = EXCLUDED.station_name,
        station_name_key = EXCLUDED.station_name_key,
        line_cd          = EXCLUDED.line_cd,
        line_name        = EXCLUDED.line_name,
        company_name     = EXCLUDED.company_name,
        pref_cd          = EXCLUDED.pref_cd,
        lon              = EXCLUDED.lon,
        lat              = EXCLUDED.lat,
        updated_at       = now()
    """
)


def sync_stations(engine: Engine, rows: tuple[StationRow, ...]) -> tuple[int, int]:
    """駅マスタをDBへ同期する。``(反映件数, 削除件数)`` を返す。

    CSVから消えた駅（廃止・コード変更）はDBからも消す。残しておくと
    「もう無い駅に掲載が同定される」ずれが生まれる。
    """
    if not rows:
        raise StationMasterError("駅が1件も読めませんでした。CSVの中身を確認してください")

    params = [
        {
            "station_cd": row.station_cd,
            "station_g_cd": row.station_g_cd,
            "station_name": row.station_name,
            "station_name_key": row.station_name_key,
            "line_cd": row.line_cd,
            "line_name": row.line_name,
            "company_name": row.company_name,
            "pref_cd": row.pref_cd,
            "lon": row.lon,
            "lat": row.lat,
        }
        for row in rows
    ]
    with engine.begin() as conn:
        conn.execute(_UPSERT, params)
        deleted = conn.execute(
            text("DELETE FROM m_stations WHERE station_cd <> ALL(:codes)"),
            {"codes": [row.station_cd for row in rows]},
        ).rowcount
    return len(params), deleted


def resolve_station_group(
    engine: Engine, station_name: str, prefecture_code: int | None = None
) -> tuple[int, str] | None:
    """駅名から駅グループコードを引く（勤務先の最寄り駅の解決に使う）。

    ⚠ **都道府県で絞れる場合は必ず絞る。** 同名異駅（日本橋＝東京/大阪、
    府中＝東京/広島）があるうえ、路線名にも同名の別物が実在する
    （「三田線」は都営と神戸電鉄）。

    一意に決まらないときは ``None`` を返す（呼び出し側でエラーにする）。
    """
    sql = """
        SELECT DISTINCT station_g_cd, station_name
          FROM m_stations
         WHERE station_name_key = :key
    """
    params: dict[str, object] = {"key": normalize_key(station_name)}
    if prefecture_code is not None:
        sql += " AND pref_cd = :pref"
        params["pref"] = prefecture_code
    with engine.connect() as conn:
        found = conn.execute(text(sql), params).all()
    if len(found) != 1:
        return None
    return int(found[0][0]), str(found[0][1])
