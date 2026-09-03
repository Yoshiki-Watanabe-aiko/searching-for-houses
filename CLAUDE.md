# CLAUDE.md

## プロジェクト概要
指定した条件に合致する物件を日本の不動産サイトから自動取得し、
MUST（未充足なら除外）＋WANT（重み付き加点）のスコアでランク付けして
新着・成約・価格変動・日次ランキングをDiscordへ通知するシステム。

**v2（Python）へ全面再設計中。Phase 5（賃貸の本運用）を再設計中。**
Phase 5C で**通勤時間**をランキングへ組み込み（→ ADR 0016）、
Phase 5D で回帰式から **NAVITIME の実ダイヤ**へ置き換えた（→ ADR 0017）。
Phase 5E で取得数に上限があるサイト（HOMES・ATHOME）を**市区ローテーション**で
回すようにした（→ 課題#36）。
初回全件スキャンの実測でランキング上位が群馬/栃木県境と外房で埋まったため、
**エリア帯**（23区／近郊60分圏）で検索パターンを2つに分割した（→ ADR 0013・課題#24）。
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
uv run house-search resolve-cities         # 市区町村IDの引き直し（マスタ入替後・ネットワーク不要）
uv run house-search sync-site-params       # サイト側フィルタ定義の同期（scan の前に必要）
uv run house-search sync-stations          # 駅マスタ（data/train_master/*.csv）→ DB
uv run house-search resolve-stations       # 掲載の駅表記を駅マスタと突き合わせる（ネットワーク不要）
uv run house-search resolve-commutes       # 駅ペアの通勤所要時間を算出しキャッシュ（ネットワーク不要）
uv run house-search fetch-commutes         # NAVITIMEから実ダイヤの通勤時間を取得（要ネットワーク・約15秒/駅）
uv run house-search fetch-commutes --region 関東   # 全国網羅。その地方の全駅×中心駅（→ ADR 0018）
uv run house-search re-segment             # 経路の原文から乗車区間を作り直す（ネットワーク不要）
uv run house-search re-segment --region 沖縄  # 地方ごと。索引もその地方に合わせる（→ 課題#35）
uv run house-search commute-stats          # 通勤時間の分布（best/worst を決める材料）
uv run house-search dedup-stats            # サイト別の重複率・ユニーク率（ネットワーク不要）
uv run house-search scan --seed --site CHINTAI_EX   # 無効化サイトの観測モード
uv run house-search scan --detail-limit 800         # 詳細取得の上限を上書き（既定40 / --full時400）
```

運用スクリプト（PowerShell 5.1。1行ずつ実行する。`&&` は使えない）:

```powershell
.\scripts\run_initial_scan.ps1                # 初回全件スキャン（切り離して起動・約6.5〜9時間）
.\scripts\run_initial_scan.ps1 -Drain         # 2晩目以降の詳細キュー掃き出し
.\scripts\run_fetch_commutes.ps1              # 通勤時間の実ダイヤ取得（切り離して起動・約4.8時間）
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
- **市区町村マスタの正典は総務省の全国地方公共団体コード**（`data/city_master/`）。
  `db/seed/06_cities.sql` は `scripts/tools/generate_city_seed.py` の生成物なので手で編集しない。
  ⚠ **サイトのエリア索引から部分文字列一致で補完してはいけない。**
  実際に名古屋市へ北名古屋市の `23234`、大阪市へ東大阪市の `27227` が混入していた。
  **別の市の一覧が返るだけで取得は成功しエラーにもならない**（→ ADR 0014）
- **政令指定都市はマスタが市と行政区の両方を持つ。** 取得URLを組み立てるときは
  行政区を持つ市の親行を外さないと、同じ掲載を市と区で二重に取りに行く
- **詳細ページに「非該当」条件を並べるサイトがある**（HOMES の `sr-only`、goo の `td` が `-`）。
  そのまま `raw_features_text` に載せると辞書が非該当の条件を拾う
- **`m_sites.is_active = false` のサイトは通常の `scan` では取りに行かない。**
  `--site` で名指ししたときだけ動く（賃貸EX の観測モードの入口）
- **能動的なボット検知は突破しない。** MINIMINI は reCAPTCHA（課題#18・**素のブラウザでも通らない**）、
  HOME'S は AWS WAF（課題#17）、ATHOME はパズル認証（課題#20）で取得できないことがある。
  ⚠ **検知ページは HTTP 200 で返る。** そのまま解析すると掲載0件になるだけでエラーにならず
  「取れているつもり」で気づけないので、判別できるサイトはアダプタが例外にする
