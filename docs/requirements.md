# 物件検索通知システム v2 要件定義書

**作成日**: 2026-06-16（v1）/ **v2改訂**: 2026-09-02
**言語/スタック**: Python 3.12+ / PostgreSQL 18 / Discord Webhook

> **本書の状態**: Phase 3（取得サイトの展開）完了時点。
> Phase 3 以降で確定した仕様を各Phase完了時に本書へ反映していく。
> 移行の全体像・Phase構成・未確定事項は [`再設計計画.md`](./再設計計画.md) を参照。
> **未確定の節には「Phase N で確定」と明記している。**

---

## 1. システム概要

指定した条件に合致する物件を日本の不動産サイトから自動取得し、
**MUST（未充足なら除外）＋ WANT（重み付き加点）のスコアでランク付けして**
Discordへ通知するシステム。

### 1.1 v1 からの本質的な変更

| 観点 | v1（Go） | v2（Python） |
|---|---|---|
| 条件モデル | 全条件AND絞り込み | MUST＋WANTのスコアリング |
| 絞り込み位置 | サイトの検索フォーム | **エリア・種別・価格上限のみサイト側。判定と採点はローカル** |
| 設備条件 | サイトのURLパラメータ（10サイト中6サイトが非対応で素通り） | 詳細ページ本文からの辞書マッチング（全サイト同一の判定器） |
| 通知 | 新着・成約・価格変動 | 左記＋日次ランキングダイジェスト |
| 名寄せ | 将来課題 | 本体要件（グループ単位で重複抑制） |
| 対象種別 | 賃貸のみ実装 | 賃貸＋売買4種別（Phase 6〜8） |

**この転換はランキング導入と論理的に不可分**である。WANT条件（オートロック無しでも
減点で残ってほしい）をサイト側フィルタに渡すと、対応サイトでは物件が除外されて
順位に現れず、非対応サイトでは素通りする。→ ADR 0003

v1 の実装は `legacy-go` ブランチ / `v1-go-final` タグに保全している。

---

## 2. 対応サイト

`m_sites` が正典。実データは [`db/seed/02_sites.sql`](../db/seed/02_sites.sql)。

**取得方式は全サイト HTTP。** Phase 3 の実測で、v1 がブラウザ自動化を使っていた
5サイトもサーバレンダリング済みHTMLを返すことが分かり、`playwright` は依存から外した
（→ [ADR 0010](./adr/0010-http-only-fetch.md)）。

| サイトコード | サイト名 | 状態 |
|---|---|---|
| SUUMO | SUUMO | ✅ 実装済み |
| HOMES | LIFULL HOME'S | ✅ 実装済み（WAFに阻まれることがある。2026-09-02 は回復 → 課題#17） |
| ATHOME | アットホーム | ✅ 実装済み（**パズル認証のボット検知が発動中** → 課題#20） |
| NIFTY | ニフティ不動産 | ✅ 実装済み（市区指定必須・他社掲載を集約するポータル） |
| GOO | goo不動産 | ✅ 実装済み（市区指定必須） |
| CHINTAI_EX | 賃貸EX | ✅ 実装済み（**観測モード**。`is_active=false`） |
| ABLE | エイブル | ✅ 実装済み（市区指定必須・価格上限が効かない） |
| MINIMINI | minimini | **取得手段なし**（HTTPでもブラウザでもreCAPTCHA）→ 課題#18 |
| APAMAN | アパマンショップ | ✅ 実装済み（市区指定必須・**robots.txt を無視する唯一の例外** → ADR 0011） |
| EHEYA | いい部屋ネット | ✅ 実装済み（掲載データは `__NEXT_DATA__` のJSON） |
| SMOCCA | スモッカ | ✅ 実装済み（市区指定必須・**1ページ90件のみ** → 課題#22） |
| SHAMAISON | シャーメゾン | 無効（v1から未実装。自社物件のみのため対象外） |

- **賃貸のスクレイピング対象は11サイト**（SHAMAISON を除く）。マスタ行数は12。
- Phase 3 時点でアダプタ実装済みは **MINIMINI を除く10サイト**。
  未実装サイトは `scan` が「スキップ（アダプタ未実装）」と明示的に報告する。
- `m_sites.is_active = false` のサイトは通常の `scan` では取りに行かない。
  `--site` で名指ししたときだけ動く（観測モードの入口）。
