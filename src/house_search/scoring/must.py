"""MUST条件の3値判定。

判定は ``pass`` / ``fail`` / ``unknown`` の3値で、**詳細ページの取得を
スキップするのは fail のみ**。売買では管理費・修繕積立金・権利形態が一覧に
出ず詳細を見ないと判定できないが、この問題は賃貸の敷金礼金でも潜在するため
Phase 1 から3値で実装している。

2段判定: 一覧ページだけで判定できる項目（MetricRegistry の
``available_on_list``）を先に評価し、fail なら詳細を取りに行かない。
"""

from __future__ import annotations

from dataclasses import dataclass

from house_search.config.metrics import MUST_ITEMS_BY_NAME
from house_search.scoring.property_view import PropertyView, normalize_layout

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

# 判定の悪いほうが勝つ（1つでも fail があれば全体は fail）。
_SEVERITY = {PASS: 0, UNKNOWN: 1, FAIL: 2}

# MUST項目名 → PropertyView から値を取り出すときの metric 名。
# 項目名から機械的に導くと ``area_min`` が ``area`` になるなど取り違えるため明示する。
_VALUE_METRIC: dict[str, str] = {
    "rent_total_max": "rent_total",
    "price_max": "price",
    "monthly_cost_max": "monthly_cost",
    "area_min": "area_sqm",
    "area_max": "area_sqm",
    "land_area_min": "land_area_sqm",
    "building_area_min": "building_area_sqm",
    "age_max": "age_years",
    "walk_minutes_max": "walk_minutes",
    "floor_min": "floor_num",
}


@dataclass(frozen=True, slots=True)
class MustCheck:
    """MUST項目1件の判定結果。"""

    name: str
    label: str
    result: str
    expected: object
    actual: object


@dataclass(frozen=True, slots=True)
class MustResult:
    """物件1件ぶんのMUST判定。"""

    result: str
    checks: tuple[MustCheck, ...]

    @property
    def is_fail(self) -> bool:
        return self.result == FAIL

    @property
    def failed_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if c.result == FAIL)

    @property
    def unknown_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if c.result == UNKNOWN)

    def passes(self, unknown_policy: str) -> bool:
        """通知・ランキングの対象にしてよいか。

        ``unknown_policy`` が ``keep`` なら判定不能を通し、``drop`` なら除外する。
        """
        if self.result == FAIL:
            return False
        if self.result == UNKNOWN:
            return unknown_policy == "keep"
        return True


def _compare_max(value: float | None, limit: float) -> str:
    if value is None:
        return UNKNOWN
    return PASS if value <= limit else FAIL


def _compare_min(value: float | None, limit: float) -> str:
    if value is None:
        return UNKNOWN
    return PASS if value >= limit else FAIL


def _check_layouts(view: PropertyView, allowed: list[str]) -> tuple[str, object]:
    actual = view.normalized_layout
    if actual is None:
        return UNKNOWN, None
    allowed_normalized = {normalize_layout(item) for item in allowed}
    return (PASS if actual in allowed_normalized else FAIL), view.layout


def _check_features(view: PropertyView, required: list[str]) -> tuple[str, object]:
    """必須設備の判定。

    詳細ページ未取得なら判定できないので unknown。取得済みなら
    抽出結果に無い＝その物件には無いとみなして fail にする。
    """
    if not view.detail_fetched:
        return UNKNOWN, None
    missing = sorted(set(required) - view.feature_codes)
    return (PASS if not missing else FAIL), missing


def evaluate_must(view: PropertyView, must: object, *, list_stage_only: bool = False) -> MustResult:
    """MUST条件を評価する。

    ``list_stage_only`` が True のときは一覧ページだけで判定できる項目に限定し、
    残りは ``unknown`` にする。詳細取得の要否を決める1段目の判定に使う。
    """
    checks: list[MustCheck] = []

    # pydantic モデルのフィールド順ではなくレジストリ定義順で回して決定性を保つ。
    for name, spec in MUST_ITEMS_BY_NAME.items():
        expected = getattr(must, name, None)
        if expected is None or expected == [] or expected == "":
            continue

        if list_stage_only and not spec.available_on_list:
            checks.append(MustCheck(name, spec.label, UNKNOWN, expected, None))
            continue

        if name in _VALUE_METRIC:
            actual = view.metric_value(_VALUE_METRIC[name])
            compare = _compare_max if name.endswith("_max") else _compare_min
            result = compare(actual, float(expected))
        elif name == "layouts":
            result, actual = _check_layouts(view, list(expected))
        elif name == "features":
            result, actual = _check_features(view, list(expected))
        else:  # pragma: no cover - レジストリに項目を足したら明示的に落とす
            raise ValueError(f"MUST項目 '{name}' の判定方法が未実装です")

        checks.append(MustCheck(name, spec.label, result, expected, actual))

    overall = PASS
    for check in checks:
        if _SEVERITY[check.result] > _SEVERITY[overall]:
            overall = check.result
    return MustResult(result=overall, checks=tuple(checks))
