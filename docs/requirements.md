# 物件検索通知システム v2 要件定義書

**作成日**: 2026-06-16（v1）/ **v2改訂**: 2026-09-02（Phase 5 着手）
**言語/スタック**: Python 3.12+ / PostgreSQL 18 / Discord Webhook

> **本書の状態**: Phase 5（賃貸の本運用）を**再設計中**。
> 初回全件スキャン（8,429掲載）の実測で、**ランキング上位が群馬/栃木県境と
> 房総半島で埋まり、東京の掲載が上位15件に1件も入らない**ことが判明した（→ 課題#24）。
> 原因はスコアに立地の概念が無いことで、**エリア帯**を導入して検索パターンを
> 2つに分割した（→ [ADR 0013](./adr/0013-area-bands.md)・[CONTEXT.md](../CONTEXT.md)）。
> Phase 5C で**通勤時間**をランキングへ組み込んだ（→ [ADR 0016](./adr/0016-commute-time-rail-graph.md)）。
> 帯ごとの初回取得とタスク登録（→ 課題#23）が残っている。
> Phase 4 以降で確定した仕様を各Phase完了時に本書へ反映していく。
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
| ATHOME | アットホーム | ✅ 実装済み。**パズル認証が発動したままのため Phase 5 で `is_active=false`** → 課題#20 |
| NIFTY | ニフティ不動産 | ✅ 実装済み（市区指定必須・他社掲載を集約するポータル） |
| GOO | goo不動産 | ✅ 実装済み（市区指定必須） |
| CHINTAI_EX | 賃貸EX | ✅ 実装済み（Phase 5 で**本採用**。近郊帯でユニーク率63% → 課題#5） |
| ABLE | エイブル | ✅ 実装済み（市区指定必須・価格上限が効かない） |
| MINIMINI | minimini | **取得手段なし**（HTTPでもブラウザでもreCAPTCHA）。`is_active=false` → 課題#18 |
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
| `scan --detail-limit` | 詳細取得の上限（サイトあたり）を上書きする。省略時 40 / `--full` 時 400 | ✅ Phase 5 |
| `scan --site` | 対象サイトを1つに絞る | ✅ Phase 1 |
| `check-sold` | 成約/掲載終了の確認 | ✅ Phase 1 |
| `digest` | 日次ランキングダイジェストの送信（`--dry-run` で件数確認） | ✅ Phase 1 |
| `rescore` | DB内の物件属性から再採点（ネットワーク不要） | ✅ Phase 1 |
| `sync-dict` | `data/feature_dictionary.yaml` → `m_condition_synonyms` | ✅ Phase 1 |
| `re-extract` | `raw_features_text` から全件再抽出（ネットワーク不要） | ✅ Phase 1 |
| `report-unknown` | 辞書未登録の表記を出現回数順に一覧 | ✅ Phase 1 |
| `coverage` | サイト別の設備抽出数分布・数値カラム非NULL率の実測 | ✅ Phase 2 |
| `regroup` | 名寄せキーを全件作り直してグループを同期（ネットワーク不要・**通知なし**） | ✅ Phase 4 |
| `resolve-cities` | 既存掲載の `city_id` を現在の `m_cities` で引き直す（ネットワーク不要） | ✅ Phase 5A |
| `sync-site-params` | `data/site_search_params.yaml` → `m_site_search_params` | ✅ Phase 5B |
| `sync-stations` | `data/train_master/*.csv` → `m_stations`（駅マスタ） | ✅ Phase 5C |
| `resolve-stations` | 掲載の駅表記を駅マスタと突き合わせる（ネットワーク不要） | ✅ Phase 5C |
| `resolve-commutes` | 駅ペアの通勤所要時間を算出してキャッシュ（ネットワーク不要） | ✅ Phase 5C |
| `fetch-commutes` | NAVITIME の乗換案内から**実ダイヤ**の通勤時間を取得（要ネットワーク・約15秒/駅） | ✅ Phase 5D |
| `commute-stats` | 通勤時間の分布を実測（best/worst を決める材料） | ✅ Phase 5C |
| `dedup-stats` | サイト別のキー充足率・クロスサイト重複率・ユニーク率の実測 | ✅ Phase 4 |

**Phase 2 で全コマンドが実装済みになった。**
`scan` はアダプタ未実装のサイトと無効化されたサイトを「スキップ」として明示的に報告する
（黙って無視すると「実装済みだが未配線」を見逃すため）。
`scan` は**アダプタ未実装のサイトを「スキップ」として明示的に報告する**。

### 4.3 シードモード

