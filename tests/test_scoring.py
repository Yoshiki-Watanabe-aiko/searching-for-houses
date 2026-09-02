"""MUST 3値判定とWANTスコアリングのテスト。"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scoring.listing_view import ListingView, normalize_layout
from house_search.scoring.must import FAIL, PASS, UNKNOWN, evaluate_must
from house_search.scoring.score import STATUS_UNKNOWN, calculate_score, rank

BASE_PATTERN = {
    "name": "テスト",
    "property_type": "CHINTAI",
    "webhook_ref": "TEST",
    "sites": ["SUUMO"],
    "search": {"prefectures": ["東京都"]},
}


def make_pattern(**overrides):
    return parse_pattern({**BASE_PATTERN, **overrides})


def make_view(**overrides) -> ListingView:
    defaults = {
        "listing_id": 1,
        "price": 60000,
        "mgmt_fee_monthly": 3000,
        "area_sqm": 35.0,
        "layout": "1LDK",
        "age_years": 10,
        "walk_minutes": 10,
        "floor_num": 3,
        "detail_fetched": True,
        "feature_codes": frozenset(),
    }
    return ListingView(**{**defaults, **overrides})


# --- 間取り正規化 --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1SLDK", "1LDK"),
        ("2SLDK", "2LDK"),
        ("1LDK", "1LDK"),
        ("ワンルーム", "1R"),
        (" 2DK ", "2DK"),
        (None, None),
    ],
)
def test_間取りの正規化はサービスルーム表記を無視する(raw, expected) -> None:
    # 1SLDK は 1LDK に納戸が付いた形。1LDK を許容するなら除外する理由がない
    assert normalize_layout(raw) == expected


# --- MUST 3値判定 --------------------------------------------------------


def test_上限を超えたらfail() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000})
    assert evaluate_must(make_view(price=70000, mgmt_fee_monthly=1000), pattern.must).result == FAIL


def test_上限ちょうどはpass() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000})
    assert evaluate_must(make_view(price=67000, mgmt_fee_monthly=3000), pattern.must).result == PASS


def test_値が取れなければunknown() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000})
    result = evaluate_must(make_view(price=None, mgmt_fee_monthly=None), pattern.must)
    assert result.result == UNKNOWN
    assert result.unknown_names == ("rent_total_max",)


def test_面積の下限は_area_sqm_を見る() -> None:
    # 項目名から機械的に metric 名を導くと area_min が area になって取り違える
    pattern = make_pattern(must={"area_min": 30.0})
    assert evaluate_must(make_view(area_sqm=29.9), pattern.must).result == FAIL
    assert evaluate_must(make_view(area_sqm=30.0), pattern.must).result == PASS


def test_所在階の下限は_floor_num_を見る() -> None:
    pattern = make_pattern(must={"floor_min": 2})
    assert evaluate_must(make_view(floor_num=1), pattern.must).result == FAIL
    assert evaluate_must(make_view(floor_num=2), pattern.must).result == PASS


def test_失敗が1つでもあれば全体がfail() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000, "area_min": 30.0})
    result = evaluate_must(make_view(area_sqm=20.0, price=None), pattern.must)
    assert result.result == FAIL
    assert result.failed_names == ("area_min",)


def test_必須設備は詳細未取得ならunknown_取得済みなら不足でfail() -> None:
    pattern = make_pattern(must={"features": ["SEC_AUTOLOCK"]})
    assert evaluate_must(make_view(detail_fetched=False), pattern.must).result == UNKNOWN
    assert evaluate_must(make_view(detail_fetched=True), pattern.must).result == FAIL
    passed = make_view(detail_fetched=True, feature_codes=frozenset({"SEC_AUTOLOCK"}))
    assert evaluate_must(passed, pattern.must).result == PASS


def test_一覧段階では詳細でしか判定できない項目をunknownにする() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000, "features": ["SEC_AUTOLOCK"]})
    result = evaluate_must(make_view(detail_fetched=True), pattern.must, list_stage_only=True)
    # 賃料は判定でき、設備は保留される → 詳細を取りに行くべき状態
    assert result.result == UNKNOWN
    assert not result.is_fail


def test_一覧段階でfailなら詳細を取りに行かない() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000})
    result = evaluate_must(make_view(price=200000), pattern.must, list_stage_only=True)
    assert result.is_fail


def test_unknown_policyで判定不能の扱いが変わる() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000})
    result = evaluate_must(make_view(price=None, mgmt_fee_monthly=None), pattern.must)
    assert result.passes("keep") is True
    assert result.passes("drop") is False


def test_failはunknown_policyに関わらず通さない() -> None:
    pattern = make_pattern(must={"rent_total_max": 70000})
    result = evaluate_must(make_view(price=200000), pattern.must)
    assert result.passes("keep") is False


# --- WANTスコア ----------------------------------------------------------


def test_設備が全て揃えば満点() -> None:
    pattern = make_pattern(
        want={"features": [{"code": "SEC_AUTOLOCK", "weight": 10}]},
    )
    view = make_view(feature_codes=frozenset({"SEC_AUTOLOCK"}))
    assert calculate_score(view, pattern.want).score == 100.0


def test_数値条件はbest_worstで線形正規化される() -> None:
    pattern = make_pattern(
        want={"numeric": [{"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000}]}
    )
    # 賃料60000 + 管理費0 → ちょうど中間
    assert calculate_score(make_view(price=60000, mgmt_fee_monthly=0), pattern.want).score == 50.0


def test_範囲外の値はクランプされる() -> None:
    pattern = make_pattern(
        want={"numeric": [{"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000}]}
    )
    assert calculate_score(make_view(price=30000, mgmt_fee_monthly=0), pattern.want).score == 100.0
    assert calculate_score(make_view(price=90000, mgmt_fee_monthly=0), pattern.want).score == 0.0


def test_any_ofはいずれか1つ該当すれば満点() -> None:
    pattern = make_pattern(
        want={"features": [{"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 10}]}
    )
    for code in ("STRUCT_RC", "STRUCT_SRC"):
        view = make_view(feature_codes=frozenset({code}))
        assert calculate_score(view, pattern.want).score == 100.0
    wood = make_view(feature_codes=frozenset({"STRUCT_WOOD"}))
    assert calculate_score(wood, pattern.want).score == 0.0


def test_any_ofは分母を二重に消費しない() -> None:
    # RC と SRC へ別々に weight を振ると片方は必ず miss になり、
    # 満たしているのに満点が取れなくなる。any_of はその穴を塞ぐためにある
    merged = make_pattern(
        want={
            "features": [
                {"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 6},
                {"code": "SEC_AUTOLOCK", "weight": 4},
            ]
        }
    )
    split = make_pattern(
        want={
            "features": [
                {"code": "STRUCT_RC", "weight": 6},
                {"code": "STRUCT_SRC", "weight": 6},
                {"code": "SEC_AUTOLOCK", "weight": 4},
            ]
        }
    )
    view = make_view(feature_codes=frozenset({"STRUCT_RC", "SEC_AUTOLOCK"}))
    assert calculate_score(view, merged.want).score == 100.0
    assert calculate_score(view, split.want).score < 100.0


def test_欠損metricは分子と分母の双方から除外される() -> None:
    # 価格未定の新築マンションを0点扱いにすると不当に沈むため、再正規化する
    pattern = make_pattern(
        want={
            "numeric": [
                {"metric": "rent_total", "weight": 90, "best": 50000, "worst": 70000},
                {"metric": "area_sqm", "weight": 10, "best": 45, "worst": 30},
            ]
        }
    )
    view = make_view(price=None, mgmt_fee_monthly=None, area_sqm=45.0)
    result = calculate_score(view, pattern.want)
    # 面積だけが満点 → 欠損を除外して再正規化すれば100点
    assert result.score == 100.0
    missing = [item for item in result.items if item.missing]
    assert [item.code for item in missing] == ["rent_total"]


def test_設備の未確認は0点だが分母には残る() -> None:
    pattern = make_pattern(
        want={
            "features": [{"code": "SEC_AUTOLOCK", "weight": 10}],
            "numeric": [{"metric": "area_sqm", "weight": 10, "best": 45, "worst": 30}],
        }
    )
    view = make_view(detail_fetched=False, area_sqm=45.0)
    result = calculate_score(view, pattern.want)
    # 未確認を満点扱いにするほうが誤りが大きいので、0点として分母に残す
    assert result.score == 50.0
    assert result.unknown_count == 1
    autolock = next(item for item in result.items if item.code == "SEC_AUTOLOCK")
    assert autolock.status == STATUS_UNKNOWN


def test_内訳は全項目を保持しJSON化できる() -> None:
    pattern = make_pattern(
        want={
            "features": [{"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 6}],
            "numeric": [{"metric": "area_sqm", "weight": 10, "best": 45, "worst": 30}],
        }
    )
    breakdown = calculate_score(make_view(), pattern.want).breakdown()
    assert len(breakdown) == 2
    assert {"code", "name", "kind", "weight", "s", "points", "status"} <= set(breakdown[0])


def test_得点上位は寄与の大きい順で同点はコード順() -> None:
    pattern = make_pattern(
        want={
            "features": [
                {"code": "SEC_AUTOLOCK", "weight": 5},
                {"code": "INT_LAUNDRY", "weight": 5},
                {"code": "BATH_SEPARATE", "weight": 9},
            ]
        }
    )
    view = make_view(feature_codes=frozenset({"SEC_AUTOLOCK", "INT_LAUNDRY", "BATH_SEPARATE"}))
    top = calculate_score(view, pattern.want).top_hits(3)
    assert [item.code for item in top] == ["BATH_SEPARATE", "INT_LAUNDRY", "SEC_AUTOLOCK"]


def test_順位は同点なら物件ID昇順で安定する() -> None:
    pattern = make_pattern(
        want={"numeric": [{"metric": "area_sqm", "weight": 1, "best": 45, "worst": 30}]}
    )
    results = {
        pid: calculate_score(make_view(listing_id=pid, area_sqm=40.0), pattern.want)
        for pid in (30, 10, 20)
    }
    assert rank(results) == {10: 1, 20: 2, 30: 3}


def test_weightが0以下なら設定エラーになる() -> None:
    with pytest.raises(ValueError):
        make_pattern(want={"features": [{"code": "SEC_AUTOLOCK", "weight": 0}]})


# --- 決定性 --------------------------------------------------------------


_DETERMINISM_SCRIPT = textwrap.dedent(
    """
    from house_search.config.pattern import parse_pattern
    from house_search.scoring.listing_view import ListingView
    from house_search.scoring.score import calculate_score

    pattern = parse_pattern({
        "name": "t", "property_type": "CHINTAI", "webhook_ref": "T", "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"]},
        "want": {
            "features": [
                {"code": "SEC_AUTOLOCK", "weight": 8},
                {"code": "INT_LAUNDRY", "weight": 10},
                {"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 6},
                {"code": "BATH_SEPARATE", "weight": 9},
            ],
            "numeric": [
                {"metric": "area_sqm", "weight": 6, "best": 45, "worst": 30},
                {"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000},
            ],
        },
    })
    view = ListingView(
        price=58000, mgmt_fee_monthly=2000, area_sqm=38.0, detail_fetched=True,
        feature_codes=frozenset({"SEC_AUTOLOCK", "STRUCT_RC", "INT_LAUNDRY"}),
    )
    result = calculate_score(view, pattern.want)
    print(result.score, [i.code for i in result.items], pattern.config_hash())
    """
)


def test_スコアと内訳順序はPYTHONHASHSEEDに依存しない() -> None:
    """set の反復順が変わってもスコア・内訳順・config_hash が一致すること。"""
    outputs = set()
    for seed in ("0", "1", "12345"):
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _DETERMINISM_SCRIPT],
            capture_output=True,
            text=True,
            check=True,
            # Windows では SYSTEMROOT 等が要るため、環境ごと引き継いで seed だけ差し替える
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        outputs.add(completed.stdout.strip())
    assert len(outputs) == 1, f"PYTHONHASHSEED で結果が変わりました: {outputs}"
