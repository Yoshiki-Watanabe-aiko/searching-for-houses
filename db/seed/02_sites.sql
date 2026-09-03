-- ============================================================
-- m_sites: サイトマスタ（13行 / 賃貸のスクレイピング対象は12サイト）
--
-- fetch_method: Phase 3 の実測で **全サイト HTTP** になった。
--   v1 で go-rod / Playwright を使っていた ATHOME / EHEYA / NIFTY / APAMAN /
--   SMOCCA の5サイトは、いずれも一覧・詳細がサーバレンダリング済みで
--   素のHTTPで取得できる（→ docs/adr/0010-http-only-fetch.md）。
--   v1 がブラウザを使っていたのは検索フォームを操作していたためで、
--   URLを直接組み立てる v2 では不要。
--
-- SHAMAISON は v1 のマスタに残っていた行。積水ハウス系の自社物件のみで
-- スクレイパー実装も無いため is_active=false で保持する（削除はしない）。
-- CHINTAI_EX（賃貸EX）は Phase 2 で base_url とエリア指定方式を確定した
-- （/search/city/{JIS5} ・ページ送りは /page/{N} のパス形式）。
-- robots.txt が Disallow: *?* でクエリ付きURLを全面禁止しているため
-- 価格上限をサイト側へ渡せず、市区の全掲載を取ってローカル判定する。
-- Phase 5 で本採用した（is_active=true）。全件では他サイトと重なりユニーク率26%
-- だったが、エリア帯を都心45分圏に絞ると63%・69代表と GOO に迫る寄与になる
-- （全体の26%は216市区を舐めて市区必須サイトと重なった結果）→ 課題#5。
--
-- MINIMINI は取得手段が無いと確定したため is_active=false にしてある（Phase 4）。
-- 通常の scan の対象一覧から外れるが、`scan --site MINIMINI` で名指しすれば
-- 将来の回復を観測できる（→ 課題#18）。
-- MINIMINI は一覧ページが reCAPTCHA のボット判定下にあり、HTTP でも
-- **素の Chromium（Playwright）でも**取得できない（Phase 3 で実測。
-- タイトルが「ブラウザをチェックしています - reCAPTCHA」で掲載0件）。
-- 通すにはフィンガープリント偽装が要るため実装しない（→ 課題#18）。
--
-- ATHOME と HOME'S は **1回の実行で取れるリクエスト数に上限がある**（実測
-- 2026-09-03。ATHOME 4件・HOMES 5件で、超えるとそれぞれ認証ページ／HTTP 202＋
-- 空ボディになる）。⚠ **どちらも1リクエスト目は正常に返る**ので、単発の疎通
-- 確認では再現できない。⚠ **間隔を広げても上限は動かない**（HOMES は4秒でも
-- 10秒でも6件目）ため、絞りはリクエスト数で掛かっている。
-- Phase 5E で**市区ローテーション**（1回の実行では上限ぶんの市区だけ取り、
-- 次回は続きの市区から）を入れ、ATHOME を is_active=true へ戻し、
-- HOMES の間隔を 10.0 → 2.5秒へ戻した（→ 課題#17・#20・#36）。
--
-- APAMAN だけは robots.txt が `User-agent: * / Disallow: /` で全パスを
-- 禁じているが、ユーザーの明示的な判断で取得する（→ ADR 0011）。
-- そのぶん取得間隔を他サイトより長くしてある。
-- ============================================================