**初回全件取得を通知なしの記録専用モードで走らせる**ことで、旧通知履歴を捨てても
「再掲載が全部新着として再通知される」問題が構造的に発生しなくなる。
パターン新規追加時・長期停止からの再開時にも使う汎用機能。→ ADR 0006

### 4.4 タスクスケジューラー構成

**✅ Phase 5 で確定。** 登録は [`scripts/register_tasks.ps1`](../scripts/register_tasks.ps1)、
実体は [`scripts/task_runner.ps1`](../scripts/task_runner.ps1)。

| タスク名 | トリガー | 実行 | 実行時間上限 |
|---|---|---|---|
| `HouseSearch-Scan` | **2時間ごと・01:15起点** | `task_runner.ps1 -Task scan` | PT1H50M |
| `HouseSearch-Sweep` | **毎週日曜 02:00** | `-Task sweep`（`scan --full`） | PT10H |
| `HouseSearch-CheckSold` | 毎日 09:00 | `-Task check-sold` | PT1H |
| `HouseSearch-Digest` | 毎日 20:00 | `-Task digest` | PT30M |
| `HouseSearch-Backup` | 毎日 03:30 | `-Task backup` → `backup_db.ps1` | PT30M |

⚠ **HOME'S の取得間隔を 2.5 → 10秒へ広げた**（2026-09-03・ユーザー判断 → 課題#17）。
スロットリングで本番でもほとんど取れていなかったため。**最悪72分ぶん伸びうる**ので、
運用開始後にスキャン全体の所要を実測し、`MultipleInstances=IgnoreNew` で
スキップが起きていないかを確かめること。

**毎時ではなく2時間ごとにした理由**: 増分スキャンは実測ベースで**約72分**かかる
（一覧1116リクエスト＋詳細320リクエスト・サイト直列）。毎時トリガーだと前回の終了前に
次が起動し、`MultipleInstances=IgnoreNew` でスキップされて開始時刻が不定に揺れる。

**01:15 起点にした理由**: **レート制御は `SiteFetcher` のプロセス内にしかない。**
別プロセスの `scan` と `check-sold` が並走すると同一サイトへの実効間隔が半分になる。
奇数時+15分起点なら 07:15（〜08:27）と 09:15 の間に 09:00 の check-sold が収まる。
20:00 のダイジェストは 19:15 の scan と重なるが、DB読み＋Webhook送信だけで
スクレイピングしないため問題ない。

共通設定: `MultipleInstances=IgnoreNew` / `StartWhenAvailable=true` /
**アイドル条件は付けない**（`RunOnlyIfIdle` を付けると手動の `schtasks /run` でも
`Queued` のまま走らない。このシステムはレート待ちの sleep が大半でCPUをほぼ使わない）。

**登録には管理者権限が要る**（2026-09-02 実測）。`LogonType=S4U`（ログオフ中も実行・
パスワード保存なし）のタスク作成には `SeTcbPrivilege` が必要で、標準ユーザーで実行すると
`schtasks` が「アクセスが拒否されました」で失敗する。**そのため `register_tasks.ps1` は
自己昇格する**（UAC で管理者アカウントの資格情報を入力する）。
`-DryRun`（XML生成と整形式検査だけ）は権限が要らないので昇格しない。

⚠ **自己昇格するとタスクの実行アカウントがずれる罠がある。** 標準ユーザーが UAC で
管理者の資格情報を入力すると**昇格後のプロセスはその管理者として動く**ため、
`WindowsIdentity::GetCurrent()` をそのまま使うとタスクが管理者アカウントで登録される。
昇格前の利用者名を `-TaskUser` で子プロセスへ引き継いで回避している。
**登録後は `schtasks /query /tn HouseSearch-Scan /fo LIST /v` の「実行ユーザー」を確認すること。**

**登録は初回全件スキャンの完了後に有効化する。** 取得を伴う2本（Scan / CheckSold）は
`<Enabled>false</Enabled>` で登録され、`register_tasks.ps1 -EnableScraping` で有効化する。
初回スキャンと並走させないための措置（上記のプロセス内レート制御の話と同じ理由）。

登録に `Register-ScheduledTask`（PowerShell の CIM 経由）は使わない。自分自身のタスクを
登録するだけでも 0x80070005 で拒否されることがあるため、**XMLを UTF-16 で書き出して
`schtasks /create /XML`** で登録する（UTF-8 だと読めない）。

---

## 5. 検索パターン設定（YAML v2）

### 5.1 ファイル配置

- 既定: リポジトリ直下の `configs/*.yaml`（環境変数 `CONFIGS_DIR` で変更可）。
  **サブディレクトリは読まない**（`glob("*.yaml")` は非再帰）
