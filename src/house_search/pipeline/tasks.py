"""scan 以外のコマンド本体。

digest / rescore / check-sold / re-extract / report-unknown / coverage。
このうち rescore と re-extract は**ネットワークを一切使わない**DBバッチで、
辞書や重みを変えたときの作り直しがスクレイピング無しで完結する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from house_search import dedup
from house_search.commute.resolve import resolve_destination_group
from house_search.extract.extractor import (
    SOURCE_DETAIL,
    derive_features,
    extract_from_text,
    merge_features,
)
from house_search.notify.format import DigestEntry, build_digest_message, notifiable_from
from house_search.pipeline import persist
from house_search.pipeline.runtime import Runtime
from house_search.scoring.anomaly import collect_price_anomalies
from house_search.scoring.listing_view import ListingView
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
    #: 対象0件のため送らなかった。⚠ **送信失敗と必ず区別する**（→ 課題#28）。
    #: CLI は ``not sent`` で終了コード1を返すので、ここを混ぜると
    #: タスクの「前回の結果」で本物の失敗を見分けられなくなる。
    skipped: bool = False


@dataclass(slots=True)
class RescoreResult:
    """再採点の結果。"""

    pattern_name: str
    scored: int
    must_pass: int
    config_hash: str
    # 相場に対して極端に安い掲載（サイト側のデータ異常の疑い → 課題#50）
    price_anomalies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReExtractResult:
    """再抽出の結果。"""

    listings: int
    features: int
    unknown_tokens: int


@dataclass(slots=True)
class CheckSoldResult:
    """成約確認の結果。"""

    checked: int = 0
    sold: int = 0
    notified: int = 0
    #: 確認した掲載のうち「上位N位だから選ばれた」件数（→ 課題#26）
    from_top_rank: int = 0
    errors: list[str] = field(default_factory=list)


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
        destination = resolve_destination_group(conn, pattern.commute)
        if pattern.commute is not None and destination is None:
            raise ValueError(
                f"通勤時間の目的地 '{pattern.commute.destination_station}' を"
                "駅マスタから一意に解決できません。sync-stations の実行と"
                "commute.destination_prefecture の指定を確認してください"
            )
        views = persist.load_listing_views(
            conn,
            property_type_code=pattern.property_type,
            site_codes=list(pattern.sites),
            # scan と同じくエリア帯に閉じる（帯外の既存データを採点しない）
            city_names=list(pattern.search.cities) or None,
            commute_destination_g_cd=destination,
        )

    passed: list[ListingView] = []
    with runtime.engine.begin() as conn:
        for view in views.values():
            must = evaluate_must(view, pattern.must)
            score = calculate_score(view, pattern.want) if not must.is_fail else None
            persist.save_score(
                conn,
                listing_id=view.listing_id,
                pattern_name=pattern.name,
                must_result=must.result,
                score=score.score if score else None,
                breakdown=score.breakdown() if score else [],
                config_hash=config_hash,
            )
            result.scored += 1
            if must.passes(pattern.must.unknown_policy):
                result.must_pass += 1
                passed.append(view)
        # エリア帯から外れた掲載の古いスコア行を消す（残すと二重採点になる）
        persist.prune_scores(conn, pattern.name, list(views))
        persist.update_ranks(conn, pattern.name)
    # ⚠ MUST を通った掲載だけを見る（scan と同じ）
    result.price_anomalies = collect_price_anomalies(passed)
    return result


def needs_rescore(runtime: Runtime, pattern) -> bool:
    """保存済みの ``config_hash`` と食い違っていないか。

    検索範囲や通知先を変えただけではハッシュは変わらない
    （スコアに効くのは ``property_type`` と ``want`` だけ）。
    """
    with runtime.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM t_listing_scores "
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
                "SELECT listing_id, rank_in_pattern FROM t_listing_scores "
                "WHERE pattern_name = :name AND must_result <> 'fail' AND score IS NOT NULL "
                "ORDER BY rank_in_pattern ASC LIMIT :top_n"
            ),
            {"name": pattern.name, "top_n": pattern.ranking.top_n},
        ).all()
        listing_ids = [row.listing_id for row in rows]
        views = persist.load_listing_views(
            conn,
            listing_ids=listing_ids,
            commute_destination_g_cd=resolve_destination_group(conn, pattern.commute),
        )
        # 順位はグループ代表にしか振っていないので、ここに並ぶのは
        # 「代表 ＋ 未グループ物件」だけになる（= ランキングがグループ単位）
        memberships = dedup.group_membership(conn, listing_ids)

    entries = [
        DigestEntry(
            rank=row.rank_in_pattern or index,
            prop=notifiable_from(
                views[row.listing_id],
                member_count=memberships[row.listing_id].member_count,
                other_site_codes=memberships[row.listing_id].other_site_codes,
            ),
            score=calculate_score(views[row.listing_id], pattern.want),
        )
        for index, row in enumerate(rows, start=1)
        if row.listing_id in views
    ]

    # ⚠ 対象0件なら送らない（→ 課題#28）。見出しだけの便が定期的に届くと
    #   ダイジェストそのものが読まれなくなり、「読まれない通知は本物のエラーを
    #   見逃すという形で実害になる」（要件定義書 §14.1）。
    #   ⚠ 送信履歴にも残さない（送っていない便を追記専用テーブルへ入れない）。
    if not entries:
        return DigestResult(pattern_name=pattern.name, entries=0, sent=False, skipped=True)

    message = build_digest_message(
        entries, pattern_name=pattern.name, digest_group=pattern.ranking.digest_group
    )
    if dry_run:
        return DigestResult(pattern_name=pattern.name, entries=len(entries), sent=False)

    # ⚠ ダイジェストは digest_webhook_ref（未指定なら webhook_ref）へ送る。
    #   上位N件だけを個別通知とは別のチャンネルへ流せるようにするため
    webhook_url = runtime.settings.webhook_url(pattern.effective_digest_webhook_ref)
    sent = runtime.sender.send(webhook_url, message)
    with runtime.engine.begin() as conn:
        persist.record_digest(
            conn,
            pattern_name=pattern.name,
            digest_group=pattern.ranking.digest_group,
            top_n=len(entries),
            listing_ids=[entry.prop.listing_id for entry in entries],
            status="sent" if sent else "failed",
        )
    return DigestResult(pattern_name=pattern.name, entries=len(entries), sent=sent)


def re_extract_rows(conn: Any, *, limit: int | None = None, family: str | None = None) -> list[Any]:
    """再抽出の対象行。⚠ **掲載ごとの種別（`property_family`）を必ず持たせる。**

    ここを固定値にすると、売買掲載が**賃貸の辞書で再抽出される**。
    設備数もエラーも異常を示さないので、実データを1件ずつ見るまで気づけない
    （→ 課題#4）。

    ``family`` を渡したときはそのファミリの掲載だけに絞る（辞書の育成中に
    片方のファミリだけを回したいときに使う）。
    """
    sql = (
        "SELECT p.id, p.url, p.site_id, s.code AS site_code, p.raw_features_text, "
        "       p.floor_num, p.total_floors, pt.family AS property_family, "
        "       pt.code AS property_type "
        "FROM t_listings p JOIN m_sites s ON s.id = p.site_id "
        "JOIN m_property_types pt ON pt.id = p.property_type_id "
        "WHERE p.raw_features_text IS NOT NULL"
    )
    params: dict[str, Any] = {}
    if family:
        sql += " AND pt.family = :family"
        params["family"] = family
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(text(sql), params).all())


def re_extract(
    runtime: Runtime, *, family: str | None = None, limit: int | None = None
) -> ReExtractResult:
    """``raw_features_text`` から設備を全件抽出し直す（ネットワーク不要）。

    辞書を育てたあとはこれを回すだけで既存物件へ反映される。
    原文を保存してあることの効き目がここに出る。

    ⚠ **辞書のファミリは掲載ごとに決める**（``family`` は絞り込みであって
    上書きではない）。固定にすると売買掲載が賃貸辞書で抽出される。
    """
    result = ReExtractResult(listings=0, features=0, unknown_tokens=0)

    with runtime.engine.connect() as conn:
        rows = re_extract_rows(conn, limit=limit, family=family)

    for row in rows:
        extraction = extract_from_text(
            row.raw_features_text,
            runtime.dictionary,
            family=row.property_family,
            site_code=row.site_code,
            source=SOURCE_DETAIL,
        )
        derived = derive_features(
            floor_num=row.floor_num, total_floors=row.total_floors, age_years=None
        )
        features = merge_features(derived, extraction.features)
        # 保存済み原文に宣伝の生成文が混ざるサイトは未知表記を数え直さない。
        # scan では設備タグの部分だけを収集元にできるが、再抽出では
        # 分割し直せないため文断片で一覧が埋まる（→ 課題#19）
        scraper = get_scraper(row.site_code, row.property_type)
        mine_unknown = getattr(scraper, "mine_unknown_tokens", True) if scraper else True
        unknown = extraction.unknown_tokens if mine_unknown else ()
        with runtime.engine.begin() as conn:
            saved = persist.save_features(conn, row.id, features, runtime.condition_ids)
            persist.save_unknown_tokens(
                conn,
                unknown,
                site_id=row.site_id,
                property_family=row.property_family,
                sample_url=row.url,
            )
        result.listings += 1
        result.features += saved
        result.unknown_tokens += len(unknown)
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


_CHECK_TARGETS_SQL = text(
    """
    WITH candidates AS (
        SELECT p.id, p.url, s.code AS site_code, p.last_seen_at, sc.rank_in_pattern
          FROM t_listings p
          JOIN m_sites s ON s.id = p.site_id
          JOIN m_property_types pt ON pt.id = p.property_type_id
          -- そのパターンで採点されている掲載だけを追う。エリア帯を絞ると
          -- 帯外の掲載は last_seen_at が更新されなくなり「最も古い」に
          -- なるため、これが無いと確認枠が帯外で埋まってしまう
          JOIN t_listing_scores sc
            ON sc.listing_id = p.id
           AND sc.pattern_name = :pattern_name
           AND sc.must_result <> 'fail'
         WHERE p.status = 'active' AND pt.code = :ptype AND s.code = ANY(:sites)
    ),
    top_ranked AS (
        SELECT id, url, site_code, last_seen_at, rank_in_pattern
          FROM candidates
         WHERE :top_rank_limit > 0
           AND rank_in_pattern IS NOT NULL
           AND rank_in_pattern <= :top_rank_limit
    ),
    stale AS (
        SELECT id, url, site_code, last_seen_at, rank_in_pattern
          FROM candidates
         ORDER BY last_seen_at ASC
         LIMIT :limit
    )
    -- ⚠ UNION の ORDER BY には式を書けない（出力列名か番号のみ）ので
    -- 外側の SELECT で包む
    SELECT * FROM (
        SELECT * FROM top_ranked
        UNION
        SELECT * FROM stale
    ) AS merged
    -- 順位のある掲載を先に確認する。途中で打ち切られても
    -- ダイジェストに出る範囲が守られるようにするため
    ORDER BY (rank_in_pattern IS NULL), rank_in_pattern, last_seen_at
    """
)


def select_check_targets(
    conn: Connection, pattern, *, limit: int, top_rank_limit: int
) -> list[Any]:
    """成約確認の対象を選ぶ。

    2つの母集団の**和集合**を返す。

    1. **上位 ``top_rank_limit`` 位の掲載**（毎回確認する）
    2. ``last_seen_at`` が古い順に ``limit`` 件（一覧から消えた掲載が自然に集まる）

    ⚠ **1が無いと、ランキング最上位に成約済みが居座る**（→ 課題#26）。
    2だけでは順位がまったく考慮されず、実測で東京23区帯の1位・2位が
    どちらも掲載終了（HTTP 404）のままダイジェストの先頭を占めていた。
    ⚠ **「一巡に何日かかるか」という指標ではこの実害が見えない。**
    平均滞留が3日でも、滞留した掲載がたまたま上位だと影響は桁違いに大きい。

    ⚠ ``top_rank_limit=0`` で1を無効にできる（従来の挙動へ戻す逃げ道）。
    """
    return list(
        conn.execute(
            _CHECK_TARGETS_SQL,
            {
                "ptype": pattern.property_type,
                "sites": list(pattern.sites),
                "pattern_name": pattern.name,
                "limit": limit,
                "top_rank_limit": top_rank_limit,
            },
        ).all()
    )


def check_sold(
    runtime: Runtime, pattern, *, limit: int = 100, top_rank_limit: int = 50
) -> CheckSoldResult:
    """掲載中の物件が成約・掲載終了になっていないかを確認する。"""
    result = CheckSoldResult()
    with runtime.engine.connect() as conn:
        rows = select_check_targets(
            conn, pattern, limit=limit, top_rank_limit=top_rank_limit
        )
    result.from_top_rank = sum(
        1
        for row in rows
        if row.rank_in_pattern is not None and row.rank_in_pattern <= top_rank_limit
    )

    by_site: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        by_site.setdefault(row.site_code, []).append((row.id, row.url))

    sold_ids: list[int] = []
    for site_code, items in by_site.items():
        scraper = get_scraper(site_code, pattern.property_type)
        if scraper is None:
            continue
        client = runtime.http_client(user_agent=scraper.user_agent)
        fetcher = SiteFetcher(
            site_code=site_code,
            client=client,
            rate_limit=RateLimit(min_interval_sec=runtime.settings.default_min_interval_sec),
        )
        try:
            for listing_id, url in items:
                result.checked += 1
                if scraper.is_sold(fetcher, url):
                    sold_ids.append(listing_id)
        finally:
            client.close()

    if sold_ids:
        with runtime.engine.begin() as conn:
            persist.mark_status(conn, sold_ids, "sold")
            # 代表が成約したグループは代表を選び直す。集合演算なので
            # 「誰が代表だったか」を覚えておく必要がない
            dedup.sync_groups(conn)
    result.sold = len(sold_ids)
    return result


@dataclass(frozen=True, slots=True)
class SiteCoverage:
    """サイト1件ぶんの充足率（``coverage`` コマンドの出力）。"""

    site_code: str
    listings: int
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
            SELECT listing_id, count(*) AS n
            FROM t_listing_features
            GROUP BY listing_id
        )
        SELECT s.code AS site_code,
               count(*) AS listings,
               count(p.detail_fetched_at) AS detail_fetched,
               count(f.n) AS with_features,
               COALESCE(avg(f.n), 0) AS features_avg,
               COALESCE(min(f.n), 0) AS features_min,
               COALESCE(max(f.n), 0) AS features_max,
               {filled}
        FROM t_listings p
        JOIN m_sites s ON s.id = p.site_id
        LEFT JOIN feature_counts f ON f.listing_id = p.id
        GROUP BY s.code
        ORDER BY s.code
        """
    )
    with runtime.engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        SiteCoverage(
            site_code=row["site_code"],
            listings=row["listings"],
            detail_fetched=row["detail_fetched"],
            with_features=row["with_features"],
            features_avg=float(row["features_avg"]),
            features_min=int(row["features_min"]),
            features_max=int(row["features_max"]),
            column_filled={c: int(row[f"filled_{c}"]) for c in COVERAGE_COLUMNS},
        )
        for row in rows
    ]


