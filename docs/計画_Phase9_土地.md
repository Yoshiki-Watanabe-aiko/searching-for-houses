# Phase 9: 土地（TOCHI）を6種別目として追加する 実装計画書

> 状態: **承認済み・9a 実装済み**（2026-09-11）。2026-09-11 に Plan サブエージェント（Opus）が読み取りのみで作成。
> §12 の論点1・2・12・13・14 はすべて推奨どおりに決まった（→ 課題#61）。論点3〜11 は各段の直前に聞く。
> ⚠ 実装中に1点を追加した: §7 手順8 の「雛形だけを置いた一時ディレクトリで validate-config」を流すと、
> 孤児スコアの確認が稼働中の6パターンすべてに DELETE を案内していた。実運用の configs 以外では確認を省くようにした（→ ADR 0024）。


- 作成日: 2026-09-11
- 基準コミット: `65d6e5b`
- 調査はすべて読み取りのみで行いました。実サイトには一度もアクセスしていません。DB は SELECT だけで、書き込み・マイグレーション・`sync-*`・`rescore` は実行していません。
- 凡例:
  - 【実測】ファイル・DB の SELECT・ローカル計算で確かめた事実
  - 【推測】根拠のある見込み
  - 【未測定】まだ確かめていないこと

---

## 0. 結論

1. **推奨は、骨格（9a）だけを単独の PR で先に入れる進め方です。** アダプタから定期取得まで（9b〜9e）は、実サイトでの着手前実測を済ませてから別の PR に分けます。
   - 9a はネットワークにも DB 書き込みにも触れないので、いま走っている SUUMO の掃き出しと並行して進められます。
   - 9a が終われば、Phase 9 の完了条件（再設計計画 §11「雛形 YAML が validate-config を通り、既存パターンの config_hash が変わらない」）を満たします。
2. **テーブルの再作成は要りません【実測】。**
   - `m_property_types.family` は varchar(20) で、CHECK 制約はありません。制約は PK・`UNIQUE(code)`・NOT NULL だけです。
   - シードは `ON CONFLICT (code) DO UPDATE` です。行を1つ足すだけで済みます。
   - `m_market_rates` にも CHECK 制約はありません。
3. **`BUY_TYPES` には TOCHI を入れません。**
   - `price` と `price_max` の2箇所だけを `BUY_TYPES | TOCHI_TYPES` へ明示的に広げます。
   - `BUY_TYPES` 自体に入れると、`layouts` と `market_rate_ratio` が黙って土地にも使えるようになります。
4. **9a で塞ぐ「黙って壊れる」箇所（実測で見つけたもの）**
   - `dedup/key.py:68-73` は KODATE_BUY 以外を `else` で「間取り＋専有面積＋階」として扱っています。土地は間取りが無いので、**キーが常に None になり、例外も出ずに名寄せされません**。
   - MUST の `features` は「詳細取得済みで抽出結果に無ければ fail」という判定です（`scoring/must.py:131-134`）。土地の設備辞書は空なので、土地パターンに `features` を1つでも書くと、**詳細を取得した全件が fail になります**。
   - `tests/test_metrics.py` の `:45`（`walk_minutes == ALL`）に加えて、`:41`（`price == BUY_TYPES`）と `:122`（`market_rate_ratio == {CHINTAI} | BUY_TYPES`）も、**左右が同時に変わるので検出できない形**です。
   - config_hash を固定しているのは**賃貸2本だけ**です（`tests/test_buy_foundation.py:32-38`）。売買4本は固定されていません。
5. **9b 以降で塞ぐもの（実測で見つけたもの）**
   - `notify/format.py:146` の `_BUY_FAMILIES` には**すでに `"TOCHI_BUY"` が入っています**。このままだと土地の通知に「管理費等 不明」と出ます。
   - `scrape/suumo_kodate.py:174` は `/tochi/` を含むリンクを捨てています。土地アダプタをこのクラスの派生で書くと、**0件のまま正常終了します**。
   - `t_listings` の一意キーは `(site_id, external_id)` で、種別を含みません。UPSERT も `property_type_id` を更新しません。SUUMO の売買4種別の ID はすべて `nc_` と8桁で、**同じ名前空間を共有しています**。
   - 土地の取引相場（国交省 XIT001 の「宅地(土地)」）は、市区の中央値が **354〜3,803,077 円/㎡（約1万倍）** に広がり、**10市区が `MIN_RATE = 5,000` を下回ります**。相場生成に土地をそのまま足すと、`build_buy_market_rates.py` が停止します。

---

## 1. スコープの比較と推奨

| | A: 骨格だけで止める（9a） | B: 縦切りを一括（9a〜9e） | **推奨: A を先に入れ、B の残りを段階的に** |
|---|---|---|---|
| 中身 | マスタ行・レジストリ・`TochiBuyPattern`・dedup の分岐・雛形 YAML・テスト・ドキュメント | A に加え、SUUMO 土地アダプタ・辞書・通知・（相場比）・seed・配置・定期取得 | 9a → 9b アダプタ → 9c 辞書 → 9d 相場比（任意）→ 9e 運用投入 |
| 実サイト | 不要 | 着手前実測が必須。**いまは掃き出し中なので測れない** | 9a は今すぐ着手でき、9b は掃き出しの終了後 |
| DB 書き込み | `db-seed` で1行足すだけ（掃き出し後でよい） | seed・掃き出しで数千〜数万行 | 同上 |
| 原因の切り分け | 既存6本のハッシュと名寄せキーの不変だけを見ればよい | ハッシュ・順位・件数が動いたとき、原因の候補が多い | 課題#4 の「売買と混ぜない」判断をそのまま Phase 9 の内部にも当てはめる |
| ユーザー判断 | ほぼ不要（§12 の 1・2・12・13） | 9件以上（金額表示・建築条件付き・タスク配置・相場比など） | 判断は各段の直前にまとめて聞く |
| 利点 | 小さく安全で、完了条件に一致する | 通知が早く届く | 各段の完了条件が数値で閉じる |

**推奨理由**
- Phase 9 の完了条件は A そのものです。
- アダプタは実測なしに書けません（ADR 0015「推測で書かない」）。そして実測はいまできません。
- `ALL_PROPERTY_TYPES` の意味を変える作業とスクレイパーの追加を同じ PR に混ぜると、ハッシュや順位が動いたときに原因が2系統になります。

**指示の外で1つ提案します。** 参照箇所をコメントで1つずつ判定する方法だけに頼らず、**レジストリの対応表をまるごとリテラルで固定するテスト**（全 metric と全 MUST 項目の「使える種別の集合」の表）を置くことを勧めます（§7 手順1）。こうしておくと、将来 `ALL_PROPERTY_TYPES` や `BUY_TYPES` の中身が変わったときに、差分が必ずテストの書き換えとしてレビューに現れます。

---

## 2. 参照箇所の棚卸し【実測】

src 側で `ALL_PROPERTY_TYPES` / `BUY_TYPES` / `MANSION_TYPES` / `KODATE_TYPES` を参照しているのは `config/metrics.py` だけでした。pipeline・scoring・scrape・scripts からの参照は0件で、9/5 の測定と一致します。tests 側は `tests/test_metrics.py` だけです。

### 2.1 `ALL_PROPERTY_TYPES`（コードでの参照は16箇所。9/5 時点の15から1つ増えました）