- 実運用: **エリア帯ごとに1本**（Git管理下。課題#9 解消）
  - [`configs/chintai_23ku.yaml`](../configs/chintai_23ku.yaml) — 東京23区（23市区）
  - [`configs/chintai_suburb60.yaml`](../configs/chintai_suburb60.yaml) — 近郊60分圏（59市区）
- 雛形: [`configs/examples/chintai_v2.yaml`](../configs/examples/chintai_v2.yaml)
  — **`configs/` 直下に置くと実パターンとして走ってしまう**ため `examples/` に置く
- v1形式の設定は `configs/_v1/`（Git管理外）へ退避してある

### 5.1.1 エリア帯（1パターン＝1帯）

**✅ Phase 5 で導入。** 設計判断は [ADR 0013](./adr/0013-area-bands.md)、
用語は [CONTEXT.md](../CONTEXT.md)。

**エリア帯**は「賃料相場が一様とみなせる地理的範囲」。相場の違う範囲を
1つのパターンで見ると、**安い側が構造的に上位を独占して順位が意味を失う**。
実測（8,429掲載）では4都県を1本で見た結果、上位100件が
千葉県茂原市11・埼玉県本庄市9・毛呂山町7… と群馬/栃木県境と外房で占められ、
**東京の掲載が上位15件に1件も入らなかった**（→ 課題#24）。

| 帯 | 市区 | `rent_total_max` | `rent_total` の best/worst |
|---|---:|---:|---|
| 東京23区賃貸 | 23 | **100,000** | 70,000〜100,000 |
| 近郊60分圏賃貸 | 59 | 70,000 | 50,000〜70,000 |

- **帯は行政区画では切れない。** 同じ埼玉県に都心30分の川口市と
  群馬県境の神川町が同居する。「東京都／3県」で切っても、東京都519件の
  ボリューム帯はあきる野市53・青梅市49と多摩地域で23区は出てこない。
  帯は**通勤の許容時間**で市区を選び `search.cities` に列挙する
- **帯どうしは重ならない**（`tests/test_pattern.py` が回帰テストする）。
  重なると同じ掲載が2つのランキングに出て通知も二重になる
- **帯ごとに MUST と `best`/`worst` を変える。** 帯1で帯2と同じ
  50,000〜70,000 を使うと23区の掲載はほぼ全て0点になり weight 40 が死ぬ
- **帯1の上限は「広く取ってから締める」手順で決めた。** 7万円では23区に
  足立区19件・葛飾区11件しか無い。120,000円で取得して分布（554件・中央値105,000円）を
  見てから100,000円に締めた。⚠ この順序でなければならない。締める方向は
  `rescore` だけで試せるが、**緩める方向は取り直しになる**
  （MUST 1段目で `fail` の掲載はDBにも入らないため）
- ⚠ **市区リストは通勤時間の近似**であり、駅データに基づくものではない。
  実ダイヤで測ると帯2は45分以内が9.7%しかなく、**「近郊60分圏」へ改称した**
  （→ 課題#32）。あわせて**通勤60分以内の掲載が1件も無い4市区**
  （ふじみ野市・東村山市・国立市・清瀬市＝計261掲載）をリストから外している。
  取っても MUST で全件落ちるため取得が丸ごと無駄になるため。
  ⚠ **市区を減らす方向は取り直しにならない**（既存データの採点が絞られるだけ）が、
  増やす方向は MUST 1段目で fail した掲載がDBに無いため取り直しになる
- **採点もエリア帯に閉じる。** エリア帯は取得URLを絞るだけなので、
  `scan` と `rescore` の双方で `pattern.search.cities` を
  `load_property_views` へ渡す。渡さないとDBに残る帯外の掲載にも帯のスコアが付き、
  23区のランキングが本庄市で埋まる

**副次的な効果**: 対象市区が216→86に減り、増分スキャンが約72分→約53分に短縮する。
また `cities` を明示すると `requires_city=False` のサイトも市区展開されるため、
都道府県4本しか叩いていなかった SUUMO・HOMES・EHEYA の母集団が増える
（SUUMO は一覧で2,248件しか見ておらず、APAMAN の37,163件の**1/17**だった）。

### 5.1.2 通勤時間

**✅ Phase 5C で導入。** 設計判断は [ADR 0016](./adr/0016-commute-time-rail-graph.md)、
実装は `src/house_search/commute/`。

エリア帯で相場の違う範囲を混ぜる問題は解けたが、**帯の中でどこが通いやすいか**は
依然として順位に現れなかった。通勤時間を MUST と WANT の両方へ配線してこれを埋める。