- **サイトの取得可否を単発のリクエストで判断しない。** HOMES・ATHOME は
  **1リクエスト目が正常に返る**が、市区を変えて連続で叩くと HOMES は6件目から
  HTTP 202＋空ボディ、ATHOME は5件目からボット認証ページになる（2026-09-03 実測）。
  ⚠ **HOMES は間隔を広げても上限が動かない**（4秒でも10秒でも6件目）。
  絞りは**リクエスト数**で掛かっているので、`min_interval_sec` を上げる対策は
  所要時間を伸ばすだけで取得量は増えない（→ 課題#17・#36）
- **取得数に上限があるサイトは市区ローテーションで回す**（`scrape/rotation.py` →
  課題#36）。回転量はアダプタのクラス属性 `city_rotation_limit`（HOMES 5・ATHOME 4）。
  ⚠ **カーソルは位置番号でなく JIS5桁で持つ**（市区リストは YAML 編集で増減する）。
  ⚠ **1回の実行では1帯だけが枠を使う**（帯が2つあるので素朴に実装すると予算が
  2倍消費され、後半の帯が全部 202 になる）。
  ⚠ **カーソルは取得を試みる前に進める**（失敗した市区を再試行し続けると
  そこから先へ永久に進めない）。
  ⚠ **`--full` でも1ページ・詳細0件に固定する**（上限はリクエスト数に掛かる）。
  ⚠ **予算は時間で回復する**（2分後の再実行は全て 202。回復窓の長さは未測定）
- **サイトのエリア索引を解析するときは、取得HTMLを必ず保存する。**
  ATHOME の市区リンクは `href='...'` と**単一引用符**で、`href="..."` 決め打ちだと
  71市区のうち10市区しか拾えない。⚠ **エラーにならず件数が減るだけ**なので、
  保存が無いと取得予算を試行錯誤で使い切る（`collect_city_slugs.py --from-cache`）
- **`resolve_areas` は検索値の無い市区を黙って落とす。** スラグ系サイトは
  `m_city_site_values` に行が無いとその市区が対象から消えるだけでエラーにならない。
  実測で HOMES は帯82市区のうち32、ATHOME は49の検索値が無かった（→ 課題#36）。
  Phase 5E で収集して **HOMES 82/82・ATHOME 81/82** まで埋めた
  （`scripts/tools/collect_city_slugs.py`）。⚠ **市区の同定は索引に埋まっている
  JIS5桁で行う**（部分文字列一致は他市のコードを混入させる → ADR 0014）
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
- **エリア帯は取得URLしか絞らない。採点も閉じないと帯外が混ざる。**
  `load_property_views` は種別とサイトでしか絞らないので、`scan` と `rescore` の
  双方で `pattern.search.cities` を渡す。渡さないとDBに残る帯外の掲載にも
  帯のスコアが付き、23区のランキングが本庄市で埋まる（→ ADR 0013）
- **相場が違う範囲を1つの検索パターンに混ぜない。** 賃料 weight 40・面積 20 に対し
  立地の配点は0なので、安くて広い郊外が構造的に勝つ。4都県を1本で見たとき
  上位15件に東京が1件も入らなかった（→ 課題#24）。帯は都県では切れない
  （同じ埼玉県に都心30分の川口市と群馬県境の神川町が同居する）
- **SUUMO の `ct`（賃料上限・万円）は選択肢が決まっており、端数を渡すと
  HTTP 200 のまま掲載0件になる。** 実測で `ct=15.6` は0件・`ct=16.0` は100件。
  ⚠ **エラーにならないので気づけない。** HOME'S の `cond[monthmoneyroomh]` も
  0.5刻み。整数／0.5刻みへ**切り上げる**（→ 課題#29）
- **非リトライの4xxを失敗として数えないと「連続失敗で打ち切り」が発火しない。**
  `raise_for_status()` が先に例外を投げると `consecutive_failures` に到達しない。
  NIFTY の405を271回叩き続けた。⚠ 404 も同じ経路で例外になるため、
  `is_sold` の404判定に到達できず**掲載終了の検知が死んでいた**（→ 課題#25）
- **サイト側へ渡してよいのは MUST だけ**（→ ADR 0015）。設備条件と WANT は永久に渡さない。
  丸めの向きは `scrape/params.py` の `AXIS_BOUND` で軸ごとに決まり、サイト定義には書けない。
  ⚠ **キー名・選択肢・不等号の向きは推測で書かず実サイトで測る。**
  誤りには「0件になる／黙って無視される／向きが逆」の3つの現れ方があり、**どれも例外にならない**。
  測るときは先に存在しないキー（`zzz=1`）を送って件数が変わらないことを確かめ、
  判定方法自体の妥当性を担保してから測る
- **`price_max_hint` を変えたら、それを使う全サイトのURLを実測で確かめる。**
  運用中の 90,000 円は偶然どのサイトでも有効な値だったため、
  156,000 円にした瞬間に SUUMO と HOME'S が0件になった