| ファイル:行 | 対象 | TOCHI を含めるか | 理由 |
|---|---|---|---|
| metrics.py:25 | 定義 | **加える** | 6種別目として登録する |
| metrics.py:142 | metric `walk_minutes` | 含める | 駅徒歩は土地にもある |
| metrics.py:150 | metric `commute_minutes` | 含める | 駅から駅なので種別に依らない |
| metrics.py:166 / 176 / 184 / 194 | `flood_rank_avg` / `flood_area_ratio` / `landslide_area_ratio` / `liquefaction_rank_avg` | 含める | 住所で引く値で、購入なので売買と同じ扱いにする（ADR 0021・0023） |
| metrics.py:278 | MUST `walk_minutes_max` | 含める | 同上 |
| metrics.py:282 | MUST `commute_minutes_max` | 含める | `MustBase` にあるので、含めないと `test_pattern.py:584` が落ちる |
| metrics.py:291 / 302 | MUST `flood_rank_max` / `landslide_special_ratio_max` | 含める | 同上（`MustBase` にある） |
| metrics.py:329 | MUST `features` | **含める。ただし validate-config に辞書との照合を足すことが条件**（§12 論点2） | `MustBase` にあるので、外すなら `layouts` と同じくクラス側へ降ろす必要がある。含めると辞書が空の間は全件 fail の危険がある → §5.7 の防御で塞ぐ |
| metrics.py:255, 258 | コメント（layouts） | 文言を更新 | 「現時点の値は ALL と同一集合」が偽になる |
| tests/test_metrics.py:13 | `spec.property_types <= ALL` | そのまま | 有効な検査 |
| tests/test_metrics.py:45 | `walk_minutes == ALL` | **6種別のリテラル集合に書き換える** | 左右が同時に変わるので検出できない |
| tests/test_metrics.py:109 | `set(FAMILY_OF) == ALL` | そのまま | `FAMILY_OF` への足し忘れを捕まえる、有効なガード |
| tests/test_metrics.py:125 | `!= ALL or len(ALL) == 5` | 削除し、`TOCHI not in` へ置き換える | TOCHI 追加の前後どちらでも通るので、ガードとして機能していない |
| tests/test_metrics.py:117 | コメント | 更新 | — |

### 2.2 `BUY_TYPES` / `MANSION_TYPES` / `KODATE_TYPES` と明示集合

| ファイル:行 | 対象 | TOCHI | 変更後 |
|---|---|---|---|
| metrics.py:28 | `BUY_TYPES` の定義 | **入れない** | コメントに「建物を伴う売買4種別。⚠ TOCHI を入れない（layouts・相場比が土地へ開く）」と明記する |
| metrics.py:92 | metric `price` | 含める | `BUY_TYPES \| TOCHI_TYPES` |
| metrics.py:217 | metric `market_rate_ratio` | **9a では含めない**（9d で判断） | そのまま |
| metrics.py:243 | MUST `price_max` | 含める | `BUY_TYPES \| TOCHI_TYPES` |
| metrics.py:259 | MUST `layouts` | **含めない** | そのまま（`BUY_TYPES` が TOCHI を含まない限り安全） |
| metrics.py:117 / 270 | `building_area_sqm` / `building_area_min`（KODATE_TYPES） | 含めない | そのまま |
| metrics.py:125 / 269 | `land_area_sqm` / `land_area_min`（KODATE_TYPES） | 含める | `KODATE_TYPES \| TOCHI_TYPES` |
| metrics.py:101 / 109 / 247 / 264 / 267 / 322 | MANSION_TYPES 系（管理費・専有面積・所在階） | 含めない | そのまま（明示集合なので自動的に外れる） |
| metrics.py:84 / 134 / 242 / 274 / 315 | 賃料・築年・相場比下限など明示集合 | 含めない | そのまま |
| tests/test_metrics.py:41 | `price == BUY_TYPES` | 意図した変更 | 5種別のリテラルに書き換える |
| tests/test_metrics.py:122 | `market == {CHINTAI} \| BUY_TYPES` | 左右が同時に変わる | 5種別のリテラルにし、`TOCHI not in` を足す |

### 2.3 ファミリの文字列リテラルと集合（src・scripts・seed）

| ファイル:行 | 中身 | 9a | 9b 以降 |
|---|---|---|---|
| dedup/key.py:32-34, 68-73 | `if KODATE: … else: 間取り＋面積＋階` | **土地の分岐を追加し、else を明示的な分岐と例外にする** | — |
| extract/dictionary.py:33-37 `FAMILY_SECTIONS` | chintai / common（3ファミリ）/ buy（MANSION+KODATE） | **触らない** | 9c で `tochi` セクションを新設し、m_condition_property_types の線引きから機械的に決める |
| scoring/anomaly.py:63 `_BUY_FAMILIES`、:149 `== "KODATE_BUY"` | 閾値と表示 | 触らない。土地に相場比が無い間は `is_price_anomaly` が常に False【実測: :124】 | 9d で TOCHI_BUY を追加し、面積表示を土地面積にする |
| notify/format.py:146 `_BUY_FAMILIES` | **すでに TOCHI_BUY を含む** | 触らない（土地の掲載がまだ無い） | 9b で土地の分岐を入れる（管理費欄を出さない・土地面積を出す・「築年不明」を出さない） |
| notify/format.py:254-262 `_summary_line` | `area_sqm` と築年だけ | — | 土地だと「— / — / 築年不明」になる（戸建てでも面積は「—」になっている既存の穴） |
| market/rates.py:111 `BUY_FAMILIES` | CSV 取り込みの検証 | 触らない（想定外のファミリなら例外＝目に見える失敗） | 9d |
| pipeline/persist.py:809-822 | 相場比の CASE 式 | 触らない。**未知のファミリは NULL を返す**（安全側）【実測】 | 9d で `WHEN 'TOCHI_BUY' THEN p.price / NULLIF(p.land_area_sqm,0)` と segment `'LAND_SQM'` を追加 |
| pipeline/tasks.py:237, 245 | `pt.family = :family` | 変更不要（パラメータ化済み） | — |
| dedup/groups.py:72, 107 | `land_area_sqm` も SELECT 済み | 変更不要 | — |
| cli.py:64-66, 94-96 | `choices=[f.value for f in Family]` | 変更不要（Enum から TOCHI_BUY が自動で加わる） | — |
| cli.py:312、pipeline/scan.py:903、config/pattern.py:334 | `pattern.family` / `FAMILY_OF` | `FAMILY_OF` に登録すれば解決する。登録を忘れると KeyError（目に見える失敗） | — |
| scoring/listing_view.py:73、db/models/masters.py:44-47 / 224-227 / 623-626 | コメント中のファミリ列挙 | 文言を更新（DB 側のコメントは §5.6 の任意のマイグレーション） | — |
| db/seed/01_property_types.sql | 5行 | **`('TOCHI','土地','TOCHI_BUY',6)` を追加** | — |
| db/seed.py:21 `EXPECTED_MIN_ROWS["m_property_types"]` | 5 | **6 に上げる**（5 のままだと、seed を当て忘れても `>=` 判定で黙って通る） | — |
| db/seed/05_condition_property_types.sql | LAND_* 8条件は戸建て・マンションにだけ紐づいている | 触らない | 9c で TOCHI 行を追加（辞書のセクションと同時に） |
| scripts/tools/build_buy_market_rates.py:52-54, 65, 163-169 | 「宅地(土地)」は使っていない | 触らない | 9d |
| scripts/task_runner.ps1:72, 91, 94, 107 | `--family` 指定 | 触らない | 9e（`scan-buy` に TOCHI_BUY を足すか） |
| scripts/run_market_then_drain.ps1:34 | 既定は MANSION_BUY と KODATE_BUY | 触らない（土地は `-Family TOCHI_BUY` で明示する） | — |
| scripts/task_runner.ps1:102 `sweep` | **`--family` 無しなので全パターン** | — | 土地を `configs/` 直下に置いた時点で、週次の棚卸しに自動で入る |

### 2.4 tests 側で影響するもの

| ファイル:行 | 対応 |
|---|---|
| test_pattern.py:555 `_PATTERN_CLASSES` | `TochiBuyPattern` を追加する。これで MUST とレジストリの双方向検査（:572-596）が土地にも効く |
| test_pattern.py:200-207 | 雛形の読み込みの一覧に `tochi_buy_v2.yaml` を追加する |
| test_pattern.py:221-229 | `configs/` 直下のファイル一覧。9a では変えない（9e で直下へ置くときに更新） |
| test_cli.py:120-122 `--family TOCHI` を弾く | **このまま有効**（`TOCHI` は `TOCHI_BUY` ではない）。`TOCHI_BUY` を受け付けるテストを追加する |
| test_buy_foundation.py:32-38 | 基準ハッシュを6本に広げる（§5.3） |
| test_dedup_key.py | 既存3ファミリの基準値（ハッシュそのもの）＋土地の分岐 |
| test_dictionary_coverage.py:50-69 | `configs/examples` と MUST の `features` も照合対象にする |
| test_schema_conventions.py:150 | `EXPECTED_MIN_ROWS` が 6 になるので、テスト DB に seed を当て直す |