| 項目 | 内容 |
|---|---|
| 測る区間 | **駅から駅まで**。駅までの徒歩は含めない（`walk_minutes` が別に効くため） |
| 目的地 | `commute.destination_station`（＋ `destination_prefecture`） |
| MUST | `commute_minutes_max`（実運用は両帯とも60分） |
| WANT | `commute_minutes` weight 25（`walk_minutes` を 15→10 に下げて捻出） |
| 算出 | **NAVITIME の乗換案内から実ダイヤを取る**（→ ADR 0017）。未取得の駅は駅データ.jp のグラフ＋回帰式で埋める |
| 保存 | `t_station_commutes`（駅グループのペアごとに1行）。採点はキャッシュを読むだけ |

#### 実ダイヤへの置き換え（Phase 5D）

回帰式は**平均誤差5.6分・最大16.0分**で、優等列車・直通運転・乗換待ちを表現できない。
`fetch-commutes` が NAVITIME の乗換案内から「掲載が挙げる駅 → 勤務先の最寄り駅」を
直接引き、実ダイヤの所要時間へ置き換える。

- **ODPT は使わない**（→ ADR 0017）。登録に日数がかかるうえ京成・北総・東葉高速などが
  未参加で、**時刻表のある区間と無い区間が1経路に混ざる**とどちらの精度でもない値が出る
- **駅ペアの O-D を直接引く。** 必要なのは 1,154駅グループ → 目的地1つなので、
  区間を集めて足し上げるより取得が少なく、しかも厳密。
  ⚠ **乗換案内で隣接駅を引くと待ち時間が混ざる**ので区間の足し上げは過大になる
- **乗車区間（駅間）の実所要時間も同時に貯める**（`t_rail_segments`）。急行が通過する駅を
  飛ばした区間がそのまま1本の辺になるので、目的地を変えたときに取得をやり直さずに済む
- **経路の原文を残す**（`t_navitime_routes.route_text`）。設備の `raw_features_text` と
  同じ考え方で、パーサを直したら再取得せず作り直せる
- ⚠ **同名異駅は黙って別の駅で検索される。** `駅名（都道府県名）` で厳密に指定し、
  **応答が解決した駅名を照合してから保存する**
- ⚠ **月指定は `2026/09` 形式**（`202609` は黙って無視され現在時刻になる）。
  応答に載る検索日と突き合わせて食い違えば例外にする
- ⚠ **回帰式で実ダイヤの行を上書きしない。** `resolve-commutes` は
  `source='navitime'` の駅を対象から外す
- 取得間隔は **15秒**（`SiteFetcher` の ±30% ジッタが下振れしても
  robots.txt の `ClaudeBot` 向け `Crawl-delay: 10` を割らない値）。
  1駅ごとにコミットするので中断しても再実行で続きから進む
- 起動は [`scripts/run_fetch_commutes.ps1`](../scripts/run_fetch_commutes.ps1)。
  ⚠ **エージェントのバックグラウンドから起動しない**（パイプ詰まりで無言停止する）

**実測（2026-09-03・8駅）**: 回帰式との差は平均5.2分・最大15分。
最大の川崎は回帰式43分に対し実ダイヤ28分で、**優等列車を表現できないぶん過大**だった
（＝都心に近い駅が不当に沈んでいた）。

**全件取得（2026-09-03・4時間48分）**: `t_station_commutes` は
**navitime 1,126駅 / rail_graph 29駅**、`t_rail_segments` は2,759本（列車2,729・徒歩30）。
残る29駅は NAVITIME が**意図と違う駅として解決した**ため保存しなかったもので
（町屋・本八幡・獨協大学前〈草加松原〉のように短い名前や〈〉付きが多い）、
照合が正しく弾いた結果として回帰式の値のまま残っている（→ 課題#34）。

- ⚠ **Google Maps は日本の公共交通経路を返さない。** Routes API に TRANSIT を投げると
  HTTP 200 のまま本文が `{}` になる（同じ呼び出しが米国では経路を返し、日本でも DRIVE なら返る）。
  Directions API も日本は ZERO_RESULTS。駅すぱあと API はフリープランに経路探索が無い
- 所要時間は `8.7 + 1.14 × 距離km + 5.6 × 乗換回数`（分）。係数は NAVITIME の
  実測12ペア（芝公園ゆき・水曜08:30発）で最小二乗した。**平均誤差5.6分・最大16.0分**
