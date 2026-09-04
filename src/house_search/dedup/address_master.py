"""住所マスタ（``m_address_points``）の読み込みとDB同期。

正典は国土交通省「位置参照情報」の CSV（``data/address_master/``）。
``sync-addresses`` で ``m_address_points`` へ同期し、実行時はDBから索引を読む
（``m_condition_synonyms`` / ``m_site_search_params`` / ``m_stations`` と同じ構成）。

⚠ **政府標準利用規約（第2.0版）が再配布を認めているので原典をGit管理下に置ける。**
駅マスタ（再配布不可でGit外 → ADR 0016）とは事情が違い、総務省コード表
（→ ADR 0014）と同じ扱いになる。出典の明示だけが義務。

⚠ **正規化は ``normalize_base`` を共用する。** 掲載側と別の規則で原典を正規化すると、
突き合わせが静かに0件になったとき「マスタに無い」のか「正規化がずれている」のかを
区別できなくなる（→ 課題#48）。

⚠ **丁目かどうかは原典の区分コードで決める。** ``大字・字・丁目区分コード`` は
1=大字 / 2=字 / 3=丁目 で、町名の正規表現から推測する必要がない
（実測で区分3は必ず末尾が漢数字の丁目・区分3以外にアラビア数字を含む町名は0件）。
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from house_search.dedup.address import AddressIndex, normalize_base

ADDRESS_MASTER_DIRNAME = "address_master"

# 位置参照情報の版。原典の取得は scripts/tools/fetch_address_master.py。
SOURCE_VERSION = "19.0b"
SOURCE = f"mlit_isj_{SOURCE_VERSION}"

# 原典の「大字・字・丁目区分コード」。3 だけが丁目。
KIND_CHOME = "3"

LEVEL_CHOME = "chome"
LEVEL_TOWN = "town"

# 正規化後の丁目（normalize_base が漢数字をアラビア数字へ直したあとの形）。
_TRAILING_CHOME = re.compile(r"(\d+)丁目$")


class AddressMasterError(ValueError):
    """住所マスタの読み込みに失敗した。"""


@dataclass(frozen=True, slots=True)
class AddressPointRow:
    """``m_address_points`` の1行。"""

    city_jis_code: str
    town_key: str
    chome_number: int | None
    normalized_key: str
    level: str
    lon: Decimal
    lat: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class LoadResult:
    """CSVから読んだ結果。件数と集約の内訳は ``sync-addresses`` の出力に使う。"""

    rows: tuple[AddressPointRow, ...]
    merged: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def chome_count(self) -> int:
        return sum(1 for row in self.rows if row.level == LEVEL_CHOME)

    @property
    def town_count(self) -> int:
        return sum(1 for row in self.rows if row.level == LEVEL_TOWN)


def _build_row(raw: dict[str, str]) -> AddressPointRow:
    """原典1行を ``m_address_points`` の行にする。"""
    town_name = raw["大字町丁目名"].strip()
    normalized = normalize_base(
        raw["都道府県名"].strip() + raw["市区町村名"].strip() + town_name
    )
    if not normalized:
        raise AddressMasterError(f"正規化できない住所です: {raw}")

    if raw["大字・字・丁目区分コード"] == KIND_CHOME:
        match = _TRAILING_CHOME.search(normalized)
        if match is None:
            # ⚠ 区分3なのに末尾が丁目でない = 原典の前提が崩れている。
            # 黙って町名として入れると「丁目が実在しない」と誤判定される。
            raise AddressMasterError(
                f"区分3（丁目）なのに末尾が丁目ではありません: {normalized}"
            )
        return AddressPointRow(
            city_jis_code=raw["市区町村コード"].strip(),
            town_key=normalized[: match.start()],
            chome_number=int(match.group(1)),
            normalized_key=normalized,
            level=LEVEL_CHOME,
            lon=Decimal(raw["経度"]),
            lat=Decimal(raw["緯度"]),
            source=SOURCE,
        )

    # 大字・字は町名までで確定。⚠ 「坂田西一丁目」のように丁目を含む大字が実在するが、
    # 丁目行として扱わない（原典が大字と言っているものを丁目に格上げしない）。
    return AddressPointRow(
        city_jis_code=raw["市区町村コード"].strip(),
        town_key=normalized,
        chome_number=None,
        normalized_key=normalized,
        level=LEVEL_TOWN,
        lon=Decimal(raw["経度"]),
        lat=Decimal(raw["緯度"]),
        source=SOURCE,
    )


def load_address_rows(data_dir: Path) -> LoadResult:
    """``data/address_master/*.csv`` を読んで行を組み立てる。

    ⚠ **正規化すると同じキーになる原典行がある**（「原町」と「大字原町」など）。
    先に現れた行を採るが、**黙って潰さず** ``merged`` に記録して呼び出し側が
    出力する。件数が急に増えたら正規化の欠陥を疑うため。
    """
    directory = data_dir / ADDRESS_MASTER_DIRNAME
    if not directory.is_dir():
        raise AddressMasterError(
            f"住所マスタのディレクトリがありません: {directory}\n"
            "scripts/tools/fetch_address_master.py --fetch で取得してください"
        )
    paths = sorted(directory.glob("*.csv"))
    if not paths:
        raise AddressMasterError(
            f"住所マスタのCSVが見つかりません: {directory}\n"
            "scripts/tools/fetch_address_master.py --fetch で取得してください"
        )

    rows: dict[tuple[str, str], AddressPointRow] = {}
    merged: dict[str, list[str]] = {}
    for path in paths:
        with path.open(encoding="utf-8", newline="") as fh:
            for raw in csv.DictReader(fh):
                row = _build_row(raw)
                key = (row.normalized_key, row.level)
                if key in rows:
                    merged.setdefault(row.normalized_key, []).append(
                        raw["大字町丁目名"].strip()
                    )
                    continue
                rows[key] = row

    return LoadResult(
        rows=tuple(rows.values()),
        merged=tuple((key, tuple(names)) for key, names in sorted(merged.items())),
    )


_INSERT = text(
    """
    INSERT INTO m_address_points (
        city_jis_code, town_key, chome_number, normalized_key, level,
        lon, lat, source, created_at, updated_at
    )
    VALUES (
        :city_jis_code, :town_key, :chome_number, :normalized_key, :level,
        :lon, :lat, :source, now(), now()
    )
    """
)


def sync_address_points(engine: Engine, rows: tuple[AddressPointRow, ...]) -> tuple[int, int]:
    """住所マスタをDBへ同期する。``(投入件数, 削除件数)`` を返す。

    ⚠ **差分ではなく全置換にしてある。** 自然キー ``(normalized_key, level)`` は
    正規化規則に依存するので、規則を直したときに古い行が残ると
    「直したのに一致しない」状態が生まれる。原典が正典である以上、
    毎回入れ直すのが意味的に正しい。

    ⚠ **``id`` を外部から参照しない前提**（全置換で振り直されるため）。
    掲載との紐付けは ``address_normalized`` からの JOIN で引く。
    """
    if not rows:
        raise AddressMasterError("住所が1件も読めませんでした。CSVの中身を確認してください")

    params = [
        {
            "city_jis_code": row.city_jis_code,
            "town_key": row.town_key,
            "chome_number": row.chome_number,
            "normalized_key": row.normalized_key,
            "level": row.level,
            "lon": row.lon,
            "lat": row.lat,
            "source": row.source,
        }
        for row in rows
    ]
    with engine.begin() as conn:
        deleted = conn.execute(text("DELETE FROM m_address_points")).rowcount
        conn.execute(_INSERT, params)
    return len(params), deleted


def load_address_index(conn: Connection) -> AddressIndex:
    """DBから丁目の実在判定用の索引を作る。

    ⚠ **空でも例外にしない。** マスタ未同期の環境でも従来どおり動く必要があるため
    （``AddressIndex.is_empty`` で呼び出し側が気づける）。
    """
    rows: Iterable[tuple[str, int | None]] = conn.execute(
        text("SELECT town_key, chome_number FROM m_address_points")
    ).all()  # type: ignore[assignment]
    return AddressIndex.build(rows)