---

## 3. テーブル再作成が要らないことの確認【実測】

- **DDL**（`migrations/versions/2026_09_01_1621-118b7160b30d_initial_schema_v2.py:48-57`）では `family` は `String(20)`、NOT NULL です。
- **DB の実物**（`pg_constraint`）: `m_property_types` の制約は `pk_m_property_types`・`uq_m_property_types_code`・NOT NULL の各列だけで、**CHECK 制約はありません**。`m_market_rates` にも CHECK 制約はありません。
- **DB の中身**: 現在は5行（CHINTAI / SHINCHIKU_MANSION / CHUKO_MANSION / SHINCHIKU_KODATE / CHUKO_KODATE、sort_order 1〜5）です。`TOCHI` と `TOCHI_BUY` はどちらも20文字以内に収まります。
- **シード**（`db/seed/01_property_types.sql`）は `ON CONFLICT (code) DO UPDATE` なので、行を足すだけで冪等です。
- **`t_listings`** は `land_area_sqm`・`type_specific_attrs`（JSONB）・`price_min`/`price_max` をすでに持っています。建ぺい率・容積率・接道・用途地域は、戸建てと同じく JSONB に入れます（`suumo_kodate.py:243-255` の前例）。
- **結論**: 列の追加も再作成もありません。DB への書き込みは「`db-seed` で1行足す」だけです。コメント文言の更新をするなら COMMENT だけのマイグレーションを足しますが、これは任意です（§5.6）。

---

## 4. 実測で得た事実のうち、他の節で使うもの

### 4.1 現在の config_hash（`65d6e5b`・`configs/` 直下の6本・2026-09-11 に YAML から計算）

| パターン | 種別 | config_hash |
|---|---|---|
| 東京23区賃貸 | CHINTAI | `80a59af050a2306bab541e025a28ff0e23a9199f9c649fbb8d8f4da0c5bfef2a`（テストで固定済み） |
| 近郊60分圏賃貸 | CHINTAI | `2c3869bd61f9cfc4aa604cde11b172f9fc07ebe7733619a380c43067e53bd096`（テストで固定済み） |
| 中古一戸建て | CHUKO_KODATE | `fa3cadaacfdc31490f3f5807e1b3d749a89f7ecc1f1437f32d38cd64d319bf43`（**未固定**） |
| 中古マンション | CHUKO_MANSION | `00a8612c09683689d01cdee36a84b02a612a67b9b977bf592c7794fb32fccef3`（**未固定**） |
| 新築一戸建て | SHINCHIKU_KODATE | `fd6be1af039a4c4ff619f36883959a3726a260cdfb3b897ff400dd79edd9589f`（**未固定**） |
| 新築マンション | SHINCHIKU_MANSION | `3738c14dc49540ba97f6f5a4160df0c549554358a871f63d78bba30127dcebee`（**未固定**） |

### 4.2 robots の判定（オフライン）

判定条件:
- `tests/fixtures/robots/suumo.txt` を `RobotsRules.parse` に通しました。
- UA は既定値の `house-search/2.0 (personal property watcher)` です。
- ⚠ フィクスチャの robots は取得時点のものです。実行時は取得処理が本物の robots を読み直します。

| 候補URL | 判定 |
|---|---|
| `/tochi/tokyo/sc_chiyoda/` | 許可 |
| `/tochi/tokyo/sc_chiyoda/?po=1&pj=2` | 許可 |
| `/tochi/tokyo/sc_chiyoda/?po=1&pj=2&page=2` | 許可 |
| `/tochi/kanagawa/sc_yokohamashiaoba/?po=1&pj=2&page=3`（スラグは形を見るための仮の値） | 許可 |
| `/tochi/tokyo/sc_suginami/nc_20810245/`（実在リンク） | 許可 |
| `/tochi/tokyo/sc_suginami/nc_20810245/?fmlg2=r014` | 許可 |
| `/tochi/tokyo/` | 許可 |
| `/tochi/tokyo/sc_chiyoda/?po=1&pj=2&sort=1` | **禁止**（`/*?*sort=`） |
| `/jj/bukken/ichiran/JJ010FJ001/?ar=030&bs=030&ta=13&sc=13101` | **禁止**（旧形式の一覧。売買と同じく SEO パスしか使えない） |
| 参考: `/ms/chuko/tokyo/sc_chiyoda/?po=1&pj=2&page=2`、`/chukoikkodate/tokyo/sc_hachioji/?po=1&pj=2&page=2` | 許可 |

### 4.3 保存済み HTML から分かったこと

保存先は `data/probe/suumo_buy/`（Git 管理外）です。

- 土地の詳細 URL は2本見つかりました。形は **`/tochi/{pref}/sc_{slug}/nc_{8桁}/`** です。
  - `city_shinchiku_k.html:1392` → `/tochi/tokyo/sc_suginami/nc_20810245/?fmlg2=r014`。見出しに「建築条件付き」とあり、8,980万円、杉並区方南1、京王線 笹塚 徒歩10分です。
  - `hs_tb_100.html:3473` → `/tochi/tokyo/sc_hachioji/nc_75327402/?fmlg=bo001`
  - どちらも**ピックアップ枠（`mediabox`）の中**で、`div.property_unit` ではありません。
- **土地の一覧ページと詳細ページそのものは1枚も保存されていません【未測定】。**
- 売買一覧の「他の種別」リンクに、`/tochi/tokyo/sc_hachioji/`・`sc_chiyoda`・`sc_minato`・`sc_itabashi` がありました（tests/fixtures/suumo_buy と suumo_kodate）。**土地も同じ `sc_{slug}` を使っています**。Tokyo の4市区で確認しました。
- DB 上の SUUMO 売買の掲載で、URL に `/tochi/` を含むものは **0件**です。戸建てアダプタの除外が効いています。
- SUUMO 売買の external_id は4種別とも `nc_` と8桁（11文字）で、同じ名前空間です。件数は中古戸建て11,189・中古マンション13,277・新築戸建て5,938・新築マンション909です。

### 4.4 国交省 XIT001 の「宅地(土地)」

ローカルの原典 JSON を、相場生成スクリプトと同じ直近4四半期（2025Q2〜2026Q1、16ファイル）で数えました。

- 取引は **13,802件** です（埼玉 3,710 / 千葉 3,062 / 東京 3,726 / 神奈川 3,304）。
- 価格区分は **100% が「不動産取引価格情報」** で、成約価格情報は0件です。したがって `PRIMARY_STAT` は STAT_01 しか選べません。
- `UnitPrice` は100%埋まっています。`TradePrice` と `Area` が数値にならない行は0件です。
- n≥10 の市区は **219**（埼玉 66/71・千葉 52/59・東京 48/59・神奈川 53/57）です。
- ⚠ 市区の㎡単価の中央値は、**最小が 354 円/㎡（比企郡鳩山町）、最大が 3,803,077 円/㎡（港区）** です。**10市区が `MIN_RATE = 5,000` を下回り**、下位3つはすべて郡部です。

### 4.5 取得速度と既存の件数（DB）

- SUUMO は `min_interval_sec = 2.5`、`max_pages_per_run = 5` です。
- `t_listings` の件数は CHINTAI 26,999 / KODATE_BUY 17,127 / MANSION_BUY 14,186 です。

---

## 5. 設計（9a）

### 5.1 `config/metrics.py`

