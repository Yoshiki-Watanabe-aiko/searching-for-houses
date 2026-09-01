# 実装言語を Go から Python へ移行する

**状態: 採用（2026-09-01・Phase 0 で実施）**

v2 の実装言語を Python（3.12+）とし、v1 の Go 実装は `legacy-go` ブランチ /
`v1-go-final` タグへ保全したうえでリポジトリ本体から削除する。
技術構成は Playwright / httpx＋lxml / SQLAlchemy 2.x＋psycopg / Alembic / pydantic / uv。

## 背景・検討した選択肢

v2 の中心はランキング（MUST＋WANTのスコアリング）と設備情報のローカル抽出であり、
v1 が持っていた「サイトのURLパラメータに条件を載せる」ロジックは全面的に不要になる
（→ [ADR 0003](./0003-local-filtering.md)）。**再利用できるのは各サイトのDOM選択子程度**で、
言語の継続によって節約できるコストは小さい。

一方で v2 が新たに必要とするもの——スキーマ検証（pydantic）・辞書ベースのテキスト正規化と
マッチング・DBマイグレーション（Alembic）——は Python のほうが道具立てが厚い。
本人の他プロジェクト（stock-trading-system, cash_management, camera-process-automation 等）も
Python＋PostgreSQL＋Alembic で揃っており、規約・運用ノウハウをそのまま流用できる。

Go を継続する案も検討したが、上記の再利用可能性の低さと、
ブラウザ自動化を go-rod（Playwright 相当の自前実装）に依存し続けることの保守性を踏まえて見送った。

## Consequences

- **v1 の実装は残らない。** 復元が必要なら `git checkout v1-go-final`
  （リモートにも push 済み）。旧DBは `F:\backups\searching-for-houses-legacy\db_20260901.dump`
- Playwright はブラウザの別途インストール（`playwright install chromium`）が要る。
  タスクスケジューラ登録時は `.venv\Scripts\python.exe` をフルパス指定する
- `alembic.ini` は ASCII のみに保つ。日本語コメントを1行でも書くと日本語Windows（cp932）で
  `alembic upgrade` そのものが `UnicodeDecodeError` で落ちる。設定の意図は `migrations/env.py` に書く
- 並行運用期間は設けない。v1 は 2026-06-19 を最後に停止しており（`last_seen_at` の最大値、
  タスクスケジューラ登録もなし）、代替すべき稼働中システムが存在しないため