- **ブラウザ自動化は使わない。** v1 の本書は ATHOME を「HTTP + goquery」と記載していたが、
  実際は go-rod を使っていた（`cmd/main.go:82`）。ただし理由は検索フォームの操作であり、
  URLを直接組み立てる v2 では5サイトともHTTPで取得できる（→ ADR 0010）。
- **能動的なボット検知は突破しない。** ページが 200 で返るため、
  検知ページを判別できるサイトはアダプタが例外にする（黙って0件になると気づけないため）。

---

## 3. 対応物件種別

| コード | 名称 | ファミリ |
|---|---|---|
| CHINTAI | 賃貸 | CHINTAI |
| SHINCHIKU_MANSION | 新築マンション | MANSION_BUY |
| CHUKO_MANSION | 中古マンション | MANSION_BUY |
| SHINCHIKU_KODATE | 新築一戸建て | KODATE_BUY |
| CHUKO_KODATE | 中古一戸建て | KODATE_BUY |

**ファミリ**は metric体系・dedup_key の構成要素・YAMLスキーマの分岐単位。
新築/中古の差は築年数・価格未定・リノベ関連の数項目だけなので、5種別を5クラスに割らない。

### 3.1 サイト×種別の実装対象

実装対象は 55セル中 **31セル**（賃貸11サイト ＋ 売買4種別×5サイト）。
詳細マトリクスは [`再設計計画.md` §11.1](./再設計計画.md)。

---

## 4. 実行モデル

### 4.1 プロセスモデル

毎回起動 → 実行 → 終了。Windows タスクスケジューラーで定期実行（常駐しない）。

### 4.2 CLI

エントリポイント: `house-search`（`uv run house-search ...`）

| コマンド | 動作 | 実装 |
|---|---|---|
| `db-seed` | マスタデータ（`db/seed/*.sql`）を投入する。`--test-db` でテストDBへ | ✅ Phase 0 |
| `validate-config` | 検索パターンYAMLと `webhook_ref` の参照を検証する | ✅ Phase 0 |
| `scan` | 一覧取得 → MUST判定 → 詳細 → 抽出 → スコア → 通知 | ✅ Phase 1 |
| `scan --seed` | **シードモード**。通知を送らず記録だけ行う | ✅ Phase 1 |
| `scan --full` | 全量スキャン（`m_sites.max_pages_per_run` まで） | ✅ Phase 1 |
| `scan --site` | 対象サイトを1つに絞る | ✅ Phase 1 |
| `check-sold` | 成約/掲載終了の確認 | ✅ Phase 1 |
| `digest` | 日次ランキングダイジェストの送信（`--dry-run` で件数確認） | ✅ Phase 1 |
| `rescore` | DB内の物件属性から再採点（ネットワーク不要） | ✅ Phase 1 |
| `sync-dict` | `data/feature_dictionary.yaml` → `m_condition_synonyms` | ✅ Phase 1 |
| `re-extract` | `raw_features_text` から全件再抽出（ネットワーク不要） | ✅ Phase 1 |
| `report-unknown` | 辞書未登録の表記を出現回数順に一覧 | ✅ Phase 1 |
| `coverage` | サイト別の設備抽出数分布・数値カラム非NULL率の実測 | ✅ Phase 2 |

**Phase 2 で全コマンドが実装済みになった。**
`scan` はアダプタ未実装のサイトと無効化されたサイトを「スキップ」として明示的に報告する
（黙って無視すると「実装済みだが未配線」を見逃すため）。
`scan` は**アダプタ未実装のサイトを「スキップ」として明示的に報告する**。

### 4.3 シードモード

**初回全件取得を通知なしの記録専用モードで走らせる**ことで、旧通知履歴を捨てても
「再掲載が全部新着として再通知される」問題が構造的に発生しなくなる。
パターン新規追加時・長期停止からの再開時にも使う汎用機能。→ ADR 0006

### 4.4 タスクスケジューラー構成

**Phase 5 で確定・登録する。** 予定は毎時スキャン／毎日9:00成約確認／
毎日20:00ダイジェスト／毎日3:30 pg_dump の4本。

---

## 5. 検索パターン設定（YAML v2）

### 5.1 ファイル配置

- 既定: リポジトリ直下の `configs/*.yaml`（環境変数 `CONFIGS_DIR` で変更可）。
  **サブディレクトリは読まない**（`glob("*.yaml")` は非再帰）
