# CLAUDE.md

## プロジェクト概要
指定した条件に合致する物件を日本の不動産サイトから自動取得し、
MUST（未充足なら除外）＋WANT（重み付き加点）のスコアでランク付けして
新着・成約・価格変動・日次ランキングをDiscordへ通知するシステム。

**v2（Python）へ全面再設計中。Phase 5（賃貸の本運用）は実装完了・実行待ち。**
進捗と残作業は `docs/再設計計画.md` を参照。
v1（Go）の実装は `legacy-go` ブランチ / `v1-go-final` タグに保全済み。

## 技術スタック
- Python 3.12+（パッケージ管理は uv。Windowsタスクスケジューラーで定期実行、常駐プロセスではない）
- スクレイピング: httpx + lxml + cssselect（**全10サイトHTTP取得**。Playwrightは Phase 3 で撤去 → ADR 0010）
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

# 実働するもの（Phase 2 で全コマンド実装済み）
uv run house-search sync-dict              # 辞書YAML → DB（scan の前に必要）
uv run house-search scan --seed            # 通知なしの記録専用モード
uv run house-search scan --site SUUMO
uv run house-search digest --dry-run       # 送信せず件数確認
uv run house-search rescore                # 再採点（ネットワーク不要）
uv run house-search re-extract             # 設備の再抽出（ネットワーク不要）
uv run house-search report-unknown         # 辞書未登録の表記
uv run house-search coverage               # サイト別の抽出充足率（ネットワーク不要）
uv run house-search regroup                # 名寄せの再構築（ネットワーク不要・通知なし）
uv run house-search dedup-stats            # サイト別の重複率・ユニーク率（ネットワーク不要）
uv run house-search scan --seed --site CHINTAI_EX   # 無効化サイトの観測モード
uv run house-search scan --detail-limit 800         # 詳細取得の上限を上書き（既定40 / --full時400）
```

運用スクリプト（PowerShell 5.1。1行ずつ実行する。`&&` は使えない）:

```powershell
.\scripts\run_initial_scan.ps1                # 初回全件スキャン（切り離して起動・約6.5〜9時間）
.\scripts\run_initial_scan.ps1 -Drain         # 2晩目以降の詳細キュー掃き出し
.\scripts\backup_db.ps1                       # pg_dump（14世代保持）
.\scripts\register_tasks.ps1 -DryRun          # タスクXMLの生成と検証（権限不要）
.\scripts\register_tasks.ps1                  # タスク登録（★管理者権限が要る → 課題#23）
.\scripts\register_tasks.ps1 -EnableScraping  # 取得タスクを有効化（初回スキャン完了後）
```

## 参照ファイル
- 再設計計画（Phase構成・未確定事項） → @docs/再設計計画.md
- 要件定義書          → @docs/requirements.md
- 詳細設計書          → @docs/詳細設計書/
  （`01_サイト取得設計.md` / `02_設備抽出辞書設計.md` / `03_スコアリング設計.md` / `04_名寄せ設計.md`）
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
- **市区の検索値が JIS5桁のサイトは `m_cities.jis_code` から導く。**
  `m_city_site_values` に縛ると対象4都県で 67/253市区しか指定できない（東京は23区のみ）
- **詳細ページに「非該当」条件を並べるサイトがある**（HOMES の `sr-only`、goo の `td` が `-`）。
  そのまま `raw_features_text` に載せると辞書が非該当の条件を拾う
- **`m_sites.is_active = false` のサイトは通常の `scan` では取りに行かない。**
  `--site` で名指ししたときだけ動く（賃貸EX の観測モードの入口）
- **能動的なボット検知は突破しない。** MINIMINI は reCAPTCHA（課題#18・**素のブラウザでも通らない**）、
  HOME'S は AWS WAF（課題#17）、ATHOME はパズル認証（課題#20）で取得できないことがある。
  ⚠ **検知ページは HTTP 200 で返る。** そのまま解析すると掲載0件になるだけでエラーにならず
  「取れているつもり」で気づけないので、判別できるサイトはアダプタが例外にする
- **robots.txt を無視するのは APAMAN だけ**（`ignore_robots=True`・ユーザー判断 → ADR 0011）。
  他のサイトでこのフラグを立ててはいけない。取得間隔・上限はこのフラグでも緩めない
- **市区の検索値は3系統ある。** JIS5桁（SUUMO/GOO/ABLE/賃貸EX/EHEYA/SMOCCA）／
  JIS5桁の下3桁（APAMAN）／サイト固有スラグ（HOMES/ATHOME/NIFTY/MINIMINI）。
  スラグ系だけが `m_city_site_values` を引く
- 面積の単位は ㎡（U+33A1）・m²・`m<sup>2</sup>` とばらつく。
  `parse_area_sqm` は NFKC 正規化してから読む
- **名寄せの住所は「丁目まで」で打ち切る。** サイトによって粒度が
  「番地まで（HOME'S）／丁目まで（多数）／町名まで（SUUMO）」とばらつき、
  番地を残すとクロスサイトの名寄せが原理的に成立しない（→ ADR 0012）
- **名寄せキーに建物名・築年月・総階数・賃料を入れてはいけない。** 匿名掲載
  （`ＪＲ相模線 上溝駅 2階建 築41年`）が実在し、入れると真の一致が分断される。
  面積も丸めない（丸めても一致は増えず隣接住戸を余分に潰すだけ）
- **`refresh_dedup_keys` は一覧の upsert 直後と詳細の保存後の両方で呼ぶ。**
  階数・住所は詳細ページで初めて埋まる掲載があり、片方だけだとキー充足率が上がらない
- `sync_groups` は差分管理をしない**冪等な集合演算**。代表の交代・掲載の消失は
  「次の同期で自然に直る」ので、イベント駆動の張り替えを足さないこと
- **`regroup` は通知を送らない。** 既存データへの初回適用で `cheaper_listing` が
  大量発火するのを避けるため。通知は次回の `scan` の差分に任せる
- **順位はグループ代表と未グループ物件にだけ振る**（`update_ranks`）。
  `digest` は `rank_in_pattern` 起点なので、ここを崩すとランキングに重複が戻る
- **レート制御は `SiteFetcher` のプロセス内にしかない。** 別プロセスの `scan` 同士や
  `scan` と `check-sold` が並走すると同一サイトへの実効間隔が半分になる。
  タスクのトリガー時刻を分離し、初回スキャン中は取得タスクを無効にしてあるのはこのため
- **`scan` はサイトを直列に回す。** 増分でも約72分かかるので毎時実行には収まらない
  （一覧1116リクエスト＋詳細320リクエスト）。タスクは2時間ごと
- **`max_pages_per_run` は「一覧URL 1本あたり」のページ数**で、エリアごとに掛かる。
  市区必須サイトは216〜240本の一覧URLを持つので、`--full` では5倍に効く
- **PowerShell 5.1 は stdout と stderr に同じファイルを指定できない。**
  `Start-Process` のリダイレクトは必ず別ファイルにする
- **タスク用スクリプトと切り離し用スクリプトを流用し合わない。**
  `run_initial_scan.ps1` は `Start-Process` で切り離す側、`task_runner.ps1` は
  `-Wait` で待つ側。前者をタスクから呼ぶと即完了扱いになり二重起動する
- **S4U のタスク登録には管理者権限が要る**（`SeTcbPrivilege`）。
  通常アカウント `wy469` は標準ユーザーで `BUILTIN\Administrators` に入っていない

## AI回答方針
- 複数実装がある場合はトレードオフを説明してから推奨案を提示する
- より良い設計があれば指示に縛られず積極的に提案する
- セキュリティ上の懸念点は必ず指摘する