- ⚠ **一律の表定速度では長距離が過大になる**（平均18.2分・最大72分のずれ）。優等列車を表現できないため
- **駅の同定率は99.4%**（10,259/10,322掲載）。同名駅は掲載の所在都道府県で絞ると
  曖昧が168件→23件に減る。残る23件（浅草・早稲田・弘明寺）は unknown 扱い
- **通勤時間はグループ内の最短を採る**（設備の和集合と同じ）。サイトによって挙げる駅が違うため
- ⚠ **`best`/`worst` は帯ごとに母集団の分布へ合わせる**（→ 課題#31）。
  `commute-stats` が分布と0点張り付き率を出す。
  ⚠ **実ダイヤへ置き換えたら母集団が動くので付け直す。** 回帰式のときの
  帯1 18〜47分・帯2 30〜60分に対し、実ダイヤでは帯1が 12〜60分（中央39分）、
  帯2が 25〜80分（中央58分）へ後ろへずれた。回帰式向けの値を据え置くと
  帯1は0点が24.8%に張り付き、帯2は満点が1.5%しか出ず上端が使われない。

  | 帯 | 母集団（MUST通過） | best/worst | 満点 | 0点 |
  |---|---|---|---:|---:|
  | 東京23区賃貸 | 274件・12〜60分・中央39分 | 25〜55 | 6.6% | 0.4% |
  | 近郊60分圏賃貸 | 1,574件・25〜60分・中央53分 | 40〜60 | 7.3% | 7.3% |
- ⚠ **駅の接続情報CSVは再配布不可でGit管理外。** 無い環境では通勤時間を更新できないが、
  `scan` はエラーとして記録するだけで処理を止めない（通勤時間が unknown になる）

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
  site_filters:                     # ★MUSTをサイト側のフォームにも渡す（→ ADR 0015）
    enabled: true                   # 既定 false。事故時はここを false に戻せば従来動作
    axes: ["area_min", "walk_minutes_max", "layouts"]
    exclude_sites: []

commute:                            # 通勤時間の基準（→ ADR 0016）
  destination_station: "芝公園"       # 勤務先の最寄り駅
  destination_prefecture: "東京都"    # 同名異駅を避けるため指定を推奨

must:                               # 未充足なら除外
  rent_total_max: 70000
  commute_minutes_max: 60           # 駅から駅まで（徒歩は walk_minutes_max で別に効く）
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
    # best/worst は MUST の上限ではなく**母集団の分布**に合わせる（→ 課題#31）。
    # 実ダイヤへ置き換えたら分布が動くので付け直すこと（→ 課題#34）
    - { metric: commute_minutes, weight: 25, best: 40, worst: 60 }

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
| `commute_minutes`（勤務先の最寄り駅まで） | 低いほど良 | ○ | ○ | ○ | ○ | ○ |

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
- 内訳は `t_listing_scores.score_breakdown`(JSONB) に全項目を保存する
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

1. **原文保存**: 詳細ページの設備ブロックを**テキストのまま** `t_listings.raw_features_text` へ
   （詳細HTML全体は保存しない）
2. **辞書マッチング**: NFKC正規化 → 小文字化 → トークン化 → 辞書照合 → `t_listing_features` 生成

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

**✅ Phase 4 で実装済み。** 実装は `src/house_search/dedup/`、
詳細は [`詳細設計書/04_名寄せ設計.md`](./詳細設計書/04_名寄せ設計.md)、
設計判断は [ADR 0012](./adr/0012-dedup-normalization.md)。

- **キー**: `sha256("v1|{ファミリ}|{正規化住所}|{ファミリ別の構成要素}")`
  - 賃貸/マンション: 間取り＋専有面積＋所在階
  - 戸建て: 土地面積＋建物面積＋間取り（専有面積を流用しない）
- **正規化住所は丁目までで打ち切る。** サイトによって粒度が
  「番地まで（HOME'S）／丁目まで（多数）／町名まで（SUUMO）」とばらつき、
  番地を残すとクロスサイトの名寄せが原理的に成立しない
- **面積は丸めない**（小数第2位）。丸めても一致は増えず隣接住戸を余分に潰すだけだった
- **建物名・築年月・総階数はキーに含めない。** 匿名掲載が実在し、
  含めるとクロスサイト一致がすべて分断される
- **構成要素が1つでも欠けたらキーを作らない**（グループ化せず単独で残す）
- **完全一致のみ自動グループ化。** 曖昧一致の候補フラグは作っていない
- **代表選定**: 月額（`rent_total`）最安 → 設備抽出数 → `m_sites.representative_priority`
  → `listing_id`（同点でも順位が揺れないように）
- **スコアはグループ内の抽出情報の和集合**で計算する。
  `detail_fetched` も「グループ内の誰かが取れていれば真」にする
