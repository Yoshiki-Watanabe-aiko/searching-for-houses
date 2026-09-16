---
paths:
  - "alembic.ini"
  - "migrations/**"
  - "db/**"
  - "src/house_search/db/**"
  - "tests/test_schema_conventions.py"
---

# DB・マイグレーション・シードの注意

CLAUDE.md の「実装上の注意」から領域別に分けたもの（→ CLAUDE.md の表）。
実際に踏んだ落とし穴なので、該当する処理を書く・直す前に読む。

- `alembic.ini` は **ASCIIのみ**にする。日本語コメントを1行でも書くと日本語Windows（cp932）で
  `alembic upgrade` そのものが `UnicodeDecodeError` で落ちる。設定の意図は `migrations/env.py` に書く
- `db/seed/*.sql` には「建ぺい率（%）」のように `%` を含む日本語が入る。psycopg3 は
  パラメータを渡すと `%` をプレースホルダとして解釈するため、シードSQLはパラメータ無しで
  DBAPIカーソルへ直接流す（`src/house_search/db/seed.py`）
- 列を追加するときは監査カラム（`created_at`/`updated_at`）を最終列に保つため
  テーブル再作成が要る。`tests/test_schema_conventions.py` が列順を回帰テストしている
