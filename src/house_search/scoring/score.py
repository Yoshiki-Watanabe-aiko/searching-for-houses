"""WANTスコアの計算。

``score = 100 × Σ(wᵢ × sᵢ) / Σ(wᵢ)`` の0〜100点。単位の異なる条件は
正規化値 s で無次元化されるため、weight だけで相対優先度を表現できる。

要点:
  - **欠損metricは分子・分母の双方から除外して再正規化**する。0点扱いにすると
    価格未定の新築マンションが不当に沈む
  - WANT の設備が判定不能（詳細ページ未取得）なら0点だが status は unknown にし、
    通知に「未確認N項目」として出す。中間値の補完はしない
  - 加算は条件コード順に固定する。``PYTHONHASHSEED`` を変えても同じ値になる
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from house_search.config.metrics import METRICS_BY_NAME, normalize
from house_search.scoring.listing_view import ListingView

STATUS_HIT = "hit"
STATUS_MISS = "miss"
STATUS_UNKNOWN = "unknown"

KIND_FEATURE = "feature"
KIND_NUMERIC = "numeric"


@dataclass(frozen=True, slots=True)
class ScoreItem:
    """採点内訳1件。"""

    code: str
    name: str
    kind: str
    weight: float
    s: float
    points: float
    status: str
    missing: bool = False
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """``t_listing_scores.score_breakdown`` へ入れる形。"""
        payload: dict[str, Any] = {
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "weight": self.weight,
            "s": round(self.s, 4),
            "points": round(self.points, 4),
            "status": self.status,
        }
        if self.missing:
            payload["missing"] = True
        if self.value is not None:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """1物件・1パターンぶんの採点結果。"""

    score: float
    items: tuple[ScoreItem, ...]

    @property
    def unknown_count(self) -> int:
        """判定不能だった項目数（通知に「未確認N項目」として出す）。"""
        return sum(1 for item in self.items if item.status == STATUS_UNKNOWN)

    def top_hits(self, limit: int = 3) -> tuple[ScoreItem, ...]:
        """得点への寄与が大きい順に上位を返す（同点は条件コード順で安定させる）。"""
        hits = [item for item in self.items if item.points > 0]
        hits.sort(key=lambda item: (-item.points, item.code))
        return tuple(hits[:limit])

    def breakdown(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.items]


def _feature_item(feat: Any, view: ListingView) -> ScoreItem:
    """WANT設備1件を採点する。``any_of`` はいずれか1つ該当すれば満点。"""
    codes = feat.codes
    label = " / ".join(codes) if len(codes) > 1 else codes[0]
    if not view.detail_fetched:
        status, s = STATUS_UNKNOWN, 0.0
    elif any(code in view.feature_codes for code in codes):
        status, s = STATUS_HIT, 1.0
    else:
        status, s = STATUS_MISS, 0.0
    return ScoreItem(
        code=feat.key,
        name=label,
        kind=KIND_FEATURE,
        weight=feat.weight,
        s=s,
        points=feat.weight * s,
        status=status,
    )


def _numeric_item(item: Any, view: ListingView) -> ScoreItem:
    """WANT数値条件1件を採点する。値が取れなければ欠損として扱う。"""
    spec = METRICS_BY_NAME[item.metric]
    value = view.metric_value(item.metric)
    if value is None:
        return ScoreItem(
            code=item.metric,
            name=spec.label,
            kind=KIND_NUMERIC,
            weight=item.weight,
            s=0.0,
            points=0.0,
            status=STATUS_UNKNOWN,
            missing=True,
        )
    s = normalize(value, best=item.best, worst=item.worst)
    return ScoreItem(
        code=item.metric,
        name=spec.label,
        kind=KIND_NUMERIC,
        weight=item.weight,
        s=s,
        points=item.weight * s,
        status=STATUS_HIT if s > 0 else STATUS_MISS,
        value=value,
    )


def calculate_score(view: ListingView, want: Any) -> ScoreResult:
    """WANTスコアを計算する。

    設備は条件コード順、数値は metric 名順に固定して加算するため、
    プロセスをまたいでも同じ入力からは同じスコアが出る。
    """
    items: list[ScoreItem] = [
        _feature_item(feat, view) for feat in sorted(want.features, key=lambda f: f.key)
    ]
    items.extend(
        _numeric_item(item, view) for item in sorted(want.numeric, key=lambda i: i.metric)
    )

    # 欠損metricは分子・分母の双方から外して再正規化する。
    # 設備の unknown は0点として分母に残す（「無い」ことの証拠ではないが、
    # 未確認のまま満点扱いにするほうが誤りが大きい）。
    scored = [item for item in items if not item.missing]
    total_weight = sum(item.weight for item in scored)
    score = 100.0 * sum(item.points for item in scored) / total_weight if total_weight else 0.0

    return ScoreResult(score=round(score, 3), items=tuple(items))


def rank(results: dict[int, ScoreResult]) -> dict[int, int]:
    """物件ID → パターン内順位（1始まり）。

    同点は物件IDの昇順で決める（順位が実行ごとに揺れないように）。
    """
    ordered = sorted(results.items(), key=lambda kv: (-kv[1].score, kv[0]))
    return {listing_id: index for index, (listing_id, _) in enumerate(ordered, start=1)}