- **Google Maps は日本の公共交通経路を返さない。** Routes API に TRANSIT を投げると
  **HTTP 200 のまま本文が `{}`** になる（同じ呼び出しが米国では経路を返し、日本でも DRIVE なら返る）。
  Directions API も日本は ZERO_RESULTS。通勤時間は駅データ.jp の接続情報から
  自前でダイクストラする（→ ADR 0016）
- **所要時間を一律の表定速度で出すと長距離が大きく過大になる**（実測で平均18.2分・最大72分のずれ）。
  優等列車を表現できないため。距離＋乗換＋定数の回帰式で平均5.6分まで縮む
- **駅データ.jp 無料版に新幹線の駅は1件も無い**（路線は存在するが駅が0件）。
  本庄早稲田のような新幹線専用駅は同定できない
- **`best`/`worst` は母集団の分布を見てから決める**（→ 課題#31）。MUST の上限に
  機械的に合わせると、母集団がその範囲の内側に固まっていたとき配点が死ぬ。
  `commute-stats` が分布と0点張り付き率を出す。
  ⚠ **通勤時間の算出方法を変えたら付け直す。** 回帰式→実ダイヤで母集団が
  帯1 18〜47分→12〜60分・帯2 30〜60分→25〜80分へ動き、据え置くと
  帯1は0点が24.8%に張り付いた
- **通勤時間はグループ内の最短を採る**（設備の和集合と同じ）。サイトによって挙げる駅が違う
- **NAVITIME の月指定は `2026/09` 形式。** `202609` を渡すと**黙って無視され現在時刻**になる
  （深夜に実行すると始発帯の値が返り、朝の通勤時間だと思い込む）。
  応答の前後便リンクに検索日が載っているので `parse_search` が突き合わせて例外にする
- **NAVITIME は同名異駅を黙って別の駅で検索する。** `orvStationName=大久保` は
  「大久保（東京都）」として処理され **HTTP 200 で普通の結果が返る**。
  ⚠ **都道府県を添えるとかえって別の駅になることがある**（`松田（神奈川県）` → 新松田 /
  `厚木（神奈川県）` → 本厚木。県を外すと正しい）。県付きの表記を使うのは**同名異駅があるときだけ**なので、
  **`駅名` → `駅名（都道府県）` の順に試し、応答が解決した駅名を照合して通った方を採る**
  （→ ADR 0017）
- **NAVITIME は自己申告のUAを 403 で拒否する。** robots.txt は `/transfer/` を
  `User-agent: *` に許可しており、UAの選別だけが別の関門になっている。
  HOME'S と同じ扱いでブラウザ相当UA（`BROWSER_USER_AGENT`）を使い、間隔と robots は変えない。
  ⚠ 取得間隔は **15秒**。`SiteFetcher` が ±30% のジッタを掛けるので、
  11秒にすると下振れ7.7秒で `ClaudeBot` の `Crawl-delay: 10` を破る
- **NAVITIME の経路は「発」から「着」までが1区間とは限らない。** 直通運転で列車が変わると
  `（直通）東京` の行が挟まり、その前後で路線名と分が別々に出る。1区間として読むと
  辺の重みが実際より5分短くなる（実測: 赤羽→新橋は18分ではなく18分＋2分＋停車）
- **回帰式（`resolve-commutes`）で実ダイヤの行を上書きしない。** 掲載が挙げる駅を
  全部まとめて書き直すため、素朴に流すと4.8時間かけて採った実測値が見積もりへ戻る。
  `t_station_commutes.source='navitime'` の駅は対象から外している
- **`fetch-commutes` の `--depart-on` を動かさない。** 出発日は `t_navitime_routes` の
  一意キーの一部なので、変えると再実行のたびに全駅を取り直すことになる
- **乗車区間の駅名索引は用途に合う範囲で作る。** 掲載のある都道府県で固定すると
  その外の地方では区間が1本も貯まらない（沖縄18駅で72本すべてを捨てた → 課題#35）。
  ⚠ **全国に広げるのも誤り**で、同名異駅（三田・大手町）が一意でなくなり
  解決率が 94.8% → 66.3% に落ちる。`--region` のときだけその地方へ切り替える
- **経路の駅名には注記が付く。** 乗換駅の路線注記（`本八幡〔新宿線〕`）と角括弧の
  副名称（`押上[スカイツリー前]`）を落とさないと索引を引けない。⚠ `normalize_key` が
  落とすのは `〈〉` と `()` だけなので、`strip_station_note` を別に通す
- **駅名の照合を直したら `re-segment`。** 経路の原文から区間を作り直せるので
  4.8時間の再取得は要らない（設備の `re-extract` と同じ位置づけ）

## AI回答方針
- 複数実装がある場合はトレードオフを説明してから推奨案を提示する
- より良い設計があれば指示に縛られず積極的に提案する
- セキュリティ上の懸念点は必ず指摘する
