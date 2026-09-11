"""土地（TOCHI・Phase 9a）の骨格 — 6種別目を足しても既存が黙って変わらないことの固定。

土地は「建物を伴わない売買」なので、既存の売買とは使える項目が大きく違う。
⚠ ここで固定しているのは、いずれも**例外にならず黙って壊れる**形である（→ 課題#61）。

1. レジストリ: 土地で使える metric / MUST が過不足なく決まっている。
   ⚠ `BUY_TYPES` に TOCHI を入れると間取り・相場比が土地へ黙って開く
2. スキーマ: 建物系の項目を土地パターンに書いたら**読み込みの段で弾く**
3. 名寄せ: 土地は「住所＋土地面積」で組む。⚠ 既存の `else` に落ちると
   間取り＋専有面積＋階を要求して**キーが常に None**になり、名寄せが静かに死ぬ
4. 設備条件: 辞書に無い条件コードを書いたら validate-config が NG にする。
   ⚠ 辞書に無い条件を MUST に1つ書くと、詳細取得済みが**全件 fail**になる。
   9c で土地の辞書（`tochi:`）ができたので、雛形の設備の WANT は
   ``tests/test_tochi_dictionary.py`` が固定している
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from house_search.cli import build_parser
from house_search.config import metrics as m
from house_search.config.pattern import load_pattern_file, parse_pattern
from house_search.dedup import key as dedup_key
from house_search.extract.dictionary import load_dictionary

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "configs" / "examples" / "tochi_buy_v2.yaml"
DICTIONARY_PATH = REPO / "data" / "feature_dictionary.yaml"


# --- 1. レジストリ -----------------------------------------------------------


def test_土地で使えるmetricは8つ() -> None:
    """定義順（＝スコア加算順）まで固定する。"""
    assert [spec.name for spec in m.metrics_for("TOCHI")] == [
        "price",
        "land_area_sqm",
        "walk_minutes",
        "commute_minutes",
        "flood_rank_avg",
        "flood_area_ratio",
        "landslide_area_ratio",
        "liquefaction_rank_avg",
    ]


def test_土地で使えるMUSTは7つ() -> None:
    assert [spec.name for spec in m.must_items_for("TOCHI")] == [
        "price_max",
        "land_area_min",
        "walk_minutes_max",
        "commute_minutes_max",
        "flood_rank_max",
        "landslide_special_ratio_max",
        "features",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "layouts",
        "area_min",
        "area_max",
        "building_area_min",
        "age_max",
        "floor_min",
        "monthly_cost_max",
        "rent_total_max",
        "market_rate_ratio_min",
    ],
)
def test_建物系のMUSTは土地に開かない(name: str) -> None:
    assert "TOCHI" not in m.MUST_ITEMS_BY_NAME[name].property_types


@pytest.mark.parametrize(
    "name",
    [
        "area_sqm",
        "building_area_sqm",
        "age_years",
        "monthly_cost",
        "rent_total",
        # ⚠ 相場比は土地の相場を入れる段（9d）で改めて判断する（→ 課題#61）
        "market_rate_ratio",
    ],
)
def test_建物系のmetricは土地に開かない(name: str) -> None:
    assert "TOCHI" not in m.METRICS_BY_NAME[name].property_types


def test_土地は独立したファミリになる() -> None:
    assert m.FAMILY_OF["TOCHI"] == "TOCHI_BUY"


def test_BUY_TYPESは建物を伴う売買4種別のまま() -> None:
    """⚠ `BUY_TYPES` に TOCHI を入れると `layouts` と `market_rate_ratio` が黙って開く。"""
    assert "TOCHI" not in m.BUY_TYPES
    assert "TOCHI" in m.ALL_PROPERTY_TYPES


# --- 2. スキーマ -------------------------------------------------------------


def _tochi(**overrides) -> dict:
    data = {
        "name": "土地テスト",
        "property_type": "TOCHI",
        "webhook_ref": "TOCHI",
        "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"]},
        "must": {"price_max": 80_000_000, "land_area_min": 60.0},
        "want": {
            "numeric": [
                {"metric": "price", "weight": 25, "best": 15_000_000, "worst": 60_000_000},
                {"metric": "land_area_sqm", "weight": 25, "best": 200, "worst": 80},
            ]
        },
    }
    data.update(overrides)
    return data


def test_土地パターンが読める() -> None:
    pattern = parse_pattern(_tochi())
    assert type(pattern).__name__ == "TochiBuyPattern"
    assert pattern.family == "TOCHI_BUY"
    assert pattern.must.price_max == 80_000_000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("layouts", ["3LDK"]),
        ("area_min", 50.0),
        ("building_area_min", 90.0),
        ("age_max", 20),
    ],
)
def test_土地パターンに建物系のMUSTは書けない(field: str, value: object) -> None:
    """⚠ 書けてしまうと判定されず全件 unknown になるだけで例外にならない。"""
    must = {"price_max": 80_000_000, field: value}
    with pytest.raises(ValidationError) as exc:
        parse_pattern(_tochi(must=must))
    assert field in str(exc.value)


@pytest.mark.parametrize(
    "metric", ["area_sqm", "building_area_sqm", "age_years", "market_rate_ratio"]
)
def test_土地パターンに建物系のWANTは書けない(metric: str) -> None:
    want = {"numeric": [{"metric": metric, "weight": 10, "best": 1, "worst": 2}]}
    with pytest.raises(ValidationError) as exc:
        parse_pattern(_tochi(want=want))
    assert f"metric '{metric}' は物件種別 TOCHI には適用できません" in str(exc.value)


def test_雛形YAMLが読める() -> None:
    pattern = load_pattern_file(TEMPLATE)
    assert type(pattern).__name__ == "TochiBuyPattern"
    assert pattern.property_type == "TOCHI"
    # ⚠ MUST の設備条件は書かない（→ 4.）。WANT は 9c で辞書ができたので置いてある
    # （中身は tests/test_tochi_dictionary.py の Test雛形 が辞書と突き合わせる）
    assert pattern.must.features == []


def test_雛形YAMLはconfigs直下に無い() -> None:
    """⚠ 直下に置いた瞬間から定期スキャンが新規掲載を全件通知する（→ ADR 0006）。"""
    assert not (REPO / "configs" / "tochi_buy_v2.yaml").exists()


def test_familyにTOCHI_BUYを指定できる() -> None:
    args = build_parser().parse_args(["scan", "--family", "TOCHI_BUY"])
    assert args.family == ["TOCHI_BUY"]


# --- 3. 名寄せ ---------------------------------------------------------------


def test_名寄せの土地ファミリ定数() -> None:
    assert dedup_key.FAMILY_TOCHI_BUY == "TOCHI_BUY"


def test_土地の名寄せは住所と土地面積で組む() -> None:
    components = dedup_key.dedup_components(
        family="TOCHI_BUY",
        address_normalized="東京都杉並区方南1丁目",
        land_area_sqm="120.50",
    )
    assert components == ["v2", "TOCHI_BUY", "東京都杉並区方南1丁目", "120.50"]


def test_土地は間取りと専有面積が無くてもキーを作れる() -> None:
    """⚠ 既存の else（間取り＋専有面積＋階）に落ちると、ここが None になる。"""
    value = dedup_key.compute_dedup_key(
        family="TOCHI_BUY",
        address_normalized="東京都杉並区方南1丁目",
        land_area_sqm="120.50",
    )
    assert value is not None and len(value) == 64


def test_土地面積が欠けたらキーを作らない() -> None:
    assert (
        dedup_key.compute_dedup_key(
            family="TOCHI_BUY", address_normalized="東京都杉並区方南1丁目", land_area_sqm=None
        )
        is None
    )


def test_戸建てと同じ住所と土地面積でも別のキーになる() -> None:
    """古家付きの土地と中古戸建てを同一視しない（ファミリをハッシュに混ぜる）。"""
    tochi = dedup_key.compute_dedup_key(
        family="TOCHI_BUY", address_normalized="東京都杉並区方南1丁目", land_area_sqm="120.50"
    )
    kodate = dedup_key.compute_dedup_key(
        family="KODATE_BUY",
        address_normalized="東京都杉並区方南1丁目",
        layout="3LDK",
        land_area_sqm="120.50",
        building_area_sqm="90.00",
    )
    assert tochi != kodate


def test_未知のファミリは例外にする() -> None:
    """⚠ 黙って既存の分岐へ落とすと、キーが None になるか別の意味のキーになる。"""
    with pytest.raises(ValueError, match="未知のファミリ"):
        dedup_key.dedup_components(
            family="SOMETHING_NEW",
            address_normalized="東京都杉並区方南1丁目",
            layout="1LDK",
            area_sqm="30.00",
            floor_num=1,
        )


# --- 4. 設備条件と辞書 ------------------------------------------------------


def test_導出コードは導出関数の出力と一致する() -> None:
    """⚠ 導出の条件を足したのに定数を直さないと、validate-config が偽陽性を出す。"""
    from house_search.extract.extractor import DERIVED_CODES, derive_features

    produced = {
        f.code
        for f in (
            *derive_features(floor_num=1, total_floors=1, age_years=1),
            *derive_features(floor_num=5, total_floors=5, age_years=30),
        )
    }
    assert produced == DERIVED_CODES


def test_土地の辞書に無い設備条件を拾う() -> None:
    from house_search.extract.pattern_check import unknown_condition_codes

    dictionary = load_dictionary(DICTIONARY_PATH)
    pattern = parse_pattern(
        _tochi(must={"price_max": 80_000_000, "features": ["LAND_BUILD_CONDITION"]})
    )
    assert unknown_condition_codes(pattern, dictionary) == ("LAND_BUILD_CONDITION",)


def test_WANTのany_ofも照合する() -> None:
    from house_search.extract.pattern_check import unknown_condition_codes

    dictionary = load_dictionary(DICTIONARY_PATH)
    want = {
        "features": [{"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 5}],
        "numeric": [{"metric": "price", "weight": 25, "best": 1, "worst": 2}],
    }
    pattern = parse_pattern(_tochi(want=want))
    assert unknown_condition_codes(pattern, dictionary) == ("STRUCT_RC", "STRUCT_SRC")


def test_既存ファミリの辞書にある条件は拾わない() -> None:
    """賃貸の辞書にある条件と導出コードは OK（既存6本に偽陽性を出さない側の担保）。"""
    from house_search.extract.pattern_check import unknown_condition_codes

    dictionary = load_dictionary(DICTIONARY_PATH)
    chintai = parse_pattern(
        {
            "name": "賃貸テスト",
            "property_type": "CHINTAI",
            "webhook_ref": "X",
            "sites": ["SUUMO"],
            "search": {"prefectures": ["東京都"]},
            "must": {"features": ["SEC_AUTOLOCK"]},
            "want": {"features": [{"code": "LOC_FLOOR_2UP", "weight": 3}]},
        }
    )
    assert unknown_condition_codes(chintai, dictionary) == ()


def _refuse_orphan_check(names: list[str]) -> int:
    raise AssertionError("別ディレクトリの検証で孤児スコアの確認が走った")


def test_validate_configは辞書に無い条件をNGにする(tmp_path: Path, monkeypatch) -> None:
    import yaml

    from house_search import cli

    # 別ディレクトリの検証では孤児スコアの確認（DB）に進まないことも同時に確かめる
    monkeypatch.setattr(cli, "_warn_orphan_scores", _refuse_orphan_check)
    bad = _tochi(must={"price_max": 80_000_000, "features": ["LAND_BUILD_CONDITION"]})
    (tmp_path / "tochi.yaml").write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    assert cli.main(["validate-config", "--configs-dir", str(tmp_path), "--skip-webhook"]) == 1

    (tmp_path / "tochi.yaml").write_text(
        yaml.safe_dump(_tochi(), allow_unicode=True), encoding="utf-8"
    )
    assert cli.main(["validate-config", "--configs-dir", str(tmp_path), "--skip-webhook"]) == 0


def test_別ディレクトリの検証では孤児スコアを案内しない(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """⚠ 雛形だけを置いた一時ディレクトリを検証すると、比べる相手が実運用の configs
    ではないので、**稼働中の全パターンが「孤児」と出て DELETE 文まで案内していた**
    （2026-09-11 に実際に出た。従うと約5.7万行の採点が消える → 課題#61）。
    """
    import yaml

    from house_search import cli

    monkeypatch.setattr(cli, "_warn_orphan_scores", _refuse_orphan_check)
    (tmp_path / "tochi.yaml").write_text(
        yaml.safe_dump(_tochi(), allow_unicode=True), encoding="utf-8"
    )
    assert cli.main(["validate-config", "--configs-dir", str(tmp_path), "--skip-webhook"]) == 0
    assert "孤児スコアの確認は省きます" in capsys.readouterr().out
