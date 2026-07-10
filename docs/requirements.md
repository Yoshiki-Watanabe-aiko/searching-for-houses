# 物件検索通知システム 要件定義書

**作成日**: 2026-06-16  
**言語/スタック**: Go / PostgreSQL / Discord Webhook

---

## 1. システム概要

指定した検索条件に合致する物件を日本の不動産サイトから自動取得し、新着・成約・価格変動をDiscordへ通知するシステム。

---

## 2. 対応サイト

| サイトコード | サイト名 | スクレイピング方式 |
|---|---|---|
| SUUMO | SUUMO | HTTP + goquery |
| HOMES | LIFULL HOME'S | HTTP + goquery |
| ATHOME | athome | HTTP + goquery |
| GOO | goo不動産 | HTTP + goquery |
| EHEYA | いい部屋ネット | Playwright (go-rod) |
| ABLE | エイブル | HTTP + goquery |
| MINIMINI | minimini | HTTP + goquery |
| NIFTY | ニフティ不動産 | Playwright (go-rod) |
| APAMAN | アパマンショップ | Playwright (go-rod) |
| SMOCCA | スモッカ | Playwright (go-rod) |

> LIFULL HOME'S と ホームズは同一サイト (HOMES)。合計 10 サイト。  
> go-rod 利用サイトは Chrome のインストールが前提。

---

## 3. 対応物件種別

| コード | 名称 |
|---|---|
| CHINTAI | 賃貸 |
| SHINCHIKU_MANSION | 新築マンション |
| CHUKO_MANSION | 中古マンション |
| SHINCHIKU_KODATE | 新築一戸建て |
| CHUKO_KODATE | 中古一戸建て |

---

## 4. 実行モデル

### 4.1 プロセスモデル

- **毎回起動 → 実行 → 終了**モデル（常駐プロセスではない）
- Windows タスクスケジューラーによる定期実行・クラッシュ時自動再起動

### 4.2 CLI フラグ

| フラグ | 動作 |
|---|---|
| (なし) | 通常実行 — 各サイト最新1ページのみスクレイプ |
| `--full-scan` | 全量スキャン — 各サイト最大 `FULL_SCAN_MAX_PAGES` ページ |
| `--check-sold` | 成約確認 — DB内の全アクティブ物件の詳細URLへ直接アクセスして成約/削除を確認 |

### 4.3 推奨タスクスケジューラー設定

| タスク | コマンド | 頻度 |
|---|---|---|
| 通常スクレイプ | `house-notifier.exe` | 毎時 |
| 成約確認 | `house-notifier.exe --check-sold` | 1日1回（例: 毎朝9時） |
| 初回 / 手動 | `house-notifier.exe --full-scan` | 手動 |

---

## 5. 検索パターン設定（YAML）

### 5.1 ファイル配置

- デフォルト: 実行ファイルと同じディレクトリの `configs/*.yaml`
- 環境変数 `CONFIGS_DIR` でパスをオーバーライド可能
- 複数 YAML ファイルを直列実行

### 5.2 YAML スキーマ

```yaml
name: "東京1LDK賃貸"              # パターン名（DB・通知ログで使用）
property_type: "CHINTAI"          # 物件種別コード
site_ids:                         # スクレイプ対象サイト
  - "SUUMO"
  - "HOMES"
  - "ATHOME"
discord_webhook: "https://discord.com/api/webhooks/xxx/yyy"
conditions:
  area:
    prefectures: ["東京都"]
    cities: []                    # 区市町村（空=都道府県全体）
                                  # 値は cities テーブルの canonical_name を指定
                                  # 例: ["新宿区", "横浜市西区", "大阪市北区"]
                                  # 政令指定都市の区は「横浜市西区」のように市名を prefix
    stations: []                  # 最寄り駅名
    walk_minutes_max: 10          # 徒歩分数上限
  price:
    rent_max: 200000              # 賃料上限（円）
    price_max:                    # 売買価格上限（円）
    include_mgmt_fee: true        # 管理費込みで上限判定
    no_reikin: false              # 礼金なし限定
    no_shikikin: false            # 敷金なし限定
  building:
    layouts: ["1LDK", "2K", "2DK"]
    area_min: 40.0                # 面積下限（㎡）
    area_max:                     # 面積上限（㎡）
    age_max: 15                   # 築年数上限
    floor_min:                    # 階数下限
  features:                       # 設備・条件コード
    - MOVEIN_PET
    - SEC_AUTOLOCK
    - BATH_SEPARATE
```

