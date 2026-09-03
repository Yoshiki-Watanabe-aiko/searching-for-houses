"""地方ごとの通勤先定義（data/commute_destinations.yaml）。

⚠ **47都道府県の網羅を機械で確かめる。** 穴があるとその県の駅が黙って
対象外になり、「全国を網羅したつもり」で気づけない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.commute.regions import (
    ALL_PREFECTURE_CODES,
    REGIONS_FILENAME,
    RegionConfigError,
    find_region,
    load_regions,
)
from house_search.config.settings import load_settings


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / REGIONS_FILENAME
    path.write_text(body, encoding="utf-8")
    return path


class TestLoadRegions:
    def test_都道府県に穴があれば読み込みで落とす(self, tmp_path: Path) -> None:
        """1県でも欠けたら例外。黙って対象外にすると網羅の失敗に気づけない。"""
        path = _write(
            tmp_path,
            "regions:\n"
            "  - name: 全部\n"
            "    pref_cds: [1, 2, 3]\n"
            "    destination: { station: 札幌, prefecture: 北海道 }\n",
        )
        with pytest.raises(RegionConfigError, match="どの地方にも属さない"):
            load_regions(path)

    def test_都道府県が2つの地方に重複していれば落とす(self, tmp_path: Path) -> None:
        """重複すると同じ駅を2つの目的地で取りにいき、取得が倍になる。"""
        codes = ", ".join(str(c) for c in sorted(ALL_PREFECTURE_CODES))
        path = _write(
            tmp_path,
            "regions:\n"
            f"  - name: 全部\n    pref_cds: [{codes}]\n"
            "    destination: { station: 東京, prefecture: 東京都 }\n"
            "  - name: 重複\n    pref_cds: [13]\n"
            "    destination: { station: 大阪, prefecture: 大阪府 }\n",
        )
        with pytest.raises(RegionConfigError, match="両方にあります"):
            load_regions(path)

    def test_定義が不完全なら落とす(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "regions:\n  - name: 目的地なし\n    pref_cds: [1]\n")
        with pytest.raises(RegionConfigError, match="不完全"):
            load_regions(path)

    def test_regionsが空なら落とす(self, tmp_path: Path) -> None:
        with pytest.raises(RegionConfigError, match="regions がありません"):
            load_regions(_write(tmp_path, "version: 1\n"))


class TestCanonicalFile:
    """正典 data/commute_destinations.yaml を固定する。"""

    def test_47都道府県を漏れなく重複なく覆っている(self) -> None:
        regions = load_regions(load_settings().data_dir / REGIONS_FILENAME)
        covered: set[int] = set()
        for region in regions:
            covered |= set(region.pref_cds)
        assert covered == set(ALL_PREFECTURE_CODES)

    def test_8地方と目的地がユーザー確定どおり(self) -> None:
        """2026-09-03 にユーザーが確定させた対応。勝手に変えない。"""
        regions = load_regions(load_settings().data_dir / REGIONS_FILENAME)
        assert {r.name: r.station for r in regions} == {
            "北海道": "札幌",
            "東北": "仙台",
            "関東": "東京",
            "中部": "名古屋",
            "近畿": "大阪",
            "中国": "広島",
            "四国": "高松",
            "九州・沖縄": "博多",
        }

    def test_地方名で引ける(self) -> None:
        regions = load_regions(load_settings().data_dir / REGIONS_FILENAME)
        kanto = find_region(regions, "関東")
        assert kanto is not None
        assert kanto.station == "東京"
        assert 13 in kanto.pref_cds  # 東京都
        assert find_region(regions, "無い地方") is None
