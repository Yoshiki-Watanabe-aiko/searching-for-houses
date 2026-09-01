"""Discord へ送るメッセージの整形。

個別通知は物件1件=1embed。ダイジェストは上位N件を1メッセージのテキスト表に
まとめる（embed は10個/メッセージが上限で、上位15件を1件1embedにすると
収まらないため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from house_search.scoring.score import STATUS_UNKNOWN, ScoreResult

# 通知種別 → embed の色（requirements.md §9）。
COLORS: dict[str, int] = {
    "new": 0x57F287,
    "sold": 0xED4245,
    "price_down": 0x5865F2,
    "price_up": 0xFEE75C,
    "cheaper_listing": 0x9B59B6,
}
TYPE_LABELS: dict[str, str] = {
    "new": "🆕 新着",
    "sold": "🔴 成約・掲載終了",
    "price_down": "🔵 値下がり",
    "price_up": "🟡 値上がり",
    "cheaper_listing": "💰 他サイトで安値掲載",
}
DIGEST_COLOR = 0x3498DB

# Discord の上限。description は 4096字、1メッセージ合計 6000字。
MAX_DESCRIPTION_CHARS = 4096
# ダイジェスト1行あたりの想定文字数から決めた安全側の打ち切り。
DIGEST_TRUNCATION_NOTE = "…（以降は文字数上限のため省略）"


@dataclass(frozen=True, slots=True)
class NotifiableProperty:
    """通知に必要な物件情報。DB行から詰め替えて使う。"""

    property_id: int
    site_code: str
    url: str
    title: str | None
    price: int | None
    mgmt_fee_monthly: int | None
    rent_total: int | None
    layout: str | None
    area_sqm: float | None
    age_years: int | None
    walk_minutes: int | None
    address: str | None
    image_url: str | None = None
    price_prev: int | None = None


def _yen(value: int | None) -> str:
    return f"{value:,}円" if value is not None else "—"


def _summary_line(prop: NotifiableProperty) -> str:
    """間取り・面積・築年・徒歩を1行にまとめる。"""
    parts = [
        prop.layout or "—",
        f"{prop.area_sqm:.1f}㎡" if prop.area_sqm is not None else "—",
        f"築{prop.age_years}年" if prop.age_years is not None else "築年不明",
        f"徒歩{prop.walk_minutes}分" if prop.walk_minutes is not None else "徒歩不明",
    ]
    return " / ".join(parts)


def build_property_embed(
    prop: NotifiableProperty,
    score: ScoreResult,
    *,
    notification_type: str,
    pattern_name: str,
    rank_in_pattern: int | None = None,
) -> dict[str, Any]:
    """新着・価格変動などの個別通知1件。"""
    label = TYPE_LABELS.get(notification_type, notification_type)
    rank_text = f"パターン内 {rank_in_pattern}位" if rank_in_pattern else "順位未確定"

    fields: list[dict[str, Any]] = [
        {
            "name": "スコア",
            "value": f"**{score.score:.1f}** / 100（{rank_text}）",
            "inline": True,
        },
        {
            "name": "月額",
            "value": f"{_yen(prop.rent_total)}\n（賃料 {_yen(prop.price)} + 管理費 "
            f"{_yen(prop.mgmt_fee_monthly)}）",
            "inline": True,
        },
        {"name": "条件", "value": _summary_line(prop), "inline": False},
    ]

    if notification_type in ("price_down", "price_up") and prop.price_prev is not None:
        diff = (prop.price or 0) - prop.price_prev
        sign = "+" if diff > 0 else ""
        fields.insert(
            1,
            {
                "name": "価格変動",
                "value": f"{_yen(prop.price_prev)} → {_yen(prop.price)}（{sign}{diff:,}円）",
                "inline": True,
            },
        )

    if hits := score.top_hits(3):
        fields.append(
            {
                "name": "得点上位",
                "value": "\n".join(f"・{item.name}（{item.points:.0f}点）" for item in hits),
                "inline": False,
            }
        )

    unknown = score.unknown_count
    footer = f"{pattern_name} / {prop.site_code}"
    if unknown:
        footer += f" / 未確認 {unknown}項目"

    embed: dict[str, Any] = {
        "title": (prop.title or "（物件名なし）")[:250],
        "url": prop.url,
        "description": f"{label}　{prop.address or ''}".strip(),
        "color": COLORS.get(notification_type, DIGEST_COLOR),
        "fields": fields,
        "footer": {"text": footer[:2000]},
    }
    if prop.image_url:
        embed["thumbnail"] = {"url": prop.image_url}
    return embed


def build_property_message(
    prop: NotifiableProperty,
    score: ScoreResult,
    *,
    notification_type: str,
    pattern_name: str,
    rank_in_pattern: int | None = None,
) -> dict[str, Any]:
    """個別通知1件ぶんのメッセージ本体。"""
    return {
        "embeds": [
            build_property_embed(
                prop,
                score,
                notification_type=notification_type,
                pattern_name=pattern_name,
                rank_in_pattern=rank_in_pattern,
            )
        ]
    }


@dataclass(frozen=True, slots=True)
class DigestEntry:
    """ダイジェスト1行ぶん。"""

    rank: int
    prop: NotifiableProperty
    score: ScoreResult


def _digest_line(entry: DigestEntry) -> str:
    prop = entry.prop
    unknown = entry.score.unknown_count
    unknown_note = f" ⚠︎未確認{unknown}" if unknown else ""
    title = (prop.title or "（物件名なし）")[:34]
    return (
        f"**{entry.rank}. [{title}]({prop.url})**\n"
        f"　`{entry.score.score:5.1f}点` {_yen(prop.rent_total)} / "
        f"{_summary_line(prop)}\n"
        f"　{prop.address or '住所不明'} ({prop.site_code}){unknown_note}"
    )


def build_digest_message(
    entries: list[DigestEntry],
    *,
    pattern_name: str,
    digest_group: str | None = None,
) -> dict[str, Any]:
    """日次ランキングダイジェスト。

    上位N件を1メッセージのテキストにまとめる。``digest_group`` が指定された
    パターンはセクション見出しを付けて並記できるようにしてある
    （スコアは種別間で混ぜない）。
    """
    heading = f"📊 **{pattern_name}** の上位 {len(entries)} 件"
    if digest_group:
        heading = f"📊 [{digest_group}] **{pattern_name}** の上位 {len(entries)} 件"

    lines: list[str] = []
    used = len(heading) + 2
    for entry in entries:
        line = _digest_line(entry)
        if used + len(line) + 2 > MAX_DESCRIPTION_CHARS - len(DIGEST_TRUNCATION_NOTE):
            lines.append(DIGEST_TRUNCATION_NOTE)
            break
        lines.append(line)
        used += len(line) + 2

    description = "\n\n".join(lines) if lines else "条件に合う物件がありませんでした。"
    return {
        "embeds": [
            {
                "title": heading[:250],
                "description": description,
                "color": DIGEST_COLOR,
                "footer": {"text": "スコアは MUST 通過物件のみ。0〜100点で高いほど条件に合致"},
            }
        ]
    }


def build_error_message(*, title: str, detail: str) -> dict[str, Any]:
    """エラーチャンネルへ送るメッセージ。"""
    return {
        "embeds": [
            {
                "title": f"⚠️ {title}"[:250],
                "description": detail[:MAX_DESCRIPTION_CHARS],
                "color": COLORS["sold"],
            }
        ]
    }


def count_unknown(score: ScoreResult) -> int:
    """未確認項目数（通知の付記に使う）。"""
    return sum(1 for item in score.items if item.status == STATUS_UNKNOWN)
