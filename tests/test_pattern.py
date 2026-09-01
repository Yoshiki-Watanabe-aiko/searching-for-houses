"""検索パターンYAML（v2スキーマ）のテスト。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from house_search.config.pattern import (
    ChintaiPattern,
    KodateBuyPattern,
    MansionBuyPattern,
    load_pattern_file,
    parse_pattern,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _chintai(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "テスト賃貸",
        "property_type": "CHINTAI",
        "webhook_ref": "CHINTAI_ALONE",
        "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"], "price_max_hint": 90000},
        "must": {"rent_total_max": 70000, "area_min": 30.0},
        "want": {
            "features": [{"code": "SEC_AUTOLOCK", "weight": 8}],
            "numeric": [{"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000}],
        },
    }
    base.update(overrides)
    return base


def test_賃貸パターンが読める() -> None:
    pattern = parse_pattern(_chintai())
    assert isinstance(pattern, ChintaiPattern)
    assert pattern.must.rent_total_max == 70000
    assert pattern.ranking.top_n == 15  # 既定値


def test_物件種別で3ファミリへ分岐する() -> None:
    mansion = parse_pattern(
        _chintai(
            property_type="CHUKO_MANSION",
            must={"price_max": 50_000_000},
            want={"numeric": [{"metric": "price", "weight": 10, "best": 3e7, "worst": 5e7}]},
        )
    )
    assert isinstance(mansion, MansionBuyPattern)

    kodate = parse_pattern(
        _chintai(
            property_type="SHINCHIKU_KODATE",
            must={"price_max": 60_000_000, "land_area_min": 100.0},
            want={"numeric": [{"metric": "land_area_sqm", "weight": 8, "best": 150, "worst": 90}]},
        )
    )
    assert isinstance(kodate, KodateBuyPattern)


def test_種別に適用できないmetricは弾く() -> None:
    """戸建てに専有面積 metric を流用させない（混線と名寄せ事故の防止）。"""
    with pytest.raises(ValidationError, match="area_sqm"):
        parse_pattern(
            _chintai(
                property_type="CHUKO_KODATE",
                must={"price_max": 50_000_000},
                want={"numeric": [{"metric": "area_sqm", "weight": 5, "best": 90, "worst": 60}]},
            )
        )


def test_新築に築年数metricは使えない() -> None:
    with pytest.raises(ValidationError, match="age_years"):
        parse_pattern(
            _chintai(
                property_type="SHINCHIKU_MANSION",
                must={"price_max": 80_000_000},
                want={"numeric": [{"metric": "age_years", "weight": 4, "best": 0, "worst": 30}]},
            )
        )


def test_未知のmetricは弾く() -> None:
    with pytest.raises(ValidationError, match="未知の metric"):
        parse_pattern(
            _chintai(
                want={"numeric": [{"metric": "tsubo_tanka", "weight": 5, "best": 1, "worst": 2}]}
            )
        )


def test_種別に適用できないMUST項目は弾く() -> None:
    """賃貸に土地面積のMUSTを書けないようにする。"""
    with pytest.raises(ValidationError):
        parse_pattern(_chintai(must={"rent_total_max": 70000, "land_area_min": 100.0}))


def test_metricの重複を弾く() -> None:
    with pytest.raises(ValidationError, match="重複"):
        parse_pattern(
            _chintai(
                want={
                    "numeric": [
                        {"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000},
                        {"metric": "rent_total", "weight": 3, "best": 40000, "worst": 60000},
                    ]
                }
            )
        )


def test_bestとworstが同値なら弾く() -> None:
    with pytest.raises(ValidationError, match="0除算"):
        parse_pattern(
            _chintai(
                want={"numeric": [{"metric": "rent_total", "weight": 10, "best": 5, "worst": 5}]}
            )
        )


def test_綴り間違いを黙って無視しない() -> None:
    with pytest.raises(ValidationError):
        parse_pattern(_chintai(rankingg={"top_n": 5}))


def test_weightは正の数のみ() -> None:
    with pytest.raises(ValidationError):
        parse_pattern(_chintai(want={"features": [{"code": "SEC_AUTOLOCK", "weight": 0}]}))


def test_config_hashは検索範囲の変更では変わらない() -> None:
    """エリアを足しただけで全件再スコアが走らないようにする。"""
    a = parse_pattern(_chintai())
    b = parse_pattern(
        _chintai(search={"prefectures": ["東京都", "千葉県"], "price_max_hint": 120000})
    )
    assert a.config_hash() == b.config_hash()


def test_config_hashはWANTの変更で変わる() -> None:
    a = parse_pattern(_chintai())
    b = parse_pattern(
        _chintai(
            want={
                "features": [{"code": "SEC_AUTOLOCK", "weight": 9}],
                "numeric": [{"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000}],
            }
        )
    )
    assert a.config_hash() != b.config_hash()


def test_config_hashはPYTHONHASHSEEDに依存しない() -> None:
    """スコア再計算の判定に使う以上、プロセス間で同じ値でなければならない。

    dict/set の反復順に依存する実装が紛れ込むと、再起動のたびに全件再スコアが走る。
    """
    src_dir = str(REPO_ROOT / "src")
    script = (
        "import json,sys;"
        f"sys.path.insert(0, r'{src_dir}');"
        "from house_search.config.pattern import parse_pattern;"
        "print(parse_pattern(json.loads(sys.argv[1])).config_hash())"
    )
    payload = json.dumps(_chintai())

    hashes = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", script, payload],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        hashes.add(out.stdout.strip())
    assert len(hashes) == 1, f"PYTHONHASHSEED でハッシュが変わった: {hashes}"


def test_同梱の雛形YAMLが読める() -> None:
    pattern = load_pattern_file(REPO_ROOT / "configs" / "examples" / "chintai_v2.yaml")
    assert isinstance(pattern, ChintaiPattern)
    assert pattern.webhook_ref == "CHINTAI_ALONE"
    assert len(pattern.want.features) == 8
    assert len(pattern.want.numeric) == 4


def test_実運用の検索パターンが読める() -> None:
    """v1 から変換した chintai_alone.yaml が v2 スキーマを満たすこと（課題#9）。"""
    pattern = load_pattern_file(REPO_ROOT / "configs" / "chintai_alone.yaml")
    assert pattern.property_type == "CHINTAI"
    assert pattern.webhook_ref == "CHINTAI_ALONE"
    # RC / SRC は排他なので any_of で1項目にまとめてある
    any_of_items = [f for f in pattern.want.features if f.any_of]
    assert [f.codes for f in any_of_items] == [("STRUCT_RC", "STRUCT_SRC")]