@dataclass(slots=True)
class RegroupResult:
    """名寄せの再構築結果。"""

    keys_refreshed: int
    groups: int
    grouped_listings: int
    representative_changes: int
    cheaper_candidates: int


def regroup(runtime: Runtime) -> RegroupResult:
    """名寄せキーを全件作り直してグループを同期する（ネットワーク不要）。

    正規化ルールを変えたあとのバックフィルはこれ1本で済む。
    **通知は送らない。** 既存データへ初めて適用したときに
    ``cheaper_listing`` が大量発火するのを避けるため、
    候補の件数だけを返して実際の通知は次回の ``scan`` の差分に任せる。
    """
    with runtime.engine.begin() as conn:
        refreshed = dedup.refresh_dedup_keys(conn, address_index=runtime.address_index)
        changes = dedup.sync_groups(conn)
        groups = conn.execute(text("SELECT count(*) FROM t_listing_groups")).scalar_one()
        grouped = conn.execute(
            text("SELECT count(*) FROM t_listings WHERE group_id IS NOT NULL")
        ).scalar_one()
    return RegroupResult(
        keys_refreshed=refreshed,
        groups=int(groups),
        grouped_listings=int(grouped),
        representative_changes=len(changes),
        cheaper_candidates=sum(1 for change in changes if change.is_cheaper),
    )


