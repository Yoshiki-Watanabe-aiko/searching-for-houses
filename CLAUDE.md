# CLAUDE.md

⚠ **このファイルは毎セッション全文が読み込まれる。** 数KBに保ち、実際に踏んだ落とし穴は
`.claude/rules/` の領域別ファイルへ書く（→ 末尾「実装上の注意」の表）。`@` は数KBの規約だけに付ける。

## プロジェクト概要
指定した条件に合致する物件を日本の不動産サイトから自動取得し、
MUST（未充足なら除外）＋WANT（重み付き加点）のスコアでランク付けして
新着・成約・価格変動・日次ランキングをDiscordへ通知するシステム。

**v2（Python）へ全面再設計中。Phase 5（賃貸の本運用）を再設計中。**
Phase 5C で**通勤時間**をランキングへ組み込み（→ ADR 0016）、
Phase 5D で回帰式から **NAVITIME の実ダイヤ**へ置き換えた（→ ADR 0017）。
Phase 5E で取得数に上限があるサイト（HOMES・ATHOME）を**市区ローテーション**で
回すようにした（→ 課題#36）。
Phase 5F で **UR賃貸住宅**を追加した。3段のJSON API（POST）で団地→住戸→住戸詳細と辿り、
GET＋HTML の枠に収まらないため**任意フック**で `pipeline.scan` から委譲を受ける
（→ ADR 0019・課題#37）。
初回全件スキャンの実測でランキング上位が群馬/栃木県境と外房で埋まったため、
**エリア帯**（23区／近郊60分圏）で検索パターンを2つに分割した（→ ADR 0013・課題#24）。
進捗と残作業は `docs/再設計計画.md` を参照。
v1（Go）の実装は `legacy-go` ブランチ / `v1-go-final` タグに保全済み。

