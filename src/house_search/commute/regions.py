"""地方ごとの通勤先（`data/commute_destinations.yaml`）を読む。

全国の駅を網羅するとき、目的地を1つに固定しても意味を成さない
（北海道の駅から芝公園までの所要時間には使い道がない）。地方ごとに中心駅を決め、
その地方の駅はその駅までの所要時間を採る。

⚠ **47都道府県を漏れなく重複なく覆っていることを読み込み時に検証する。**
穴があるとその県の駅が黙って対象外になり、「網羅したつもり」で気づけない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REGIONS_FILENAME = "commute_destinations.yaml"

# 総務省の都道府県コード（JIS X 0401）。1〜47 で欠番はない。
ALL_PREFECTURE_CODES = frozenset(range(1, 48))


class RegionConfigError(ValueError):
    """地方定義の読み込みに失敗した。"""


@dataclass(frozen=True)
class RegionDestination:
    """1つの地方と、その地方の通勤先。"""

    name: str
    pref_cds: frozenset[int]
    station: str
    prefecture: str


def load_regions(path: Path) -> tuple[RegionDestination, ...]:
    """地方定義を読み、都道府県コードの網羅を検証して返す。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("regions") or []
    if not entries:
        raise RegionConfigError(f"{path} に regions がありません")

    regions: list[RegionDestination] = []
    seen: dict[int, str] = {}
    for entry in entries:
        name = entry.get("name")
        destination = entry.get("destination") or {}
        station = destination.get("station")
        prefecture = destination.get("prefecture")
        codes = entry.get("pref_cds") or []
        if not (name and station and prefecture and codes):
            raise RegionConfigError(f"地方定義が不完全です: {entry!r}")
        for code in codes:
            if code in seen:
                raise RegionConfigError(
                    f"都道府県コード {code} が '{seen[code]}' と '{name}' の両方にあります"
                )
            seen[code] = name
        regions.append(
            RegionDestination(
                name=str(name),
                pref_cds=frozenset(int(c) for c in codes),
                station=str(station),
                prefecture=str(prefecture),
            )
        )

    missing = ALL_PREFECTURE_CODES - seen.keys()
    if missing:
        raise RegionConfigError(
            f"どの地方にも属さない都道府県コードがあります: {sorted(missing)}"
            "（その県の駅が黙って対象外になる）"
        )
    unknown = seen.keys() - ALL_PREFECTURE_CODES
    if unknown:
        raise RegionConfigError(f"都道府県コードの範囲外です: {sorted(unknown)}")
    return tuple(regions)


def find_region(regions: tuple[RegionDestination, ...], name: str) -> RegionDestination | None:
    """地方名から定義を引く。"""
    return next((r for r in regions if r.name == name), None)
