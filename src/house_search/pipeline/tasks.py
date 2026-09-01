"""scan 以外のコマンド本体。

digest / rescore / check-sold / re-extract / report-unknown / coverage。
このうち rescore と re-extract は**ネットワークを一切使わない**DBバッチで、
辞書や重みを変えたときの作り直しがスクレイピング無しで完結する。
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
from house_search.notify.format import DigestEntry, NotifiableProperty, build_digest_message
from house_search.pipeline import persist
from house_search.pipeline.runtime import Runtime
from house_search.scoring.must import evaluate_must
from house_search.scoring.score import calculate_score
from house_search.scrape import get_scraper
from house_search.scrape.fetch import RateLimit, SiteFetcher


@dataclass(slots=True)
class DigestResult:
    """ダイジェスト送信の結果。"""

    pattern_name: str
    entries: int
    sent: bool


@dataclass(slots=True)
class RescoreResult:
    """再採点の結果。"""

    pattern_name: str
    scored: int
    must_pass: int
    config_hash: str


@dataclass(slots=True)
class ReExtractResult:
    """再抽出の結果。"""

    properties: int
    features: int
    unknown_tokens: int


@dataclass(slots=True)
class CheckSoldResult:
    """成約確認の結果。"""

    checked: int = 0
    sold: int = 0
    notified: int = 0
    errors: list[str] = field(default_factory=list)


def _notifiable(view) -> NotifiableProperty:
    return NotifiableProperty(
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
    )


def rescore(runtime: Runtime, pattern) -> RescoreResult:
    """DB内の物件属性と抽出済み設備から採点し直す（ネットワーク不要）。

    スコアは保存済みデータからの純関数なので、重みを変えたら
    この1コマンドで全件やり直せる。
    """
    config_hash = pattern.config_hash()
    result = RescoreResult(
        pattern_name=pattern.name, scored=0, must_pass=0, config_hash=config_hash
    )

    with runtime.engine.connect() as conn:
        views = persist.load_property_views(
            conn, property_type_code=pattern.property_type, site_codes=list(pattern.sites)
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
            result.scored += 1
            if must.passes(pattern.must.unknown_policy):
                result.must_pass += 1
        persist.update_ranks(conn, pattern.name)
    return result


def needs_rescore(runtime: Runtime, pattern) -> bool:
    """保存済みの ``config_hash`` と食い違っていないか。

    検索範囲や通知先を変えただけではハッシュは変わらない
    （スコアに効くのは ``property_type`` と ``want`` だけ）。
    """
    with runtime.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM t_property_scores "
                "WHERE pattern_name = :name AND config_hash <> :hash"
            ),
            {"name": pattern.name, "hash": pattern.config_hash()},
        ).scalar_one()
    return bool(row)


def digest(runtime: Runtime, pattern, *, dry_run: bool = False) -> DigestResult:
    """スコア上位N件のランキングダイジェストを送る。"""
    with runtime.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT property_id, rank_in_pattern FROM t_property_scores "
                "WHERE pattern_name = :name AND must_result <> 'fail' AND score IS NOT NULL "
                "ORDER BY rank_in_pattern ASC LIMIT :top_n"
            ),
            {"name": pattern.name, "top_n": pattern.ranking.top_n},
        ).all()
        property_ids = [row.property_id for row in rows]
        views = persist.load_property_views(conn, property_ids=property_ids)

    entries = [
        DigestEntry(
            rank=row.rank_in_pattern or index,
            prop=_notifiable(views[row.property_id]),
            score=calculate_score(views[row.property_id], pattern.want),
        )
        for index, row in enumerate(rows, start=1)
        if row.property_id in views
    ]

    message = build_digest_message(
        entries, pattern_name=pattern.name, digest_group=pattern.ranking.digest_group
    )
    if dry_run:
        return DigestResult(pattern_name=pattern.name, entries=len(entries), sent=False)

    webhook_url = runtime.settings.webhook_url(pattern.webhook_ref)
    sent = runtime.sender.send(webhook_url, message)
    with runtime.engine.begin() as conn:
        persist.record_digest(
            conn,
            pattern_name=pattern.name,
            digest_group=pattern.ranking.digest_group,
            top_n=len(entries),
            property_ids=[entry.prop.property_id for entry in entries],
            status="sent" if sent else "failed",
        )
    return DigestResult(pattern_name=pattern.name, entries=len(entries), sent=sent)


def re_extract(
    runtime: Runtime, *, family: str = "CHINTAI", limit: int | None = None
) -> ReExtractResult:
    """``raw_features_text`` から設備を全件抽出し直す（ネットワーク不要）。

    辞書を育てたあとはこれを回すだけで既存物件へ反映される。
    原文を保存してあることの効き目がここに出る。
    """
    result = ReExtractResult(properties=0, features=0, unknown_tokens=0)
    sql = (
        "SELECT p.id, p.url, p.site_id, s.code AS site_code, p.raw_features_text, "
        "       p.floor_num, p.total_floors "
        "FROM t_properties p JOIN m_sites s ON s.id = p.site_id "
        "WHERE p.raw_features_text IS NOT NULL"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"

    with runtime.engine.connect() as conn:
        rows = conn.execute(text(sql)).all()

    for row in rows:
        extraction = extract_from_text(
            row.raw_features_text,
            runtime.dictionary,
            family=family,
            site_code=row.site_code,
            source=SOURCE_DETAIL,
        )
        derived = derive_features(
            floor_num=row.floor_num, total_floors=row.total_floors, age_years=None
        )
        features = merge_features(derived, extraction.features)
        with runtime.engine.begin() as conn:
            saved = persist.save_features(conn, row.id, features, runtime.condition_ids)
            persist.save_unknown_tokens(
                conn,
                extraction.unknown_tokens,
                site_id=row.site_id,
                property_family=family,
                sample_url=row.url,
            )
        result.properties += 1
        result.features += saved
        result.unknown_tokens += len(extraction.unknown_tokens)
    return result


def report_unknown(runtime: Runtime, *, limit: int = 50) -> list[tuple[str, str, int, str | None]]:
    """辞書未登録の表記を出現回数順に返す。

    ここに挙がった語を辞書YAMLへ追記 → ``sync-dict`` → ``re-extract`` で
    既存物件にも反映される、という育成ループの入口。
    """
    with runtime.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.token, s.code AS site_code, t.occurrence_count, t.sample_url "
                "FROM t_unknown_tokens t JOIN m_sites s ON s.id = t.site_id "
                "ORDER BY t.occurrence_count DESC, t.token ASC LIMIT :limit"
            ),
            {"limit": limit},
        ).all()
    return [(row.token, row.site_code, row.occurrence_count, row.sample_url) for row in rows]


def check_sold(runtime: Runtime, pattern, *, limit: int = 100) -> CheckSoldResult:
    """掲載中の物件が成約・掲載終了になっていないかを確認する。"""
    result = CheckSoldResult()
    with runtime.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT p.id, p.url, s.code AS site_code FROM t_properties p "
                "JOIN m_sites s ON s.id = p.site_id "
                "JOIN m_property_types pt ON pt.id = p.property_type_id "
                "WHERE p.status = 'active' AND pt.code = :ptype AND s.code = ANY(:sites) "
                "ORDER BY p.last_seen_at ASC LIMIT :limit"
            ),
            {
                "ptype": pattern.property_type,
                "sites": list(pattern.sites),
                "limit": limit,
            },
        ).all()

    by_site: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        by_site.setdefault(row.site_code, []).append((row.id, row.url))

    sold_ids: list[int] = []
    for site_code, items in by_site.items():
        scraper = get_scraper(site_code)
        if scraper is None:
            continue
        client = runtime.http_client(user_agent=scraper.user_agent)
        fetcher = SiteFetcher(
            site_code=site_code,
            client=client,
            rate_limit=RateLimit(min_interval_sec=runtime.settings.default_min_interval_sec),
        )
        try:
            for property_id, url in items:
                result.checked += 1
                if scraper.is_sold(fetcher, url):
                    sold_ids.append(property_id)
        finally:
            client.close()

    if sold_ids:
        with runtime.engine.begin() as conn:
            persist.mark_status(conn, sold_ids, "sold")
    result.sold = len(sold_ids)
    return result


@dataclass(frozen=True, slots=True)
class SiteCoverage:
    """サイト1件ぶんの充足率（``coverage`` コマンドの出力）。"""

    site_code: str
    properties: int
    detail_fetched: int
    with_features: int
    features_avg: float
    features_min: int
    features_max: int
    column_filled: dict[str, int]


# 充足率を測る型付き列。MUST判定・metric の入力になるものを並べる。
COVERAGE_COLUMNS = (
    "price",
    "mgmt_fee_monthly",
    "rent_total",
    "deposit_amount",
    "key_money_amount",
    "area_sqm",
    "layout",
    "floor_num",
    "total_floors",
    "built_on",
    "walk_minutes",
    "address",
    "city_id",
    "raw_features_text",
)


def measure_coverage(runtime: Runtime) -> list[SiteCoverage]:
    """サイト別の設備抽出数分布と数値カラム非NULL率を実測する。

    「実装済みだが未配線」を検出するための計測（→ 課題#11）。
    アダプタを足しただけで抽出が動いていないサイトは、
    ``detail_fetched`` は増えるのに ``features_avg`` が 0 のままになる。
    """
    filled = ", ".join(
        f"count(p.{column}) AS filled_{column}" for column in COVERAGE_COLUMNS
    )
    query = text(
        f"""
        WITH feature_counts AS (
            SELECT property_id, count(*) AS n
            FROM t_property_features
            GROUP BY property_id
        )
        SELECT s.code AS site_code,
               count(*) AS properties,
               count(p.detail_fetched_at) AS detail_fetched,
               count(f.n) AS with_features,
               COALESCE(avg(f.n), 0) AS features_avg,
               COALESCE(min(f.n), 0) AS features_min,
               COALESCE(max(f.n), 0) AS features_max,
               {filled}
        FROM t_properties p
        JOIN m_sites s ON s.id = p.site_id
        LEFT JOIN feature_counts f ON f.property_id = p.id
        GROUP BY s.code
        ORDER BY s.code
        """
    )
    with runtime.engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        SiteCoverage(
            site_code=row["site_code"],
            properties=row["properties"],
            detail_fetched=row["detail_fetched"],
            with_features=row["with_features"],
            features_avg=float(row["features_avg"]),
            features_min=int(row["features_min"]),
            features_max=int(row["features_max"]),
            column_filled={c: int(row[f"filled_{c}"]) for c in COVERAGE_COLUMNS},
        )
        for row in rows
    ]