```python
TOCHI = "TOCHI"
ALL_PROPERTY_TYPES = frozenset({CHINTAI, SHINCHIKU_MANSION, CHUKO_MANSION, SHINCHIKU_KODATE, CHUKO_KODATE, TOCHI})
# ⚠ 建物を伴う売買4種別。TOCHI を入れない（layouts・market_rate_ratio が土地へ開く → 課題#61）
BUY_TYPES = frozenset({SHINCHIKU_MANSION, CHUKO_MANSION, SHINCHIKU_KODATE, CHUKO_KODATE})
TOCHI_TYPES = frozenset({TOCHI})

class Family(StrEnum):
    ...
    TOCHI_BUY = "TOCHI_BUY"

FAMILY_OF[TOCHI] = Family.TOCHI_BUY
```

- `price` と `price_max` は `BUY_TYPES | TOCHI_TYPES` にします。
- `land_area_sqm` と `land_area_min` は `KODATE_TYPES | TOCHI_TYPES` にします。
- それ以外は §2 の表のとおりです。
- 結果として、土地で使える metric は **8つ** です（定義順）: `price`, `land_area_sqm`, `walk_minutes`, `commute_minutes`, `flood_rank_avg`, `flood_area_ratio`, `landslide_area_ratio`, `liquefaction_rank_avg`。
- 土地で使える MUST は **7つ** です: `price_max`, `land_area_min`, `walk_minutes_max`, `commute_minutes_max`, `flood_rank_max`, `landslide_special_ratio_max`, `features`。
- このうち一覧だけで判定できる3つ（price・land_area_sqm・walk_minutes）は、`scan._listing_view` がすでに渡しています【実測: scan.py:140, 147, 155】。したがって **`_listing_view` と `tests/test_first_stage_view.py` は変更不要**です。

### 5.2 `config/pattern.py`

```python
class TochiBuyMust(MustBase):
    """土地売買の MUST。⚠ 建物の概念が無いので間取り・専有面積・建物面積・築年数・所在階は置かない。"""
    price_max: int | None = Field(default=None, description="物件価格の上限（円）")
    land_area_min: float | None = Field(default=None, description="土地面積の下限（㎡）")

class TochiBuyPattern(PatternBase):
    """土地売買の検索パターン。"""
    property_type: Literal["TOCHI"]
    must: TochiBuyMust = Field(default_factory=TochiBuyMust)

SearchPattern = Annotated[ChintaiPattern | MansionBuyPattern | KodateBuyPattern | TochiBuyPattern, Field(discriminator="property_type")]
```

- `MustBase` から受け継ぐのは `commute_minutes_max`・`flood_rank_max`・`landslide_special_ratio_max`・`walk_minutes_max`・`features`・`unknown_policy` です。
- `layouts` は Phase 6 ですでに `MustBase` から降ろしてあるので、土地の YAML に書くと `extra="forbid"` で**例外になります**（目に見える失敗）。
- `site_filters.axes` も、TOCHI の Must に無い軸は `pattern.py:388-392` が弾きます。

### 5.3 config_hash を変えないための禁止事項

- **禁止**: `WantSpec` / `NumericWant` / `FeatureWant` / `CommuteSpec` にフィールドを足すこと、型や既定値を変えること。
  - ハッシュの入力は `property_type`・`want.model_dump()`・`commute` だけです（`pattern.py:395-417`）。
  - 既定値付きのフィールドでも `model_dump` の出力に現れるので、**既存6本のハッシュがすべて変わり、全件の再採点が走ります**。
- **影響しないもの**: `MustBase` や各 Must クラスへの項目追加、`RankingSpec`・`SearchSpec`、レジストリ（metrics.py）の変更は、どれもハッシュの入力に入りません。
- 担保の方法:
  - `test_buy_foundation.py` の `BASELINE_CONFIG_HASH` を §4.1 の6本に広げます。
  - `validate-config` が出力する `config_hash[:12]` の6本（`80a59af050a2` `2c3869bd61f9` `fa3cadaacfdc` `00a8612c0968` `fd6be1af039a` `3738c14dc495`）を目視で照合します。

### 5.4 名寄せキー（`dedup/key.py`）

```python
FAMILY_TOCHI_BUY = "TOCHI_BUY"
...
if family == FAMILY_KODATE_BUY:
    parts = [_area(land_area_sqm), _area(building_area_sqm), normalize_layout(layout)]
elif family == FAMILY_TOCHI_BUY:
    # 建物が無いので住所（丁目まで）＋土地面積だけ。⚠ 分譲地の同一丁目・同一面積の
    # 隣接区画は1グループに潰れる（既知の限界。賃貸の同一仕様別住戸 → 課題#13 と同型）
    parts = [_area(land_area_sqm)]
elif family in (FAMILY_CHINTAI, FAMILY_MANSION_BUY):
    parts = [normalize_layout(layout), _area(area_sqm), _int(floor_num)]
else:
    raise ValueError(f"名寄せキーを組めない未知のファミリです: {family!r}")
```

- **`DEDUP_KEY_VERSION` は v2 のまま上げません**（`key.py:29` で確認しました）。
  - 土地のキーは新しい名前空間（`v2|TOCHI_BUY|…`）なので、既存のキーとは交わりません。
  - 版を上げると全件の `regroup` が要ります。
  - 課題#4 の記述「`v1|TOCHI_BUY|…`」は v2 に直します。
- `else` を例外にしても、既存の3ファミリの経路は字面が同じです。DB にあるファミリも3つだけでした【実測】。
- **既存のキーが1ビットも変わらないことの担保**
  1. 基準値テスト: CHINTAI・MANSION_BUY・KODATE_BUY の代表入力を2件ずつ用意し、**HEAD の時点で計算した sha256 の16進値**を固定します。変更の前も後も通ることを確かめます。
  2. DB での照合（読み取りのみ・任意）: 全掲載について `normalize_address` と `compute_dedup_key` を新しいコードで計算し直し、保存値と違う件数を数えます。**HEAD で数えた件数と変更後の件数が一致する**ことを確かめます（理想は0件）。

### 5.5 雛形 YAML（`configs/examples/tochi_buy_v2.yaml`。⚠ `configs/` 直下には置かない）

```yaml
# 土地（TOCHI・Phase 9）の雛形。⚠ 配点・best/worst・MUST はすべて暫定。
# 4都県の母集団（MUST 通過）を seed で集めてから p10〜p90 に合わせる（→ 課題#31・#34）。
# ⚠ `name:` は配置したら変えない。⚠ 直下へ置く前に run_initial_scan.ps1 -ConfigsDir で scan --seed を流す（ADR 0006）
name: "土地"
property_type: "TOCHI"
webhook_ref: "TOCHI"
digest_webhook_ref: "TOCHI_DIGEST"
sites: ["SUUMO"]
search:
  prefectures: ["東京都", "千葉県", "埼玉県", "神奈川県"]
  cities: []                 # スラグのある市区へ自動展開（郡部は未対応 → §6.2）
  price_max_hint: null
  site_filters: { enabled: false, axes: [], exclude_sites: [] }
commute:
  destination_station: "芝公園"
  destination_prefecture: "東京都"
must:
  price_max: 80000000        # 暫定
  land_area_min: 60.0        # 暫定（狭小地を外す）
  walk_minutes_max: 20       # バス便は徒歩 NULL → unknown → keep
  features: []               # ⚠ 土地の設備辞書が無い間は書かない（全件 fail になる）
  unknown_policy: keep
want:
  features: []               # 同上（全件 miss で分母にだけ乗る）
  numeric:
    - { metric: price,                 weight: 25, best: 15000000, worst: 60000000 }  # 暫定
    - { metric: land_area_sqm,         weight: 25, best: 200,      worst: 80 }        # 暫定
    - { metric: walk_minutes,          weight: 10, best: 3,        worst: 20 }
    - { metric: commute_minutes,       weight: 10, best: 40,       worst: 90 }
    - { metric: flood_rank_avg,        weight: 25, best: 0,        worst: 4 }         # 売買と同じ（ADR 0021）
    - { metric: landslide_area_ratio,  weight: 10, best: 0,        worst: 0.1 }
    - { metric: liquefaction_rank_avg, weight: 10, best: 1,        worst: 4.5 }       # ADR 0023
ranking: { top_n: 15, digest_group: null, notify_max_rank: 200 }
```