def measure_dedup(runtime: Runtime) -> list[dedup.SiteDedupStats]:
    """サイト別の名寄せ実測（``dedup-stats``）。

    ``coverage``（設備抽出の充足）とは測るものが違うので別コマンドにしてある。
    """
    with runtime.engine.connect() as conn:
        return dedup.dedup_stats(conn)


@dataclass(slots=True)
class ResolveCitiesResult:
    """市区町村の引き直し結果。"""

    total: int
    resolved_before: int
    resolved_after: int
    changed: int


def resolve_cities(runtime: Runtime, patterns: list) -> ResolveCitiesResult:
    """既存掲載の ``city_id`` を現在の ``m_cities`` で引き直す（ネットワーク不要）。

    市区町村マスタを入れ替えたあとのバックフィル。全国化で新しく登録された市区や、
    誤っていた jis_code の訂正は、既に保存済みの掲載には自動では反映されない。

    都道府県を前置しない住所（賃貸EX 形式）の照合は検索パターンの対象都道府県に
    依存するため、**全パターンの都道府県を合わせた範囲**で引く。パターンごとに
    引き直すと、同じ掲載が最後に処理したパターンの範囲で上書きされてしまう。

    ⚠ **解決済みの city_id を NULL では上書きしない。** 範囲が狭まったせいで
    引けなくなった掲載の情報を捨てる理由がない。

    ⚠⚠ **`prefecture` も一緒に引き直す。** 初版は ``resolve_city`` が返す都道府県を
    捨てて city_id だけ更新していたため、**住所と prefecture 列が食い違う掲載**が
    残った（実測3件。`東京都立川市…` なのに `prefecture='長野県'`）。
    ``normalize_base`` はこの列を住所へ前置するので、食い違うと
    `長野県東京都立川市…` という**実在しない住所**が `dedup_key` になり、
    名寄せが静かに失敗する（→ 課題#48）。両方を同じタプルから同時に書くこと。
    """
    prefectures = sorted({pref for p in patterns for pref in p.search.prefectures})
    with runtime.engine.begin() as conn:
        index = persist.load_city_index(conn, search_prefectures=prefectures)
        rows = conn.execute(
            text(
                "SELECT id, address, city_id, prefecture FROM t_listings"
                " WHERE address IS NOT NULL"
            )
        ).all()
        updates: list[dict[str, object]] = []
        resolved_before = resolved_after = 0
        for listing_id, address, city_id, prefecture in rows:
            if city_id is not None:
                resolved_before += 1
            resolved_prefecture, resolved = persist.resolve_city(address, index)
            new_city_id = resolved if resolved is not None else city_id
            new_prefecture = resolved_prefecture if resolved_prefecture else prefecture
            if new_city_id is not None:
                resolved_after += 1
            if new_city_id != city_id or new_prefecture != prefecture:
                updates.append(
                    {
                        "listing_id": listing_id,
                        "city_id": new_city_id,
                        "prefecture": new_prefecture,
                    }
                )
        if updates:
            conn.execute(
                text(
                    "UPDATE t_listings SET city_id = :city_id, prefecture = :prefecture,"
                    " updated_at = now() WHERE id = :listing_id"
                ),
                updates,
            )
    return ResolveCitiesResult(
        total=len(rows),
        resolved_before=resolved_before,
        resolved_after=resolved_after,
        changed=len(updates),
    )
