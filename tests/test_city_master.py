"""市区町村マスタの原典（総務省コード表）の回帰テスト。

``m_cities`` の欠落と誤りは「その市区が丸ごと検索対象から漏れる」
「別の市区のURLを叩く」に直結する。実測で名古屋市に北名古屋市のコードが、
大阪市に東大阪市のコードが混入していた（→ ADR 0014・課題#16）ので、
原典CSVと生成SQLの整合をここで固定する。

CSVを読むだけなのでDBは要らない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tools"))

from generate_city_seed import (  # noqa: E402
    CSV_PATH,
    SQL_PATH,
    CityRow,
    _assert_unique,
    read_csv,
    render_sql,
)

# 総務省コード表（基準日 2024-01-01）の行数。
# 市区町村1,747 = 一般に言う1,718市町村 ＋ 東京23区 ＋ 北方領土6村。
EXPECTED_TOTAL = 1918
EXPECTED_WARDS = 171


@pytest.fixture(scope="module")
def rows() -> list[CityRow]:
    return read_csv()


def test_全国47都道府県を網羅している(rows: list[CityRow]) -> None:
    assert len({row.prefecture for row in rows}) == 47


def test_行数が総務省コード表と一致する(rows: list[CityRow]) -> None:
    wards = [row for row in rows if row.parent_city]
    assert len(rows) == EXPECTED_TOTAL
    assert len(wards) == EXPECTED_WARDS


def test_jis_codeが全件そろっていて5桁である(rows: list[CityRow]) -> None:
    assert all(len(row.jis_code) == 5 and row.jis_code.isdigit() for row in rows)


def test_一意制約を破る組が無い(rows: list[CityRow]) -> None:
    """(都道府県, canonical_name) と jis_code の一意性。

    後者は ADR 0014 で部分ユニーク索引にしたので、ここで落ちるということは
    ``db-seed`` が途中で失敗するということ。
    """
    _assert_unique(rows)


def test_同名の泊村が郡名で区別されている(rows: list[CityRow]) -> None:
    """全国で唯一、同一都道府県内で市区町村名が衝突する組。

    後志総合振興局 古宇郡泊村（01403）と根室振興局 国後郡泊村（01696）。
    """
    tomari = sorted(
        (row.jis_code, row.canonical_name) for row in rows if row.city_name == "泊村"
    )
    assert tomari == [("01403", "泊村"), ("01696", "国後郡泊村")]


@pytest.mark.parametrize(
    ("prefecture", "canonical_name", "jis_code"),
    [
        # Phase 2 の実測補完で他市のコードが混入していた2件
        ("愛知県", "名古屋市", "23100"),
        ("大阪府", "大阪市", "27100"),
        # 2024年の区再編（7区→3区）に追随しておらず旧区のコードが付いていた3件
        ("静岡県", "浜松市中央区", "22138"),
        ("静岡県", "浜松市浜名区", "22139"),
        ("静岡県", "浜松市天竜区", "22140"),
        # 巻き添えで壊されていないことの確認
        ("愛知県", "北名古屋市", "23234"),
        ("大阪府", "東大阪市", "27227"),
    ],
)
def test_誤りが判明した市区のコードが正しい(
    rows: list[CityRow], prefecture: str, canonical_name: str, jis_code: str
) -> None:
    found = [
        row
        for row in rows
        if row.prefecture == prefecture and row.canonical_name == canonical_name
    ]
    assert len(found) == 1, f"{prefecture} {canonical_name} が1行でない"
    assert found[0].jis_code == jis_code


def test_政令市の区は市名を前置した正規名になっている(rows: list[CityRow]) -> None:
    wards = [row for row in rows if row.parent_city]
    assert all(row.canonical_name == row.parent_city + row.city_name for row in wards)
    yokohama = {row.canonical_name for row in wards if row.parent_city == "横浜市"}
    assert "横浜市西区" in yokohama
    assert len(yokohama) == 18


def test_特別区と町村には親市が付かない(rows: list[CityRow]) -> None:
    chiyoda = next(
        row for row in rows if row.prefecture == "東京都" and row.city_name == "千代田区"
    )
    assert chiyoda.parent_city is None
    assert chiyoda.canonical_name == "千代田区"


def test_生成SQLがCSVと同期している(rows: list[CityRow]) -> None:
    """``06_cities.sql`` は生成物。CSVを更新したら作り直す必要がある。

    手で SQL を編集したり、CSV だけ更新して生成を忘れたりすると、
    ``db-seed`` が流すマスタと原典が食い違う。
    """
    assert SQL_PATH.read_text(encoding="utf-8") == render_sql(rows), (
        "db/seed/06_cities.sql が原典と食い違っています。"
        " uv run python scripts/tools/generate_city_seed.py で作り直してください"
    )


def test_原典CSVが存在する() -> None:
    assert CSV_PATH.exists(), "data/city_master/soumu_local_gov_codes.csv が無い"
