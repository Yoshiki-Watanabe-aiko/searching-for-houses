"""scan コマンドの本体。

一覧取得 → MUST3値判定 → 詳細取得 → 設備抽出 → スコア → 通知 を1本に貫く。

コスト緩和の要は2段判定で、一覧に載る項目（賃料・間取り・面積・築年・徒歩）で
先に MUST を評価し、``fail`` の掲載は**DBにも入れず詳細も取りに行かない**。
``price_max_hint`` はサイト側に緩めの上限を渡すためのバッファなので、
1段目を通さないと大半が無関係な物件で埋まる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from house_search.extract.extractor import (
    SOURCE_DETAIL,
    derive_features,
    extract_from_text,
    merge_features,
)
from house_search.notify.format import NotifiableProperty, build_property_message
from house_search.pipeline import persist
from house_search.pipeline.runtime import Runtime
from house_search.scoring.must import evaluate_must
from house_search.scoring.property_view import PropertyView
from house_search.scoring.score import calculate_score
from house_search.scrape import get_scraper
from house_search.scrape.base import ScrapedListing
from house_search.scrape.fetch import RateLimit, RobotsDisallowed, SiteAborted, SiteFetcher

# 1回の実行で取りに行く詳細ページの上限（サイトあたり）。
# 詳細取得は1件1リクエストなので、増分実行が何時間も走らないよう頭を押さえる。
# 取り残しは detail_fetched_at IS NULL のキューに残り、次回実行で拾われる。
DEFAULT_DETAIL_LIMIT = 40
FULL_DETAIL_LIMIT = 400


@dataclass(slots=True)
class SiteOutcome:
    """1サイトぶんの実行結果。"""

    site_code: str
    listings_seen: int = 0
    listings_kept: int = 0
    properties_new: int = 0
    details_fetched: int = 0
    features_extracted: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScanSummary:
    """パターン1件ぶんの実行結果。"""

    pattern_name: str
    sites: list[SiteOutcome] = field(default_factory=list)
    skipped_sites: list[str] = field(default_factory=list)
    scored: int = 0
    must_pass: int = 0
    notified: int = 0
    notify_failed: int = 0

    @property
    def listings_seen(self) -> int:
        return sum(site.listings_seen for site in self.sites)

    @property
    def listings_kept(self) -> int:
        return sum(site.listings_kept for site in self.sites)

    @property
    def properties_new(self) -> int:
        return sum(site.properties_new for site in self.sites)

    @property
    def details_fetched(self) -> int:
        return sum(site.details_fetched for site in self.sites)

    @property
    def errors(self) -> list[str]:
        return [error for site in self.sites for error in site.errors]


def _listing_view(listing: ScrapedListing) -> PropertyView:
    """一覧の情報だけから作る採点用ビュー（1段目のMUST判定に使う）。"""
    return PropertyView(
        site_code=listing.site_code,
        url=listing.url,
        title=listing.title,
        price=listing.price,
        mgmt_fee_monthly=listing.mgmt_fee_monthly,
        area_sqm=listing.area_sqm,
        layout=listing.layout,
        floor_num=listing.floor_num,
        total_floors=listing.total_floors,
        age_years=listing.age_years,
        walk_minutes=listing.walk_minutes,
        address=listing.address,
        detail_fetched=False,
    )


def _site_rate_limit(runtime: Runtime, site_code: str) -> RateLimit:
    """``m_sites`` のレート制御設定を読む。"""
    with runtime.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT min_interval_sec, max_pages_per_run, daily_request_cap "
                "FROM m_sites WHERE code = :code"
            ),
            {"code": site_code},
        ).first()
    if row is None:
        return RateLimit(min_interval_sec=runtime.settings.default_min_interval_sec)
    return RateLimit(
        min_interval_sec=float(row.min_interval_sec),
        max_pages_per_run=int(row.max_pages_per_run),
        daily_request_cap=row.daily_request_cap,
    )


def _collect_listings(
    scraper, fetcher: SiteFetcher, pattern, *, max_pages: int, outcome: SiteOutcome
) -> list[ScrapedListing]:
    """一覧ページを辿って掲載を集める。"""
    collected: list[ScrapedListing] = []
    for base_url in scraper.list_urls(pattern, {}):
        for page in range(1, max_pages + 1):
            url = scraper.page_url(base_url, page)
            try:
                response = fetcher.get(url)
            except (SiteAborted, RobotsDisallowed):
                raise
            except Exception as exc:  # noqa: BLE001 - 1ページの失敗で実行を止めない
                outcome.errors.append(f"一覧取得に失敗: {url} ({exc})")
                break
            listings = scraper.parse_list(response.text)
            collected.extend(listings)
            if scraper.is_last_page(len(listings)):
                break
    return collected


def _fetch_details(
    runtime: Runtime,
    scraper,
    fetcher: SiteFetcher,
    *,
    site_id: int,
    family: str,
    limit: int,
    outcome: SiteOutcome,
) -> None:
    """詳細未取得の物件を取りに行き、設備を抽出して保存する。"""
    with runtime.engine.connect() as conn:
        queue = persist.detail_queue(conn, site_id=site_id, limit=limit)

    for property_id, url in queue:
        try:
            response = fetcher.get(scraper.detail_url(url))
        except (SiteAborted, RobotsDisallowed):
            raise
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で実行を止めない
            outcome.errors.append(f"詳細取得に失敗: {url} ({exc})")
            continue

        detail = scraper.parse_detail(response.text)
        extraction = extract_from_text(
            detail.raw_features_text,
            runtime.dictionary,
            family=family,
            site_code=scraper.site_code,
            source=SOURCE_DETAIL,
        )
        # 型付き列からの導出を先に渡して、辞書照合より優先させる。
        derived = derive_features(
            floor_num=detail.floor_num,
            total_floors=detail.total_floors,
            age_years=None,
        )
        features = merge_features(derived, extraction.features)

        with runtime.engine.begin() as conn:
            persist.save_detail(conn, property_id, detail)
            saved = persist.save_features(conn, property_id, features, runtime.condition_ids)
            persist.save_unknown_tokens(
                conn,
                extraction.unknown_tokens,
                site_id=site_id,
                property_family=family,
                sample_url=url,
            )
        outcome.details_fetched += 1
        outcome.features_extracted += saved


def _score_pattern(runtime: Runtime, pattern, summary: ScanSummary) -> dict[int, PropertyView]:
    """パターン対象の物件を採点して保存する。"""
    config_hash = pattern.config_hash()
    with runtime.engine.connect() as conn:
        views = persist.load_property_views(
            conn,
            property_type_code=pattern.property_type,
            site_codes=list(pattern.sites),
        )

    with runtime.engine.begin() as conn:
        for view in views.values():
            must = evaluate_must(view, pattern.must)
            score = calculate_score(view, pattern.want) if not must.is_fail else None
            persist.save_score(
                conn,
                property_id=view.property_id,
                pattern_name=pattern.name,
                must_result=must.result,
                score=score.score if score else None,
                breakdown=score.breakdown() if score else [],
                config_hash=config_hash,
            )
            summary.scored += 1
            if must.passes(pattern.must.unknown_policy):
                summary.must_pass += 1
        persist.update_ranks(conn, pattern.name)
    return views


def _notify(
    runtime: Runtime,
    pattern,
    views: dict[int, PropertyView],
    outcomes: list[persist.UpsertOutcome],
    summary: ScanSummary,
) -> None:
    """新着・価格変動の個別通知を送る。"""
    webhook_url = runtime.settings.webhook_url(pattern.webhook_ref)

    with runtime.engine.connect() as conn:
        ranks = {
            property_id: rank
            for property_id, rank in conn.execute(
                text(
                    "SELECT property_id, rank_in_pattern FROM t_property_scores "
                    "WHERE pattern_name = :name AND rank_in_pattern IS NOT NULL"
                ),
                {"name": pattern.name},
            )
        }

    for outcome in outcomes:
        notification_type = outcome.notification_type
        if notification_type is None:
            continue
        view = views.get(outcome.property_id)
        if view is None:
            continue

        must = evaluate_must(view, pattern.must)
        if not must.passes(pattern.must.unknown_policy):
            continue

        with runtime.engine.connect() as conn:
            if persist.already_notified(
                conn,
                property_id=outcome.property_id,
                pattern_name=pattern.name,
                notification_type=notification_type,
            ):
                continue

        score = calculate_score(view, pattern.want)
        prop = NotifiableProperty(
            property_id=view.property_id,
            site_code=view.site_code or "",
            url=view.url or "",
            title=view.title,
            price=view.price,
            mgmt_fee_monthly=view.mgmt_fee_monthly,
            rent_total=view.rent_total,
            layout=view.layout,
            area_sqm=view.area_sqm,
            age_years=view.age_years,
            walk_minutes=view.walk_minutes,
            address=view.address,
            price_prev=outcome.price_prev,
        )
        message = build_property_message(
            prop,
            score,
            notification_type=notification_type,
            pattern_name=pattern.name,
            rank_in_pattern=ranks.get(view.property_id),
        )
        sent = runtime.sender.send(webhook_url, message)
        with runtime.engine.begin() as conn:
            persist.record_notification(
                conn,
                property_id=outcome.property_id,
                pattern_name=pattern.name,
                notification_type=notification_type,
                price_at_notify=view.price,
                score_at_notify=score.score,
                status="sent" if sent else "failed",
            )
        if sent:
            summary.notified += 1
        else:
            summary.notify_failed += 1


def scan_pattern(
    runtime: Runtime,
    pattern,
    *,
    site_filter: str | None = None,
    seed_mode: bool = False,
    full_scan: bool = False,
) -> ScanSummary:
    """検索パターン1件ぶんのスキャンを実行する。"""
    summary = ScanSummary(pattern_name=pattern.name)
    family = pattern.family.value
    property_type_id = runtime.property_type_ids[pattern.property_type]
    all_outcomes: list[persist.UpsertOutcome] = []

    target_sites = [s for s in pattern.sites if site_filter is None or s == site_filter]

    for site_code in target_sites:
        scraper = get_scraper(site_code)
        if scraper is None:
            summary.skipped_sites.append(site_code)
            continue

        site_id = runtime.site_ids.get(site_code)
        if site_id is None:
            summary.skipped_sites.append(site_code)
            continue

        outcome = SiteOutcome(site_code=site_code)
        rate_limit = _site_rate_limit(runtime, site_code)
        max_pages = rate_limit.max_pages_per_run if full_scan else 1
        detail_limit = FULL_DETAIL_LIMIT if full_scan else DEFAULT_DETAIL_LIMIT

        with runtime.engine.begin() as conn:
            run_row = persist.start_run(
                conn,
                run_id=runtime.run_id,
                mode="seed" if seed_mode else ("full" if full_scan else "scan"),
                pattern_name=pattern.name,
                site_id=site_id,
            )

        client = runtime.http_client()
        fetcher = SiteFetcher(site_code=site_code, client=client, rate_limit=rate_limit)
        status = "completed"
        try:
            listings = _collect_listings(
                scraper, fetcher, pattern, max_pages=max_pages, outcome=outcome
            )
            outcome.listings_seen = len(listings)

            # 1段目のMUST判定。fail はDBにも入れず詳細も取りに行かない。
            kept = [
                listing
                for listing in listings
                if not evaluate_must(
                    _listing_view(listing), pattern.must, list_stage_only=True
                ).is_fail
            ]
            outcome.listings_kept = len(kept)

            with runtime.engine.begin() as conn:
                outcomes = persist.upsert_listings(
                    conn,
                    kept,
                    site_id=site_id,
                    property_type_id=property_type_id,
                    city_index=runtime.city_index,
                )
            outcome.properties_new = sum(1 for o in outcomes if o.is_new)
            all_outcomes.extend(outcomes)

            _fetch_details(
                runtime,
                scraper,
                fetcher,
                site_id=site_id,
                family=family,
                limit=detail_limit,
                outcome=outcome,
            )
        except (SiteAborted, RobotsDisallowed) as exc:
            status = "aborted"
            outcome.errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - 1サイトの失敗で他サイトを止めない
            status = "failed"
            outcome.errors.append(f"想定外のエラー: {exc}")
        finally:
            client.close()
            with runtime.engine.begin() as conn:
                persist.finish_run(
                    conn,
                    run_row,
                    status=status,
                    items_seen=outcome.listings_seen,
                    items_new=outcome.properties_new,
                    items_failed=len(outcome.errors),
                    phase="detail",
                )
                for message in outcome.errors:
                    persist.log(
                        conn,
                        run_id=runtime.run_id,
                        level="ERROR",
                        message=message,
                        site_code=site_code,
                        pattern_name=pattern.name,
                    )
        summary.sites.append(outcome)

    views = _score_pattern(runtime, pattern, summary)

    if seed_mode:
        # シードモードは通知を送らず記録だけ行う。旧通知履歴を捨てても
        # 「再掲載が全部新着として再通知される」問題が構造的に起きなくなる（→ ADR 0006）
        return summary

    _notify(runtime, pattern, views, all_outcomes, summary)
    return summary