## 技術スタック
- Python 3.12+（パッケージ管理は uv。Windowsタスクスケジューラーで定期実行、常駐プロセスではない）
- スクレイピング: httpx + lxml + cssselect（**全11サイトHTTP取得**。UR はJSON APIへの POST。Playwrightは Phase 3 で撤去 → ADR 0010）
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
uv run house-search re-extract --family TOCHI_BUY  # そのファミリだけ再抽出（1ファミリの辞書を足したとき）
uv run house-search report-unknown         # 辞書未登録の表記
uv run house-search coverage               # サイト別の抽出充足率（ネットワーク不要）
uv run house-search regroup                # 名寄せの再構築（ネットワーク不要・通知なし）
uv run house-search resolve-cities         # 市区町村IDの引き直し（マスタ入替後・ネットワーク不要）
uv run house-search sync-site-params       # サイト側フィルタ定義の同期（scan の前に必要）
uv run house-search sync-addresses         # 住所マスタ（data/address_master/*.csv）→ DB
uv run house-search sync-hazards           # ハザード評価（data/hazard_levels/*.csv）→ DB
uv run house-search sync-stations          # 駅マスタ（data/train_master/*.csv）→ DB
uv run house-search resolve-stations       # 掲載の駅表記を駅マスタと突き合わせる（ネットワーク不要）
uv run house-search resolve-commutes       # 駅ペアの通勤所要時間を算出しキャッシュ（ネットワーク不要）
uv run house-search fetch-commutes         # NAVITIMEから実ダイヤの通勤時間を取得（要ネットワーク・約15秒/駅）
uv run house-search fetch-commutes --region 関東   # 全国網羅。その地方の全駅×中心駅（→ ADR 0018）
uv run house-search re-segment             # 経路の原文から乗車区間を作り直す（ネットワーク不要）
uv run house-search re-segment --region 沖縄  # 地方ごと。索引もその地方に合わせる（→ 課題#35）
uv run house-search commute-stats          # 通勤時間の分布（best/worst を決める材料）
uv run house-search hazard-stats           # ハザードの解決率と分布（weight・best/worst の材料）
uv run house-search market-stats           # 相場比の解決率・分布・価格との独立性（→ 課題#49）
uv run house-search utility-stats          # 賃貸の推定光熱費（ガス種別の判定・光熱費込み月額の分布 → 課題#64）
uv run house-search sync-market-rates --buy  # 売買の㎡単価相場（buy_rates.csv）→ DB
uv run house-search dedup-stats            # サイト別の重複率・ユニーク率（ネットワーク不要）
uv run house-search scan --seed --site CHINTAI_EX   # 無効化サイトの観測モード
uv run house-search scan --detail-limit 800         # 詳細取得の上限を上書き（既定40 / --full時400）
uv run house-search web --test-db          # ブラウザ閲覧画面をテストDBで起動（127.0.0.1:8765・Ctrl+C で終了 → 課題#68）
```

運用スクリプト（PowerShell 5.1。1行ずつ実行する。`&&` は使えない）:

```powershell
.\scripts\run_initial_scan.ps1                # 初回全件スキャン（切り離して起動・約6.5〜9時間）
.\scripts\run_initial_scan.ps1 -Drain         # 2晩目以降の詳細キュー掃き出し
.\scripts\run_initial_scan.ps1 -Drain -Family MANSION_BUY,KODATE_BUY   # 売買だけ掃き出す（賃貸を含めない・約3時間）
.\scripts\run_initial_scan.ps1 -Site NIFTY    # 1サイトだけ取り直す（切り離して起動）
.\scripts\run_fetch_commutes.ps1              # 通勤時間の実ダイヤ取得（切り離して起動・約4.8時間）
.\scripts\run_fetch_commutes.ps1 -Regions 北海道,東北  # 複数地方を順に（各地方の後に re-segment まで行う）
.\scripts\run_web.ps1                         # ブラウザ閲覧画面（前面で起動・ブラウザを開く・Ctrl+C で終了 → ADR 0026）
.\scripts\backup_db.ps1                       # pg_dump（14世代保持）
.\scripts\update_market_rates.ps1            # 家賃相場の月次更新（全国・取得→CSV→DB・約90分）
.\scripts\update_market_rates.ps1 -SkipFetch # 保存済みHTMLから作り直すだけ
.\scripts\update_buy_market_rates.ps1        # 売買相場の月次更新（全国・取得→CSV→DB・約31分）
.\scripts\update_buy_market_rates.ps1 -Force # 新しい四半期が無くても作り直す
.\scripts\run_market_then_drain.ps1         # 相場の更新→売買の掃き出しを順に（切り離して起動）
.\scripts\run_market_then_drain.ps1 -SkipMarket  # 掃き出しだけ
.\scripts\register_tasks.ps1 -DryRun          # タスクXMLの生成と検証（権限不要）
.\scripts\register_tasks.ps1                  # タスク登録（要管理者。取得6本は無効で入る → 課題#23）
.\scripts\register_tasks.ps1 -EnableScraping  # 取得6本も有効にして登録し直す（⚠ 時刻や上限を直したらこれ）
.\scripts\register_tasks.ps1 -EnableOnly      # 登録済みの取得6本を有効化するだけ（定義は書き換えない）
```

## 参照ファイル

⚠ **作業のたびに育つ文書には `@` を付けない**（付けるとセッション開始時に全文が読み込まれる）。
2026-09-15 時点で 再設計計画 185KB・要件定義書 172KB・課題管理表 677KB あり、
3つで開始時点から数十万トークンを使っていた。**必要なときに該当節だけを読む**
（`Grep` で節の見出しを探し、`Read` の offset/limit で部分読みする）。

| 文書 | いつ読むか |
|---|---|
| `docs/再設計計画.md` | Phase の構成・進捗（§11 の表）・過去 Phase の経緯（§16）を確かめるとき |
| `docs/requirements.md` | 仕様の確定内容を確かめる・プログラム修正後に該当節を直すとき（§4.4 タスク／§5 YAML・metric・MUST／§6 採点／§9 通知／§13 レート制御） |
| `docs/課題管理表.md` | 課題番号の経緯・実測値・ユーザー判断を確かめる／新しい課題や対応履歴を追記するとき（`## #NN` で見出しを探す） |
| `docs/詳細設計書/` | `01_サイト取得設計.md` / `02_設備抽出辞書設計.md` / `03_スコアリング設計.md` / `04_名寄せ設計.md` |
| `docs/adr/` | 設計判断の記録（一覧と欠番の所在は `docs/adr/README.md`） |