- 価格と土地面積の best/worst は【推測】の暫定値です。戸建て（p10 1,780万・p90 7,000万、土地 p10 58・p90 197）から建物代を引いた見当で置いています。
- `market_rate_ratio` は 9a では土地に使えないので書けません。書くと validate が弾きます。

### 5.6 シードとマスタのコメント

- `db/seed/01_property_types.sql` に `('TOCHI','土地','TOCHI_BUY',6)` を足し、ヘッダのコメントを「6種別」に直します。
- `db/seed.py` の `EXPECTED_MIN_ROWS["m_property_types"]` を 6 にします。
- 任意: `masters.py` の3列のコメント（ファミリの列挙）を更新し、`op.alter_column(..., comment=...)` だけの Alembic マイグレーションを足します。表の再作成はありません。
- ⚠ DB への適用（`db-seed` / `alembic upgrade`）は、**いま走っている掃き出しが終わってから**行います。書き込みは短いものですが、ロックが競合する余地を残さないためです。

### 5.7 設備辞書との整合（`features` を含める条件）

- 9a では `FAMILY_SECTIONS` も `05_condition_property_types.sql` も**触りません**。`test_dictionary_coverage` のマスタとの整合検査はそのまま通ります。
- 代わりに **`validate-config` へ「WANT と MUST の条件コードが、そのファミリの辞書（と導出コード）にあるか」の照合を足します。**
  - これは全ファミリに効く改善です。いまは pytest を回したときにしか捕まらず、しかも対象が `configs/` 直下の WANT だけです。
  - 土地の辞書は空なので、土地パターンに `features` を1つでも書けば **NG で止まります**。
  - `DERIVED_CODES`（現在は test 側のローカル定数）は `extract/extractor.py` 側へ公開定数として移します。
- `test_dictionary_coverage.py:50` の対象に `configs/examples/*.yaml` と MUST の `features` を加えます。

---

## 6. SUUMO 土地アダプタの設計（9b 以降）

### 6.1 URL

| 用途 | 形 | 根拠 |
|---|---|---|
| 一覧 | `https://suumo.jp/tochi/{pref}/sc_{slug}/?po=1&pj=2`、2ページ目以降は `&page=N` | パスの形は【実測】（§4.3）、robots は許可【実測】。⚠ `po=1&pj=2` が土地でも新着順として効くかは【未測定】 |
| 詳細 | 一覧のリンクをそのまま使う（`/tochi/{pref}/sc_{slug}/nc_{8桁}/`） | 形は【実測】（2本） |
| 市区選択 | `/tochi/{pref}/city/`（売買の `/ms/chuko/{pref}/city/` に倣う） | 【推測】 |

### 6.2 市区スラグをそのまま使えるか

- **Tokyo の4市区では同じスラグでした【実測】。**
- 13番のシードのヘッダには「既存23行と食い違い0件＝スラグは種別に依らず共通」とあります（中古マンションの市区ページで採取したもの）。
- ⚠ **郡部は対象外です。** スラグ収集では「郡」単位のリンク14件を捨てています（ADR 0014）。土地は郡部に多いと見込まれます【推測】。§4.4 の土地単価の下位3つはすべて郡でした。
  - `resolve_areas` はスラグの無い市区を**黙って落とす**ので、4都県 251市区のうち173市区だけを見ていることになります。
  - 9b では173市区のまま進め（§12 論点10）、`/tochi/{pref}/city/` のリンク集合と173市区の差を実測して記録します。

### 6.3 一覧と詳細で取れる項目（すべて【推測】で、要実測）

| 項目 | 一覧（`div.property_unit` の dt/dd と推定） | 詳細（仕様表と推定） | 格納先 |
|---|---|---|---|
| 物件名 | 無いこともある | ○ | `title` |
| 販売価格 | 「1980万円」「1500万円～2000万円」「未定」 | ○ | `price`（レンジは下限）・`price_min`/`price_max`・`type_specific_attrs.price_undecided` |
| 所在地 | ○ | ○ | `address` |
| 沿線・駅 | ○（バス便あり） | 交通 | `station_info` と `walk_minutes`（バスなら NULL） |
| 土地面積 | 「100.5m2（30.40坪）」「100m2～120m2」 | ○（登記・実測・私道負担の注記） | `land_area_sqm`（下限）と原文を JSONB へ |
| 坪単価 | あるかもしれない | ○ | ⚠ 価格と取り違えない。保存しない |
| 建ぺい率・容積率 | あるかもしれない | ○ | JSONB（戸建てと同じキー名） |
| 用途地域・地目・土地の権利形態・私道負担・道路・接道状況・建築条件・セットバック・引渡し時期・現況 | — | ○ | JSONB。見出しは**文言で**拾う |
| 設備の段落 | — | **有無は【未測定】** | `raw_features_text`（見出しの文言で拾う `features_text` を使い回す） |
| 間取り・建物面積・築年 | 無い | 無い（建築プラン例は拾わない） | 入れない |

### 6.4 特殊なケースの扱い（案）

| ケース | 扱い |
|---|---|
| 価格未定 | `price = NULL`、`price_undecided = true`。価格があるときは **`false` を明示**（JSONB の `\|\|` マージで値が残るのを防ぐ → PR #109） |
| 価格・面積のレンジ（分譲地・区画売り） | 下限を `price`・`land_area_sqm` へ入れ、レンジは `price_min`/`price_max` へ（要件定義書 §11.4 に合わせる）。この形の掲載はレンジ用の分岐を常に通す |
| 建築条件付き | **取り込む**。原文を JSONB に入れ、通知には明示する。除外するなら「否定の MUST」が今は無いので新しい MUST 項目の設計が要る（§12 論点5）。⚠ **訂正（2026-09-12）: 9c では `LAND_BUILD_CONDITION` を抽出しない**（ユーザー判断 2026-09-11「表示専用のまま」）。判定は詳細の仕様表の「建築条件」欄（`build_condition`）で行う。実例2件の原文に「建築条件」の語は無く、逆に条件なしの掲載に「建築条件なし」のタグが付くので、辞書に入れると条件なしを拾う（→ 課題#61） |
| 借地権 | 取り込む。`土地の権利形態` を JSONB へ。相場比を入れるときは異常な安値に見える（課題#50 と同型） |
| 私道負担・セットバック | 原文を JSONB へ。有効面積の補正はしない（`land_area_min` が甘めに効く既知の限界として記録する） |
| 古家付き・更地渡し | 土地として取り込む。建物系の metric は無い |
| 再建築不可 | JSONB へ。将来の除外 MUST の候補 |
| バス便 | `walk_minutes = NULL` → unknown → keep（課題#58 と同じ。`walk_minutes_of` を使い回す） |
| 坪しか書いていない表記 | `parse_area_sqm` が坪を㎡に換算するか、または拒否するかを実測フィクスチャで固定する |

### 6.5 アダプタ固有の「黙って壊れる」危険

1. **`_SuumoKodateScraper` の派生で書くと、`/tochi/` 除外（`suumo_kodate.py:174`）を継承して0件になります。**
   - 別モジュール `scrape/suumo_tochi.py` にし、部品の関数（`list_fields`・`read_spec_table`・`features_text`・`parse_price_range`・`walk_minutes_of`・`station_info`）だけを共用します。
   - 土地の一覧では逆に「`/tochi/` を**含む**リンクだけ」を採ります。
   - フィクスチャで「1ページあたり18件以上」の下限を検査するテストを置きます。
2. **種別をまたいで external_id が衝突する危険。** 一意キーが `(site_id, external_id)` なので、同じ `nc_` が戸建てと土地で出てくると、UPSERT が**戸建ての行の価格や面積を土地の値で上書きし、種別は戸建てのまま**になります（`persist.py:200-230`）。
   - UPSERT に `WHERE t_listings.property_type_id = EXCLUDED.property_type_id` を足し、見送った件数を `RETURNING` で数えてログに出します。
   - seed の前に、実測で集めた土地の `nc_` と既存の SUUMO の ID の重なりを SELECT で数えます。
