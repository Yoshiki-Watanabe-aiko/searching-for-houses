"""閲覧画面のルート。

⚠ **GET は絶対に書き込まない。** 状態を変えるのは POST だけで、POST は
``create_app`` の ``before_request`` が CSRF トークンと送信元を検証してから届く。
⚠ 読み出しは読み取り専用トランザクションで行う（誤って書く経路を DB 側でも止める）。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from sqlalchemy.engine import Connection
from werkzeug.wrappers import Response

from house_search import dedup, marks
from house_search.notify.format import notifiable_from, summary_line
from house_search.pipeline import persist
from house_search.scoring.must import evaluate_must
from house_search.scoring.utility import utility_profile_for
from house_search.web import presenters, queries
from house_search.web.context import PatternEntry, WebContext
from house_search.web.errors import UserInputError
from house_search.web.filters import (
    MUST_LABELS,
    PAGE_SIZE,
    SORT_LABELS,
    InvalidFilterError,
    ListingFilter,
)
from house_search.web.security import safe_href, safe_next

bp = Blueprint("web", __name__)

_FLAG_FIELDS: dict[str, marks.MarkFlag] = {"favorite": "is_favorite", "excluded": "is_excluded"}
_CHECKED = "1"


def _context() -> WebContext:
    return current_app.extensions["house_search"]


@contextmanager
def _read_conn() -> Iterator[Connection]:
    with _context().engine.connect() as conn:
        # ⚠ トランザクションが始まる前に指定する（autobegin は最初の execute で起きる）
        yield conn.execution_options(postgresql_readonly=True)


def _pattern_or_404(slug: str) -> PatternEntry:
    entry = _context().pattern_by_slug(slug)
    if entry is None:
        abort(404)
    return entry


def _current_path() -> str:
    return request.full_path.rstrip("?")


@dataclass(frozen=True, slots=True)
class PatternCard:
    slug: str
    name: str
    property_type: str
    summary: queries.PatternSummary
    scored_at_text: str


@bp.get("/")
def index() -> str:
    ctx = _context()
    with _read_conn() as conn:
        cards = [
            PatternCard(
                slug=entry.slug,
                name=entry.pattern.name,
                property_type=entry.pattern.property_type,
                summary=(
                    summary := queries.pattern_summary(
                        conn,
                        pattern_name=entry.pattern.name,
                        config_hash=entry.pattern.config_hash(),
                    )
                ),
                scored_at_text=presenters.format_datetime(summary.scored_at),
            )
            for entry in ctx.patterns
        ]
    return render_template("index.html", cards=cards)


@bp.get("/patterns/<slug>")
def ranking(slug: str) -> str:
    ctx = _context()
    entry = _pattern_or_404(slug)
    pattern = entry.pattern
    family = pattern.family.value
    try:
        flt = ListingFilter.from_query(request.args)
    except InvalidFilterError as exc:
        raise UserInputError(str(exc)) from exc

    with _read_conn() as conn:
        page = queries.ranking_page(conn, pattern_name=pattern.name, family=family, flt=flt)
        ids = [row.listing_id for row in page.rows]
        views = persist.load_listing_views(
            conn,
            listing_ids=ids,
            commute_destination_g_cd=entry.destination_g_cd,
            utility_profile=utility_profile_for(pattern),
        )
        memberships = dedup.group_membership(conn, ids)
        group_marks = marks.group_marks(conn, ids)
        site_options = queries.site_options(conn, pattern_name=pattern.name)
        city_options = queries.city_options(conn, pattern_name=pattern.name)

    destination = pattern.commute.destination_station if pattern.commute else None
    notifiables = {
        listing_id: notifiable_from(
            view,
            member_count=memberships[listing_id].member_count,
            other_site_codes=memberships[listing_id].other_site_codes,
            commute_destination=destination,
        )
        for listing_id, view in views.items()
    }
    items = presenters.ranking_items(
        page.rows,
        views=views,
        notifiables=notifiables,
        memo_counts={key: value.memo_count for key, value in group_marks.items()},
        config_hash=pattern.config_hash(),
    )
    total_pages = max(1, math.ceil(page.total / PAGE_SIZE))
    return render_template(
        "ranking.html",
        slug=slug,
        pattern_name=pattern.name,
        destination=destination,
        items=items,
        total=page.total,
        flt=flt,
        page_count=total_pages,
        prev_url=(
            url_for("web.ranking", slug=slug, **flt.to_query(page=flt.page - 1))
            if flt.page > 1
            else None
        ),
        next_url=(
            url_for("web.ranking", slug=slug, **flt.to_query(page=flt.page + 1))
            if flt.page < total_pages
            else None
        ),
        sort_labels=SORT_LABELS,
        must_labels=MUST_LABELS,
        site_options=site_options,
        city_options=city_options,
        site_names=ctx.site_names,
        price_label=queries.PRICE_LABELS[family],
        area_label=queries.AREA_LABELS[family],
        current_path=_current_path(),
    )


@bp.get("/patterns/<slug>/listings/<int:listing_id>")
def listing(slug: str, listing_id: int) -> str:
    ctx = _context()
    entry = _pattern_or_404(slug)
    pattern = entry.pattern
    with _read_conn() as conn:
        row = queries.listing_row(conn, listing_id)
        # ⚠ 別の種別の掲載をこのパターンの条件で判定しない（MUST が意味を失う）
        if row is None or row.property_type_code != pattern.property_type:
            abort(404)
        score = queries.score_row(conn, pattern_name=pattern.name, listing_id=listing_id)
        views = persist.load_listing_views(
            conn,
            listing_ids=[listing_id],
            commute_destination_g_cd=entry.destination_g_cd,
            # 掲載終了の掲載も詳細は見られるようにする（グループ員の一覧から辿れるので）
            active_only=False,
            utility_profile=utility_profile_for(pattern),
        )
        membership = dedup.group_membership(conn, [listing_id])[listing_id]
        members = queries.group_members(conn, pattern_name=pattern.name, listing_id=listing_id)
        member_marks = marks.marks_of(conn, [member.id for member in members])
        group_mark = marks.group_marks(conn, [listing_id])[listing_id]

    view = views.get(listing_id)
    if view is None:
        abort(404)
    destination = pattern.commute.destination_station if pattern.commute else None
    prop = notifiable_from(
        view,
        member_count=membership.member_count,
        other_site_codes=membership.other_site_codes,
        commute_destination=destination,
    )
    price_title, price_body = presenters.price_block(prop)
    own_mark = member_marks.get(listing_id)
    return render_template(
        "listing.html",
        slug=slug,
        pattern_name=pattern.name,
        listing_id=listing_id,
        title=row.title or "（物件名なし）",
        external_href=safe_href(row.url),
        site_text=f"{row.site_name}（{row.site_code}）",
        address=row.address or "住所不明",
        status_label=presenters.LISTING_STATUS_LABELS.get(row.status, row.status),
        is_active=row.status == "active",
        property_type_name=row.property_type_name,
        representative_id=(
            row.representative_listing_id
            if row.representative_listing_id not in (None, listing_id)
            else None
        ),
        score=score,
        is_stale=bool(score and score.config_hash != pattern.config_hash()),
        scored_at_text=presenters.format_datetime(score.scored_at if score else None),
        price_title=price_title,
        price_body=price_body,
        summary_text=summary_line(prop),
        destination=destination,
        stations=presenters.station_items(view),
        breakdown=presenters.breakdown_items(score.score_breakdown if score else None),
        unknown_policy=pattern.must.unknown_policy,
        must_items=presenters.must_items(evaluate_must(view, pattern.must)),
        must_result_label=presenters.MUST_RESULT_LABELS.get(
            score.must_result if score else "", "未採点"
        ),
        detail_fetched=view.detail_fetched,
        features=sorted(ctx.condition_names.get(code, code) for code in view.feature_codes),
        hazards=presenters.hazard_items(view),
        attrs=presenters.attr_items(row.type_specific_attrs),
        members=presenters.member_items(
            members, family=pattern.family.value, current_id=listing_id, marks=member_marks
        ),
        first_seen_text=presenters.format_datetime(row.first_seen_at),
        last_seen_text=presenters.format_datetime(row.last_seen_at),
        detail_fetched_text=presenters.format_datetime(row.detail_fetched_at),
        group_mark=group_mark,
        # ⚠ 印があってメモが NULL の行もある。None を渡すと Jinja2 が「None」と描画し、
        #   次の保存でその4文字がメモとして書き込まれる（→ 課題#68・2026-09-15 本番で実測）
        own_memo=(own_mark.memo if own_mark else None) or "",
        memo_max=marks.MARK_MEMO_MAX_CHARS,
        current_path=_current_path(),
    )


@bp.post("/listings/<int:listing_id>/mark")
def save_mark(listing_id: int) -> Response:
    form = request.form
    try:
        with _context().engine.begin() as conn:
            if not queries.listing_exists(conn, listing_id):
                abort(404)
            marks.save_group_mark(
                conn,
                listing_id=listing_id,
                is_favorite=form.get("favorite") == _CHECKED,
                is_excluded=form.get("excluded") == _CHECKED,
                memo=form.get("memo"),
            )
    except marks.MemoTooLongError as exc:
        raise UserInputError(str(exc)) from exc
    return redirect(safe_next(form.get("next")), code=303)


@bp.post("/listings/<int:listing_id>/flags")
def set_flag(listing_id: int) -> Response:
    form = request.form
    flag = _FLAG_FIELDS.get(form.get("flag", ""))
    if flag is None:
        abort(400)
    with _context().engine.begin() as conn:
        if not queries.listing_exists(conn, listing_id):
            abort(404)
        marks.set_group_flag(
            conn, listing_id=listing_id, flag=flag, on=form.get("value") == _CHECKED
        )
    return redirect(safe_next(form.get("next")), code=303)