- 実運用: [`configs/chintai_alone.yaml`](../configs/chintai_alone.yaml)（Git管理下。課題#9 解消）
- 雛形: [`configs/examples/chintai_v2.yaml`](../configs/examples/chintai_v2.yaml)
  — **`configs/` 直下に置くと実パターンとして走ってしまう**ため `examples/` に置く
- v1形式の設定は `configs/_v1/`（Git管理外）へ退避してある

### 5.2 スキーマ

型定義は `src/house_search/config/pattern.py`。`property_type` を discriminator にした
discriminated union で3ファミリへ分岐する。未知のキーはエラーにする（綴り間違いを黙って無視しない）。

```yaml
name: "東京賃貸一人暮らし"
property_type: "CHINTAI"
webhook_ref: "CHINTAI_ALONE"       # .env の DISCORD_WEBHOOK_CHINTAI_ALONE を参照
sites: [SUUMO, HOMES, ATHOME, GOO, ABLE, MINIMINI, EHEYA, NIFTY, APAMAN, SMOCCA]

search:                             # サイト側へ渡す唯一の条件
  prefectures: ["東京都", "千葉県", "埼玉県", "神奈川県"]
  cities: []                        # 空ならABLE/SMOCCAは都道府県内全市区へ自動展開
  price_max_hint: 90000             # MUST上限の2〜3割増し（管理費別計上サイト対策のバッファ）

must:                               # 未充足なら除外
  rent_total_max: 70000
  layouts: ["1LDK", "2K", "2DK", "2LDK", "3LDK"]
  area_min: 30.0
  walk_minutes_max: 20
  features: []
  unknown_policy: keep              # 判定不能なMUSTを keep=通す / drop=除外

want:
  features:                         # 該当なら weight 満点を加点
    - { code: INT_LAUNDRY,   weight: 10 }
    - { code: BATH_SEPARATE, weight: 9 }
    # 排他グループ。いずれか1つ満たせば満点（別々に weight を振ると
    # 片方が必ず miss になり、スコア上限が構造的に下がる）
    - { any_of: [STRUCT_RC, STRUCT_SRC], weight: 6 }
  numeric:                          # best〜worst で線形正規化
    - { metric: rent_total, weight: 10, best: 50000, worst: 70000 }
    - { metric: area_sqm,   weight: 6,  best: 45,    worst: 30 }

ranking:
  top_n: 15
  digest_group: null                # 同一グループをダイジェストに並記（スコアは混ぜない）
```

### 5.3 種別ごとに使える metric

レジストリは `src/house_search/config/metrics.py`。
YAMLは読み込み時にこのレジストリと突き合わせて検証される。

| metric | 方向 | 賃貸 | 新築M | 中古M | 新築K | 中古K |
|---|---|:---:|:---:|:---:|:---:|:---:|
| `rent_total`（賃料＋管理費） | 低いほど良 | ○ | - | - | - | - |
| `price` | 低いほど良 | - | ○ | ○ | ○ | ○ |
| `monthly_cost`（管理費+修繕積立金） | 低いほど良 | - | ○ | ○ | - | - |
| `area_sqm`（専有面積） | 高いほど良 | ○ | ○ | ○ | - | - |
| `building_area_sqm` | 高いほど良 | - | - | - | ○ | ○ |
| `land_area_sqm` | 高いほど良 | - | - | - | ○ | ○ |
| `age_years` | 低いほど良 | ○ | - | ○ | - | ○ |
| `walk_minutes` | 低いほど良 | ○ | ○ | ○ | ○ | ○ |

- **戸建てに `area_sqm` を流用しない。** 専有面積が存在せず土地面積・建物面積の2軸になるため
- **坪単価・㎡単価は metric にしない。** price と area に既に weight を配れる以上、
  二重に重みが掛かって解釈が濁る（`price_per_sqm` は表示用の派生カラムとしてのみ保持）

### 5.4 MUST判定の3値化

MUST判定は `pass` / `fail` / `unknown` の3値。**詳細ページの取得をスキップするのは `fail` のみ。**
一覧ページだけで判定できない項目（`monthly_cost_max` / `floor_min` / `features`）は
レジストリの `available_on_list=False` で明示している。

---

## 6. スコアリング

**✅ Phase 1 で実装済み。** 実装は `src/house_search/scoring/`、
詳細は [`詳細設計書/03_スコアリング設計.md`](./詳細設計書/03_スコアリング設計.md)。