INSERT INTO m_sites (
    code, name, base_url, fetch_method, is_active,
    min_interval_sec, max_pages_per_run, daily_request_cap,
    representative_priority, notes
) VALUES
    ('SUUMO',      'SUUMO',           'https://suumo.jp',          'HTTP',       TRUE,  2.5, 5, NULL,  10, NULL),
    ('HOMES',      'LIFULL HOME''S',  'https://www.homes.co.jp',   'HTTP',       TRUE,  2.5, 5, NULL,  20, '1回の実行で5リクエストが上限。6件目からHTTP 202＋空ボディになる。⚠ 間隔を広げても上限は動かない（4秒でも10秒でも6件目・実測 2026-09-03）ので、10.0秒へ広げた対策を Phase 5E で 2.5秒へ戻した。取得量は市区ローテーション（1回5市区・次回は続きから）で確保する（→ 課題#17・#36）'),
    ('ATHOME',     'アットホーム',     'https://www.athome.co.jp',  'HTTP',       TRUE,  6.0, 5, NULL,  30, '1回の実行で4リクエストが上限。5件目からパズル認証のページ（HTTP 200・8KB）になる。⚠ 1件目は正常に返るので単発の疎通確認では再現できない。Phase 5E で市区ローテーション（1回4市区）を入れて is_active=true へ戻した（→ 課題#20・#36）'),
    ('NIFTY',      'ニフティ不動産',   'https://myhome.nifty.com',  'HTTP',       TRUE,  3.0, 5, NULL,  40, '他社サイトの掲載を集約するポータル。市区指定が必須。外部ドメインへ飛ぶ掲載は取り込まない'),
    ('GOO',        'goo不動産',        'https://house.goo.ne.jp',   'HTTP',       TRUE,  2.5, 5, NULL,  50, '掲載重複が多い'),
    ('CHINTAI_EX', '賃貸EX',          'https://chintai-ex.jp',     'HTTP',       TRUE,  2.5, 5, NULL,  60, 'robots.txtがクエリ付きURLを全面禁止。価格上限を渡せず市区の全掲載を取る'),
    ('ABLE',       'エイブル',         'https://www.able.co.jp',    'HTTP',       TRUE,  2.5, 5, NULL,  70, '市区指定が必須。都道府県のみだと0件になるため市区へ自動展開する'),
    ('MINIMINI',   'minimini',        'https://minimini.jp',       'HTTP',       FALSE, 2.5, 5, NULL,  80, '一覧ページがreCAPTCHAのボット判定下。HTTPでも素のブラウザでも取得不可（Phase 3 で実測）。取得手段が無いと確定したため Phase 4 で is_active=false（--site で名指しすれば回復の観測はできる）'),
    ('APAMAN',     'アパマンショップ', 'https://www.apamanshop.com','HTTP',       TRUE,  4.0, 5, NULL,  90, 'robots.txtが全パスを禁止（Disallow: /）。ユーザーの明示的判断で取得する（ADR 0011）。市区指定が必須'),
    ('EHEYA',      'いい部屋ネット',   'https://www.eheya.net',     'HTTP',       TRUE,  3.0, 5, NULL, 100, '大東建託グループ・自社物件中心。掲載データは __NEXT_DATA__ のJSONから読む。賃料上限をクエリで渡せない'),
    ('SMOCCA',     'スモッカ',         'https://smocca.jp',         'HTTP',       TRUE,  3.0, 5, NULL, 110, '市区指定が必須。robots.txtがページ送り(/*/page/)を禁止しているため1ページ目90件のみ取得する'),
    ('SHAMAISON',  'シャーメゾン',     'https://www.shamaison.com', 'HTTP',       FALSE, 2.5, 5, NULL, 120, 'v1から未実装。積水ハウス系の自社物件のみのため対象外'),
    ('UR',         'UR賃貸住宅',       'https://www.ur-net.go.jp',  'HTTP',       TRUE,  3.0, 5, NULL, 130, '都市再生機構。取得は3段のJSON API(POST)で、団地と住戸が別階層（→ ADR 0019・詳細設計書 §9.3）。市区で検索する手段が無くUR独自のarea区分しかないため、都県の全areaを取って応答のskcsでローカルに絞る。礼金・仲介手数料・更新料が制度上ゼロで保証人も不要なので、合成トークンで既存WANTへ載せる。⚠ APIホストのrobots.txtはHTTP 403（不在ではない）'),
    ('LEOPALACE',  'レオパレス21',     'https://www.leopalace21.com', 'HTTP',   FALSE, 2.5, 5, NULL, 140, '自社物件のみ。一覧は建物カードの中に住戸が並ぶ形で、MUST1段目に要る項目が全部ある（→ 詳細設計書 §11）。市区スラグはサイトマップ1本で全国1,000件採れ、末尾にJIS5桁が埋まっている。2.5秒間隔で20市区を連続取得しても検知は出ない。⚠ 掲載終了が404にならないのでタイトルで判別する。⚠ **is_active=false**（ユーザー判断 2026-09-04）: 在庫の96.2%が1Kで1LDK以上は0件・面積中央21.9㎡のため、検索パターンの must.layouts と area_min を構造的に1件も満たさない。アダプタは残してあるので --site LEOPALACE で名指しすれば在庫変化を観測できる → 詳細設計書 §11.8'),
    ('DROOM',      'D-room',           'https://www.droom-daiwaliving.net', 'HTTP', TRUE,  2.5, 5, NULL, 150, '自社物件（大和ハウス系）。市区の検索値がJIS5桁そのものなのでスラグ収集が要らない（→ 詳細設計書 §12）。⚠ ページ送りは page_num で**0始まり**。⚠ レスポンシブでPC用テーブルとSP用カードに同じ住戸が並び、管理費はSP用にしか、詳細URLはPC用にしか無いので号室で突き合わせる。⚠ 掲載終了が404にならず「現在、空室はございません。」のタイトルで返る。サイト側フィルタは cff=Y と対で送ると rent_total そのもので絞れる。2.5秒間隔で20市区を連続取得しても検知は出ない')
ON CONFLICT (code) DO UPDATE SET
    name                    = EXCLUDED.name,
    base_url                = EXCLUDED.base_url,
    fetch_method            = EXCLUDED.fetch_method,
    is_active               = EXCLUDED.is_active,
    min_interval_sec        = EXCLUDED.min_interval_sec,
    max_pages_per_run       = EXCLUDED.max_pages_per_run,
    daily_request_cap       = EXCLUDED.daily_request_cap,
    representative_priority = EXCLUDED.representative_priority,
    notes                   = EXCLUDED.notes,
    updated_at              = now();