def test_codeとany_ofの同時指定はエラーになる() -> None:
    with pytest.raises(ValidationError, match="code か any_of"):
        parse_pattern(
            _chintai(want={"features": [{"code": "A", "any_of": ["B", "C"], "weight": 1}]})
        )


def test_codeもany_ofも無いとエラーになる() -> None:
    with pytest.raises(ValidationError, match="code か any_of"):
        parse_pattern(_chintai(want={"features": [{"weight": 1}]}))


def test_any_ofが1件だけならエラーになる() -> None:
    with pytest.raises(ValidationError, match="2つ以上"):
        parse_pattern(_chintai(want={"features": [{"any_of": ["STRUCT_RC"], "weight": 1}]}))


def test_any_of内の条件コードも重複検査の対象になる() -> None:
    with pytest.raises(ValidationError, match="重複"):
        parse_pattern(
            _chintai(
                want={
                    "features": [
                        {"code": "STRUCT_RC", "weight": 1},
                        {"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 1},
                    ]
                }
            )
        )


def test_any_ofのキーは条件コードの昇順で安定する() -> None:
    pattern = parse_pattern(
        _chintai(want={"features": [{"any_of": ["STRUCT_SRC", "STRUCT_RC"], "weight": 1}]})
    )
    assert pattern.want.features[0].key == "STRUCT_RC|STRUCT_SRC"


def test_config_hashはany_ofの記法を区別する() -> None:
    merged = parse_pattern(
        _chintai(want={"features": [{"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 6}]})
    )
    split = parse_pattern(
        _chintai(
            want={
                "features": [
                    {"code": "STRUCT_RC", "weight": 6},
                    {"code": "STRUCT_SRC", "weight": 6},
                ]
            }
        )
    )
    # スコアの出方が変わる以上、自動再スコアが走るようハッシュも変わるべき
    assert merged.config_hash() != split.config_hash()