- 正規化: `s = clamp((worst - x) / (worst - best), 0, 1)`
- 合計: `score = 100 × Σ(wᵢ × sᵢ) / Σ(wᵢ)` の0〜100点
- **欠損metricは分子・分母の双方から除外して再正規化**し、内訳に `"missing": true` を記録
- WANTの判定不能は0点＋「未確認」表示。中間値補完はしない
- 決定性: 条件コード順にソートしてから加算する
- 内訳は `t_property_scores.score_breakdown`(JSONB) に全項目を保存する
- **`any_of`**: 同時に満たしえない条件（RC / SRC）は排他グループで1項目にまとめる。
  別々に weight を振ると片方が必ず miss になるのに分母には両方が乗り、
  全物件のスコア上限が構造的に下がる
- **重みの初期値は確定済み**（2026-09-01・案A バランス型 / 数値45%）。
  設備22項目=110、数値4 metric=90。詳細は詳細設計書 §8

### 6.1 再スコアリング

スコアは「DB保存済みの物件属性＋抽出済みfeatures」からの純関数のため、
再計算はネットワーク不要のDBバッチになる。YAMLのスコア関連部分（`property_type` と `want`）の
SHA256 を `config_hash` として保存し、不一致なら自動再スコアする。
検索範囲や通知先の変更ではハッシュは変わらない。

---

## 7. 設備情報のローカル抽出

**✅ Phase 1 で実装済み。** 実装は `src/house_search/extract/`、
詳細は [`詳細設計書/02_設備抽出辞書設計.md`](./詳細設計書/02_設備抽出辞書設計.md)。
辞書初版は賃貸 **80条件 / 257パターン**。

1. **原文保存**: 詳細ページの設備ブロックを**テキストのまま** `t_properties.raw_features_text` へ
   （詳細HTML全体は保存しない）
2. **辞書マッチング**: NFKC正規化 → 小文字化 → トークン化 → 辞書照合 → `t_property_features` 生成

原文保存が要。辞書を改善したら再スクレイピングせずDB内の原文から全件再抽出できる（`re-extract`）。

辞書は **Git管理YAML（`data/feature_dictionary.yaml`）が正 → `sync-dict` で `m_condition_synonyms` へ同期**。
賃貸ブロックと売買ブロックの2部構成にする（証明書・性能評価系の語彙は賃貸と別体系のため）。

マッチしなかったトークンは `t_unknown_tokens` へ記録し、`report-unknown` → 辞書追記 →
`sync-dict` → `re-extract` で反映する運用ループを回す。

**照合は正規化済み本文全体への部分一致**で行う（トークンに切ってから照合しない）。
サイトによって区切りが「、」「／」「・」とばらつき、中黒で切ると
「バス・トイレ別」のように語中に区切り文字を含む条件を取りこぼすため。

**閾値条件は型付き列から導出する**（`source='DERIVED'`）。
「2階以上」「最上階」「築浅」は文字列照合では表現できない。

---

## 8. クロスサイト名寄せ

**Phase 4 で実装する。** 設計は [`再設計計画.md` §6](./再設計計画.md)。

- **キー**: ファミリ識別子＋正規化住所＋種別ごとの構成要素 の SHA256（`dedup_key`）
  - 賃貸/マンション: 住所＋間取り＋専有面積＋階数
  - 戸建て: 住所＋土地面積＋建物面積＋間取り
- **完全一致のみ自動グループ化。** 曖昧一致は候補フラグ止まり
- **建物名はキーに含めない**（賃貸では非公開・伏字が多く偽陰性の主因）
- **代表選定**: 月額/価格が最安 → 設備抽出数が最多 → サイト優先順（`m_sites.representative_priority`）
- スコアはグループ内の抽出情報の和集合で計算する

---

## 9. 通知仕様

**✅ Phase 1 で実装済み。** 実装は `src/house_search/notify/`。

