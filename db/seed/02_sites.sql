-- ============================================================
-- m_sites: サイトマスタ（12行 / 賃貸のスクレイピング対象は11サイト）
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
-- is_active=false のまま観測モードで運用し、Phase 4 の名寄せで
-- ユニーク物件率を実測してから有効化する（scan --site CHINTAI_EX --seed）。
--
-- MINIMINI は一覧ページが reCAPTCHA のボット判定下にあり、HTTP でも
-- **素の Chromium（Playwright）でも**取得できない（Phase 3 で実測。
-- タイトルが「ブラウザをチェックしています - reCAPTCHA」で掲載0件）。
-- 通すにはフィンガープリント偽装が要るため実装しない（→ 課題#18）。
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
    ('HOMES',      'LIFULL HOME''S',  'https://www.homes.co.jp',   'HTTP',       TRUE,  2.5, 5, NULL,  20, NULL),
    ('ATHOME',     'アットホーム',     'https://www.athome.co.jp',  'HTTP',       TRUE,  6.0, 5, NULL,  30, 'Phase 3 でHTTP取得を確認。ただしパズル認証のボット検知があり、3秒間隔で47件連続取得したら発動した。間隔を6秒に広げてある'),
    ('NIFTY',      'ニフティ不動産',   'https://myhome.nifty.com',  'HTTP',       TRUE,  3.0, 5, NULL,  40, '他社サイトの掲載を集約するポータル。市区指定が必須。外部ドメインへ飛ぶ掲載は取り込まない'),
    ('GOO',        'goo不動産',        'https://house.goo.ne.jp',   'HTTP',       TRUE,  2.5, 5, NULL,  50, '掲載重複が多い'),
    ('CHINTAI_EX', '賃貸EX',          'https://chintai-ex.jp',     'HTTP',       FALSE, 2.5, 50, NULL, 60, 'robots.txtがクエリ付きURLを全面禁止。価格上限を渡せず市区の全掲載を取る。観測モード中'),
    ('ABLE',       'エイブル',         'https://www.able.co.jp',    'HTTP',       TRUE,  2.5, 5, NULL,  70, '市区指定が必須。都道府県のみだと0件になるため市区へ自動展開する'),
    ('MINIMINI',   'minimini',        'https://minimini.jp',       'HTTP',       TRUE,  2.5, 5, NULL,  80, '一覧ページがreCAPTCHAのボット判定下。HTTPでも素のブラウザでも取得不可（Phase 3 で実測）。アダプタ未実装'),
    ('APAMAN',     'アパマンショップ', 'https://www.apamanshop.com','HTTP',       TRUE,  4.0, 5, NULL,  90, 'robots.txtが全パスを禁止（Disallow: /）。ユーザーの明示的判断で取得する（ADR 0011）。市区指定が必須'),
    ('EHEYA',      'いい部屋ネット',   'https://www.eheya.net',     'HTTP',       TRUE,  3.0, 5, NULL, 100, '大東建託グループ・自社物件中心。掲載データは __NEXT_DATA__ のJSONから読む。賃料上限をクエリで渡せない'),
    ('SMOCCA',     'スモッカ',         'https://smocca.jp',         'HTTP',       TRUE,  3.0, 5, NULL, 110, '市区指定が必須。robots.txtがページ送り(/*/page/)を禁止しているため1ページ目90件のみ取得する'),
    ('SHAMAISON',  'シャーメゾン',     'https://www.shamaison.com', 'HTTP',       FALSE, 2.5, 5, NULL, 120, 'v1から未実装。積水ハウス系の自社物件のみのため対象外')
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
