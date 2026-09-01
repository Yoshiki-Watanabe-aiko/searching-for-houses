# CLAUDE.md

## プロジェクト概要
指定した条件に合致する物件を日本の不動産サイトから自動取得し、
MUST（未充足なら除外）＋WANT（重み付き加点）のスコアでランク付けして
新着・成約・価格変動・日次ランキングをDiscordへ通知するシステム。

**v2（Python）へ全面再設計中。Phase 1（SUUMO賃貸の縦切り）まで完了。**
進捗と残作業は `docs/再設計計画.md` を参照。
v1（Go）の実装は `legacy-go` ブランチ / `v1-go-final` タグに保全済み。

## 技術スタック
- Python 3.12+（パッケージ管理は uv。Windowsタスクスケジューラーで定期実行、常駐プロセスではない）
- スクレイピング: httpx + lxml + cssselect / Playwright（ATHOME・EHEYA・NIFTY・APAMAN・SMOCCA の5サイト）
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

# Phase 1 で実働するもの
uv run house-search sync-dict              # 辞書YAML → DB（scan の前に必要）
uv run house-search scan --seed            # 通知なしの記録専用モード
uv run house-search scan --site SUUMO
uv run house-search digest --dry-run       # 送信せず件数確認
uv run house-search rescore                # 再採点（ネットワーク不要）
uv run house-search re-extract             # 設備の再抽出（ネットワーク不要）
uv run house-search report-unknown         # 辞書未登録の表記
```

## 参照ファイル
- 再設計計画（Phase構成・未確定事項） → @docs/再設計計画.md
- 要件定義書          → @docs/requirements.md
- 詳細設計書          → @docs/詳細設計書/
  （`01_サイト取得設計.md` / `02_設備抽出辞書設計.md` / `03_スコアリング設計.md`）
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
- **検索パターンYAMLは `configs/` 直下だけが読まれる**（`glob("*.yaml")` は非再帰）。
  雛形を直下に置くと実パターンとして `scan` が走り、同じWebhookへ二重通知される
- 設備の辞書照合は**本文全体への部分一致**。トークンに切ってから照合すると
  「バス・トイレ別」のように語中に中黒を含む条件を取りこぼす
- SUUMO の管理費・敷金・礼金欄の「-」は**0円**の意味。`None` にすると
  `rent_total` が「管理費不明」になり MUST 判定が `unknown` に落ちる
- `scan` の前に `sync-dict` が要る（辞書が空だとエラー終了する）

## AI回答方針
- 複数実装がある場合はトレードオフを説明してから推奨案を提示する
- より良い設計があれば指示に縛られず積極的に提案する
- セキュリティ上の懸念点は必ず指摘する