3. **`po=1&pj=2` が土地で効かない危険。** 効かないと、増分スキャン（1ページ目だけ）が新着を黙って取りこぼします。`zzz=1` の対照と並べて並び順を実測します。
4. **坪と㎡の取り違え。** 値が約3.3倍ずれても例外になりません。
5. **詳細の見出しの揺れ**（戸建てでは `secTitleInnerR` と `secTitleInnerK` が混在していました → 課題#4）。クラス名ではなく**文言で**拾います。
6. **掲載終了の判定。** 戸建ては HTTP 404 でしたが、土地は【未測定】です。200 のまま「掲載終了」を表示する作りなら、成約を検知できません。
7. **郡部が抜ける**（§6.2）。
8. **土地の辞書が空の間は、詳細の本文がすべて「未知表記」として記録され続けます。** 辞書を育てる入力が土地の語で埋まるので、9c まで土地の未知表記は集計で分けて見ます。

### 6.6 着手前に実サイトで測る項目

- ⚠ **別の SUUMO プロセス（掃き出し・scan）が止まっているときだけ実施します。**
- ⚠ `probe_suumo_buy.py` は `pg_advisory_lock` を取りません。実施前にプロセスとログを確認してください。可能ならプローブにロックを取らせる改善を先に入れます。

| # | 測るもの | 方法（ADR 0015 に沿って） | 件数 |
|---|---|---|---|
| 1 | 市区選択ページのスラグの集合（郡を含む）と173市区の差 | `/tochi/{pref}/city/` ×4都県。保存して `collect_city_slugs.py --from-cache` で解析する | 4 |
| 2 | 一覧の DOM・1ページの件数・`po/pj` の効き方 | 都心（例: 世田谷区）・**バス便を含む郊外（八王子市）**・郡部に近い市（例: 印西市）の3市区で、①既定 ②`?po=1&pj=2` ③`?po=1&pj=2&page=2` ④**`?zzz=1`（効かないキーの対照）** を比べる。**効くと分かっているキー（`page`）も同時に動かす** | 12 |
| 3 | 詳細の仕様表の見出しの文言・設備段落の有無 | 通常の売地・建築条件付き・借地権・分譲地（区画レンジ）・価格未定・古家付き。**一覧の DOM が同じでも詳細は別物でありうる**ので、それぞれ1件ずつ取る | 6〜8 |
| 4 | 掲載終了の応答 | 一覧から消えた ID を1件（404 か、200 の文言か） | 1 |
| 5 | robots.txt の再取得 | 取得処理の実行時読み込みで確かめる（フィクスチャとの差分も見る） | 1 |
| 6 | 3市区の総件数（一覧の見出しに出る件数） | #2 と同じページから読む | 0 |

- **合計は約25リクエスト**で、2.5秒間隔なら約1分です。
- ✅ **2026-09-11 に実施（25リクエスト）。結果は課題#61「9b の着手前実測」が正典。** 本節と §6.1〜§6.5 の【推測】【未測定】は、そちらの実測で読み替える（借地権・再建築不可は未測定のまま）。
- **取得した HTML はすべて** `data/probe/suumo_tochi/` にメタ情報の JSON と一緒に保存し、`--from-cache` で解析だけやり直せるようにします。
- テスト用フィクスチャは、この保存物から `tests/fixtures/suumo_tochi/` へ移します。

### 6.7 取得量の見積もり（土地の掲載数は【未測定】で、戸建てと同じ桁と仮定）

| 場面 | リクエスト数 | 所要（2.5秒間隔） |
|---|---|---|
| 初回 seed の一覧（最大5ページ×173市区） | 最大865 | 約36分 |
| 詳細の掃き出し（仮に1万件） | 約10,000 | 約7時間（`run_initial_scan.ps1 -Drain -Family TOCHI_BUY`） |
| 日次の増分（1ページ×173＋詳細200＋掲載終了の確認40） | 約413 | **約17分** |
| 週次の棚卸し（`sweep` に自動で入る。5ページ×173＋詳細400） | 約1,265 | 約53分（上限 PT10H。現在の所要は要確認） |

### 6.8 定期タスクの置き場所

- **推奨: `scan-buy` の鎖に `--family TOCHI_BUY` を足す**（`task_runner.ps1:90-95`）。
  - 所要は約61分から**約78分**に伸びます。上限 PT1H40M（100分）には22分の余裕が残ります。
  - 11:15 の賃貸スキャンはすでに毎日飛ばされているので、新たに失うものはありません。
  - **タスクの再登録（管理者権限と `-EnableScraping`）が要りません。**
- 代案: 別タスク `HouseSearch-ScanTochi` を立てる。切り分けやすく、単独で止められますが、再登録が要ります。

---

## 7. 実装手順

### 9a 骨格（1つの PR・ネットワークなし）

| # | 作業 | 完了条件（数値） |
|---|---|---|
| 0 | 基準値を採る（読み取りのみ） | ①`uv run pytest -q` の失敗0件と通過件数を記録。②6本の config_hash が §4.1 と一致。③（任意）DB で名寄せキーを計算し直し、保存値と違う件数を記録 |
| 1 | **守りのテストを先に書く**（HEAD のままで通ること） | ①`BASELINE_CONFIG_HASH` を6本に。②名寄せキーの基準値（3ファミリ×2件）。③**レジストリのスナップショット**（全 metric と全 MUST 項目の種別集合をリテラルで固定）。④`test_metrics.py:41/45/122` をリテラル集合に書き換え。⑤seed の SQL を正規表現で読み、`FAMILY_OF` と照合するテスト（DB 不要）。**追加分がすべて通る**。さらに意図的に壊して確かめる: `NumericWant` に既定値付きのフィールドを一時的に足すと**ハッシュのテストが6本とも落ちる**／`BUY_TYPES` に要素を一時的に足すとスナップショットが落ちる → 元に戻す |
| 2 | **土地の期待を書いたテストを先に書き、落ちることを確かめる** | ①土地の metric が §5.1 の8つ、MUST が7つ（完全一致）。②`layouts`・`market_rate_ratio`・`area_*`・`building_*`・`age_*`・`floor_min`・`monthly_cost_*` に TOCHI が無い。③土地パターンに `layouts`/`area_min`/`building_area_min`/`age_max` を書くと ValidationError、WANT に `area_sqm`/`building_area_sqm`/`age_years`/`market_rate_ratio` を書くと ValueError。④名寄せ: 土地は住所＋土地面積で組む／面積が欠けたら None／戸建てと同じ住所・同じ土地面積でも別キー／未知のファミリは例外。⑤`--family TOCHI_BUY` を受け付ける。⑥雛形が読める。⑦validate-config が辞書に無い条件コードを NG にする。**これらが修正前に全部落ちること**（AttributeError・ImportError・assert）を確かめ、落ちた件数を記録する。⚠ ④の「土地は住所＋土地面積」は、**現行コードでは else に落ちて None が返るので落ちる**。これが黙って壊れる箇所の実証になる |
| 3 | `metrics.py`（§5.1） | 手順2の①② が通る。スナップショットの差分が「TOCHI が加わった13箇所」だけ（metric 8・MUST 7 のうち、定義行を除く） |
| 4 | `pattern.py`（§5.2）。`_PATTERN_CLASSES` に追加 | 手順2の③⑥ と `test_pattern.py:572-596` が土地でも通る |
| 5 | `dedup/key.py`（§5.4） | 手順2の④と、手順1の基準値（既存3ファミリ）が**両方通る** |
| 6 | seed の SQL と `EXPECTED_MIN_ROWS = 6`（コメントのマイグレーションは任意） | 手順1の⑤が6行で通る |
| 7 | validate-config に辞書との照合を入れる（§5.7）。`DERIVED_CODES` を src へ移す | 手順2の⑦が通る。`configs/` 直下の6本は **6本とも OK**（既存に偽陽性が出ない） |
| 8 | `configs/examples/tochi_buy_v2.yaml`（§5.5） | `validate-config --configs-dir <雛形だけ置いた一時ディレクトリ> --skip-webhook` が OK・終了コード0。webhook の値を入れたなら、`--skip-webhook` 無しでも OK |
| 9 | `.env.example` に `DISCORD_WEBHOOK_TOCHI` と `DISCORD_WEBHOOK_TOCHI_DIGEST` を足す（プレースホルダ。空値の行にインラインコメントを書かない規約に従う） | — |
| 10 | ドキュメント（§9）・ADR 0024・課題 | — |
| 11 | 総合確認 | `pytest -q` の失敗0件（件数は手順0から増えるだけ）。`validate-config` の6本のハッシュ先頭12桁が §5.3 と一致。`ruff check` に違反なし。`rg -n "ALL_PROPERTY_TYPES\|BUY_TYPES" src tests` の結果が §2 の表と一致 |
| 12 | **掃き出しが終わってから**（DB 書き込み・人が実行）。✅ 2026-09-11 適用済み（→ 課題#61） | `uv run house-search db-seed` → `m_property_types` が **6行**。テスト DB にも seed を当て、`test_schema_conventions` が通る。（任意）DB での名寄せキーの差が手順0と同じ件数 |

