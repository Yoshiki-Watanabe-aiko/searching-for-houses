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
    p_digest.add_argument(
        "--dry-run", action="store_true", help="送信せず件数だけ確認する"
    )

    p_rescore = sub.add_parser("rescore", help="DB内の物件属性から再採点（ネットワーク不要）")
    p_rescore.add_argument("--pattern", help="対象の検索パターン名")

    p_sync = sub.add_parser("sync-dict", help="data/feature_dictionary.yaml → m_condition_synonyms")
    p_sync.add_argument("--test-db", action="store_true", help="テストDBへ同期する")

    p_reextract = sub.add_parser("re-extract", help="raw_features_text から全件再抽出")
    p_reextract.add_argument("--limit", type=int, help="処理件数の上限（動作確認用）")

    p_unknown = sub.add_parser("report-unknown", help="辞書未登録の表記を出現回数順に一覧")
    p_unknown.add_argument("--limit", type=int, default=50, help="表示件数（既定50）")

    sub.add_parser(
        "coverage", help="サイト別の設備抽出数分布・数値カラム非NULL率を実測する"
    )

    sub.add_parser(
        "regroup",
        help="名寄せキーを全件作り直してグループを同期する（ネットワーク不要・通知なし）",
    )
    sub.add_parser("dedup-stats", help="サイト別の名寄せ実測（クロスサイト重複率・ユニーク率）")
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

    return 1 if failures else 0


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
                f"MUST1段目通過 {site.listings_kept:4d} → 新規 {site.properties_new:4d} / "
                f"詳細 {site.details_fetched:3d}件 / 設備 {site.features_extracted:4d}件"
            )
        if summary.skipped_sites:
            print(f"  スキップ（アダプタ未実装）: {', '.join(summary.skipped_sites)}")
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
                "  [警告] マスタに無い条件コード: "
                f"{', '.join(result.unknown_condition_codes)}",
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
        f"再抽出: {result.properties}物件 / 設備 {result.features}件 / "
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
            f"{row.site_code:<12}{row.properties:>7}{row.detail_fetched:>7}"
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
            f"{(100 * row.column_filled[c] / row.properties if row.properties else 0):>10.0f}"
            for c in COVERAGE_COLUMNS
        )
        print(f"{row.site_code:<12}{cells}")

    stalled = [r.site_code for r in rows if r.detail_fetched and not r.with_features]
    if stalled:
        # 「アダプタは足したが抽出が動いていない」を検出するための警告
        print()
        print(f"⚠ 詳細取得済みなのに設備が1件も抽出できていないサイト: {', '.join(stalled)}")
    return 0


def _cmd_regroup(args: argparse.Namespace) -> int:
    from house_search.pipeline.runtime import build_runtime
    from house_search.pipeline.tasks import regroup

    result = regroup(build_runtime())
    print(f"名寄せキーを更新した物件: {result.keys_refreshed}件")
    print(f"グループ: {result.groups}件 / グループ化された掲載: {result.grouped_properties}件")
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
            f"{row.site_code:<12}{row.properties:>6}{row.with_key:>7}{100 * row.key_rate:>7.0f}"
            f"{row.representative:>6}{row.shared_with_other_sites:>10}"
            f"{100 * row.unique_rate:>10.0f}"
        )

    print()
    print("住所の粒度（名寄せキーは丁目までで打ち切る）")
    print("-" * 60)
    for row in rows:
        detail = " / ".join(f"{label} {count}" for label, count in row.granularity.items())
        print(f"{row.site_code:<12}{detail}")

    total = sum(row.properties for row in rows)
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
}


def main(argv: Sequence[str] | None = None) -> int:
    """エントリポイント。終了コードを返す。"""
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