| タイプ | トリガー | Discord Embed カラー |
|---|---|---|
| `new` | 新着物件を初めて検出、または再掲載（グループ単位で重複抑制） | 🟢 緑 (#57F287) |
| `sold` | 詳細URLが成約/削除ページに遷移 | 🔴 赤 (#ED4245) |
| `price_down` | 前回価格より値下がり | 🔵 青 (#5865F2) |
| `price_up` | 前回価格より値上がり | 🟡 黄 (#FEE75C) |
| `cheaper_listing` | 同一物件の他サイトで代表より安い掲載 | 未定 |
| ランキングダイジェスト | 日次 | 1メッセージにスコア上位N件 |

- 即時通知はスコア・パターン内順位・得点上位3項目・未確認項目数を載せる
- Discord制約: description 4096字/1embed、6000字/1メッセージ、10embed/1メッセージ
- **種別横断ランキングは作らない。** 正規化基準が異なるスコアを混ぜると数字が意味を失う。
  「中古Mと中古Kを並べて見たい」は `digest_group` によるセクション並記で応える
- 送信間隔は **2秒/件**。429 は `retry_after` に従って最大3回まで再送する
- **ダイジェストは1メッセージ1embedのテキスト表**にする。上位15件は
  embed 10個/メッセージの上限を超えるため。4096字を超える分は打ち切って明示する
- 送信失敗は例外にせず `t_notifications.status='failed'` として記録する
  （1件の失敗で実行全体を止めないため）
- **送信タイミング**: ダイジェストは毎日20:00・上位15件（2026-09-01 確定）
- エラー通知は `.env` の `DISCORD_WEBHOOK_ERRORS`

---

## 10. 物件ステータス管理

| ステータス | 意味 | 遷移条件 |
|---|---|---|
| `active` | 掲載中 | 初回取得時・再掲載時 |
| `sold` | 成約済み | `check-sold` で成約ページ検出 |
| `removed` | 掲載終了 | `check-sold` で404/削除ページ検出 |

**再掲載処理**: `sold`/`removed` の物件が再取得されたら `active` に戻し `new` 通知。

---

## 11. データベース

DB名は `searching_for_houses`、テストDBは `searching_for_houses_test`。
DDLは Alembic（`migrations/`）、マスタデータは `db/seed/*.sql`（冪等）。

### 11.1 テーブル一覧（マスタ8＋トランザクション9）

| テーブル | 内容 |
|---|---|
| `m_property_types` | 物件種別（5種別・ファミリ付き） |
| `m_sites` | サイト（12行。取得方式・レート制御・代表選定優先順） |
| `m_condition_categories` | 条件カテゴリ（19。売買用に CERT・LAND を追加） |
| `m_conditions` | 条件（148。`is_extractable` でローカル抽出対象かを持つ） |
| `m_condition_property_types` | 条件×物件種別（487行） |
| `m_condition_synonyms` | **設備抽出辞書**（条件コード → 表記パターン） |
| `m_cities` | 市区町村（947行。`canonical_name` がYAML指定値の正典） |
| `m_city_site_values` | 市区町村×サイトの検索値（**縦持ち**・1833行。JIS系サイトは `m_cities.jis_code` から導出するのでこの表を引かない） |
| `t_properties` | 物件（1行=1サイト掲載） |
| `t_property_features` | 設備・特性の抽出結果 |
| `t_property_scores` | パターン別スコア（内訳JSONB・`config_hash`） |
| `t_property_groups` | クロスサイト名寄せグループ |
| `t_notifications` | 個別通知の送信履歴（追記専用） |
| `t_ranking_digests` | ダイジェスト送信履歴（追記専用） |
| `t_scrape_runs` | 実行チェックポイント（中断・再開用） |
| `t_scrape_logs` | 実行ログ（全件永久保持・追記専用） |
| `t_unknown_tokens` | 辞書未登録の設備表記 |

DB規約準拠: `m_`/`t_` 接頭辞、全テーブル・全カラムに日本語コメント、
監査カラム（`created_at`/`updated_at`）を最終列。
いずれも `tests/test_schema_conventions.py` の回帰テストで担保している。

v1 の11テーブルは pg_dump アーカイブ（`F:\backups\searching-for-houses-legacy\db_20260901.dump`）
の後に drop 済み。旧データは移行していない（→ ADR 0006）。

### 11.2 市区町村の検索値

v1 はサイトごとに列を持つワイドテーブルだった（ADR 0001）が、賃貸EX追加で
「サイトを増やすたびに DDL 変更（監査カラム末尾維持のためテーブル再作成）が要る」問題が
顕在化したため縦持ちへ転換した（→ ADR 0009）。以後のサイト追加は行の挿入だけで済む。

**市区の検索値には3系統ある**（Phase 2 で2系統、Phase 3 で3系統目が判明）。

| 系統 | サイト | 値の出どころ |
|---|---|---|
| JIS5桁 | SUUMO / GOO / ABLE / CHINTAI_EX / **EHEYA / SMOCCA** | `m_cities.jis_code` から導出 |
| JIS5桁の**下3桁** | **APAMAN**（新宿区 13104 → `104`） | 同上（アダプタが末尾3桁を切る） |
| サイト固有スラグ | HOMES（`tokyo/chiyoda-city`）/ MINIMINI（`chiyodaku`）/ **ATHOME**（`tokyo/adachi-city`）/ **NIFTY**（`adachiku`） | `m_city_site_values` |

JIS系は `m_city_site_values` に行が無くても値を作れる。マッピング表に縛ると
対象4都県253市区のうち **67市区しか指定できず**、東京都は23区だけで多摩地域が
丸ごと落ちていた。そのため JIS系は `m_cities.jis_code` から導出する方式に変えた。

`m_cities.jis_code` 自体も初版は947件中789件が NULL だったため、Phase 2 で
エイブルのエリア索引から実測して473件を補完し、26件（大阪市・福岡市の区で
コードがずれていた）を訂正した。対象4都県で **216/253市区**が指定できる。
残りの未登録分は課題#16。

**`m_city_site_values` に行が存在しない = そのサイトでは当該市区の検索値が未登録。**
市区が必須でないサイトは都道府県レベル検索へフォールバックし、
必須のサイト（ABLE / GOO / CHINTAI_EX / SMOCCA）はその市区を対象から外す。

> **Phase 3 で全サイトの形式が確定した。**
> `m_city_site_values` の初版は EHEYA・SMOCCA について、東京23区の行が JIS5桁、
> それ以外の行がスラグという矛盾した状態だった。実URLで確かめた結果
> **JIS5桁が正**である（`https://www.eheya.net/tokyo/area/13121/search/` /
> `https://smocca.jp/search/tokyo/city/13121`）。
>
> 逆に **ATHOME・NIFTY は初版が JIS5桁で誤り**だった。実際はサイト固有スラグで、
> 各サイトのエリア索引から実測した902行を
> [`db/seed/08_city_site_values_slugs.sql`](../db/seed/08_city_site_values_slugs.sql) に置いた。
> ⚠ ATHOME はボット検知が発動したため**東京都ぶんしか集まっていない**（→ 課題#21）。
> `requires_city=False` なので都道府県単位の検索は動く。

### 11.3 `t_properties` の主要カラム

metric・MUST判定の入力になる数値は型付き列、正規化が未確立の文字列系は JSONB
（`type_specific_attrs`）に置くハイブリッド方式。

| カラム | 用途 |
|---|---|
| `price` / `price_prev` | 現在価格・直前価格（円）。賃貸=月額賃料、売買=物件価格 |
| `price_min` / `price_max` | 価格レンジ（新築の棟単位掲載） |
| `mgmt_fee_monthly` | 管理費・共益費（円/月）。賃貸・マンション売買の双方 |
| `repair_reserve_monthly` | 修繕積立金（円/月）。マンション売買 |
| `rent_total` | **生成列**。`price + COALESCE(mgmt_fee_monthly, 0)`（`price` が NULL なら NULL） |
| `area_sqm` / `land_area_sqm` / `building_area_sqm` | 専有面積 / 土地面積 / 建物面積（㎡） |
| `raw_features_text` | 設備ブロック原文（再抽出の入力） |
| `type_specific_attrs` | 接道・建ぺい率/容積率・権利形態・引渡時期・`price_undecided` 等 |
| `dedup_key` / `group_id` | 名寄せ |
| `detail_fetched_at` | 詳細取得済み判定。NULL が詳細取得キューになる |

### 11.4 新築物件の掲載粒度

新築マンション・新築分譲戸建ては**1物件=1棟/1プロジェクト**で価格がレンジ表示になる。

- `price` にレンジ下限、`price_min`/`price_max` にレンジを入れる
- 価格未定は `price NULL` ＋ `type_specific_attrs.price_undecided = true`
- スコアはレンジ下限で計算し内訳に `"range": true` を記録。価格未定は price metric 欠損として再正規化
- 通知は棟単位。住戸タイプ別の追跡はしない

### 11.5 セットアップ

```powershell
.\scripts\setup_db.ps1
uv run alembic upgrade head
uv run alembic -x test=true upgrade head
uv run house-search db-seed
uv run house-search db-seed --test-db
```

`scripts/setup_db.ps1` は `~/.claude/.env` の管理者資格情報を読んでロールとDBを冪等に作る。

---

## 12. 環境変数（.env）

| キー | 必須 | 説明 |
|---|---|---|
| `DATABASE_URL` | ✅ | 本番DB接続URL（`postgresql+psycopg://user:pass@host:port/dbname`） |
| `DATABASE_TEST_URL` | — | テストDB接続URL。未設定時はDB統合テストをスキップ |
| `DISCORD_WEBHOOK_ERRORS` | ✅ | グローバルエラー通知チャンネル |
| `DISCORD_WEBHOOK_{論理名}` | ✅ | 検索パターンの `webhook_ref` が参照する通知先 |
| `CONFIGS_DIR` | — | 検索パターンYAMLのディレクトリ |
| `DATA_DIR` | — | 設備抽出辞書などGit管理データのディレクトリ |
| `LOG_DIR` | — | 実行ログの出力先 |
| `DEFAULT_MIN_INTERVAL_SEC` | — | サイト個別設定が無い場合のリクエスト間隔（秒） |
| `REQUEST_TIMEOUT_SEC` | — | HTTPリクエストのタイムアウト（秒） |
| `USER_AGENT` | — | スクレイピング時に名乗る User-Agent |

- **Webhook URL は全て `.env` に集約する。** YAMLは `webhook_ref` で論理名を参照し、
  未定義参照は `validate-config` と起動時バリデーションでエラーにする
- 空値項目にインラインコメントを書かない（python-dotenv が `# コメント` を値として読む）

---

## 13. レート制御・robots.txt

**✅ Phase 1 で実装済み。** 実装は `src/house_search/scrape/fetch.py`、
詳細は [`詳細設計書/01_サイト取得設計.md`](./詳細設計書/01_サイト取得設計.md) §5。

- サイトごとに `m_sites.min_interval_sec`（既定2.5秒＋±30%ジッタ）・
  `max_pages_per_run`・`daily_request_cap`
- 429/5xx は指数バックオフ、連続失敗でサイト打ち切り＋エラー通知
- robots.txt はオリジンごとに起動時1回だけ取得し Disallow ならスキップ
- User-Agent は既定で `.env` の `USER_AGENT`。アダプタが宣言したサイトだけ差し替える
  （LIFULL HOME'S は自己申告UAを 403 で拒否するため）
- **能動的なボット検知は突破しない**（→ 課題#17・#18・#20）。
  MINIMINI の reCAPTCHA は Phase 3 で素の Chromium でも試したが通らず、
  **ブラウザに替えても結論は変わらない**ことを実測で確認した
- ボット検知のページは **200 で返ることがある**。そのまま解析すると0件になり
  エラーにならないため、判別できるサイトはアダプタが例外にする（ATHOME で実装）
- **robots.txt を無視するのは APAMAN だけ**。`SiteFetcher.ignore_robots` を
  アダプタが明示的に宣言したときにしか効かず、既定は `False`。
  取得間隔・日次上限・バックオフはこのフラグでも緩めない
  （→ [ADR 0011](./adr/0011-apaman-robots-exception.md)）
- 詳細取得は1回の実行あたりサイト単位で上限（既定40件 / `--full` は400件）。
  取り残しは `detail_fetched_at IS NULL` のキューに残り次回実行で拾われる

---

## 14. エラーハンドリング

- **1サイトがエラー**: 処理継続 ＋ `t_scrape_logs` 記録 ＋ エラーチャンネルへ通知
- **YAML 読み込み失敗**: そのパターンをスキップ ＋ エラーチャンネルへ通知
- **Discord API 失敗**: `t_notifications.status='failed'` で記録（リトライは Phase 1 で判断）

---

## 15. プロジェクト構成

```
f:\searching-for-houses\
├── src/house_search/
│   ├── cli.py                  # サブコマンド
│   ├── config/
│   │   ├── settings.py         # .env 読み込み（pydantic-settings）
│   │   ├── metrics.py          # MetricRegistry（metric・MUST項目の一元管理）
│   │   └── pattern.py          # 検索パターンYAML v2 の型定義
│   ├── db/
│   │   ├── base.py             # DeclarativeBase・監査カラムMixin
│   │   ├── session.py          # エンジン・セッション
│   │   ├── seed.py             # マスタデータ投入
│   │   └── models/
│   │       ├── masters.py      # m_* 8テーブル
│   │       └── transactions.py # t_* 9テーブル
│   ├── scrape/
│   │   ├── fetch.py            # レート制御・リトライ・robots.txt
│   │   ├── base.py             # 共通型とパース補助
│   │   ├── area.py             # 検索対象エリア（都道府県・市区）の解決
│   │   ├── prefectures.py      # 都道府県名 → URLスラグ
│   │   ├── suumo.py / homes.py / goo.py / able.py / chintai_ex.py
│   │   ├── athome.py / eheya.py / nifty.py / apaman.py / smocca.py
│   ├── extract/
│   │   ├── normalize.py        # NFKC正規化・トークン化
│   │   ├── dictionary.py       # 辞書のロードとDB同期
│   │   └── extractor.py        # 辞書照合・導出・未知表記
│   ├── scoring/
│   │   ├── property_view.py    # 採点の入力（不変オブジェクト）
│   │   ├── must.py             # MUST 3値判定
│   │   └── score.py            # WANTスコア
│   ├── notify/
│   │   ├── discord.py          # Webhook送信
│   │   └── format.py           # Embed・ダイジェスト整形
│   └── pipeline/
│       ├── runtime.py          # 実行時オブジェクト一式
│       ├── persist.py          # upsert・キュー・ログ
│       ├── scan.py             # scan の本体
│       └── tasks.py            # digest / rescore / check-sold / re-extract
├── migrations/                 # Alembic
├── db/seed/                    # マスタデータSQL（冪等）
├── configs/                    # 検索パターンYAML（examples/ は読み込み対象外）
├── data/feature_dictionary.yaml # 設備抽出辞書（正典）
├── scripts/setup_db.ps1        # DB・ロール作成（冪等）
├── tests/
│   ├── test_metrics.py
│   ├── test_pattern.py
│   ├── test_settings.py
│   ├── test_schema_conventions.py   # DB規約の回帰テスト
│   ├── test_extract.py / test_scoring.py / test_notify.py / test_fetch.py
│   ├── test_area.py / test_persist.py
│   ├── test_scrape_{suumo,homes,goo,able,chintai_ex}.py       # 実HTMLフィクスチャ
│   ├── test_scrape_{athome,eheya,nifty,apaman,smocca}.py
│   └── fixtures/{10サイト}/                                    # 実HTML（一覧・詳細）
├── docs/
├── alembic.ini                 # ASCIIのみ（cp932環境で落ちるため）
├── pyproject.toml
└── .env / .env.example
```

---

## 16. 依存ライブラリ

| ライブラリ | 用途 |
|---|---|
| `sqlalchemy` 2.x | ORM・スキーマ定義 |
| `alembic` | マイグレーション |
| `psycopg[binary]` 3.x | PostgreSQL ドライバー |
| `pydantic` / `pydantic-settings` | 設定・YAMLスキーマ検証 |
| `httpx` | HTTP取得 |
| `lxml` + `cssselect` | HTMLパース（CSSセレクタ） |
| `pyyaml` | YAML読み込み |
| 開発: `pytest` / `pytest-cov` / `ruff` | テスト・lint |

パッケージ管理は `uv`（`uv sync` / `uv run`）。

---

## 17. テスト方針

- **HTMLフィクスチャ方式**: 各サイトの一覧・詳細ページの実HTMLを `tests/fixtures/{site}/` に保存し、
  パーサ・抽出・スコアリングをネットワークなしでユニットテストする（Phase 2〜）
- **DB統合テスト**は `DATABASE_TEST_URL` 設定時のみ実行（未設定ならスキップ）
- **DB規約の回帰テスト**: 列順・コメント・テーブル集合を `information_schema` / `pg_description` で固定
- **実データ充足率の実測**（`coverage` コマンド）を各Phaseの完了条件に組み込み、
  「実装済みだが未配線」を防ぐ

---

## 18. 参考

- 移行の全体像・Phase構成・リスク → [`再設計計画.md`](./再設計計画.md)
- 設計判断の記録 → [`adr/`](./adr/)
- 未解決の課題 → [`課題管理表.md`](./課題管理表.md)
- サイト別の検索フォーム調査資料 → [`詳細設計書/資料_サイト別検索条件一覧.md`](./詳細設計書/資料_サイト別検索条件一覧.md)
