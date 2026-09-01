-- ============================================================
-- m_sites: サイトマスタ（12行 / 賃貸のスクレイピング対象は11サイト）
--
-- fetch_method の実測（旧 cmd/main.go の配線を確認して訂正）:
--   Playwright 必須は ATHOME / EHEYA / NIFTY / APAMAN / SMOCCA の5サイト。
--   docs/requirements.md が ATHOME を「HTTP + goquery」としていたのは誤り。
--   Phase 3 で HTTP へ降格できるかを再検証する。
--
-- SHAMAISON は v1 のマスタに残っていた行。積水ハウス系の自社物件のみで
-- スクレイパー実装も無いため is_active=false で保持する（削除はしない）。
-- CHINTAI_EX（賃貸EX）は Phase 2 で URL・エリア指定方式・base_url を確定し、
-- 観測モードでユニーク物件率を実測してから有効化する。
-- ============================================================

INSERT INTO m_sites (
    code, name, base_url, fetch_method, is_active,
    min_interval_sec, max_pages_per_run, daily_request_cap,
    representative_priority, notes
) VALUES
    ('SUUMO',      'SUUMO',           'https://suumo.jp',          'HTTP',       TRUE,  2.5, 5, NULL,  10, NULL),
    ('HOMES',      'LIFULL HOME''S',  'https://www.homes.co.jp',   'HTTP',       TRUE,  2.5, 5, NULL,  20, NULL),
    ('ATHOME',     'アットホーム',     'https://www.athome.co.jp',  'PLAYWRIGHT', TRUE,  3.0, 5, NULL,  30, 'v1でも go-rod 使用。Phase 3 で HTTP 降格可否を再検証'),
    ('NIFTY',      'ニフティ不動産',   'https://myhome.nifty.com',  'PLAYWRIGHT', TRUE,  3.0, 5, NULL,  40, '接続制限あり'),
    ('GOO',        'goo不動産',        'https://house.goo.ne.jp',   'HTTP',       TRUE,  2.5, 5, NULL,  50, '掲載重複が多い'),
    ('CHINTAI_EX', '賃貸EX',          NULL,                        'HTTP',       FALSE, 2.5, 5, NULL,  60, 'Phase 2 で base_url・エリア指定方式を確定し観測モードで有効化する'),
    ('ABLE',       'エイブル',         'https://www.able.co.jp',    'HTTP',       TRUE,  2.5, 5, NULL,  70, '市区指定が必須。都道府県のみだと0件になるため市区へ自動展開する'),
    ('MINIMINI',   'minimini',        'https://minimini.jp',       'HTTP',       TRUE,  2.5, 5, NULL,  80, NULL),
    ('APAMAN',     'アパマンショップ', 'https://www.apamanshop.com','PLAYWRIGHT', TRUE,  3.0, 5, NULL,  90, '接続制限あり'),
    ('EHEYA',      'いい部屋ネット',   'https://www.eheya.net',     'PLAYWRIGHT', TRUE,  3.0, 5, NULL, 100, '大東建託グループ・自社物件中心'),
    ('SMOCCA',     'スモッカ',         'https://smocca.jp',         'PLAYWRIGHT', TRUE,  3.0, 5, NULL, 110, '市区指定が必須。都道府県のみだと0件になるため市区へ自動展開する'),
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