### 9b SUUMO 土地アダプタ（§6.6 の実測の後・1つの PR）

1. 実測（§6.6）を行い、HTML を保存し、フィクスチャに移します。
2. **テストを先に書き、落ちることを確かめます。** 対象は、一覧の件数の下限、価格・面積・レンジ・未定、バス便が NULL になること、JSONB のキー、`is_sold`、URL の組み立て、`SCRAPERS[("SUUMO","TOCHI")]` の登録、そして**戸建ての一覧が相変わらず `/tochi/` を捨てること**です。
3. `scrape/suumo_tochi.py` と `scrape/__init__.py` への登録。
4. UPSERT の種別ガード（§6.5-2）。**既存の売買4種別で見送りが0件**になることを DB で確かめます。
5. `notify/format.py` の土地分岐（金額欄・1行要約）。純関数のテストを書きます。

完了条件: 追加したテストが修正前に落ち、修正後に通ること。`scan --seed --pattern 土地` の一時ディレクトリ実行で、3市区の一覧の件数が実測の総件数と一致すること（1ページ目の件数）。

- ✅ **2026-09-11 に実装・検証済み**（→ 課題#61「9b の実装」）。一覧60件＝20件×3市区で一致・1639テスト緑。
- ⚠ 手順4の「UPSERT の種別ガード」は**ファミリ単位**に絞った。SUUMO は新築と中古で `nc_` を使い回す実例が本番に2件あり、「種別が違えば見送る」と稼働中の売買の挙動が変わるため（→ 課題#62）。

### 9c 土地の設備辞書

- `05_condition_property_types.sql` に TOCHI の行（LAND_* 8条件のほか、実測で当たる LOC_*・ライフライン系）を足します。
- `FAMILY_SECTIONS` に `tochi` を新設し、`data/feature_dictionary.yaml` に `tochi:` を足します。
- `test_dictionary_coverage` のマスタ整合検査を TOCHI_BUY にも広げます。
- ✅ **2026-09-12 に実装**（→ 課題#61「9c の実装」）。⚠ ユーザー判断（2026-09-11）で上の案から変えた点:
  TOCHI の紐づけは LAND_* 8条件ではなく**辞書で使う7条件だけ**（角地・南道路・即引渡し可・都市ガスと、
  新設の整形地・前道6m以上・更地渡し）。建築条件付きは辞書に入れない（§6.4 の訂正）。平坦地は入れない。

### 9d 土地の相場比（任意・§12 論点3）

- `build_buy_market_rates.py` に「宅地(土地)」→ TOCHI_BUY/LAND_SQM/STAT_01 を足し、**`MIN_RATE` をファミリごとにします**（そのままでは10市区で停止します）。
- `rates.py` と `persist.py` の CASE 式、metrics の相場比の集合、`anomaly.py` を更新します。
- seed 後の母集団で、価格と相場比の順位相関 |r|<0.6 のゲートと、異常閾値を実測してから配点します。

### 9e 運用投入

1. Discord チャンネルを作り、`.env` に値を入れます。
2. `validate-config`（webhook も確認）を通します。
3. `run_initial_scan.ps1 -ConfigsDir <一時ディレクトリ>` で `scan --seed` を流します。
4. 掃き出し（`-Drain -Family TOCHI_BUY`）を行います。
5. best/worst を p10〜p90 に合わせます。
6. `configs/tochi.yaml` に配置し、`test_pattern.py:221` の一覧を更新します。
7. `task_runner.ps1` の `scan-buy` に TOCHI_BUY を足します。
8. 初回実行の所要を実測します。

---

## 8. 影響するファイル

- **9a**:
  - `src/house_search/config/metrics.py`
  - `src/house_search/config/pattern.py`
  - `src/house_search/dedup/key.py`
  - `src/house_search/cli.py`（validate-config の辞書照合）
  - `src/house_search/extract/extractor.py`（`DERIVED_CODES` の公開）
  - `src/house_search/db/seed.py`
  - `db/seed/01_property_types.sql`
  - `configs/examples/tochi_buy_v2.yaml`（新規）
  - `.env.example`
  - `tests/test_metrics.py`・`test_pattern.py`・`test_dedup_key.py`・`test_buy_foundation.py`・`test_cli.py`・`test_dictionary_coverage.py`
  - 新規 `tests/test_property_type_seed.py`
  - コメントのみ: `scoring/listing_view.py`、`db/models/masters.py`（＋任意のマイグレーション）
- **9b**:
  - `src/house_search/scrape/suumo_tochi.py`（新規）
  - `scrape/__init__.py`
  - `pipeline/persist.py`（UPSERT の種別ガード）
  - `notify/format.py`
  - `scripts/tools/probe_suumo_buy.py`（kind の追加。ロックも取らせるなら）
  - `tests/fixtures/suumo_tochi/`・`tests/test_scrape_suumo_tochi.py`
- **9c**: `db/seed/05_condition_property_types.sql`、`data/feature_dictionary.yaml`、`extract/dictionary.py`
- **9d**: `scripts/tools/build_buy_market_rates.py`、`market/rates.py`、`pipeline/persist.py`、`scoring/anomaly.py`、`config/metrics.py`
- **9e**: `configs/tochi.yaml`（新規）、`scripts/task_runner.ps1`、`tests/test_pattern.py`

---

## 9. ドキュメントの更新

| 文書 | 9a | 9b 以降 |
|---|---|---|
| `docs/requirements.md` §3 | 表に `TOCHI \| 土地 \| TOCHI_BUY` を追加。「5種別」→「6種別」 | §3.1 のセル数 31→32（アダプタ投入時） |
| 同 §5.3 | metric 表に「土地」列を追加。`BUY_TYPES` に TOCHI を入れない理由を明記 | 9d で相場比の土地欄 |
| 同 §5.4 | MUST の土地欄。`features` と辞書照合（validate-config） | — |
| 同 §8 | 名寄せキーの構成に土地（住所＋土地面積）と既知の限界を追加 | — |
| 同 §12 | `DISCORD_WEBHOOK_TOCHI` / `_DIGEST` | — |
| 同 §4.4・§9 | — | タスク配置・土地の金額表示 |
| `docs/再設計計画.md` §11 | Phase 9 の行を「9a 完了」に。完了条件の「既存2パターン」を「既存6パターン」に直す。9b〜9e の段を追加 | 各段の完了で更新 |
| `docs/課題管理表.md` | **新しく課題#61「土地（TOCHI）の追加」を立てる**（推奨）。#4 の③（:110-124）には「→ 課題#61」の1行だけ足し、`v1` を `v2` に直す。#4 は数千行あるので追記しない | 実測の記録 |
| `docs/adr/0024-...md`（新規） | **要る**。決定は3つ: ①TOCHI_BUY ファミリを足し、`BUY_TYPES` は「建物を伴う売買」の意味に固定する、②土地の名寄せキー＝住所＋土地面積（v2 のまま・隣接区画が潰れる限界）、③`features` は全種別に置くが、validate-config が辞書に無いコードを弾く | 9d で相場比を入れるなら決定を追記 |
| `CLAUDE.md` の「実装上の注意」 | 「`BUY_TYPES` に TOCHI を入れない」「土地の辞書が無い間は `features` を書かない（validate-config が弾く）」を1行ずつ | 土地の掃き出し手順 |
| `CONTEXT.md` | 用語「土地（TOCHI）/ TOCHI_BUY」 | — |