---

## 6. 通知仕様

### 6.1 通知タイプ

| タイプ | トリガー | Discord Embed カラー |
|---|---|---|
| `new` | 新着物件を初めて検出、または再掲載 | 🟢 緑 (#57F287) |
| `sold` | 詳細URLが成約/削除ページに遷移 | 🔴 赤 (#ED4245) |
| `price_down` | 前回価格より1円以上値下がり | 🔵 青 (#5865F2) |
| `price_up` | 前回価格より1円以上値上がり | 🟡 黄 (#FEE75C) |

### 6.2 通知フォーマット

- Discord **Rich Embed**、**1件ずつ個別送信**
- Embed 内容: タイトル、価格（変動時は変動額も）、間取り、面積、住所、最寄り駅、築年数、サムネイル画像、物件URL

### 6.3 重複通知防止ルール

- **`new`**: 初回登録時、または `removed`/`sold` → `active` に再掲載された時のみ通知
- **`sold`**: 同パターン・同物件で `sold` 通知済みであればスキップ
- **`price_up`/`price_down`**: 直近の同タイプ通知の `price_at_notify` と現在価格が同じであればスキップ（価格が変わるたびに通知）

### 6.4 送信レート制限

- 送信間隔: **2秒/件**
- 1ポーリングの通知件数上限: **なし**

### 6.5 チャンネル構成

| 用途 | 設定箇所 |
|---|---|
| 各検索パターンの通知 | YAML の `discord_webhook` |
| グローバルエラー通知 | `.env` の `ERROR_DISCORD_WEBHOOK` |

---

## 7. 物件ステータス管理

| ステータス | 意味 | 遷移条件 |
|---|---|---|
| `active` | 掲載中 | 初回取得時・再掲載時 |
| `sold` | 成約済み | `--check-sold` で成約ページ検出 |
| `removed` | 掲載終了 | `--check-sold` で404/削除ページ検出 |

**再掲載処理**: `sold`/`removed` の物件がスクレイピングで再び取得 → `active` に戻し `new` 通知

---

## 8. データベース

### 8.1 DB 構成

| ファイル | 内容 |
|---|---|
| `db/01_schema.sql` | マスタテーブル（物件種別・サイト・条件等） |
| `db/02_master_data.sql` | マスタデータ（10サイト・5物件種別・128条件等） |
| `db/03_app_tables.sql` | アプリテーブル（properties・notifications・scrape_logs） |
| `db/04_cities.sql` | 市区町村テーブル（cities・city_site_mappings） |
| `db/05_city_data.sql` | 市区町村マスタデータ（全国主要都市の区情報） |
| `db/06_site_mappings.sql` | サイト別URLマッピングデータ（city_site_mappings 行データ） |

### 8.2 主要テーブル

#### properties

| カラム | 型 | 説明 |
|---|---|---|
| `external_id` | VARCHAR(500) | サイト固有の物件ID |
| `price` | BIGINT | 現在価格（円） |
| `price_prev` | BIGINT | 価格変動前の価格 |
| `address_hash` | VARCHAR(64) | SHA256（将来の重複検知用、現時点は未使用） |
| `status` | VARCHAR(20) | active / sold / removed |
| `last_seen_at` | TIMESTAMPTZ | 最終スクレイプ確認日時 |

#### notifications

UNIQUE 制約なし。重複防止はアプリケーションコードで制御。

| カラム | 型 | 説明 |
|---|---|---|
| `notification_type` | VARCHAR(20) | new / sold / price_up / price_down |
| `price_at_notify` | BIGINT | 通知時の価格（重複チェック用） |

#### scrape_logs

全件永久保持（自動削除なし）。

#### cities

市区町村マスタ。YAML の `cities` フィールドに指定する名称の正規テーブル。

| カラム | 型 | 説明 |
|---|---|---|
| `id` | SERIAL PK | 内部ID |
| `prefecture` | VARCHAR(20) | 都道府県名 |
| `parent_city` | VARCHAR(50) | 政令指定都市名（例: 横浜市）。23区・市区の場合は NULL |
| `city_name` | VARCHAR(50) | 区名・市名（例: 新宿区） |
| `canonical_name` | VARCHAR(100) | YAML 指定用の正規名。同一都道府県内で一意。<br>政令市の区は「横浜市西区」のように市名を prefix |

UNIQUE 制約: `(prefecture, canonical_name)`

#### city_site_mappings

city_id を主キーとするワイドテーブル。各サイトの URL 値を 1 行に格納。

| カラム | 型 | 説明 |
|---|---|---|
| `city_id` | INTEGER PK (FK → cities.id) | 都市ID |
| `suumo` | VARCHAR(100) | SUUMO の URL パスセグメント（例: `sc_shinjuku`） |
| `homes` | VARCHAR(100) | HOMES の URL パスセグメント（例: `tokyo/shinjuku-city`） |
| `athome` | VARCHAR(100) | athome の JIS 5桁コード（例: `13104`） |
| `goo` | VARCHAR(100) | goo の JIS 5桁コード（例: `13104`） |
| `able` | VARCHAR(100) | エイブルの JIS 5桁コード（例: `13104`） |
| `minimini` | VARCHAR(100) | minimini の JIS 5桁コード（例: `13104`） |
| `eheya` | VARCHAR(100) | いい部屋ネットの URL スラグ（例: `tokyo/shinjuku`） |
| `nifty` | VARCHAR(100) | ニフティの URL スラグ（例: `tokyo/shinjuku`） |
| `apaman` | VARCHAR(100) | アパマンの JIS 5桁コード（例: `13104`） |
| `smocca` | VARCHAR(100) | スモッカの URL スラグ（例: `tokyo/shinjuku`） |

NULL のカラムはそのサイトで当該市区のURL値が不明または非対応を意味し、都道府県レベル検索にフォールバックする。

**サイト別URLパターン**

| サイト | URL値の種類 | 使用箇所 |
|---|---|---|
| SUUMO | `sc_{ward}` スラグ | パスセグメント `/chintai/{slug}/` |
| HOMES | `{pref}/{city}-city` スラグ | パスセグメント `/chintai/{slug}/` |
| ATHOME | JIS 5桁コード | パスセグメント `/chintai/{code}/list/` |
| GOO | JIS 5桁コード | クエリ `g=city&v={code}`（都道府県は `g=pref&v={code}`） |
| ABLE | JIS 5桁コード | クエリ `city={code}` |
| MINIMINI | JIS 5桁コード | クエリ `city={code}` |
| EHEYA | `{pref}/{ward}` スラグ | パスセグメント `/chintai/{slug}/list/` |
| NIFTY | `{pref}/{ward}` スラグ | パスセグメント `/rent/{slug}/list/` |
| APAMAN | JIS 5桁コード | クエリ `city={code}` |
| SMOCCA | `{pref}/{ward}` スラグ | パスセグメント `/chintai/{slug}/list/` |

**対応済み都市（06_site_mappings.sql）**

| 都道府県 | 対応区 | 対応サイト数 |
|---|---|---|
| 東京都 | 23区 | 10サイト（全サイト） |
| 神奈川県 | 横浜市18区・川崎市7区・相模原市3区 | 9サイト（SUUMO除く） |
| 大阪府 | 大阪市24区 | 9サイト（SUUMO除く） |
| 埼玉県 | さいたま市10区 | 5サイト（HOMES/ATHOME/GOO/ABLE/MINIMINI） |
| 千葉県 | 千葉市6区 | 5サイト |
| 愛知県 | 名古屋市16区 | 5サイト |
| 京都府 | 京都市11区 | 5サイト |
| 北海道 | 札幌市10区 | 9サイト（SUUMO除く） |
| 福岡県 | 福岡市7区 | 9サイト（SUUMO除く） |
| 兵庫県 | 神戸市9区 | 5サイト |
| 静岡県 | 静岡市3区・浜松市3区 | 5サイト |
| 新潟県 | 新潟市8区 | 5サイト |

> 滋賀県・宮崎県・長野県は政令指定都市なし。都道府県レベル検索のみ対応。

### 8.3 セットアップコマンド

```powershell
.\db\setup.ps1
```

---

## 9. 環境変数（.env）

| キー | 必須 | 説明 |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL 接続URL (`postgres://user:pass@host:port/dbname`) |
| `ERROR_DISCORD_WEBHOOK` | ✅ | グローバルエラー通知チャンネルの Webhook URL |
| `FULL_SCAN_MAX_PAGES` | — | `--full-scan` 時の最大ページ数（デフォルト: 5） |
| `CONFIGS_DIR` | — | configs/ ディレクトリのパス（省略時: 実行ファイルと同じディレクトリ） |

---

## 10. エラーハンドリング

- **1サイトがエラー**: 処理継続 + DB ログ記録 + グローバルエラーチャンネルへ通知
- **YAML 読み込み失敗**: そのパターンをスキップ + グローバルエラーチャンネルへ通知
- **Discord API 失敗**: DB ログ記録のみ（リトライなし）

---

## 11. プロジェクト構成

```
f:\searching-for-houses\
├── cmd/
│   └── main.go
├── internal/
│   ├── config/
│   │   ├── app.go
│   │   └── search.go
│   ├── db/
│   │   ├── client.go
│   │   ├── property_repo.go
│   │   ├── notification_repo.go
│   │   ├── log_repo.go
│   │   └── city_repo.go
│   ├── model/
│   │   └── property.go
│   ├── scraper/
│   │   ├── interface.go
│   │   ├── http_base.go
│   │   ├── rod_base.go
│   │   ├── suumo/scraper.go
│   │   ├── homes/scraper.go
│   │   ├── athome/scraper.go
│   │   ├── goo/scraper.go
│   │   ├── eheya/scraper.go        # Playwright
│   │   ├── able/scraper.go
│   │   ├── minimini/scraper.go
│   │   ├── nifty/scraper.go        # Playwright
│   │   ├── apaman/scraper.go       # Playwright
│   │   └── smocca/scraper.go       # Playwright
│   ├── notifier/
│   │   └── discord.go
│   └── runner/
│       └── runner.go
├── configs/
│   └── example_chintai_tokyo.yaml
├── db/
│   ├── 01_schema.sql
│   ├── 02_master_data.sql
│   ├── 03_app_tables.sql
│   ├── 04_cities.sql
│   ├── 05_city_data.sql
│   ├── 06_site_mappings.sql
│   └── setup.ps1
├── docs/
│   └── requirements.md
├── .env
├── go.mod
└── go.sum
```

---

## 12. 依存ライブラリ

| ライブラリ | 用途 |
|---|---|
| `github.com/jackc/pgx/v5` | PostgreSQL ドライバー |
| `github.com/PuerkitoBio/goquery` | HTML パース |
| `github.com/go-rod/rod` | ブラウザ自動化（Playwright相当） |
| `gopkg.in/yaml.v3` | YAML 設定ファイル読み込み |
| `github.com/joho/godotenv` | .env ファイルロード |

---

## 13. 将来の拡張予定

- **クロスサイト重複検知**: `address_hash` を使って異なるサイトの同一物件を名寄せし、最初の1回のみ通知