- **順位は代表と未グループ物件にだけ振る。** `digest` は `rank_in_pattern` を
  起点に引くので、これだけでランキングがグループ単位になる
- **同一建物・同一階・同一仕様の別住戸は1グループに潰れる**（2026-09-02 ユーザー判断）。
  グループには全掲載を保持し、通知に件数と全掲載サイトを明示する

### 8.1 実測（2026-09-02・301掲載）

301掲載 → **253グループ**。キー充足率は SUUMO の1件を除き100%、**偽陽性0件**。

| サイト | 掲載 | 代表 | ユニーク率 |
|---|---:|---:|---:|
| スモッカ 88 / SUUMO 60 / いい部屋ネット 53 | — | 88 / **33** / 38 | 98% / 93% / 96% |
| ニフティ 32 / APAMAN 28 / goo 17 | — | 32 / 25 / 15 | 97% / 100% / 82% |
| 賃貸EX 11 / ABLE 6 / HOME'S 6 | — | 10 / 6 / 6 | 91% / 100% / 100% |

**重複の主因はクロスサイトではなくサイト内。** SUUMO は60掲載中27件（45%）が重複で、
これが課題#13 の実態。クロスサイト重複は13掲載にとどまる。

## 9. 通知仕様

**✅ Phase 1 で実装済み。** 実装は `src/house_search/notify/`。