---

## 10. リスク（黙って壊れるかで分類）

| リスク | 黙って壊れるか | 段 | 対策 |
|---|---|---|---|
| 名寄せが else に落ちてキーが None になる | **黙る** | 9a | 明示的な分岐と例外、テストで RED を確認 |
| `BUY_TYPES` に TOCHI を入れて layouts・相場比が土地に開く | **黙る** | 9a | 定義にコメント、スナップショットと `TOCHI not in` のテスト |
| テストの左右が同時に変わる（:41/:45/:122） | **黙る** | 9a | リテラル集合に書き換え |
| 売買4本の config_hash が変わる | **黙る**（全件の再採点・順位が動く） | 9a | 基準値を6本に。Want 系のモデルは変更禁止 |
| 土地の `features` が全件 fail／全件 miss | **黙る** | 9a〜9c | validate-config の辞書照合、テンプレートは `[]` |
| `EXPECTED_MIN_ROWS` が 5 のままで seed の当て忘れを見逃す | **黙る** | 9a | 6 に上げる |
| seed の未適用で `property_type_ids["TOCHI"]` が無い | 目に見える（KeyError） | 9e | 手順12 |
| `FAMILY_OF` への登録忘れ | 目に見える（KeyError・test:109） | 9a | — |
| 雛形を `configs/` 直下に置いて二重通知 | 目に見える（テスト:215） | — | 既存のテスト |
| 戸建ての派生にして `/tochi/` 除外を継承、0件 | **黙る** | 9b | 別モジュール、件数の下限テスト |
| 種別をまたいだ external_id の衝突で戸建ての行が上書きされる | **黙る** | 9b | UPSERT の種別ガードと見送り件数のログ、事前の重なり SELECT |
| `po/pj` が効かず新着を取りこぼす | **黙る** | 9b | `zzz=1` の対照で実測 |
| 坪と㎡の取り違え | **黙る** | 9b | フィクスチャで固定 |
| 郡部が落ちる（173/251） | **黙る** | 9b | 差を実測して記録。対応は別の課題 |
| 掲載終了が 404 ではない | **黙る**（成約を検知できない） | 9b | 実測 #4 |
| 通知に「管理費等 不明」「築年不明」が出て土地面積が出ない | **黙る**（誤読の元） | 9b | 土地の分岐と純関数テスト |
| 土地相場の `MIN_RATE` で相場生成が停止 | 目に見える（SystemExit） | 9d | ファミリごとの下限 |
| 土地単価の分散が大きく（約1万倍）、相場比が雑音になる | **黙る** | 9d | 相関ゲートと母集団分布を見てから配点 |
| 掃き出し中にプローブを並走させ、実効間隔が半分になる | **黙る**（サイトに負荷） | 9b | 実施条件の明記、プローブにロック |
| 長時間の scan 中に YAML を変えて配点を失う（課題#59） | **黙る** | 9e | 配置は scan の終了後、終了後に `rescore` |
| `ScanBuy` の所要が上限に近づく | 目に見える（強制終了） | 9e | 初回の実行ログで実測し、上限を見直す |

## 11. セキュリティ上の懸念

- **Webhook URL**: YAML には論理名しか書きません。`.env.example` はプレースホルダだけで、空値の行にインラインコメントを付けない規約を守ります。値の有無は出力に出しません。
- **robots とレート**: レート制御はプロセスの中にしかありません。**SUUMO へ同時に2つのプロセスを走らせない**ことが唯一の防御なので、プローブにも取得ロックを取らせる改善を勧めます。
- **SQL**: 相場比の CASE 式への追加は固定のリテラルで、利用者の入力は入りません。UPSERT のガードもバインド変数だけです。
- **取得した HTML**: lxml で解析するだけで、実行はしません。保存先の `data/probe` は Git 管理外です。個人情報は含みません（掲載の公開情報だけ）。
- **Discord に出す文字列**: 土地の見出しは既存と同じ整形経路を通ります。9b で表示を変えるときも、既存のエスケープを外しません。
- **新たな認証情報**: 要りません。

---

## 12. ユーザー判断が要る論点（それぞれ推奨を先頭に置いています）

1. **スコープ**: ①9a を単独の PR で先に入れ、9b〜9e は実測の後に分ける（推奨）／②縦切りを一括。
2. **`features` MUST の土地への適用**: ①全種別に置いたまま、validate-config で辞書に無いコードを NG にする（推奨。全ファミリの取り違えも拾える）／②`layouts` と同じくファミリごとのクラスへ降ろし、土地には置かない（WANT 側の穴は残る）。
3. **土地の相場比**: ①9d で入れる。ただし seed 後に相関ゲートと異常閾値を実測してから配点し、`MIN_RATE` はファミリごとにする（推奨）／②入れない。
4. **土地の通知の金額表示**: ①価格とローン返済額を出し、管理費欄は出さず、「土地のみ・建物代別」と注記する（推奨）／②ローンを出さない。→ ✅ **①に決定（2026-09-11）**
5. **建築条件付き・借地権・再建築不可**: ①取り込んで JSONB と通知に明示し、除外 MUST は後で設計する（推奨）／②すぐ除外 MUST を新設する（否定条件の MUST は今は無く、設計が要る）。→ ✅ **①に決定（2026-09-11）**
6. **パターン名（`name:`・後から変えない）**: ①「土地」（推奨）／②「土地（4都県）」などの別名。
7. **MUST の初期値**: ①`price_max` 8,000万円・`land_area_min` 60㎡・`walk_minutes_max` 20分（暫定・推奨）／②戸建てと同じく1億円・40㎡。
8. **通勤先**: ①他の売買と同じ芝公園（推奨）／②変える。
9. **定期取得の置き場所**: ①`scan-buy` に `--family TOCHI_BUY` を足す（再登録不要・所要が約78分に伸びる。推奨）／②別タスク `HouseSearch-ScanTochi`（管理者による再登録と `-EnableScraping` が要る）。
10. **郡部**: ①9b は173市区のまま進め、郡のリンクの扱いは別の課題にする（推奨）／②9b で郡まで対応する（ADR 0014 の拡張が要る）。→ ✅ **①に決定（2026-09-11）**。⚠ 郡とは別に、`m_cities` にあるのにスラグが無い市町が13件あると分かった（課題#61。足すと既存の売買4パターンにも広がる）
11. **Discord チャンネル（TOCHI / TOCHI_DIGEST）を作る時期**: ①9e の前（推奨。9a の完了は `--skip-webhook` で判定）／②いま作って、9a の完了条件に webhook の確認も含める。
12. **課題の起票**: ①新しく課題#61を立て、#4 からは1行で参照する（推奨）／②#4 に追記する。
13. **ADR 0024 を書くか**: ①書く（推奨）／②書かない。
14. **マスタの列コメントの更新（COMMENT だけのマイグレーション）**: ①9a に含め、掃き出しの後に適用する（推奨）／②9d でまとめて行う。

---

### Critical Files for Implementation
- f:\searching-for-houses\src\house_search\config\metrics.py
- f:\searching-for-houses\src\house_search\config\pattern.py
- f:\searching-for-houses\src\house_search\dedup\key.py
- f:\searching-for-houses\tests\test_metrics.py
- f:\searching-for-houses\tests\test_buy_foundation.py
