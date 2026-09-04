"""住所マスタ（``m_address_points``）の読み込みのテスト。

フィクスチャは位置参照情報（令和7年版）の**実データからの抜粋**で、
実装の都合で作った値は1つも入れていない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from house_search.dedup.address import AddressIndex, normalize_address
from house_search.dedup.address_master import (
    LEVEL_CHOME,
    LEVEL_TOWN,
    SOURCE,
    AddressMasterError,
    load_address_rows,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def loaded():
    # load_address_rows は data_dir/address_master/*.csv を読む。
    return load_address_rows(FIXTURE_DIR)


def _by_key(loaded) -> dict[str, object]:
    return {row.normalized_key: row for row in loaded.rows}


def test_丁目の行は町名と丁目番号に分かれる(loaded) -> None:
    row = _by_key(loaded)["東京都千代田区内幸町1丁目"]
    assert row.level == LEVEL_CHOME
    assert row.town_key == "東京都千代田区内幸町"
    assert row.chome_number == 1
    assert row.city_jis_code == "13101"
    assert row.source == SOURCE


def test_大字の行は町名までで丁目番号を持たない(loaded) -> None:
    row = _by_key(loaded)["埼玉県深谷市中瀬"]
    assert row.level == LEVEL_TOWN
    assert row.town_key == "埼玉県深谷市中瀬"
    assert row.chome_number is None


def test_区分1なら町名に丁目を含んでいても丁目行にしない(loaded) -> None:
    # 桶川市「坂田西一丁目」は原典が大字と言っている。丁目へ格上げしない。
    row = _by_key(loaded)["埼玉県桶川市坂田西1丁目"]
    assert row.level == LEVEL_TOWN
    assert row.chome_number is None


def test_正規化で同じキーになる原典行は集約したうえで記録される(loaded) -> None:
    # 飯能市の「原町」と「大字原町」は normalize_base が「大字」を落とすので同じキーになる。
    merged = dict(loaded.merged)
    assert "埼玉県飯能市原町" in merged
    assert merged["埼玉県飯能市原町"] == ("大字原町",)
    # 集約後は1行だけ残る。
    keys = [row.normalized_key for row in loaded.rows]
    assert keys.count("埼玉県飯能市原町") == 1


def test_区分3なのに末尾が丁目でなければ例外にする() -> None:
    from house_search.dedup.address_master import _build_row

    with pytest.raises(AddressMasterError):
        _build_row(
            {
                "都道府県名": "東京都",
                "市区町村名": "千代田区",
                "市区町村コード": "13101",
                "大字町丁目名": "内幸町",  # 丁目が無いのに区分3
                "緯度": "35.670839",
                "経度": "139.758119",
                "大字・字・丁目区分コード": "3",
            }
        )


def test_読み込んだ行から作った索引で番地誤認が直る(loaded) -> None:
    index = AddressIndex.build(
        [(row.town_key, row.chome_number) for row in loaded.rows]
    )
    # 丁目が存在しない大字（区分1）に番地が付いた形
    assert normalize_address("埼玉県深谷市中瀬1480-1", index=index) == "埼玉県深谷市中瀬"
    # 丁目が実在する町はこれまでどおり
    assert (
        normalize_address("東京都千代田区内幸町2-1-1", index=index)
        == "東京都千代田区内幸町2丁目"
    )
    # 実在しない丁目番号（内幸町は2丁目まで）
    assert normalize_address("東京都千代田区内幸町5-3", index=index) == "東京都千代田区内幸町"


def test_ディレクトリが無ければ例外にする(tmp_path: Path) -> None:
    with pytest.raises(AddressMasterError):
        load_address_rows(tmp_path)
