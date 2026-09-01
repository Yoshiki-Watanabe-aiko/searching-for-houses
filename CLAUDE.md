# CLAUDE.md

## プロジェクト概要
指定した条件に合致する物件を日本の不動産サイトから自動取得し、
MUST（未充足なら除外）＋WANT（重み付き加点）のスコアでランク付けして
新着・成約・価格変動・日次ランキングをDiscordへ通知するシステム。

**v2（Python）へ全面再設計中。** 進捗と残作業は `docs/再設計計画.md` を参照。
v1（Go）の実装は `legacy-go` ブランチ / `v1-go-final` タグに保全済み。

## 技術スタック
- Python 3.12+（パッケージ管理は uv。Windowsタスクスケジューラーで定期実行、常駐プロセスではない）
- スクレイピング: httpx + lxml / Playwright（ATHOME・EHEYA・NIFTY・APAMAN・SMOCCA の5サイト）
- DB: PostgreSQL 18（SQLAlchemy 2.x + psycopg3 / Alembic）
- 設定・スキーマ検証: pydantic / pydantic-settings
- テスト: pytest（`DATABASE_TEST_URL` 未設定時はDB統合テストをスキップ）
- バージョン管理: Git

## よく使うコマンド

```powershell
uv sync
uv run pytest
uv run ruff check src/ tests/
uv run alembic upgrade head
uv run alembic -x test=true upgrade head
uv run house-search db-seed
uv run house-search validate-config
```

## 参照ファイル
- 再設計計画（Phase構成・未確定事項） → @docs/再設計計画.md
- 要件定義書          → @docs/requirements.md
- 詳細設計書          → @docs/詳細設計書/
- 課題管理表          → @docs/課題管理表.md
- 設計判断の記録      → @docs/adr/

## 実装上の注意（実際に踏んだもの）
- `alembic.ini` は **ASCIIのみ**にする。日本語コメントを1行でも書くと日本語Windows（cp932）で
  `alembic upgrade` そのものが `UnicodeDecodeError` で落ちる。設定の意図は `migrations/env.py` に書く
- `db/seed/*.sql` には「建ぺい率（%）」のように `%` を含む日本語が入る。psycopg3 は
  パラメータを渡すと `%` をプレースホルダとして解釈するため、シードSQLはパラメータ無しで
  DBAPIカーソルへ直接流す（`src/house_search/db/seed.py`）
- 列を追加するときは監査カラム（`created_at`/`updated_at`）を最終列に保つため
  テーブル再作成が要る。`tests/test_schema_conventions.py` が列順を回帰テストしている

## AI回答方針
- 複数実装がある場合はトレードオフを説明してから推奨案を提示する
- より良い設計があれば指示に縛られず積極的に提案する
- セキュリティ上の懸念点は必ず指摘する
