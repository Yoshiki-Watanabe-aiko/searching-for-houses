"""コマンドラインインタフェース。

Phase 0 では ``db-seed`` と ``validate-config`` だけが実働する。
残りのサブコマンドは骨格のみで、呼ばれたら「どの Phase で実装するか」を告げて
終了コード 2 で終わる。黙って何もしないと「実装済みだが未配線」を見逃すため、
未実装であることを必ず表に出す。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from house_search import __version__

# 未実装サブコマンドと、実装予定の Phase。
PLANNED: dict[str, str] = {
    "scan": "Phase 1（SUUMO賃貸で取得→MUST判定→詳細→抽出→スコア→通知まで貫通）",
    "check-sold": "Phase 1",
    "digest": "Phase 1（日次ランキングダイジェスト）",
    "rescore": "Phase 1（DB内の物件属性から再採点。ネットワーク不要）",
    "sync-dict": "Phase 1（data/feature_dictionary.yaml → m_condition_synonyms）",
    "re-extract": "Phase 1（raw_features_text から全件再抽出）",
    "report-unknown": "Phase 1（t_unknown_tokens を出現回数順に一覧）",
    "coverage": "Phase 2（サイト別の設備抽出数分布・数値カラム非NULL率の実測）",
}


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
        "--test-db",
        action="store_true",
        help="DATABASE_TEST_URL のテストDBへ投入する",
    )

    p_validate = sub.add_parser(
        "validate-config", help="検索パターンYAMLと webhook_ref の参照を検証する"
    )
    p_validate.add_argument(
        "--configs-dir", help="検証対象ディレクトリ（省略時は .env の CONFIGS_DIR）"
    )
    p_validate.add_argument(
        "--skip-webhook",
        action="store_true",
        help="webhook_ref に対応する環境変数の存在確認を省く",
    )

    p_scan = sub.add_parser("scan", help="一覧取得〜通知（Phase 1 で実装）")
    p_scan.add_argument("--pattern", help="対象の検索パターン名（省略時は全件）")
    p_scan.add_argument("--site", help="対象サイトコード（省略時は全サイト）")
    p_scan.add_argument(
        "--seed",
        action="store_true",
        help=(
            "シードモード。通知を送らず記録だけ行う。"
            "初回全件取得・パターン新規追加・長期停止からの再開で使う"
        ),
    )
    p_scan.add_argument(
        "--full", action="store_true", help="全量スキャン（max_pages_per_run まで辿る）"
    )

    for name in ("check-sold", "digest", "rescore", "sync-dict", "re-extract"):
        sub.add_parser(name, help=f"{PLANNED[name]} で実装")
    sub.add_parser("report-unknown", help=f"{PLANNED['report-unknown']} で実装")
    sub.add_parser("coverage", help=f"{PLANNED['coverage']} で実装")

    return parser


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
    from pathlib import Path

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


def main(argv: Sequence[str] | None = None) -> int:
    """エントリポイント。終了コードを返す。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "db-seed":
        return _cmd_db_seed(args)
    if args.command == "validate-config":
        return _cmd_validate_config(args)

    planned = PLANNED.get(args.command)
    print(
        f"'{args.command}' は未実装です（実装予定: {planned}）。"
        "docs/再設計計画.md の Phase 構成を参照してください。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