| タイプ | トリガー | Discord Embed カラー |
|---|---|---|
| `new` | 新着物件を初めて検出、または再掲載（グループ単位で重複抑制） | 🟢 緑 (#57F287) |
| `sold` | 詳細URLが成約/削除ページに遷移 | 🔴 赤 (#ED4245) |
| `price_down` | 前回価格より値下がり | 🔵 青 (#5865F2) |
| `price_up` | 前回価格より値上がり | 🟡 黄 (#FEE75C) |
| `cheaper_listing` | 同一物件の他サイトで代表より安い掲載 | 🟣 紫 (#9B59B6) |
| ランキングダイジェスト | 日次 | 1メッセージにスコア上位N件 |

- 即時通知はスコア・パターン内順位・得点上位3項目・未確認項目数を載せる
- Discord制約: description 4096字/1embed、6000字/1メッセージ、10embed/1メッセージ
- **種別横断ランキングは作らない。** 正規化基準が異なるスコアを混ぜると数字が意味を失う。
  「中古Mと中古Kを並べて見たい」は `digest_group` によるセクション並記で応える
- **通知はグループ単位で抑制する**（Phase 4）。同一住戸の別サイト掲載を
  `new` として二重に送らない。判定は `t_notifications` に**現在の所属を JOIN** して行い、
  履歴テーブルは追記専用のまま保つ
- `cheaper_listing` は代表が交代し新代表の月額が安いときに送る。
  **金額まで見て重複を避ける**ので、さらに安くなれば再通知され、同額の再検出では送らない
- グループの掲載が複数あるときは Embed に「同一条件の掲載 n件（サイト一覧）」を出し、
  ダイジェストの行末に `(SUUMO ほか2サイト)` と付ける
- 送信間隔は **2秒/件**。429 は `retry_after` に従って最大3回まで再送する
- ⚠ **`digest_group` は「見出しにラベルを付ける」だけの実装**で、
  本書が書いている「1メッセージにセクション並記」にはなっていない（→ 課題#28）。
  ダイジェストはパターンごとに1通ずつ届く。エリア帯を2つにしたので
  Discord には2通が届き、**実用上はむしろ帯ごとに分かれて読みやすい**
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

## 10.1 用語（掲載・住戸・グループ）

**✅ Phase 5 で整理。** 正典は [CONTEXT.md](../CONTEXT.md)、経緯は課題#30。

| 用語 | 意味 |
|---|---|
| **掲載** | 1つのサイトに載っている1件の募集。`t_listings` の1行 |
| **住戸** | 現実の1部屋。複数サイトに、同じサイト内にも重複して掲載される |
| **グループ** | 同一の住戸と判定した掲載の束。`t_listing_groups` |
| **代表** | グループの中で順位と通知に出す1件 |

**「物件」は使わない。** 掲載と住戸のどちらを指すか曖昧で、
名寄せの導入で「301掲載 → 253グループ」のように両者を区別する場面が
日常になったため。ただし**「物件種別」（賃貸／新築M／中古M…）は残す**。
業界用語で曖昧さが無く、掲載でも住戸でもない概念のため
（`m_property_types` / `property_type` / `property_family`）。

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
| `m_cities` | 市区町村（**1,918行・47都道府県**。総務省の全国地方公共団体コードが正典 → ADR 0014。`canonical_name` がYAML指定値の正典） |
| `m_site_search_params` | **サイト側の絞り込みパラメータ定義**（MUST限定・サイト×物件種別×軸 → ADR 0015） |
| `m_stations` | **駅マスタ**（駅データ.jp 無料版が正典・10,465駅 / 8,766駅グループ → ADR 0016） |
| `m_city_site_values` | 市区町村×サイトの検索値（**縦持ち**・1833行。JIS系サイトは `m_cities.jis_code` から導出するのでこの表を引かない） |
| `t_listings` | **掲載**（1行=1サイトの1件の募集）|
| `t_listing_features` | 掲載から抽出した設備・特性 |
| `t_listing_scores` | パターン別スコア（内訳JSONB・`config_hash`） |
| `t_listing_groups` | 同一**住戸**と判定した掲載のグループ（クロスサイト名寄せ）|
| `t_notifications` | 個別通知の送信履歴（追記専用） |
| `t_ranking_digests` | ダイジェスト送信履歴（追記専用） |
| `t_scrape_runs` | 実行チェックポイント（中断・再開用） |
| `t_scrape_logs` | 実行ログ（全件永久保持・追記専用） |
| `t_listing_stations` | 掲載の駅表記と駅マスタの同定結果 |
| `t_station_commutes` | 駅ペアの通勤所要時間キャッシュ |
| `t_navitime_routes` | **NAVITIME の乗換案内が返した経路候補の原文**（再解析の入力 → ADR 0017） |
| `t_rail_segments` | **乗車区間（駅間）の実所要時間**。目的地を変えたときの再計算に使う |
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

`m_cities.jis_code` は **Phase 5A で全件そろった**（1,918/1,918）。正典を
**総務省「全国地方公共団体コード」**（政府標準利用規約・基準日2024-01-01）へ移し、
47都道府県・全市区町村＋政令指定都市の行政区を投入した（→ [ADR 0014](./adr/0014-nationwide-city-master.md)・課題#16）。
原典CSVは `data/city_master/` に置き、`db/seed/06_cities.sql` は
`scripts/tools/generate_city_seed.py` の生成物にしてある。

⚠ **サイトのエリア索引から実測する方式をやめたのが要点。** 初版は
エイブルの索引から**部分文字列一致**で補完しており、947件・15都道府県しか無いうえに
**他市のコードが5件混入していた**（名古屋市に北名古屋市の `23234`、
大阪市に東大阪市の `27227`、浜松市の3区に2024年の区再編前のコード）。
前2件は同じコードを持つ行が2つある状態で、JIS系サイトでどちらを指定しても
別の市の一覧が返るが**取得は成功しエラーにもならない**。
`uq_m_cities_jis_code`（部分ユニーク索引）で以後はDB側が弾く。

**政令指定都市は市と行政区の両方を保持する。** サイトによって指定できる粒度が
違うため。ただし取得URLを組み立てるときは**行政区を持つ市の親行を外す**
（外すと横浜市 `14100` と横浜市西区 `14103` の二重取得を防げる）。
`search.cities` に市名だけを書いた場合はその市の行政区へ展開する。

⚠ **サイト固有スラグ（HOMES/ATHOME/NIFTY/MINIMINI）は全国化していない。**
JISから導出できず各サイトのエリア索引から集めるしかない（→ 課題#21）。

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

### 11.3 `t_listings` の主要カラム

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

### 11.6 バックアップ（課題#8）

[`scripts/backup_db.ps1`](../scripts/backup_db.ps1) が `pg_dump -Fc` でダンプし、
**`pg_restore --list` で読み直して無傷を検証**してから世代管理を行う。

```powershell
.\scripts\backup_db.ps1
.\scripts\backup_db.ps1 -RetentionDays 30
```

| 項目 | 既定値 |
|---|---|
| 出力先 | `F:\backups\searching-for-houses\` |
| ファイル名 | `searching_for_houses_yyyyMMdd_HHmmss.dump` |
| 形式 | カスタム形式・圧縮レベル6（`-Fc -Z 6`） |
| 保持世代 | 14日 |
| 接続ロール | アプリロール `searching_for_houses`（`.env` の `DATABASE_URL` から読む） |

- **パスワードは環境変数 `PGPASSWORD` でのみ渡す。** 引数に載せるとプロセス一覧と
  シェル履歴に平文で残る。`finally` で必ず消す
- `pg_dump` には `-w` を付ける。付けないと認証情報が足りないときプロンプトで固まり、
  タスクが実行時間の上限まで居座る
- **検証を省かない。** `pg_restore --list` の `TABLE DATA` が17件（マスタ8＋
  トランザクション9）未満なら失敗として終了コード1を返す。これが無いと
  「0バイトのファイルが毎日増えるだけ」の状態に気づけない
- 実測（2026-09-02・301掲載時点）: 334KB / `TABLE DATA` 18件

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
- **非リトライの4xxは、404 とそれ以外で扱いを分ける**（→ 課題#25）
  - **404**: 例外にせず `response` を返す。「その掲載が無い」という正常な状態変化で、
    呼び出し側（`is_sold`）が掲載終了の判定に使う。**例外にすると判定に到達できず、
    掲載終了の検知がデッドコードになる**（実際にそうなっていた）
  - **403・405 などの拒否系**: 再試行はしないが `failures` に数え、
    5回連続で打ち切る。数えないと打ち切りが永久に発火せず、
    初回全件スキャンで NIFTY の 405 を**271回叩き続けた**
- **MUST をサイト側へ渡す軸は `search.site_filters` で指定する**（→ ADR 0015）。
  丸めの向きは軸の意味から機械的に決まり（上限は切り上げ・下限は切り下げ・
  間取りは全項目を表現できるときだけ）、サイト定義には書けないようにしてある。
  正典は `data/site_search_params.yaml` で、`sync-site-params` で
  `m_site_search_params` へ同期し実行時はDBから読む。
  **配線済みは SUUMO・HOMES・GOO**（実測 2026-09-03・足立区）。
  SUUMO は母集団 62,030 → 27,150件（57%削減）。
  HOMES は総物件数 52,515件に対し面積下限30㎡で 27,689件・徒歩20分で 50,400件・
  間取り5種で 27,029件。⚠ HOMES の間取りは `cond[madori][15]=15` と
  **値ごとにキーが変わる**（チェックボックスの name 属性がこの形）。
  ⚠ HOMES の築年数の選択肢に **7年は無い**（SUUMO にはある）。サイト間で流用しない
- ⚠ **LIFULL HOME'S は絞られると HTTP 202 ＋ 空ボディを返す**（実測 2026-09-03。
  4秒間隔で6リクエスト目に入り、**パラメータなしのURLでも**そうなった）。
  `SiteFetcher` は 400 未満を成功として返すので、アダプタが判別して例外にする。
  ⚠ **本番でもこれが起きていた**（`t_scrape_logs` の HOMES は112件すべて
  `Document is empty`、掲載は10件しか入っていない → 課題#17）
- ⚠ **サイト側の絞り込みパラメータに無効値を渡すと HTTP 200 のまま0件になる**
  （→ 課題#29）。SUUMO の `ct`（賃料上限・万円）は選択肢が決まっており、
  `ct=15.6` は0件・`ct=16.0` は100件・指定なしは160件だった（実測）。
  **エラーにならないので「取れているつもり」で気づけない。**
  SUUMO は整数の万円、HOME'S は 0.5 刻みへ**切り上げる**
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
│   ├── dedup/
│   │   ├── address.py          # 名寄せ用の住所正規化（丁目まで）
│   │   ├── key.py              # dedup_key の合成
│   │   └── groups.py           # グループ同期・代表選定・実測
│   ├── scoring/
│   │   ├── listing_view.py    # 採点の入力（不変オブジェクト）
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
├── scripts/
│   ├── setup_db.ps1            # DB・ロール作成（冪等）
│   ├── run_initial_scan.ps1    # 初回全件スキャン（Start-Process で切り離す側）
│   ├── task_runner.ps1         # タスクから呼ばれる実体（-Wait で待つ側）
│   ├── backup_db.ps1           # pg_dump（14世代保持・課題#8）
│   └── register_tasks.ps1      # タスクスケジューラ登録（schtasks /XML・要管理者）
├── tests/
│   ├── test_metrics.py
│   ├── test_pattern.py
│   ├── test_settings.py
│   ├── test_schema_conventions.py   # DB規約の回帰テスト
│   ├── test_extract.py / test_scoring.py / test_notify.py / test_fetch.py
│   ├── test_area.py / test_persist.py
│   ├── test_dedup_address.py / test_dedup_key.py    # DB不要
│   ├── test_dedup_groups.py                          # DB統合（未設定ならスキップ）
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
- 名寄せ設計 → [`詳細設計書/04_名寄せ設計.md`](./詳細設計書/04_名寄せ設計.md)
- サイト別の検索フォーム調査資料 → [`詳細設計書/資料_サイト別検索条件一覧.md`](./詳細設計書/資料_サイト別検索条件一覧.md)