## 実装上の注意（実際に踏んだもの）

実測で踏んだ落とし穴は **`.claude/rules/` に領域別に置いてある**（2026-09-17 に本節の 59KB を移した → 課題#72）。
各ファイルの `paths:` に当たるファイルを `Read` した時点で自動で読み込まれる。
⚠ **ファイルを開く前の設計・調査の段階では発火しない。** 下表の作業に着手するときは、先に自分で読む。
⚠ **新しい注意はこの節ではなく、該当する領域のファイルへ足す**（本節に積んでいた頃は毎セッション推定2〜3万トークンを使っていた）。
どこにも当たらなければファイルを新設し、**必ず `paths:` を付けて下表にも載せる**
（`paths:` の無いルールは毎セッション読み込まれる。`tests/test_claude_rules.py` が `paths:` の有無・
パスの実在・下表への記載・本ファイルの大きさを検査する）。

| ファイル | 着手する作業 | 自動で読み込まれる主なパス |
|---|---|---|
| `.claude/rules/scrape-sites.md` | サイトの追加・アダプタの一覧/詳細の解析・金額/面積/交通欄の読み取り・権利形態/建築条件の判定 | `src/house_search/scrape/` 全体 |
| `.claude/rules/scrape-fetch.md` | robots・取得間隔・リトライ・市区ローテーション・サイト側へ渡すフィルタ（**サイトの追加では scrape-sites と両方を読む**） | `scrape/fetch.py`・`params.py`・`rotation.py`・`data/site_search_params.yaml`・`scripts/tools/probe_*.py` |
| `.claude/rules/area.md` | 市区の検索値・市区町村マスタ・エリア索引の収集 | `scrape/area.py`・`data/city_master/`・`scripts/tools/collect_city_slugs.py` |
| `.claude/rules/pipeline.md` | 一覧の upsert・詳細キュー・任意フック・詳細ページ由来の項目の取り直し | `src/house_search/pipeline/` |
| `.claude/rules/dedup.md` | 住所の正規化・名寄せ・住所マスタ | `src/house_search/dedup/`・`data/address_master/` |
| `.claude/rules/hazard.md` | ハザード評価の生成・丁目照合・配点 | `src/house_search/hazard/`・`data/hazard_levels/`・`scripts/tools/build_hazard_levels.py` |
| `.claude/rules/commute.md` | 駅の同定・NAVITIME・通勤時間・乗車区間・駅徒歩 | `src/house_search/commute/`・`data/train_master/` |
| `.claude/rules/scoring-config.md` | 検索パターンYAML・配点・best/worst・設備辞書・採点・エリア帯 | `configs/`・`src/house_search/config/`・`scoring/`・`extract/` |
| `.claude/rules/operations.md` | 定期タスク・運用スクリプト（PowerShell）・CLI・スキャンの完了判定 | `scripts/*.ps1`・`cli.py`・`pipeline/runtime.py`・`pipeline/tasks.py` |
| `.claude/rules/web.md` | 閲覧画面・印（お気に入り・除外・メモ）・ダイジェストの除外 | `src/house_search/web/`・`marks.py`・`notify/` |
| `.claude/rules/db.md` | マイグレーション・シードSQL・列の追加 | `alembic.ini`・`migrations/`・`db/`・`src/house_search/db/` |

## AI回答方針
- 複数実装がある場合はトレードオフを説明してから推奨案を提示する
- より良い設計があれば指示に縛られず積極的に提案する
- セキュリティ上の懸念点は必ず指摘する
