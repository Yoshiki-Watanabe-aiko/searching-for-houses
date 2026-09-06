"""scan コマンドの本体。

一覧取得 → MUST3値判定 → 詳細取得 → 設備抽出 → スコア → 通知 を1本に貫く。

コスト緩和の要は2段判定で、一覧に載る項目（賃料・間取り・面積・築年・徒歩）で
先に MUST を評価し、``fail`` の掲載は**DBにも入れず詳細も取りに行かない**。
``price_max_hint`` はサイト側に緩めの上限を渡すためのバッファなので、
1段目を通さないと大半が無関係な物件で埋まる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from sqlalchemy import text

from house_search import dedup
from house_search.extract.extractor import (
    SOURCE_DETAIL,
    derive_features,
    extract_from_text,
    merge_features,
)
from house_search.notify.format import build_listing_message, notifiable_from
from house_search.pipeline import persist
from house_search.pipeline.runtime import Runtime
from house_search.scoring.anomaly import collect_price_anomalies
from house_search.scoring.listing_view import ListingView
from house_search.scoring.must import evaluate_must
from house_search.scoring.score import calculate_score
from house_search.scrape import get_scraper, resolve_areas
from house_search.scrape.area import AreaTarget
from house_search.scrape.base import ScrapedListing
from house_search.scrape.fetch import (
    PlaintextRedirect,
    RateLimit,
    RobotsDisallowed,
    SiteAborted,
    SiteFetcher,
)
from house_search.scrape.rotation import next_cursor, rotate_areas

# 1回の実行で取りに行く詳細ページの上限（サイトあたり）。
# 詳細取得は1件1リクエストなので、増分実行が何時間も走らないよう頭を押さえる。
# 取り残しは detail_fetched_at IS NULL のキューに残り、次回実行で拾われる。
DEFAULT_DETAIL_LIMIT = 40
FULL_DETAIL_LIMIT = 400


def resolve_detail_limit(full_scan: bool, override: int | None) -> int:
    """1サイトぶんの詳細取得上限を決める。

    ``--detail-limit`` は初回全件スキャン用の逃げ道。一覧を取り直さずに
    詳細キューだけを掃けるようにするためにある（``--full`` を回し直すと
    一覧1100リクエストを毎回取り直すことになる）。

    ``m_sites.daily_request_cap`` は全サイト NULL で日次の安全弁が無いため、
    「上限なし」は用意しない。掃き切れない場合は実行を分けて回す。
    """
    if override is not None:
        if override < 1:
            raise ValueError("--detail-limit は1以上を指定してください")
        return override
    return FULL_DETAIL_LIMIT if full_scan else DEFAULT_DETAIL_LIMIT


@dataclass(slots=True)
class SiteOutcome:
    """1サイトぶんの実行結果。"""

    site_code: str
    listings_seen: int = 0
    listings_kept: int = 0
    listings_new: int = 0
    details_fetched: int = 0
    features_extracted: int = 0
    # 詳細取得の途中で掲載終了と分かった件数（→ 課題#55）。
    # ⚠ **エラーではない**ので errors には入れない。混ぜると本物の失敗と
    # 区別できず、読まれない通知が本物のエラーを隠す（→ 課題#45）
    details_sold: int = 0
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
    # ranking.notify_max_rank の圏外で送らなかった件数。
    # 絞り込みを実行サマリに出さないと「通知が来ない」のが
    # 意図どおりなのか不具合なのか見分けられない
    notify_out_of_rank: int = 0
    groups_changed: int = 0
    # 相場に対して極端に安い掲載（サイト側のデータ異常の疑い → 課題#50）。
    # ⚠ エラーではないので errors には入れない。既知の偽陽性で
    # エラーチャンネルが埋まると本物を見逃す（→ 課題#45）
    price_anomalies: list[str] = field(default_factory=list)
    # サイトに属さないエラー（通勤時間の目的地が引けない等）。
    # errors は property なので、ここに入れないと append しても消える
    pattern_errors: list[str] = field(default_factory=list)

    @property
    def listings_seen(self) -> int:
        return sum(site.listings_seen for site in self.sites)

    @property
    def listings_kept(self) -> int:
        return sum(site.listings_kept for site in self.sites)

    @property
    def listings_new(self) -> int:
        return sum(site.listings_new for site in self.sites)

    @property
    def details_fetched(self) -> int:
        return sum(site.details_fetched for site in self.sites)

    @property
    def errors(self) -> list[str]:
        return [
            *self.pattern_errors,
            *(error for site in self.sites for error in site.errors),
        ]


def _listing_view(listing: ScrapedListing) -> ListingView:
    """一覧の情報だけから作る採点用ビュー（1段目のMUST判定に使う）。"""
    return ListingView(
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


def _inactive_sites(runtime: Runtime) -> set[str]:
    """``m_sites.is_active = false`` のサイトコード。"""
    with runtime.engine.connect() as conn:
        rows = conn.execute(text("SELECT code FROM m_sites WHERE is_active = false")).scalars()
        return set(rows)


def _site_areas(runtime: Runtime, scraper, pattern) -> list[AreaTarget]:
    """サイトの性質に合わせて対象エリアを解決する。

    市区が必須のサイト（ABLE・GOO・賃貸EX）は ``search.cities`` が空でも
    都道府県内の全市区へ自動展開する（→ 課題#1）。
    """
    search = pattern.search
    with runtime.engine.connect() as conn:
        return resolve_areas(
            conn,
            site_code=scraper.site_code,
            prefectures=list(search.prefectures),
            cities=list(search.cities),
            requires_city=scraper.requires_city,
            city_value_source=scraper.city_value_source,
        )


def _rotate_areas(
    runtime: Runtime, scraper, pattern, areas: list[AreaTarget], *, size: int
) -> list[AreaTarget] | None:
    """取得数に上限があるサイトの取得枠を確保し、続きの市区を切り出す。

    枠を確保できなければ ``None``（この実行では別の帯が使う）。

    ⚠ **カーソルは取得を試みる前に進める。** スロットリングやボット検知で
    失敗した市区を再試行し続けると、そこから先へ永久に進めなくなる。
    """
    site_id = runtime.site_ids[scraper.site_code]
    with runtime.engine.begin() as conn:
        claim = persist.claim_city_rotation(
            conn, site_id=site_id, pattern_name=pattern.name, run_id=runtime.run_id
        )
    if not claim.claimed:
        return None

    rotated = rotate_areas(areas, last_city_jis=claim.last_city_jis, size=size)
    with runtime.engine.begin() as conn:
        persist.advance_city_rotation(
            conn,
            site_id=site_id,
            pattern_name=pattern.name,
            last_city_jis=next_cursor(rotated),
        )
    return rotated


def site_filter_query(scraper, pattern, site_params) -> dict[str, list[str]]:
    """MUST から、そのサイトへ渡せるフィルタのクエリを作る（→ ADR 0015）。

    足すのは **``site_filters.enabled`` が真で、アダプタが対応を宣言していて、
    そのサイトが除外指定に入っていない**ときだけ。クエリ文字列を持たないURL体系の
    サイト（賃貸EX・スモッカ）へ機械的に付けても効かないので、対応の可否は
    アダプタの宣言（``supports_site_filters``）に委ねる。

    渡すのは MUST だけなので、サイト側で落ちる掲載はローカルでも ``fail`` になる。
    取得量が減るだけで順位も通知も変わらない。
    """
    filters = pattern.search.site_filters
    if not filters.enabled or site_params is None:
        return {}
    if not getattr(scraper, "supports_site_filters", False):
        return {}
    if scraper.site_code in filters.exclude_sites:
        return {}
    return site_params.build_query(
        site_code=scraper.site_code,
        property_type=pattern.property_type,
        must=pattern.must,
        axes=filters.axes,
    )


def _with_site_filters(scraper, pattern, areas: list[AreaTarget], site_params) -> list[str]:
    """一覧URLに、サイト側フィルタのクエリを足して返す。

    ⚠ ``scraper.list_urls`` の結果を**1:1で加工する**（順序も件数も変えない）。
    対照取得はこの対応を前提に、同じ位置の素のURLを使う。
    """
    urls = scraper.list_urls(pattern, areas)
    query = site_filter_query(scraper, pattern, site_params)
    if not query:
        return list(urls)
    suffix = urlencode([(key, value) for key, values in query.items() for value in values])
    # ⚠ **クエリの有無で区切り文字を変える。** 「list_urls は必ずクエリ付きのURLを返す」
    # という前提でここを & 固定にしていたが、GOO は price_max_hint が無いと
    # クエリ無しのURLを返すため ``....html&fl=30`` という壊れたURLになる
    # （0件になるだけで例外にならない類の事故 → 課題#29）
    return [f"{url}{'&' if '?' in url else '?'}{suffix}" for url in urls]


def _collect_listings(
    scraper,
    fetcher: SiteFetcher,
    pattern,
    *,
    areas: list[AreaTarget],
    max_pages: int,
    outcome: SiteOutcome,
    site_params=None,
) -> list[ScrapedListing]:
    """一覧ページを辿って掲載を集める。

    ⚠ **GET＋HTML一覧という前提が成り立たないサイトがある。** UR賃貸は
    JSON API への POST で、しかも団地と住戸の2段になっている（→ ADR 0019）。
    そこで**任意フック ``collect_listings``** を宣言したアダプタには丸ごと委譲する。
    ``supports_site_filters`` と同じ宣言ベースの拡張で、既存アダプタは変わらない。
    """
    hook = getattr(scraper, "collect_listings", None)
    if hook is not None:
        return list(
            hook(fetcher, pattern, areas, max_pages=max_pages, outcome=outcome)
        )

    collected: list[ScrapedListing] = []
    filtered_urls = _with_site_filters(scraper, pattern, areas, site_params)
    # 対照取得に使う「フィルタを外した同じURL」。_with_site_filters は list_urls の
    # 結果を1:1で加工するので、順序と件数が一致する（strict=True で担保）
    plain_urls = list(scraper.list_urls(pattern, areas))
    control_checked = False
    # 対照取得で分かったことは**すぐには申告しない**（下の「サイト全体が0件のときだけ」を参照）
    blackout: str | None = None
    for base_url, plain_url in zip(filtered_urls, plain_urls, strict=True):
        for page in range(1, max_pages + 1):
            url = scraper.page_url(base_url, page)
            try:
                response = fetcher.get(url)
            except (SiteAborted, RobotsDisallowed):
                raise
            except Exception as exc:  # noqa: BLE001 - 1ページの失敗で実行を止めない
                outcome.errors.append(f"一覧取得に失敗: {url} ({exc})")
                break
            try:
                listings = scraper.parse_list(response.text)
            except Exception as exc:  # noqa: BLE001 - 壊れたページで他ページを止めない
                # 空応答や想定外のDOMで落ちても、そのページを飛ばして続ける
                outcome.errors.append(f"一覧の解析に失敗: {url} ({exc})")
                break
            collected.extend(listings)
            # ⚠ **フィルタ付きで0件になったら、外して1回だけ取り直す**（→ 課題#29）。
            # 無効値は HTTP 200 のまま0件を返し例外にならないので、対照を取らないと
            # 「絞り込めた」のか「壊れた」のか区別がつかない。
            # サイトごとに1回だけ行う（掲載が本当に無いエリアで毎回叩かないため）
            if page == 1 and not listings and not control_checked and base_url != plain_url:
                control_checked = True
                # ⚠ **市区ローテーションのサイトでは対照を取らない**（→ 課題#36・#39）。
                # 取得数の上限（HOMES 5・ATHOME 4）は**リクエスト数**に掛かっており、
                # 対照の1本がそのまま市区1つぶんの枠を食う。しかも対照自身が
                # 上限を踏んで検知ページを掴むので、「対照取得に失敗」という
                # **原因を取り違えたエラー**が出るだけで切り分けの役に立たない
                if getattr(scraper, "city_rotation_limit", None) is None:
                    blackout = _check_filter_blackout(scraper, fetcher, plain_url, outcome)
            if scraper.is_last_page(len(listings)):
                break
    # ⚠⚠ **申告するのは「そのサイトから1件も取れなかったとき」だけ**
    # （ユーザー判断 2026-09-05 → 課題#45）。
    # 一部の市区が0件になるのは**正しい絞り込み**である。千代田区・中央区に
    # 「30㎡以上・13万円以下」の住戸が実在しないのがその例で、実測では
    # SUUMO・GOO・HOMEMATE・CHINTAI_NET・DROOM の5サイトから
    # **2時間ごとに6件**のエラーが飛んでいた。
    # ⚠ **読まれない通知は、本物のエラーを見逃すという形で実害になる**
    # （→ requirements.md §14.1。1件ずつ送るのをやめた理由と同じ）。
    # ⚠ 検出力は落ちない。丸めは `AXIS_BOUND` が型で強制するので、
    # フィルタ値が壊れるならそのサイトの全URLで壊れ、`collected` が空になる。
    if blackout and not collected:
        outcome.errors.append(blackout)
    return collected


def _check_filter_blackout(
    scraper, fetcher: SiteFetcher, plain_url: str, outcome
) -> str | None:
    """サイト側フィルタを外した対照を1回だけ取り、0件の原因を切り分ける。

    対照にも掲載が無ければ「そのエリアに掲載が無い」だけなので何も言わない。
    対照に掲載があれば**フィルタが原因で0件になっている**ので、その旨を**返す**。

    ⚠ **ここでは `outcome.errors` へ入れない。** 一部の市区が0件になるのは
    正常なので、呼び出し側が「そのサイトから1件も取れなかったか」を見てから
    申告する（→ 課題#45）。

    ⚠ **対照取得そのものの失敗は即エラーにする。** これは取得の失敗であって
    「絞り込めた結果の0件」ではなく、頻度も低い。
    """
    try:
        listings = scraper.parse_list(fetcher.get(plain_url).text)
    except (SiteAborted, RobotsDisallowed):
        raise
    except Exception as exc:  # noqa: BLE001 - 対照取得の失敗で実行を止めない
        outcome.errors.append(f"対照取得に失敗: {plain_url} ({exc})")
        return None
    if not listings:
        return None
    return (
        "サイト側フィルタで0件になった疑い: "
        f"フィルタ有り0件 / 外すと{len(listings)}件 ({plain_url})"
    )


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
    if limit < 1:
        # 市区ローテーションのサイトは一覧で予算を使い切るので詳細を取らない
        # （→ 課題#36）。キューには残るので、予算の回復窓を実測してから
        # 「N回に1回の詳細回」を入れるか決める
        return
    with runtime.engine.connect() as conn:
        # 枠の半分を「古い順」に充てて滞留を必ず削る（→ 課題#54）。
        # ⚠ 滞留が解消すれば未取得は新しい掲載だけになり、古い順と新しい順が
        # 同じ集合を指すので**恒常的な副作用は無い**。効くのは詰まっている間だけ
        queue = persist.detail_queue(
            conn, site_id=site_id, limit=limit, oldest_limit=limit // 2
        )

    # ⚠ **詳細もGETとは限らない。** UR賃貸は住戸詳細がJSON APIへの POST なので、
    # 任意フック ``fetch_detail`` を宣言したアダプタには「取得＋解析」を委譲する。
    # キュー・``--detail-limit``・保存・設備抽出は既存のまま共有する（→ ADR 0019）
    fetch_detail = getattr(scraper, "fetch_detail", None)
    # 平文へのリダイレクトが掲載終了を意味するかはサイトごとに違う。
    # ⚠ **アダプタが宣言したときだけ**委ねる（``SiteScraper`` Protocol には
    # 足さない。既存17アダプタに実装義務が生じる → ADR 0019）
    is_sold_redirect = getattr(scraper, "is_sold_redirect", None)
    sold_ids: list[int] = []

    for listing_id, url in queue:
        try:
            if fetch_detail is not None:
                detail = fetch_detail(fetcher, url)
            else:
                response = fetcher.get(scraper.detail_url(url))
                detail = scraper.parse_detail(response.text)
        except (SiteAborted, RobotsDisallowed):
            raise
        except PlaintextRedirect as exc:
            # 掲載が終わった詳細URLが平文へリダイレクトしている（→ 課題#55）。
            # ⚠⚠ **ここで ``sold`` にしないとキューに残り続け、次回また
            # 引かれて詳細取得の枠を恒久的に食う。** 実測（2026-09-06）で
            # SUUMO の詳細取得が 38件 → 29件 へ単調減少した。
            # ⚠ 「``check_sold`` の役割」として触らずにいたが、あちらは
            # 1日1回・297件しか見ないので追いつかない。しかも
            # **古い掲載ほど掲載終了している**ので、課題#54 の「古い順」枠が
            # これを増幅する
            if is_sold_redirect is not None and is_sold_redirect(exc):
                sold_ids.append(listing_id)
                outcome.details_sold += 1
                continue
            # 掲載終了と判断できないサイトは、従来どおり失敗として残す
            outcome.errors.append(f"詳細取得に失敗（平文へリダイレクト）: {url}")
            continue
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で実行を止めない
            outcome.errors.append(f"詳細取得に失敗: {url} ({exc})")
            continue

        try:
            extraction = extract_from_text(
                detail.raw_features_text,
                runtime.dictionary,
                family=family,
                site_code=scraper.site_code,
                source=SOURCE_DETAIL,
                unknown_text=detail.unknown_token_text,
            )
            # 型付き列からの導出を先に渡して、辞書照合より優先させる。
            derived = derive_features(
                floor_num=detail.floor_num,
                total_floors=detail.total_floors,
                age_years=None,
            )
            features = merge_features(derived, extraction.features)
        except Exception as exc:  # noqa: BLE001 - 1件の失敗でサイト全体を止めない
            # 空応答（HOME'S で実測）や想定外のDOMでも、その物件だけ飛ばす。
            # ここを括らないと1ページの破損で残りのキューが丸ごと処理されない
            outcome.errors.append(f"詳細の解析に失敗: {url} ({exc})")
            continue

        with runtime.engine.begin() as conn:
            persist.save_detail(conn, listing_id, detail)
            saved = persist.save_features(conn, listing_id, features, runtime.condition_ids)
            persist.save_unknown_tokens(
                conn,
                extraction.unknown_tokens,
                site_id=site_id,
                property_family=family,
                sample_url=url,
            )
            # 詳細で階数・住所が埋まると、一覧の時点では作れなかった名寄せキーが
            # 作れるようになる。ここで呼ばないとキー充足率が上がらない
            dedup.refresh_dedup_keys(conn, [listing_id], runtime.address_index)
        outcome.details_fetched += 1
        outcome.features_extracted += saved

    if sold_ids:
        # ⚠ **ループを抜けてからまとめて更新する。** 途中で書くと、
        # 打ち切り（``SiteAborted``）で抜けたときに一部だけ反映される
        with runtime.engine.begin() as conn:
            persist.mark_status(conn, sold_ids, "sold")
            # 代表が掲載終了したグループは代表を選び直す。集合演算なので
            # 「誰が代表だったか」を覚えておく必要がない（``check_sold`` と同じ）
            dedup.sync_groups(conn)


def _refresh_commute(
    runtime: Runtime, pattern, listing_ids: list[int], summary: ScanSummary
) -> None:
    """新着掲載の駅を同定し、通勤時間のキャッシュを更新する。

    駅表記は一覧ページにしか出ない（``ScrapedDetail`` は持たない）ので、
    ``refresh_dedup_keys`` と違って**一覧の upsert 後に1回**呼べば足りる。

    ⚠ 駅の接続情報CSVは再配布不可でGit管理外なので、無い環境もありうる。
    その場合でも ``scan`` は止めず、エラーとして記録して通勤時間を unknown のままにする。

    ⚠⚠ **回帰式で実ダイヤ（NAVITIME）の行を踏み潰さない。** ここは
    ``resolve-commutes`` と同じ見積もりを書くので、素朴に全件書き直すと
    **4.8時間かけて採った実測値が scan のたびに見積もりへ戻る**。
    しかもエラーにならず ``source`` が rail_graph に変わるだけなので気づけない
    （実測 2026-09-04: 芝公園ゆき1,155駅すべてが回帰式に戻っていた）。
    CLI 側（``cli._cmd_resolve_commutes``）には同じ保護が入っていたが、
    **こちらだけ抜けていた**（→ ADR 0017）。
    """
    from house_search.commute.graph import estimate_from, load_links, station_nodes
    from house_search.commute.resolve import (
        STATUS_NO_ROUTE,
        STATUS_OK,
        listing_prefecture_codes,
        load_station_index,
        load_station_nodes,
        referenced_station_groups,
        resolve_destination_group,
        resolve_listing_stations,
        save_commutes,
    )
    from house_search.commute.timetable import SOURCE_NAVITIME, origins_with_source

    if pattern.commute is None or not listing_ids:
        return
    try:
        with runtime.engine.begin() as conn:
            index = load_station_index(conn, listing_prefecture_codes(conn))
            if not index.by_key:
                raise ValueError("駅マスタが空です。sync-stations を実行してください")
            resolve_listing_stations(conn, index, listing_ids=listing_ids)

        with runtime.engine.connect() as conn:
            destination = resolve_destination_group(conn, pattern.commute)
            nodes = load_station_nodes(conn)
            groups = referenced_station_groups(conn)
            measured = (
                origins_with_source(
                    conn, destination_g_cd=destination, source=SOURCE_NAVITIME
                )
                if destination is not None
                else frozenset()
            )
        if destination is None:
            return  # 目的地の解決失敗は _commute_destination 側で記録する

        estimates = estimate_from(
            station_nodes(nodes), load_links(runtime.settings.data_dir), destination
        )
        rows = [
            (
                group_code,
                STATUS_OK if group_code in estimates else STATUS_NO_ROUTE,
                estimates[group_code].minutes if group_code in estimates else None,
                estimates[group_code].transfers if group_code in estimates else None,
                estimates[group_code].distance_km if group_code in estimates else None,
            )
            for group_code in groups
            if group_code not in measured
        ]
        with runtime.engine.begin() as conn:
            save_commutes(conn, destination_g_cd=destination, rows=rows)
    except Exception as exc:  # noqa: BLE001 - 通勤時間の失敗で scan 全体を止めない
        message = f"通勤時間の更新に失敗しました: {exc}"
        summary.pattern_errors.append(message)
        with runtime.engine.begin() as conn:
            persist.log(
                conn,
                run_id=runtime.run_id,
                level="ERROR",
                message=message,
                pattern_name=pattern.name,
            )


def _commute_destination(runtime: Runtime, pattern, conn, summary) -> int | None:
    """勤務先の最寄り駅を駅グループコードへ解決する。

    ⚠ 解決できないまま進むと通勤時間が全件 unknown になり、条件を書いたのに
    効いていない状態になる。**エラーとして記録して気づけるようにする。**
    """
    from house_search.commute.resolve import resolve_destination_group

    if pattern.commute is None:
        return None
    destination = resolve_destination_group(conn, pattern.commute)
    if destination is None:
        message = (
            f"通勤時間の目的地 '{pattern.commute.destination_station}' を"
            "駅マスタから一意に解決できません（通勤時間は unknown になります）"
        )
        summary.pattern_errors.append(message)
        persist.log(
            conn, level="ERROR", message=message, pattern_name=pattern.name, run_id=runtime.run_id
        )
    return destination


def _score_pattern(runtime: Runtime, pattern, summary: ScanSummary) -> dict[int, ListingView]:
    """パターン対象の物件を採点して保存する。"""
    config_hash = pattern.config_hash()
    with runtime.engine.connect() as conn:
        destination = _commute_destination(runtime, pattern, conn, summary)
        views = persist.load_listing_views(
            conn,
            property_type_code=pattern.property_type,
            site_codes=list(pattern.sites),
            # 採点をエリア帯に閉じる。帯は取得URLを絞るだけなので、
            # これが無いと帯外の既存データにも帯のスコアが付いてしまう
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
            summary.scored += 1
            if must.passes(pattern.must.unknown_policy):
                summary.must_pass += 1
                passed.append(view)
        # エリア帯から外れた掲載の古いスコア行を消す（残すと二重採点になる）
        persist.prune_scores(conn, pattern.name, list(views))
        persist.update_ranks(conn, pattern.name)
    # ⚠ MUST を通った掲載だけを見る。fail はランキングにも通知にも出ないので
    # 警告しても行動につながらない
    summary.price_anomalies = collect_price_anomalies(passed)
    return views


def _group_rank(
    ranks: dict[int, int], listing_id: int, membership: dedup.GroupMembership
) -> int | None:
    """順位を引く。非代表メンバーには代表の順位を見せる。

    順位はグループ代表にしか振らないので、非代表の掲載を新着通知するときに
    そのまま引くと「順位未確定」になってしまう。
    """
    if (rank := ranks.get(listing_id)) is not None:
        return rank
    if membership.representative_listing_id is not None:
        return ranks.get(membership.representative_listing_id)
    return None


def _within_notify_rank(rank: int | None, max_rank: int | None) -> bool:
    """個別通知を上位N位までに絞る（``ranking.notify_max_rank``）。

    ⚠ **収集・採点・ダイジェストには効かない。** 絞るのは Discord への
    個別通知だけで、圏外の掲載も従来どおりDBに入り順位も付く。

    ⚠ **順位が引けなかった掲載（None）は通す**（ユーザー判断 2026-09-05）。
    順位付けの不具合や代表交代の隙間で通知が**黙って全滅する**のを避けるため。
    落とす側に倒すと、鳴らないことが正常と見分けられない。
    """
    if max_rank is None or rank is None:
        return True
    return rank <= max_rank


def _notify_cheaper_listings(
    runtime: Runtime,
    pattern,
    views: dict[int, ListingView],
    group_changes: list[dedup.GroupChange],
    ranks: dict[int, int],
    webhook_url: str,
    summary: ScanSummary,
) -> None:
    """同一住戸がより安い掲載で見つかったグループを通知する。

    代表の交代（＝グループ内の最安が入れ替わった）を検出したものだけが対象。
    金額まで見て重複を避けるので、さらに安くなれば再通知され、
    同額の再検出（グループの作り直しなど）では送らない。
    """
    for change in group_changes:
        if not change.is_cheaper or change.current_listing_id is None:
            continue
        view = views.get(change.current_listing_id)
        if view is None:
            continue
        if not evaluate_must(view, pattern.must).passes(pattern.must.unknown_policy):
            continue

        with runtime.engine.connect() as conn:
            if persist.cheaper_listing_notified_at(
                conn,
                group_id=change.group_id,
                pattern_name=pattern.name,
                price=view.price,
            ):
                continue
            membership = dedup.group_membership(conn, [change.current_listing_id])[
                change.current_listing_id
            ]

        rank = _group_rank(ranks, view.listing_id or 0, membership)
        if not _within_notify_rank(rank, pattern.ranking.notify_max_rank):
            summary.notify_out_of_rank += 1
            continue

        previous = views.get(change.previous_listing_id or -1)
        score = calculate_score(view, pattern.want)
        message = build_listing_message(
            notifiable_from(
                view,
                member_count=membership.member_count,
                other_site_codes=membership.other_site_codes,
                previous_total=change.previous_cost,
                previous_site_code=previous.site_code if previous else None,
            ),
            score,
            notification_type=persist.CHEAPER_LISTING,
            pattern_name=pattern.name,
            rank_in_pattern=rank,
        )
        sent = runtime.sender.send(webhook_url, message)
        with runtime.engine.begin() as conn:
            persist.record_notification(
                conn,
                listing_id=change.current_listing_id,
                group_id=change.group_id,
                pattern_name=pattern.name,
                notification_type=persist.CHEAPER_LISTING,
                price_at_notify=view.price,
                score_at_notify=score.score,
                status="sent" if sent else "failed",
            )
        if sent:
            summary.notified += 1
        else:
            summary.notify_failed += 1


def _notify(
    runtime: Runtime,
    pattern,
    views: dict[int, ListingView],
    outcomes: list[persist.UpsertOutcome],
    group_changes: list[dedup.GroupChange],
    summary: ScanSummary,
) -> None:
    """新着・価格変動・他サイト安値の個別通知を送る。"""
    webhook_url = runtime.settings.webhook_url(pattern.webhook_ref)

    with runtime.engine.connect() as conn:
        ranks = {
            listing_id: rank
            for listing_id, rank in conn.execute(
                text(
                    "SELECT listing_id, rank_in_pattern FROM t_listing_scores "
                    "WHERE pattern_name = :name AND rank_in_pattern IS NOT NULL"
                ),
                {"name": pattern.name},
            )
        }
        memberships = dedup.group_membership(conn, [o.listing_id for o in outcomes])

    for outcome in outcomes:
        notification_type = outcome.notification_type
        if notification_type is None:
            continue
        view = views.get(outcome.listing_id)
        if view is None:
            continue

        must = evaluate_must(view, pattern.must)
        if not must.passes(pattern.must.unknown_policy):
            continue

        membership = memberships.get(outcome.listing_id, dedup.NO_GROUP)
        rank = _group_rank(ranks, outcome.listing_id, membership)
        if not _within_notify_rank(rank, pattern.ranking.notify_max_rank):
            summary.notify_out_of_rank += 1
            continue

        with runtime.engine.connect() as conn:
            # グループ単位で抑制する。同一住戸の別サイト掲載を
            # それぞれ「新着」として二重に送らないため
            if persist.already_notified(
                conn,
                listing_id=outcome.listing_id,
                pattern_name=pattern.name,
                notification_type=notification_type,
                group_id=membership.group_id,
            ):
                continue

        score = calculate_score(view, pattern.want)
        message = build_listing_message(
            notifiable_from(
                view,
                member_count=membership.member_count,
                other_site_codes=membership.other_site_codes,
                price_prev=outcome.price_prev,
            ),
            score,
            notification_type=notification_type,
            pattern_name=pattern.name,
            rank_in_pattern=rank,
        )
        sent = runtime.sender.send(webhook_url, message)
        with runtime.engine.begin() as conn:
            persist.record_notification(
                conn,
                listing_id=outcome.listing_id,
                group_id=membership.group_id,
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

    _notify_cheaper_listings(
        runtime, pattern, views, group_changes, ranks, webhook_url, summary
    )


def scan_pattern(
    runtime: Runtime,
    pattern,
    *,
    site_filter: str | None = None,
    seed_mode: bool = False,
    full_scan: bool = False,
    detail_limit_override: int | None = None,
) -> ScanSummary:
    """検索パターン1件ぶんのスキャンを実行する。"""
    summary = ScanSummary(pattern_name=pattern.name)
    family = pattern.family.value
    property_type_id = runtime.property_type_ids[pattern.property_type]
    all_outcomes: list[persist.UpsertOutcome] = []
    # 都道府県を前置しない住所の照合は、このパターンが対象にしている都道府県の
    # 中でだけ行う。全国マスタでは「府中市」（東京都/広島県）のように名前が
    # 衝突し、範囲を絞らないと引き当てられない（→ ADR 0014）。
    city_index = runtime.city_index.scoped_to(pattern.search.prefectures)

    target_sites = [s for s in pattern.sites if site_filter is None or s == site_filter]
    # 無効化されたサイト（観測モード待ちの賃貸EX など）は通常の実行では取りに行かない。
    # ただし --site で名指しされたときは観測のために動かす
    inactive = _inactive_sites(runtime) if site_filter is None else set()

    for site_code in target_sites:
        if site_code in inactive:
            summary.skipped_sites.append(f"{site_code}（is_active=false）")
            continue
        # ⚠ **種別まで指定して引く。** サイトコードだけで引くと
        # 売買パターンで賃貸のアダプタが動き、URL体系が違うので
        # **0件になるだけで例外にならない**（→ 課題#4）
        scraper = get_scraper(site_code, pattern.property_type)
        if scraper is None:
            summary.skipped_sites.append(f"{site_code}（アダプタ未実装）")
            continue

        site_id = runtime.site_ids.get(site_code)
        if site_id is None:
            summary.skipped_sites.append(f"{site_code}（サイトマスタに行が無い）")
            continue

        outcome = SiteOutcome(site_code=site_code)
        areas = _site_areas(runtime, scraper, pattern)
        if not areas:
            # 市区必須のサイトで検索値を1つも解決できなかった場合。
            # 都道府県で代替すると0件になるだけなので、理由を残してスキップする
            outcome.errors.append("対象市区の検索値を解決できませんでした")
            summary.sites.append(outcome)
            continue
        rate_limit = _site_rate_limit(runtime, site_code)
        max_pages = rate_limit.max_pages_per_run if full_scan else 1
        detail_limit = resolve_detail_limit(full_scan, detail_limit_override)

        rotation_size = getattr(scraper, "city_rotation_limit", None)
        if rotation_size:
            rotated = _rotate_areas(runtime, scraper, pattern, areas, size=rotation_size)
            if rotated is None:
                # この実行では別の帯がこのサイトの枠を使う。予算を分け合うと
                # どちらの帯も上限に当たって取れなくなる（→ 課題#36）
                summary.skipped_sites.append(f"{site_code}（他パターンが取得枠を使用中）")
                continue
            areas = rotated
            # ⚠ **ページ送りも詳細取得もしない。** 上限は「リクエスト数」に
            # 掛かっているので、2ページ目や詳細を1件でも取ると一覧の市区が
            # そのぶん取れなくなる。詳細は予算の回復窓を実測してから検討する
            max_pages = 1
            detail_limit = 0

        with runtime.engine.begin() as conn:
            run_row = persist.start_run(
                conn,
                run_id=runtime.run_id,
                mode="seed" if seed_mode else ("full" if full_scan else "scan"),
                pattern_name=pattern.name,
                site_id=site_id,
            )

        client = runtime.http_client(user_agent=scraper.user_agent)
        fetcher = SiteFetcher(
            site_code=site_code,
            client=client,
            rate_limit=rate_limit,
            # robots.txt を無視するのはアダプタが明示的に宣言したサイトだけ
            ignore_robots=bool(getattr(scraper, "ignore_robots", False)),
        )
        status = "completed"
        try:
            listings = _collect_listings(
                scraper,
                fetcher,
                pattern,
                areas=areas,
                max_pages=max_pages,
                outcome=outcome,
                site_params=runtime.site_params,
            )
            outcome.listings_seen = len(listings)
            # ⚠ 市区ローテーションのサイト（HOMES 5・ATHOME 4 リクエストで頭打ち）は
            # **予算切れで全市区が0件になるのが正常**なので、0件を異常と呼べない
            # （→ 課題#36）。申告すると2時間ごとに偽陽性が飛び、本物のエラーが
            # 読まれなくなる（→ 課題#45 と同じ理由）。ローテーションサイトで
            # 対照取得を行わないのと同じ判断。
            rotates_cities = bool(getattr(scraper, "city_rotation_limit", None))
            if not listings and not rotates_cities:
                # ⚠ 一覧0件は「取れているつもり」で気づけない失敗の終着点になる
                # （無効なフィルタ値・ボット検知・DOM変更のどれでも0件になり、
                # どれも例外にならない）。過去の実績と突き合わせて異常を申告する
                with runtime.engine.connect() as conn:
                    known = persist.site_listing_count(conn, site_id)
                if known:
                    outcome.errors.append(
                        f"一覧が0件。過去に {known}件 取り込んでいるサイトなので異常の疑い"
                    )

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
                    city_index=city_index,
                )
                dedup.refresh_dedup_keys(
                    conn, [o.listing_id for o in outcomes], runtime.address_index
                )
            outcome.listings_new = sum(1 for o in outcomes if o.is_new)
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
                    items_new=outcome.listings_new,
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

    # 名寄せは採点の前に済ませる。設備の和集合も「順位は代表にだけ振る」も、
    # 採点の時点でグループが確定していることを前提にしているため。
    with runtime.engine.begin() as conn:
        group_changes = dedup.sync_groups(conn)
    summary.groups_changed = len(group_changes)

    _refresh_commute(runtime, pattern, [o.listing_id for o in all_outcomes], summary)

    views = _score_pattern(runtime, pattern, summary)

    if seed_mode:
        # シードモードは通知を送らず記録だけ行う。旧通知履歴を捨てても
        # 「再掲載が全部新着として再通知される」問題が構造的に起きなくなる（→ ADR 0006）
        return summary

    _notify(runtime, pattern, views, all_outcomes, group_changes, summary)
    return summary
