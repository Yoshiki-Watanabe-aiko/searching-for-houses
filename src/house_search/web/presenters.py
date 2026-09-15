"""テンプレートへ渡す表示用の値へ詰め替える。

⚠ **テンプレートには ORM 行・設定オブジェクトを渡さず、ここで作った値だけを渡す。**
画面に出る値の出どころを限り、接続文字列や Webhook URL が紛れ込む経路を作らない。

⚠ 金額・条件・交通の文言は ``notify.format`` の関数をそのまま使う
（Discord の通知と画面で同じ物件の書き方が食い違わないように）。

⚠⚠ **ハザードの None と 0.0 を混ぜない**（→ ADR 0021）。None は「住所を照合できず
情報が無い」、0.0 は「照合できたうえで区域外と確認した」。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from house_search.config.metrics import METRICS_BY_NAME
from house_search.notify.format import (
    NotifiableListing,
    man_yen,
    price_field,
    price_summary,
    summary_line,
    yen,
)
from house_search.scoring.listing_view import ListingView
from house_search.scoring.must import FAIL, PASS, UNKNOWN, MustResult
from house_search.scoring.score import STATUS_HIT, STATUS_MISS, STATUS_UNKNOWN
from house_search.web.security import safe_href

JST = ZoneInfo("Asia/Tokyo")

MUST_RESULT_LABELS: dict[str, str] = {
    PASS: "満たす",
    FAIL: "満たさない",
    UNKNOWN: "判定できない",
}
SCORE_STATUS_LABELS: dict[str, str] = {
    STATUS_HIT: "加点",
    STATUS_MISS: "0点",
    STATUS_UNKNOWN: "未確認",
}
LISTING_STATUS_LABELS: dict[str, str] = {
    "active": "掲載中",
    "sold": "成約",
    "removed": "掲載終了",
}
_BUY_FAMILIES = frozenset({"MANSION_BUY", "KODATE_BUY", "TOCHI_BUY"})
_MONEY_UNITS = frozenset({"円", "円/月"})
#: MUST 項目 → 値の単位（表示専用。判定は scoring.must）
_MUST_UNITS: dict[str, str] = {
    "rent_total_max": "円",
    "price_max": "円",
    "monthly_cost_max": "円",
    "area_min": "㎡",
    "area_max": "㎡",
    "land_area_min": "㎡",
    "building_area_min": "㎡",
    "age_max": "年",
    "walk_minutes_max": "分",
    "commute_minutes_max": "分",
    "floor_min": "階",
}


def format_datetime(value: dt.datetime | None) -> str:
    """日時を日本時間の「YYYY-MM-DD HH:MM」で出す。無ければ「—」。"""
    if value is None:
        return "—"
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.astimezone(JST).strftime("%Y-%m-%d %H:%M")


def format_number(value: float) -> str:
    """整数なら整数で、小数なら小数第2位まで（末尾の0は落とす）。"""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def format_metric_value(code: str, value: float | None) -> str:
    """採点内訳の値を metric の単位で書く。"""
    if value is None:
        return "—"
    spec = METRICS_BY_NAME.get(code)
    unit = spec.unit if spec else ""
    if unit in _MONEY_UNITS:
        return f"{round(value):,}{unit}"
    if unit == "割合":
        return f"{value * 100:.1f}%"
    if unit == "ランク":
        return f"{value:.2f}"
    return f"{format_number(value)}{unit}"


def format_must_value(name: str, value: object) -> str:
    """MUST の条件値・実測値を書く。"""
    if value is None:
        return "不明"
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(str(item) for item in value)
        return "、".join(items) if items else "なし"
    if isinstance(value, (int, float)):
        unit = _MUST_UNITS.get(name, "")
        if unit == "円":
            return yen(round(value))
        return f"{format_number(float(value))}{unit}"
    return str(value)


def unknown_count(breakdown: Sequence[Mapping[str, Any]] | None) -> int:
    """保存済みの採点内訳のうち「未確認」の項目数。"""
    return sum(1 for item in breakdown or () if item.get("status") == STATUS_UNKNOWN)


# ---------------------------------------------------------------------------
# ランキング一覧
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RankingItem:
    listing_id: int
    rank: int
    score: float
    title: str
    external_href: str | None
    price_text: str
    summary_text: str
    address: str
    sites_text: str
    must_result: str
    must_label: str
    unknown_count: int
    is_favorite: bool
    is_excluded: bool
    memo_count: int
    first_seen_text: str
    is_stale: bool


def ranking_items(
    rows: Iterable[Any],
    *,
    views: Mapping[int, ListingView],
    notifiables: Mapping[int, NotifiableListing],
    memo_counts: Mapping[int, int],
    config_hash: str,
) -> list[RankingItem]:
    """一覧の行を表示用に詰め替える。ビューが読めなかった行は落とさず最小限で出す。"""
    items: list[RankingItem] = []
    for row in rows:
        view = views.get(row.listing_id)
        prop = notifiables.get(row.listing_id)
        if prop is not None:
            price_text = price_summary(prop)
            summary_text = summary_line(prop)
            others = len(prop.listing_sites) - 1
            sites_text = f"{prop.site_code} ほか{others}" if others > 0 else prop.site_code
        else:
            price_text = man_yen(row.price) if row.rent_total is None else yen(row.rent_total)
            summary_text = "—"
            sites_text = row.site_code
        items.append(
            RankingItem(
                listing_id=row.listing_id,
                rank=row.rank_in_pattern,
                score=float(row.score),
                title=(view.title if view else None) or "（物件名なし）",
                external_href=safe_href(view.url if view else None),
                price_text=price_text,
                summary_text=summary_text,
                address=(view.address if view else None) or "住所不明",
                sites_text=sites_text,
                must_result=row.must_result,
                must_label=MUST_RESULT_LABELS.get(row.must_result, row.must_result),
                unknown_count=unknown_count(row.score_breakdown),
                is_favorite=bool(row.is_favorite),
                is_excluded=bool(row.is_excluded),
                memo_count=memo_counts.get(row.listing_id, 0),
                first_seen_text=format_datetime(row.first_seen_at)[:10],
                is_stale=row.config_hash != config_hash,
            )
        )
    return items


# ---------------------------------------------------------------------------
# 物件の詳細
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BreakdownItem:
    name: str
    kind_label: str
    weight: str
    points: str
    status: str
    status_label: str
    value_text: str
    missing: bool
    detail_text: str | None


def breakdown_items(breakdown: Sequence[Mapping[str, Any]] | None) -> list[BreakdownItem]:
    """保存済みの採点内訳を表の行にする（再採点はしない）。"""
    items: list[BreakdownItem] = []
    for entry in breakdown or ():
        status = str(entry.get("status", ""))
        detail = entry.get("detail")
        items.append(
            BreakdownItem(
                name=str(entry.get("name") or entry.get("code") or "—"),
                kind_label="設備" if entry.get("kind") == "feature" else "数値",
                weight=format_number(float(entry.get("weight", 0))),
                points=f"{float(entry.get('points', 0)):.1f}",
                status=status,
                status_label=(
                    "欠損（分母から除外）"
                    if entry.get("missing")
                    else SCORE_STATUS_LABELS.get(status, status)
                ),
                value_text=format_metric_value(str(entry.get("code", "")), entry.get("value")),
                missing=bool(entry.get("missing")),
                detail_text=(
                    json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else None
                ),
            )
        )
    return items


@dataclass(frozen=True, slots=True)
class MustItem:
    label: str
    expected_text: str
    actual_text: str
    result: str
    result_label: str


def must_items(result: MustResult) -> list[MustItem]:
    return [
        MustItem(
            label=check.label,
            expected_text=format_must_value(check.name, check.expected),
            actual_text=format_must_value(check.name, check.actual),
            result=check.result,
            result_label=MUST_RESULT_LABELS.get(check.result, check.result),
        )
        for check in result.checks
    ]


@dataclass(frozen=True, slots=True)
class HazardItem:
    label: str
    value_text: str
    resolved: bool


#: (ListingView の属性, 表示名, 値の書き方, 0.0 を「区域外」と書くか)
_HAZARDS: tuple[tuple[str, str, str, bool], ...] = (
    ("flood_rank_max", "洪水: 丁目内の最大浸水深ランク（0〜6）", "rank", True),
    ("flood_rank_avg", "洪水: 浸水深ランクの面積加重平均（0〜6）", "rank", True),
    ("flood_area_ratio", "洪水: 浸水域が丁目に占める面積比", "ratio", True),
    ("landslide_area_ratio", "土砂災害警戒区域が丁目に占める面積比", "ratio", True),
    ("landslide_special_ratio", "土砂災害特別警戒区域（レッドゾーン）の面積比", "ratio", True),
    (
        "liquefaction_rank_avg",
        "液状化: 危険ランクの面積加重平均（1〜5・原典のレベルは 6−値）",
        "rank",
        False,
    ),
)


def hazard_items(view: ListingView) -> list[HazardItem]:
    items: list[HazardItem] = []
    for attr, label, kind, zero_is_outside in _HAZARDS:
        value = getattr(view, attr)
        if value is None:
            # ⚠ 未解決を 0 と書かない（「安全」と読まれる）
            items.append(HazardItem(label, "未解決（住所を照合できず情報なし）", False))
            continue
        text = f"{value * 100:.1f}%" if kind == "ratio" else f"{value:.2f}"
        if zero_is_outside and value == 0:
            text += "（区域外と確認）"
        items.append(HazardItem(label, text, True))
    return items


@dataclass(frozen=True, slots=True)
class MemberItem:
    listing_id: int
    site_text: str
    external_href: str | None
    title: str
    price_text: str
    status_label: str
    is_active: bool
    is_representative: bool
    is_current: bool
    rank_text: str
    detail_fetched_text: str
    last_seen_text: str
    raw_features_text: str | None
    is_favorite: bool
    is_excluded: bool
    memo: str | None


def member_items(
    rows: Iterable[Any],
    *,
    family: str,
    current_id: int,
    marks: Mapping[int, Any],
) -> list[MemberItem]:
    items: list[MemberItem] = []
    for row in rows:
        if family in _BUY_FAMILIES:
            price_text = man_yen(row.price)
        else:
            price_text = (
                f"{yen(row.rent_total)}"
                f"（賃料 {yen(row.price)} + 管理費 {yen(row.mgmt_fee_monthly)}）"
            )
        mark = marks.get(row.id)
        items.append(
            MemberItem(
                listing_id=row.id,
                site_text=f"{row.site_name}（{row.site_code}）",
                external_href=safe_href(row.url),
                title=row.title or "（物件名なし）",
                price_text=price_text,
                status_label=LISTING_STATUS_LABELS.get(row.status, row.status),
                is_active=row.status == "active",
                is_representative=bool(row.is_representative),
                is_current=row.id == current_id,
                rank_text=f"{row.rank_in_pattern}位" if row.rank_in_pattern else "—",
                detail_fetched_text=format_datetime(row.detail_fetched_at),
                last_seen_text=format_datetime(row.last_seen_at),
                raw_features_text=row.raw_features_text,
                is_favorite=bool(mark and mark.is_favorite),
                is_excluded=bool(mark and mark.is_excluded),
                memo=mark.memo if mark else None,
            )
        )
    return items


@dataclass(frozen=True, slots=True)
class AttrItem:
    key: str
    value: str


def attr_items(attrs: Mapping[str, Any] | None) -> list[AttrItem]:
    """``type_specific_attrs`` の原文を表にする（掲載サイトの表記のまま）。"""
    items: list[AttrItem] = []
    for key in sorted(attrs or {}):
        value = (attrs or {})[key]
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, bool):
            text = "はい" if value else "いいえ"
        else:
            text = "—" if value is None else str(value)
        items.append(AttrItem(str(key), text))
    return items


def price_block(prop: NotifiableListing) -> tuple[str, str]:
    """詳細の金額欄。通知と同じ見出し・本文（改行はテンプレートの CSS で出す）。"""
    return price_field(prop)


@dataclass(frozen=True, slots=True)
class StationItem:
    name: str
    walk_text: str
    commute_text: str


def station_items(view: ListingView) -> list[StationItem]:
    """駅ごとの徒歩・通勤（グループ全体）。⚠ 通知と違い駅数で打ち切らない。

    ⚠ バス便の駅は徒歩が出ない（→ 課題#58）。「徒歩不明」と明示して黙って省かない。
    """
    return [
        StationItem(
            name=station.name,
            walk_text=(
                f"徒歩{station.walk_minutes}分" if station.walk_minutes is not None else "徒歩不明"
            ),
            commute_text=(
                f"{station.commute_minutes}分" if station.commute_minutes is not None else "—"
            ),
        )
        for station in view.stations
    ]
