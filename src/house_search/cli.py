"""コマンドラインインタフェース。

Phase 1 で ``scan`` / ``check-sold`` / ``digest`` / ``rescore`` / ``sync-dict`` /
``re-extract`` / ``report-unknown`` が実働する。未実装のサブコマンドは
呼ばれたら「どの Phase で実装するか」を告げて終了コード 2 で終わる
（黙って何もしないと「実装済みだが未配線」を見逃すため）。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from house_search import __version__

# 未実装サブコマンドと、実装予定の Phase。Phase 2 ですべて実装済みになった。
PLANNED: dict[str, str] = {}

# NAVITIME から実ダイヤを取るときの既定の基準（Phase 5D）。
# ⚠ **固定値にしてある。** 出発日は t_navitime_routes の一意キーの一部なので、
# 「次の水曜」のように動かすと再実行のたびに全駅を取り直すことになる。
# 2026-09-09 は水曜で、Phase 5C の校正もこの日で行った。
DEFAULT_DEPART_ON = "2026-09-09"
DEFAULT_DEPART_AT = "08:30"


def build_parser() -> argparse.ArgumentParser:
    """サブコマンド構成を組み立てる。"""
    parser = argparse.ArgumentParser(
        prog="house-search",
        description="物件検索通知システム v2 — 横断取得・スコアリング・Discord通知",
    )
    parser.add_argument("--version", action="version", version=f"house-search {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p_seed = sub.add_parser("db-seed", help="マスタデータ（db/seed/*.sql）を投入する")
    p_seed.add_argument(
        "--test-db", action="store_true", help="DATABASE_TEST_URL のテストDBへ投入する"
    )

    p_validate = sub.add_parser(
        "validate-config", help="検索パターンYAMLと webhook_ref の参照を検証する"
    )
    p_validate.add_argument(
        "--configs-dir", help="検証対象ディレクトリ（省略時は .env の CONFIGS_DIR）"
    )
    p_validate.add_argument(
        "--skip-webhook", action="store_true", help="webhook_ref に対応する環境変数の存在確認を省く"
    )

    p_scan = sub.add_parser("scan", help="一覧取得 → MUST判定 → 詳細 → 抽出 → スコア → 通知")
    p_scan.add_argument("--pattern", help="対象の検索パターン名（省略時は全件）")
    p_scan.add_argument("--site", help="対象サイトコード（省略時はパターンの全サイト）")
    p_scan.add_argument(
        "--seed",
        action="store_true",
        help="シードモード。通知を送らず記録だけ行う（初回全件取得・長期停止からの再開で使う）",
    )
    p_scan.add_argument(
        "--full", action="store_true", help="全量スキャン（m_sites.max_pages_per_run まで辿る）"
    )
    p_scan.add_argument(
        "--detail-limit",
        type=int,
        help=(
            "詳細ページを取りに行く上限（サイトあたり）。省略時は 40 / --full 時は 400。"
            "初回全件スキャンで詳細キューを一気に掃くために使う"
        ),
    )

    p_sold = sub.add_parser("check-sold", help="成約・掲載終了の確認")
    p_sold.add_argument("--pattern", help="対象の検索パターン名")
    p_sold.add_argument("--limit", type=int, default=100, help="1回に確認する件数（既定100）")

    p_digest = sub.add_parser("digest", help="日次ランキングダイジェストの送信")
    p_digest.add_argument("--pattern", help="対象の検索パターン名")
    p_digest.add_argument("--dry-run", action="store_true", help="送信せず件数だけ確認する")

    p_rescore = sub.add_parser("rescore", help="DB内の物件属性から再採点（ネットワーク不要）")
    p_rescore.add_argument("--pattern", help="対象の検索パターン名")

    p_sync = sub.add_parser("sync-dict", help="data/feature_dictionary.yaml → m_condition_synonyms")
    p_sync.add_argument("--test-db", action="store_true", help="テストDBへ同期する")

    p_reextract = sub.add_parser("re-extract", help="raw_features_text から全件再抽出")
    p_reextract.add_argument("--limit", type=int, help="処理件数の上限（動作確認用）")

    p_unknown = sub.add_parser("report-unknown", help="辞書未登録の表記を出現回数順に一覧")
    p_unknown.add_argument("--limit", type=int, default=50, help="表示件数（既定50）")

    sub.add_parser("coverage", help="サイト別の設備抽出数分布・数値カラム非NULL率を実測する")

    sub.add_parser(
        "regroup",
        help="名寄せキーを全件作り直してグループを同期する（ネットワーク不要・通知なし）",
    )
    sub.add_parser("dedup-stats", help="サイト別の名寄せ実測（クロスサイト重複率・ユニーク率）")

    sub.add_parser(
        "sync-site-params",
        help="data/site_search_params.yaml を m_site_search_params へ同期する",
    )

    sub.add_parser(
        "resolve-cities",
        help="既存掲載の市区町村IDを現在のマスタで引き直す（ネットワーク不要）",
    )

    sub.add_parser(
        "sync-stations",
        help="data/train_master/*.csv を m_stations へ同期する（ネットワーク不要）",
    )

    p_stations = sub.add_parser(
        "resolve-stations",
        help="掲載の駅表記を駅マスタと突き合わせる（ネットワーク不要）",
    )
    p_stations.add_argument(
        "--limit", type=int, default=30, help="同定できなかった表記の表示件数（既定30）"
    )

    p_commutes = sub.add_parser(
        "resolve-commutes",
        help="駅ペアの通勤所要時間を算出してキャッシュする（ネットワーク不要）",
    )
    p_commutes.add_argument("--pattern", help="対象を1つの検索パターンに絞る")
    p_commutes.add_argument("--destination", help="目的地の駅名（既定はパターンの commute）")
    p_commutes.add_argument("--destination-prefecture", help="目的地の都道府県名")

    p_fetch = sub.add_parser(
        "fetch-commutes",
        help="NAVITIME の乗換案内から実ダイヤの通勤時間を取得する（要ネットワーク）",
    )
    p_fetch.add_argument("--pattern", help="対象を1つの検索パターンに絞る")
    p_fetch.add_argument("--destination", help="目的地の駅名（既定はパターンの commute）")
    p_fetch.add_argument("--destination-prefecture", help="目的地の都道府県名")
    p_fetch.add_argument(
        "--depart-on",
        default=DEFAULT_DEPART_ON,
        help=f"出発日 YYYY-MM-DD（既定 {DEFAULT_DEPART_ON}・平日）",
    )
    p_fetch.add_argument(
        "--depart-at", default=DEFAULT_DEPART_AT, help=f"出発時刻 HH:MM（既定 {DEFAULT_DEPART_AT}）"
    )
    p_fetch.add_argument(
        "--limit", type=int, help="取得する駅数の上限（試し取りに使う。既定は残り全部）"
    )
    p_fetch.add_argument(
        "--station", action="append", help="駅名を名指しで取得する（検証用・複数指定可）"
    )
    p_fetch.add_argument(
        "--region",
        help=(
            "地方名（data/commute_destinations.yaml）。目的地をその地方の中心駅にし、"
            "対象を掲載の有無によらずその地方の全駅へ広げる"
        ),
    )
    p_fetch.add_argument(
        "--refetch", action="store_true", help="取得済みの駅もやり直す（既定は未取得のみ）"
    )

    p_reseg = sub.add_parser(
        "re-segment",
        help="保存済みの経路原文から乗車区間を作り直す（ネットワーク不要）",
    )
    p_reseg.add_argument("--pattern", help="対象を1つの検索パターンに絞る")
    p_reseg.add_argument("--destination", help="目的地の駅名（既定はパターンの commute）")
    p_reseg.add_argument("--destination-prefecture", help="目的地の都道府県名")
    p_reseg.add_argument(
        "--region",
        help="地方名（data/commute_destinations.yaml）。目的地とその地方の駅索引を使う",
    )

    p_cstats = sub.add_parser(
        "commute-stats",
        help="通勤時間の分布を実測する（best/worst と MUST を決める材料・ネットワーク不要）",
    )
    p_cstats.add_argument("--pattern", help="対象を1つの検索パターンに絞る")
    return parser


def _load_patterns(name: str | None):
    """検索パターンを読み込む。``--pattern`` 指定があれば name で絞る。"""
    from house_search.config.pattern import load_patterns
    from house_search.config.settings import load_settings

    patterns = load_patterns(load_settings().configs_dir)
    if name:
        patterns = [p for p in patterns if p.name == name]
        if not patterns:
            raise ValueError(f"検索パターン '{name}' が見つかりません")
    return patterns


def _cmd_db_seed(args: argparse.Namespace) -> int:
    from house_search.config.settings import load_settings
    from house_search.db.seed import apply_seed
    from house_search.db.session import create_db_engine

    settings = load_settings()
    if args.test_db:
        if not settings.database_test_url:
            print("DATABASE_TEST_URL が .env に設定されていません", file=sys.stderr)
            return 1
        url = settings.database_test_url
    else:
        url = settings.database_url

    engine = create_db_engine(url)
    result = apply_seed(engine)
    for name in result.applied_files:
        print(f"適用: {name}")
    print("--- 投入後の行数 ---")
    for table, count in result.row_counts.items():
        print(f"  {table:28s} {count:6d}")

    shortfalls = result.shortfalls()
    if shortfalls:
        print("期待行数に達していないテーブルがあります:", file=sys.stderr)
        for table, (actual, expected) in shortfalls.items():
            print(f"  {table}: {actual} < {expected}", file=sys.stderr)
        return 1
    return 0


def _cmd_validate_config(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from house_search.config.pattern import load_pattern_file
    from house_search.config.settings import load_settings

    settings = load_settings()
    configs_dir = Path(args.configs_dir) if args.configs_dir else settings.configs_dir
    files = sorted(configs_dir.glob("*.yaml"))
    if not files:
        print(f"検索パターンYAMLが見つかりません: {configs_dir}", file=sys.stderr)
        return 1

    failures = 0
    known_names: list[str] = []
    for path in files:
        try:
            pattern = load_pattern_file(path)
        except (ValidationError, ValueError) as exc:
            failures += 1
            print(f"NG  {path.name}\n{exc}", file=sys.stderr)
            continue

        if not args.skip_webhook:
            try:
                settings.webhook_url(pattern.webhook_ref)
            except ValueError as exc:
                failures += 1
                print(f"NG  {path.name}: {exc}", file=sys.stderr)
                continue

        print(
            f"OK  {path.name}  name={pattern.name} "
            f"type={pattern.property_type} config_hash={pattern.config_hash()[:12]}"
        )
        known_names.append(pattern.name)

    failures += _warn_orphan_scores(known_names)
    return 1 if failures else 0


def _warn_orphan_scores(known_names: list[str]) -> int:
    """configs に無いパターン名のスコア行が残っていないか調べる。

    スコア行はパターン名ごとに持つが、パターンを廃止しても消えない。
    残っていても digest / check-sold はパターン名で絞るので実害は無いが、
    **パターン名で絞らずに集計すると順位が重複して見える**（実際に読み違えた）。
    DBへ繋げないときは黙って何もしない（検証そのものはDB無しでも通したい）。
    """
    if not known_names:
        return 0
    try:
        from sqlalchemy import text

        from house_search.db.session import get_engine

        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT pattern_name, count(*) FROM t_listing_scores "
                    "WHERE NOT (pattern_name = ANY(:names)) GROUP BY 1 ORDER BY 2 DESC"
                ),
                {"names": known_names},
            ).all()
    except Exception:  # noqa: BLE001 - DBが無くてもYAML検証は通す
        return 0

    for name, count in rows:
        print(
            f"警告  configs に無いパターン '{name}' のスコア行が {count} 件残っています。"
            " DELETE FROM t_listing_scores WHERE pattern_name = '"
            f"{name}'; で消せます",
            file=sys.stderr,
        )
    return 0


def _skipped_by_lock(command: str) -> None:
    print(
        f"{command}: 他の取得処理が実行中のためスキップしました。"
        "レート制御は SiteFetcher のプロセス内にしかないため並走させません。",
        file=sys.stderr,
    )


def _cmd_scan(args: argparse.Namespace) -> int:
    from house_search.db.session import scraping_lock

    with scraping_lock() as acquired:
        if not acquired:
            _skipped_by_lock("scan")
            return 0
        return _run_scan(args)


def _run_scan(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.scan import scan_pattern

    runtime = build_runtime()
    if not runtime.dictionary.entries:
        print(
            "設備抽出辞書が空です。先に `house-search sync-dict` を実行してください。",
            file=sys.stderr,
        )
        return 1

    patterns = _load_patterns(args.pattern)
    exit_code = 0
    for pattern in patterns:
        summary = scan_pattern(
            runtime,
            pattern,
            site_filter=args.site,
            seed_mode=args.seed,
            full_scan=args.full,
            detail_limit_override=args.detail_limit,
        )
        mode = "シードモード（通知なし）" if args.seed else "通常"
        print(f"\n=== {summary.pattern_name} / {mode} ===")
        for site in summary.sites:
            print(
                f"  {site.site_code:10s} 取得 {site.listings_seen:4d} → "
                f"MUST1段目通過 {site.listings_kept:4d} → 新規 {site.listings_new:4d} / "
                f"詳細 {site.details_fetched:3d}件 / 設備 {site.features_extracted:4d}件"
            )
        if summary.skipped_sites:
            # 理由はサイトごとに違う（アダプタ未実装 / is_active=false /
            # 市区ローテーションの枠を他パターンが使用中）。理由を一括で
            # 決め打ちすると「実装済みだが未配線」と読み違えるので、
            # 理由付きの項目はそのまま出す
            print(f"  スキップ: {', '.join(summary.skipped_sites)}")
        print(f"  採点 {summary.scored}件 / MUST通過 {summary.must_pass}件")
        if not args.seed:
            print(f"  通知 成功 {summary.notified}件 / 失敗 {summary.notify_failed}件")
        for message in summary.errors:
            print(f"  [エラー] {message}", file=sys.stderr)
            exit_code = 1
    return exit_code


def _cmd_check_sold(args: argparse.Namespace) -> int:
    from house_search.db.session import scraping_lock
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import check_sold

    with scraping_lock() as acquired:
        if not acquired:
            _skipped_by_lock("check-sold")
            return 0
        return _run_check_sold(args, build_runtime(), check_sold)


def _run_check_sold(args: argparse.Namespace, runtime, check_sold) -> int:
    for pattern in _load_patterns(args.pattern):
        result = check_sold(runtime, pattern, limit=args.limit)
        print(f"{pattern.name}: 確認 {result.checked}件 / 成約・掲載終了 {result.sold}件")
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import digest

    runtime = build_runtime()
    exit_code = 0
    for pattern in _load_patterns(args.pattern):
        result = digest(runtime, pattern, dry_run=args.dry_run)
        state = "（送信せず）" if args.dry_run else ("送信成功" if result.sent else "送信失敗")
        print(f"{result.pattern_name}: 上位 {result.entries}件 {state}")
        if not args.dry_run and not result.sent:
            exit_code = 1
    return exit_code


def _cmd_rescore(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import rescore

    runtime = build_runtime()
    for pattern in _load_patterns(args.pattern):
        result = rescore(runtime, pattern)
        print(
            f"{result.pattern_name}: 採点 {result.scored}件 / MUST通過 {result.must_pass}件 "
            f"(config_hash={result.config_hash[:12]})"
        )
    return 0


def _cmd_sync_dict(args: argparse.Namespace) -> int:
    from house_search.config.settings import load_settings
    from house_search.db.session import create_db_engine
    from house_search.extract.dictionary import load_dictionary, sync_to_db
    from house_search.pipeline.runtime import DICTIONARY_FILENAME

    settings = load_settings()
    url = settings.database_test_url if args.test_db else settings.database_url
    if not url:
        print("DATABASE_TEST_URL が .env に設定されていません", file=sys.stderr)
        return 1

    path = settings.data_dir / DICTIONARY_FILENAME
    if not path.exists():
        print(f"辞書ファイルが見つかりません: {path}", file=sys.stderr)
        return 1

    dictionary = load_dictionary(path)
    result = sync_to_db(create_db_engine(url), dictionary)
    print(f"同期完了: {result.inserted}パターンを投入（{result.deleted}件を入れ替え）")
    print(f"  条件数: {len(dictionary.entries)}")

    if result.has_unknown_refs:
        if result.unknown_condition_codes:
            print(
                f"  [警告] マスタに無い条件コード: {', '.join(result.unknown_condition_codes)}",
                file=sys.stderr,
            )
        if result.unknown_site_codes:
            print(
                f"  [警告] マスタに無いサイトコード: {', '.join(result.unknown_site_codes)}",
                file=sys.stderr,
            )
        return 1
    return 0


def _cmd_re_extract(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import re_extract

    runtime = build_runtime()
    if not runtime.dictionary.entries:
        print(
            "設備抽出辞書が空です。先に `house-search sync-dict` を実行してください。",
            file=sys.stderr,
        )
        return 1
    result = re_extract(runtime, limit=args.limit)
    print(
        f"再抽出: {result.listings}物件 / 設備 {result.features}件 / "
        f"未知表記 {result.unknown_tokens}件"
    )
    return 0


def _cmd_report_unknown(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import report_unknown

    rows = report_unknown(build_runtime(), limit=args.limit)
    if not rows:
        print("辞書未登録の表記はありません。")
        return 0
    print(f"{'出現':>6}  {'サイト':<10} 表記")
    print("-" * 70)
    for token, site_code, count, sample_url in rows:
        print(f"{count:6d}  {site_code:<10} {token}")
        if sample_url:
            print(f"{'':6}  {'':<10}   例: {sample_url}")
    print(
        "\n辞書へ追記する手順: data/feature_dictionary.yaml に追記 → "
        "`house-search sync-dict` → `house-search re-extract`"
    )
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import COVERAGE_COLUMNS, measure_coverage

    rows = measure_coverage(build_runtime())
    if not rows:
        print("計測対象の物件がありません。先に scan を実行してください。")
        return 0

    print(f"{'サイト':<12}{'物件':>7}{'詳細済':>7}{'設備有':>7}{'平均':>7}{'最小':>5}{'最大':>5}")
    print("-" * 52)
    for row in rows:
        print(
            f"{row.site_code:<12}{row.listings:>7}{row.detail_fetched:>7}"
            f"{row.with_features:>7}{row.features_avg:>7.1f}"
            f"{row.features_min:>5}{row.features_max:>5}"
        )

    print()
    print("列の非NULL率（%）")
    header = "".join(f"{c[:9]:>10}" for c in COVERAGE_COLUMNS)
    print(f"{'サイト':<12}{header}")
    print("-" * (12 + 10 * len(COVERAGE_COLUMNS)))
    for row in rows:
        cells = "".join(
            f"{(100 * row.column_filled[c] / row.listings if row.listings else 0):>10.0f}"
            for c in COVERAGE_COLUMNS
        )
        print(f"{row.site_code:<12}{cells}")

    stalled = [r.site_code for r in rows if r.detail_fetched and not r.with_features]
    if stalled:
        # 「アダプタは足したが抽出が動いていない」を検出するための警告
        print()
        print(f"⚠ 詳細取得済みなのに設備が1件も抽出できていないサイト: {', '.join(stalled)}")
    return 0


def _cmd_sync_site_params(args: argparse.Namespace) -> int:
    from house_search.config.settings import load_settings
    from house_search.config.site_params import (
        SITE_PARAMS_FILENAME,
        load_site_params,
        sync_site_params,
    )
    from house_search.db.session import get_engine

    path = load_settings().data_dir / SITE_PARAMS_FILENAME
    if not path.exists():
        print(f"正典が見つかりません: {path}")
        return 1
    table = load_site_params(path)
    applied, deleted = sync_site_params(get_engine(), table)
    print(f"同期しました: {applied}件（YAMLから消えた定義を削除: {deleted}件）")
    for spec in table.specs:
        state = "有効" if spec.is_enabled else "無効"
        print(f"  {spec.site_code}/{spec.property_type}/{spec.axis} -> {spec.param_name} [{state}]")
    return 0


def _cmd_sync_stations(args: argparse.Namespace) -> int:
    from house_search.commute.stations import load_station_rows, sync_stations
    from house_search.config.settings import load_settings
    from house_search.db.session import get_engine

    settings = load_settings()
    loaded = load_station_rows(settings.data_dir)
    engine = get_engine()
    applied, deleted = sync_stations(engine, loaded.rows)
    groups = len({row.station_g_cd for row in loaded.rows})
    print(f"同期しました: {applied}駅 / {groups}駅グループ（CSVから消えた駅を削除: {deleted}件）")
    print(
        f"  対象外: 営業中でない駅 {loaded.skipped_closed}件 / "
        f"営業中の路線に紐づかない駅 {loaded.skipped_no_line}件"
    )
    return 0


def _cmd_resolve_stations(args: argparse.Namespace) -> int:
    from house_search.commute.resolve import (
        listing_prefecture_codes,
        load_station_index,
        resolve_listing_stations,
        unmatched_station_names,
    )
    from house_search.db.session import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        prefectures = listing_prefecture_codes(conn)
        index = load_station_index(conn, prefectures)
        if not index.by_key:
            print("駅マスタが空です。先に sync-stations を実行してください")
            return 1
        stats = resolve_listing_stations(conn, index)
        unmatched = unmatched_station_names(conn, args.limit)

    print(f"照合スコープ: 都道府県コード {list(prefectures)} / {len(index.by_key)}駅名")
    print(f"{'サイト':<12}{'掲載':>7}{'駅あり':>8}{'率':>8}{'同定':>7}{'曖昧':>7}{'不明':>7}")
    for stat in stats.per_site:
        print(
            f"{stat.site_code:<12}{stat.listings:>7}{stat.with_station:>8}"
            f"{stat.rate:>7.1f}%{stat.matched_rows:>7}"
            f"{stat.ambiguous_rows:>7}{stat.unmatched_rows:>7}"
        )
    print(f"\n全体: {stats.with_station}/{stats.listings} = {stats.rate:.1f}%")
    if unmatched:
        print("\n同定できなかった表記（出現回数順）:")
        for name, count in unmatched:
            print(f"  {count:>6}  {name}")
    return 0


def _commute_destination(args: argparse.Namespace) -> tuple[str, str | None]:
    """目的地の駅名と都道府県を決める。明示指定 > 検索パターンの commute。"""
    if args.destination:
        return args.destination, args.destination_prefecture

    patterns = _load_patterns(args.pattern)
    specs = {
        (p.commute.destination_station, p.commute.destination_prefecture)
        for p in patterns
        if p.commute is not None
    }
    if not specs:
        raise ValueError(
            "目的地が決まりません。--destination を指定するか、"
            "検索パターンに commute セクションを書いてください"
        )
    if len(specs) > 1:
        raise ValueError(
            f"検索パターンごとに目的地が違います: {sorted(specs)}。--pattern で1つに絞ってください"
        )
    return specs.pop()


class _CommuteTargetError(ValueError):
    """地方定義を解決できない。"""


def _commute_region(region_name: str | None):
    """``--region`` から地方定義を引く（未指定なら None）。"""
    if not region_name:
        return None
    from house_search.commute.regions import (
        REGIONS_FILENAME,
        RegionConfigError,
        find_region,
        load_regions,
    )
    from house_search.config.settings import load_settings

    try:
        regions = load_regions(load_settings().data_dir / REGIONS_FILENAME)
    except (OSError, RegionConfigError) as error:
        raise _CommuteTargetError(f"地方定義を読めません: {error}") from error
    region = find_region(regions, region_name)
    if region is None:
        names = " / ".join(r.name for r in regions)
        raise _CommuteTargetError(f"地方 '{region_name}' がありません。指定できるのは: {names}")
    return region


def _segment_index_prefectures(conn, region) -> tuple[int, ...]:
    """乗車区間の駅名を引く索引の範囲（都道府県コード）。

    ⚠ **``--region`` のときは掲載の有無で絞ってはいけない。** 経路にはその地方の駅が
    出てくるので、掲載都道府県（実運用では1都3県）で索引を作ると1本も結び付かない
    （実測で沖縄18駅の区間72本すべてを捨てた）。

    ⚠ **かといって全国にはしない。** 同名異駅（三田・大手町・日吉）が一意でなくなり、
    芝公園ゆきの解決率が 94.8% → 66.3% へ落ちる（実測）。一意に決まらない駅名を
    捨てる安全側の挙動は保ったまま、範囲だけを目的に合わせる。
    """
    from house_search.commute.resolve import listing_prefecture_codes

    if region is not None:
        # ⚠ frozenset の反復順は実行ごとに揺れる。並べてから返す。
        return tuple(sorted(region.pref_cds))
    return listing_prefecture_codes(conn)


def _cmd_resolve_commutes(args: argparse.Namespace) -> int:
    from house_search.commute.graph import estimate_from, load_links, station_nodes
    from house_search.commute.resolve import (
        STATUS_NO_ROUTE,
        STATUS_OK,
        commute_distribution,
        commute_summary,
        load_station_nodes,
        prefecture_code_of,
        referenced_station_groups,
        save_commutes,
    )
    from house_search.commute.stations import resolve_station_group
    from house_search.commute.timetable import SOURCE_NAVITIME, origins_with_source
    from house_search.config.settings import load_settings
    from house_search.db.session import get_engine

    station_name, prefecture_name = _commute_destination(args)
    settings = load_settings()
    engine = get_engine()
    with engine.connect() as conn:
        pref_cd = prefecture_code_of(conn, prefecture_name) if prefecture_name else None
        if prefecture_name and pref_cd is None:
            print(f"都道府県 '{prefecture_name}' を m_cities から解決できません")
            return 1
        found = resolve_station_group(conn, station_name, pref_cd)
        if found is None:
            print(
                f"目的地の駅 '{station_name}' を一意に決められません。"
                "--destination-prefecture で都道府県を指定してください"
            )
            return 1
        destination_g_cd, destination_name = found
        nodes = load_station_nodes(conn)
        groups = referenced_station_groups(conn, pattern_name=args.pattern)
        # ⚠ 実ダイヤ（NAVITIME）で埋まっている駅は回帰式で塗り替えない。
        # 素朴に全件書き直すと、時間をかけて採った実測値が見積もりへ戻る。
        measured = origins_with_source(
            conn, destination_g_cd=destination_g_cd, source=SOURCE_NAVITIME
        )

    if not nodes:
        print("駅マスタが空です。先に sync-stations を実行してください")
        return 1
    if not groups:
        print("掲載に紐づく駅がありません。先に resolve-stations を実行してください")
        return 1

    links = load_links(settings.data_dir)
    estimates = estimate_from(station_nodes(nodes), links, destination_g_cd)

    rows = []
    for group_code in groups:
        if group_code in measured:
            continue
        estimate = estimates.get(group_code)
        if estimate is None:
            # 線路がつながっておらず到達できない。欠損と区別して明示的に残す
            rows.append((group_code, STATUS_NO_ROUTE, None, None, None))
        else:
            rows.append(
                (
                    group_code,
                    STATUS_OK,
                    estimate.minutes,
                    estimate.transfers,
                    estimate.distance_km,
                )
            )

    with engine.begin() as conn:
        saved = save_commutes(conn, destination_g_cd=destination_g_cd, rows=rows)
    with engine.connect() as conn:
        summary = commute_summary(conn, destination_g_cd)
        distribution = commute_distribution(conn, destination_g_cd)

    print(f"目的地: {destination_name}（駅グループ {destination_g_cd}）")
    print(f"算出しました: {saved}駅（到達可 {summary.ok} / 到達不可 {summary.no_route}）")
    if measured:
        print(f"  実ダイヤ済みのためそのままにした駅: {len(measured)}駅")
    if distribution:
        lowest, median, upper, highest = distribution
        print(
            f"所要時間の分布: 最短 {lowest}分 / 中央 {median}分 / 75% {upper}分 / 最長 {highest}分"
        )
    return 0


def _cmd_fetch_commutes(args: argparse.Namespace) -> int:  # noqa: PLR0911, PLR0912, PLR0915
    """NAVITIME の乗換案内から実ダイヤの通勤時間を取ってキャッシュへ落とす。

    ⚠ **1駅あたり15秒の間隔を空ける**（robots.txt の Crawl-delay 10 秒に対し、
    ±30%のジッタが下振れしても割らない値）。1,000駅なら4時間強かかるので、
    **エージェントのバックグラウンドからではなく ``Start-Process`` で切り離して**
    起動すること。1駅ごとにコミットするので途中で止めても続きから再開できる。
    """
    import datetime as dt

    from house_search.commute.navitime import (
        MIN_INTERVAL_SEC,
        build_search_url,
        parse_search,
        resolved_station_matches,
    )
    from house_search.commute.resolve import (
        STATUS_NO_ROUTE,
        STATUS_OK,
        prefecture_code_of,
        referenced_station_groups,
        save_commutes,
    )
    from house_search.commute.stations import (
        resolve_station_group,
        station_groups_in_prefectures,
    )
    from house_search.commute.timetable import (
        SOURCE_NAVITIME,
        build_station_resolver,
        fetched_origins,
        harvest_segments,
        merge_observations,
        origin_stations,
        save_routes,
        save_segments,
        segment_stats,
    )
    from house_search.config.settings import load_settings
    from house_search.db.session import get_engine
    from house_search.scrape.fetch import (
        BROWSER_USER_AGENT,
        RateLimit,
        SiteAborted,
        SiteFetcher,
        build_client,
    )

    try:
        depart_on = dt.date.fromisoformat(args.depart_on)
        depart_at = dt.time.fromisoformat(args.depart_at)
    except ValueError as error:
        print(f"日付・時刻の書式が不正です: {error}")
        return 1
    if depart_on < dt.date.today():
        print(
            f"出発日 {depart_on} は過去です。NAVITIME は過去日のダイヤを返さないので "
            "--depart-on に将来の平日を指定してください"
        )
        return 1

    settings = load_settings()
    engine = get_engine()

    # ⚠ 全国を網羅するときは目的地を1つに固定できない（北海道の駅から芝公園までの
    # 所要時間には使い道がない）。--region は地方ごとの中心駅を目的地にし、
    # 対象も「掲載がある駅」ではなく**その地方の全駅**へ広げる。
    try:
        region = _commute_region(args.region)
    except _CommuteTargetError as error:
        print(error)
        return 1
    if region is not None:
        station_name, prefecture_name = region.station, region.prefecture
    else:
        station_name, prefecture_name = _commute_destination(args)

    with engine.connect() as conn:
        pref_cd = prefecture_code_of(conn, prefecture_name) if prefecture_name else None
        if prefecture_name and pref_cd is None:
            print(f"都道府県 '{prefecture_name}' を m_cities から解決できません")
            return 1
        found = resolve_station_group(conn, station_name, pref_cd)
        if found is None:
            print(
                f"目的地の駅 '{station_name}' を一意に決められません。"
                "--destination-prefecture で都道府県を指定してください"
            )
            return 1
        destination_g_cd, destination_name = found

        if args.station:
            groups = []
            for name in args.station:
                hit = resolve_station_group(conn, name, None)
                if hit is None:
                    print(f"駅 '{name}' を一意に決められません")
                    return 1
                groups.append(hit[0])
        elif region is not None:
            groups = list(station_groups_in_prefectures(conn, region.pref_cds))
        else:
            groups = list(referenced_station_groups(conn, pattern_name=args.pattern))
        targets = origin_stations(conn, groups)
        done = (
            frozenset()
            if args.refetch
            else fetched_origins(
                conn,
                destination_g_cd=destination_g_cd,
                depart_on=depart_on,
                depart_at=depart_at,
            )
        )
        resolver = build_station_resolver(conn, _segment_index_prefectures(conn, region))

    pending = [
        t for t in targets if t.station_g_cd not in done and t.station_g_cd != destination_g_cd
    ]
    if args.limit is not None:
        pending = pending[: args.limit]
    if not pending:
        print(f"取得対象がありません（取得済み {len(done)}駅）")
        return 0

    destination_query = _navitime_destination_query(destination_name, prefecture_name)
    print(f"目的地: {destination_name}（駅グループ {destination_g_cd}）")
    print(
        f"{len(pending)}駅を取得します（取得済み {len(done)}駅をスキップ）。"
        f"1駅あたり約{MIN_INTERVAL_SEC:.0f}秒・"
        f"見込み {len(pending) * MIN_INTERVAL_SEC / 3600:.1f}時間"
    )

    # ⚠ NAVITIME は自己申告のUAを 403 で拒否する（実測）。robots.txt は /transfer を
    # User-agent: * に許可しており、UAの選別だけが別の関門になっている。
    # LIFULL HOME'S と同じ扱いでブラウザ相当UAを使い、間隔と robots.txt の尊重は変えない。
    client = build_client(user_agent=BROWSER_USER_AGENT, timeout_sec=settings.request_timeout_sec)
    fetcher = SiteFetcher(
        site_code="NAVITIME",
        client=client,
        rate_limit=RateLimit(min_interval_sec=MIN_INTERVAL_SEC, max_pages_per_run=10**6),
    )
    ok = failed = no_route = segments_saved = dropped_total = 0
    mismatched: list[str] = []
    try:
        for target in pending:
            # ⚠ 意図した駅として解決されたかを必ず確かめる。NAVITIME は同名異駅も
            # 名前の近い別駅も、黙って処理して HTTP 200 で普通の結果を返す。
            # 検索語は「駅名 → 駅名（都道府県）」の順に試す（→ query_candidates）。
            search = None
            last_label: str | None = None
            failure: str | None = None
            for candidate in target.query_candidates:
                url = build_search_url(
                    origin=candidate,
                    destination=destination_query,
                    depart_on=depart_on,
                    depart_at=depart_at,
                )
                try:
                    response = fetcher.get(url)
                    found = parse_search(response.text, expected_date=depart_on)
                except SiteAborted:
                    # 連続失敗による打ち切り。1駅の失敗と違い、続けても無駄なので抜ける。
                    raise
                except Exception as error:  # noqa: BLE001
                    # 1駅の失敗で数時間の実行を落とさない。件数として必ず報告する。
                    failure = f"{type(error).__name__}: {error}"
                    continue
                if resolved_station_matches(found.origin_label, target.match_names):
                    search = found
                    break
                last_label = found.origin_label

            if search is None:
                failed += 1
                if last_label is not None:
                    mismatched.append(f"{target.station_name} → {last_label}")
                else:
                    print(f"  × {target.station_name}: {failure}")
                continue

            fastest = search.fastest
            fetched_at = dt.datetime.now(dt.UTC)
            observations = []
            for route in search.routes:
                found_segments, dropped = harvest_segments(route, resolver)
                observations.extend(found_segments)
                dropped_total += dropped

            with engine.begin() as conn:
                save_routes(
                    conn,
                    origin_g_cd=target.station_g_cd,
                    destination_g_cd=destination_g_cd,
                    depart_on=depart_on,
                    depart_at=depart_at,
                    search=search,
                    fetched_at=fetched_at,
                )
                segments_saved += save_segments(
                    conn, merge_observations(observations), observed_at=fetched_at
                )
                if fastest is None:
                    no_route += 1
                    rows = [(target.station_g_cd, STATUS_NO_ROUTE, None, None, None)]
                else:
                    ok += 1
                    rows = [
                        (
                            target.station_g_cd,
                            STATUS_OK,
                            fastest.total_minutes,
                            fastest.transfers,
                            fastest.distance_km,
                        )
                    ]
                save_commutes(
                    conn,
                    destination_g_cd=destination_g_cd,
                    rows=rows,
                    source=SOURCE_NAVITIME,
                )
    except KeyboardInterrupt:
        print("\n中断しました。取得済みぶんは保存されています（再実行で続きから）")
    except SiteAborted as error:
        # 連続失敗による打ち切り。続けても無駄なので抜けるが、
        # 例外を素通しにせず「何駅まで進んだか」を必ず出す。
        print(f"\n⚠ 連続失敗で打ち切りました: {error}")
        print("  取得済みぶんは保存されています（原因を直してから再実行で続きから）")
    finally:
        client.close()

    with engine.connect() as conn:
        total, rides, walks = segment_stats(conn)

    print(f"取得: {ok}駅（経路なし {no_route} / 失敗 {failed}）")
    print(f"乗車区間: 累計 {total}本（列車 {rides} / 徒歩 {walks}）。今回 {segments_saved}本を反映")
    if dropped_total:
        print(f"  駅名を駅マスタと結び付けられず捨てた区間: {dropped_total}本")
    if mismatched:
        print("⚠ 意図と違う駅として解決されたため保存しませんでした:")
        for line in mismatched[:10]:
            print(f"  {line}")
    return 0 if failed == 0 else 1


def _cmd_re_segment(args: argparse.Namespace) -> int:
    """保存済みの経路原文から乗車区間を作り直す（ネットワーク不要）。

    設備の ``re-extract`` と同じ位置づけ。駅名の照合を直したときに、
    1駅15秒の取得をやり直さずに ``t_rail_segments`` へ反映する。
    """
    import datetime as dt

    from house_search.commute.resolve import prefecture_code_of
    from house_search.commute.stations import resolve_station_group
    from house_search.commute.timetable import (
        build_station_resolver,
        rebuild_segments,
        segment_stats,
    )
    from house_search.db.session import get_engine

    try:
        region = _commute_region(args.region)
    except _CommuteTargetError as error:
        print(error)
        return 1
    if region is not None:
        station_name, prefecture_name = region.station, region.prefecture
    else:
        station_name, prefecture_name = _commute_destination(args)

    engine = get_engine()
    with engine.begin() as conn:
        pref_cd = prefecture_code_of(conn, prefecture_name) if prefecture_name else None
        if prefecture_name and pref_cd is None:
            print(f"都道府県 '{prefecture_name}' を m_cities から解決できません")
            return 1
        found = resolve_station_group(conn, station_name, pref_cd)
        if found is None:
            print(
                f"目的地の駅 '{station_name}' を一意に決められません。"
                "--destination-prefecture で都道府県を指定してください"
            )
            return 1
        destination_g_cd, destination_name = found
        prefectures = _segment_index_prefectures(conn, region)
        result = rebuild_segments(
            conn,
            destination_g_cd=destination_g_cd,
            resolve=build_station_resolver(conn, prefectures),
            observed_at=dt.datetime.now(dt.UTC),
        )
    with engine.connect() as conn:
        total, rides, walks = segment_stats(conn)

    print(f"目的地: {destination_name}（駅グループ {destination_g_cd}）")
    print(f"駅の索引: {len(prefectures)}都道府県")
    print(f"経路の原文 {result.routes}件から作り直しました")
    print(f"乗車区間: 累計 {total}本（列車 {rides} / 徒歩 {walks}）。今回 {result.saved}本を反映")
    if result.dropped:
        print(f"  駅名を駅マスタと結び付けられず捨てた区間: {result.dropped}本")
    if result.failed:
        print(f"  ⚠ 再解析できなかった経路: {result.failed}件")
    return 0


def _navitime_destination_query(station_name: str, prefecture: str | None) -> str:
    """目的地の検索語（都道府県つき）。"""
    from house_search.commute.navitime import station_query_name

    return station_query_name(station_name, prefecture)


def _percentile(values: list[int], ratio: float) -> int:
    """整列済みの列から百分位を取る（線形補間はしない）。"""
    if not values:
        return 0
    index = min(int(len(values) * ratio), len(values) - 1)
    return values[index]


def _cmd_commute_stats(args: argparse.Namespace) -> int:
    from house_search.commute.resolve import resolve_destination_group
    from house_search.db.session import get_engine
    from house_search.pipeline import persist
    from house_search.scoring.must import evaluate_must

    engine = get_engine()
    for pattern in _load_patterns(args.pattern):
        print(f"=== {pattern.name} ===")
        if pattern.commute is None:
            print("  commute セクションがありません")
            continue
        with engine.connect() as conn:
            destination = resolve_destination_group(conn, pattern.commute)
            if destination is None:
                print(f"  目的地 '{pattern.commute.destination_station}' を解決できません")
                continue
            views = persist.load_listing_views(
                conn,
                property_type_code=pattern.property_type,
                site_codes=list(pattern.sites),
                city_names=list(pattern.search.cities) or None,
                commute_destination_g_cd=destination,
            )

        # MUST を通る掲載だけを母集団にする。落ちる掲載の分布を混ぜると
        # best/worst が実際の候補群からずれる（課題#31 と同じ失敗になる）
        passing = [
            view
            for view in views.values()
            if evaluate_must(view, pattern.must).passes(pattern.must.unknown_policy)
        ]
        known = sorted(v.commute_minutes for v in passing if v.commute_minutes is not None)
        unknown = len(passing) - len(known)
        print(f"  目的地: {pattern.commute.destination_station}")
        print(
            f"  母集団: MUST通過 {len(passing)}件（うち通勤時間あり {len(known)} / 不明 {unknown}）"
        )
        if not known:
            continue
        marks = [("最短", 0.0), ("25%", 0.25), ("中央", 0.5), ("75%", 0.75), ("90%", 0.9)]
        cells = "  ".join(f"{label} {_percentile(known, r)}分" for label, r in marks)
        print(f"  分布: {cells}  最長 {known[-1]}分")
        print("  上限候補ごとの通過件数:")
        for limit in (30, 40, 45, 50, 60, 75, 90):
            passed = sum(1 for m in known if m <= limit)
            share = passed / len(known) * 100
            print(f"    {limit:>3}分以内: {passed:>5}件（{share:>5.1f}%）")
        print("  worst 候補ごとの0点張り付き率（best=0固定）:")
        for worst in (45, 60, 75, 90, 120):
            zero = sum(1 for m in known if m >= worst)
            print(f"    worst={worst:>3}分: {zero / len(known) * 100:>5.1f}%")
        print()
    return 0


def _cmd_resolve_cities(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import resolve_cities

    patterns = _load_patterns(None)
    result = resolve_cities(build_runtime(), patterns)
    print(f"住所を持つ掲載: {result.total}件")
    print(f"市区町村ID あり: {result.resolved_before}件 -> {result.resolved_after}件")
    print(f"引き直した掲載: {result.changed}件")
    if result.resolved_after < result.resolved_before:
        # 解決済みを NULL で上書きしない作りなので、本来ここは通らない
        print("⚠ 解決率が下がりました。市区町村マスタの入れ替えを確認してください")
    print()
    print("スコアへ反映するには `house-search rescore` を実行してください。")
    return 0


def _cmd_regroup(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import regroup

    result = regroup(build_runtime())
    print(f"名寄せキーを更新した物件: {result.keys_refreshed}件")
    print(f"グループ: {result.groups}件 / グループ化された掲載: {result.grouped_listings}件")
    print(f"代表が入れ替わったグループ: {result.representative_changes}件")
    if result.cheaper_candidates:
        # regroup では通知しない。既存データへの初回適用で大量発火するため
        print(
            f"うち他サイトのほうが安いもの: {result.cheaper_candidates}件"
            "（regroup では通知しません。次回の scan の差分で通知されます）"
        )
    print()
    print("スコアへ反映するには `house-search rescore` を実行してください。")
    return 0


def _cmd_dedup_stats(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import measure_dedup

    rows = measure_dedup(build_runtime())
    if not rows:
        print("計測対象の物件がありません。先に scan を実行してください。")
        return 0

    print(
        f"{'サイト':<12}{'掲載':>6}{'キー有':>7}{'ｷｰ率%':>7}"
        f"{'代表':>6}{'他ｻｲﾄ重複':>10}{'ユニーク%':>10}"
    )
    print("-" * 60)
    for row in rows:
        print(
            f"{row.site_code:<12}{row.listings:>6}{row.with_key:>7}{100 * row.key_rate:>7.0f}"
            f"{row.representative:>6}{row.shared_with_other_sites:>10}"
            f"{100 * row.unique_rate:>10.0f}"
        )

    print()
    print("住所の粒度（名寄せキーは丁目までで打ち切る）")
    print("-" * 60)
    for row in rows:
        detail = " / ".join(f"{label} {count}" for label, count in row.granularity.items())
        print(f"{row.site_code:<12}{detail}")

    total = sum(row.listings for row in rows)
    shared = sum(row.shared_with_other_sites for row in rows)
    print()
    print(f"全体: 掲載 {total}件 / 他サイトにも同一住戸がある掲載 {shared}件")
    return 0


_COMMANDS = {
    "db-seed": _cmd_db_seed,
    "validate-config": _cmd_validate_config,
    "scan": _cmd_scan,
    "check-sold": _cmd_check_sold,
    "digest": _cmd_digest,
    "rescore": _cmd_rescore,
    "sync-dict": _cmd_sync_dict,
    "re-extract": _cmd_re_extract,
    "report-unknown": _cmd_report_unknown,
    "coverage": _cmd_coverage,
    "regroup": _cmd_regroup,
    "dedup-stats": _cmd_dedup_stats,
    "resolve-cities": _cmd_resolve_cities,
    "sync-site-params": _cmd_sync_site_params,
    "sync-stations": _cmd_sync_stations,
    "resolve-stations": _cmd_resolve_stations,
    "resolve-commutes": _cmd_resolve_commutes,
    "fetch-commutes": _cmd_fetch_commutes,
    "re-segment": _cmd_re_segment,
    "commute-stats": _cmd_commute_stats,
}


def _force_utf8_output() -> None:
    """標準出力・標準エラーを UTF-8 にする。

    ⚠ 日本語Windowsの既定は cp932 で、``⚠`` のような文字を print した時点で
    ``UnicodeEncodeError`` になる。**例外は print の途中で飛ぶため、その1行だけでなく
    後続の出力ごと失われる**（実際 fetch-commutes の「意図と違う駅」28件の一覧が
    報告ごと消えた）。scripts/*.ps1 は stdout をファイルへ向けるので
    コンソール判定にも頼れない。コマンドの入口で明示的に付け替える。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # テストの差し替え先は持たないことがある
            reconfigure(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """エントリポイント。終了コードを返す。"""
    _force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = _COMMANDS.get(args.command)
    if handler is not None:
        try:
            return handler(args)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    planned = PLANNED.get(args.command)
    print(
        f"'{args.command}' は未実装です（実装予定: {planned}）。"
        "docs/再設計計画.md の Phase 構成を参照してください。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
