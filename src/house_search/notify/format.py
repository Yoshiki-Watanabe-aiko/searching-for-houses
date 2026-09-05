"""Discord へ送るメッセージの整形。

個別通知は物件1件=1embed。ダイジェストは上位N件を1メッセージのテキスト表に
まとめる（embed は10個/メッセージが上限で、上位15件を1件1embedにすると
収まらないため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from house_search.scoring.listing_view import ListingView
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
class NotifiableListing:
    """通知に必要な物件情報。DB行から詰め替えて使う。"""

    listing_id: int
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
    # --- 売買（Phase 6） ---
    # ⚠ **既定値付きにして、賃貸だけの呼び出しをこれまでどおり動かす。**
    # ⚠ `property_family` が None のときは賃貸として表示する（既定を売買側に
    # 倒すと、渡し忘れた稼働中の経路が黙って売買表示になる）。
    repair_reserve_monthly: int | None = None
    property_family: str | None = None
    # 通勤時間は目的地（commute セクション）を設定したパターンでだけ付く任意の属性。
    # 既定値付きにして、設定していない呼び出しをこれまでどおり動かす。
    commute_minutes: int | None = None
    # --- 名寄せグループの情報（Phase 4） ---
    # 同一住戸の別掲載を1グループに畳んだ結果を通知に出すためのもの。
    # 既定値付きなので、グループを持たない呼び出しはこれまでどおり動く。
    member_count: int = 1
    other_site_codes: tuple[str, ...] = ()
    previous_total: int | None = None
    previous_site_code: str | None = None

    @property
    def monthly_cost(self) -> int | None:
        """売買の月々の負担（管理費＋修繕積立金）。⚠ 両方 None のときだけ None。

        片方だけ判っている掲載を0円扱いにしないため、`ListingView.monthly_cost`
        と同じ規則にしてある。
        """
        if self.mgmt_fee_monthly is None and self.repair_reserve_monthly is None:
            return None
        return (self.mgmt_fee_monthly or 0) + (self.repair_reserve_monthly or 0)

    @property
    def listing_sites(self) -> tuple[str, ...]:
        """この住戸が載っているサイトの一覧（自サイトを先頭に）。"""
        seen = [self.site_code] if self.site_code else []
        for code in self.other_site_codes:
            if code not in seen:
                seen.append(code)
        return tuple(seen)


def notifiable_from(
    view: ListingView,
    *,
    member_count: int = 1,
    other_site_codes: tuple[str, ...] = (),
    price_prev: int | None = None,
    previous_total: int | None = None,
    previous_site_code: str | None = None,
) -> NotifiableListing:
    """採点用ビューを通知用の値に詰め替える。

    ``scan`` と ``digest`` の双方が同じ形で通知を組み立てられるように、
    詰め替えはここ1箇所にまとめてある。
    """
    return NotifiableListing(
        listing_id=view.listing_id or 0,
        site_code=view.site_code or "",
        url=view.url or "",
        title=view.title,
        price=view.price,
        mgmt_fee_monthly=view.mgmt_fee_monthly,
        rent_total=view.rent_total,
        repair_reserve_monthly=view.repair_reserve_monthly,
        property_family=view.property_family,
        layout=view.layout,
        area_sqm=view.area_sqm,
        age_years=view.age_years,
        walk_minutes=view.walk_minutes,
        commute_minutes=view.commute_minutes,
        address=view.address,
        price_prev=price_prev,
        member_count=member_count,
        other_site_codes=other_site_codes,
        previous_total=previous_total,
        previous_site_code=previous_site_code,
    )


def _yen(value: int | None) -> str:
    return f"{value:,}円" if value is not None else "—"


# 売買のファミリ。⚠ ここに無いもの（CHINTAI・未設定）は賃貸として表示する。
_BUY_FAMILIES = frozenset({"MANSION_BUY", "KODATE_BUY", "TOCHI_BUY"})


def _is_buy(prop: NotifiableListing) -> bool:
    return (prop.property_family or "CHINTAI") in _BUY_FAMILIES


def _man_yen(value: int | None) -> str:
    """売買価格を万円・億円で表す。⚠ 未定は「価格未定」と明示する。

    0円やハイフンで出すと「安い」と誤読される（新築は価格未定が実在する）。
    """
    if value is None:
        return "価格未定"
    if value >= 100_000_000:
        oku, rest = divmod(value, 100_000_000)
        man = rest // 10_000
        return f"{oku}億{man:,}万円" if man else f"{oku}億円"
    if value % 10_000:
        return f"{value / 10_000:,.1f}万円"
    return f"{value // 10_000:,}万円"


def price_field(prop: NotifiableListing) -> tuple[str, str]:
    """個別通知に出す金額欄の ``(見出し, 本文)``。

    ⚠⚠ **`rent_total` は生成列 `price + 管理費` なので売買でも値が入る。**
    賃貸前提のまま出すと、中古マンションの通知に「35,012,000円」が**賃料**として
    並び、物件価格と管理費を足した無意味な数字を誰も異常と思わない（→ 課題#4）。
    """
    if _is_buy(prop):
        monthly = prop.monthly_cost
        note = f"\n（月々 {_yen(monthly)}）" if monthly is not None else ""
        return "価格", f"{_man_yen(prop.price)}{note}"
    return (
        "月額",
        f"{_yen(prop.rent_total)}\n（賃料 {_yen(prop.price)} + 管理費 "
        f"{_yen(prop.mgmt_fee_monthly)}）",
    )


def price_summary(prop: NotifiableListing) -> str:
    """ダイジェスト1行に出す金額。⚠ 売買と賃貸で意味が変わる。"""
    return _man_yen(prop.price) if _is_buy(prop) else _yen(prop.rent_total)


def _summary_line(prop: NotifiableListing) -> str:
    """間取り・面積・築年・徒歩・通勤を1行にまとめる。

    通勤時間は目的地を設定したパターンでだけ出る。駅を同定できなかった掲載は
    「通勤不明」と明示する（黙って省くと、条件が効いているのか分からない）。
    """
    parts = [
        prop.layout or "—",
        f"{prop.area_sqm:.1f}㎡" if prop.area_sqm is not None else "—",
        f"築{prop.age_years}年" if prop.age_years is not None else "築年不明",
        f"徒歩{prop.walk_minutes}分" if prop.walk_minutes is not None else "徒歩不明",
    ]
    if prop.commute_minutes is not None:
        parts.append(f"通勤{prop.commute_minutes}分")
    return " / ".join(parts)


def build_listing_embed(
    prop: NotifiableListing,
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
        dict(zip(("name", "value"), price_field(prop), strict=True), inline=True),
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
    elif notification_type == "cheaper_listing" and prop.previous_total is not None:
        # 同一住戸が他サイトでより安く出た。比較は月額（賃料＋管理費）で行う
        diff = (prop.rent_total or 0) - prop.previous_total
        previous_site = f"（{prop.previous_site_code}）" if prop.previous_site_code else ""
        fields.insert(
            1,
            {
                "name": "他サイトとの差",
                "value": f"{_yen(prop.previous_total)}{previous_site} → "
                f"{_yen(prop.rent_total)}（{diff:,}円）",
                "inline": True,
            },
        )

    if prop.member_count > 1:
        # 同一条件の掲載が複数あることを明示する。ランキング枠を節約するために
        # 1件へ畳んでいるので、畳んだ事実と件数を通知に残す（2026-09-02 ユーザー判断）
        fields.append(
            {
                "name": "同一条件の掲載",
                "value": f"{prop.member_count}件（{' / '.join(prop.listing_sites)}）",
                "inline": False,
            }
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


def build_listing_message(
    prop: NotifiableListing,
    score: ScoreResult,
    *,
    notification_type: str,
    pattern_name: str,
    rank_in_pattern: int | None = None,
) -> dict[str, Any]:
    """個別通知1件ぶんのメッセージ本体。"""
    return {
        "embeds": [
            build_listing_embed(
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
    prop: NotifiableListing
    score: ScoreResult


def _digest_line(entry: DigestEntry) -> str:
    prop = entry.prop
    unknown = entry.score.unknown_count
    unknown_note = f" ⚠︎未確認{unknown}" if unknown else ""
    title = (prop.title or "（物件名なし）")[:34]
    others = len(prop.listing_sites) - 1
    site_note = f"{prop.site_code} ほか{others}サイト" if others > 0 else prop.site_code
    return (
        f"**{entry.rank}. [{title}]({prop.url})**\n"
        f"　`{entry.score.score:5.1f}点` {price_summary(prop)} / "
        f"{_summary_line(prop)}\n"
        f"　{prop.address or '住所不明'} ({site_note}){unknown_note}"
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
