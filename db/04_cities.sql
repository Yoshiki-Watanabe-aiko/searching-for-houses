-- ============================================================
-- 04_cities.sql: 市区町村マスター＋サイト別検索値テーブル
-- ============================================================

-- 市区町村マスター
-- canonical_name: YAMLに記述する値
--   政令指定都市の区: parent_city || city_name (例: '横浜市緑区')
--   東京特別区・一般市町村: city_name そのまま (例: '新宿区', '川崎市')
-- 同一都道府県内で canonical_name が一致する場合のみ parent_city で区別する。
-- 既知の同県内重複:
--   神奈川県: 緑区 (横浜市 vs 相模原市), 南区 (横浜市 vs 相模原市)
--   大阪府:   西区 (大阪市 vs 堺市),   北区 (大阪市 vs 堺市)
CREATE TABLE IF NOT EXISTS cities (
    id             SERIAL       PRIMARY KEY,
    prefecture     VARCHAR(20)  NOT NULL,
    parent_city    VARCHAR(50),                -- 政令指定都市名。特別区・一般市町村は NULL
    city_name      VARCHAR(100) NOT NULL,      -- 区市町村名
    canonical_name VARCHAR(150) NOT NULL,      -- YAML に記述する値
    UNIQUE (prefecture, canonical_name)
);

-- サイト別検索値 (ワイドテーブル)
-- NULL = そのサイトはこの市区町村レベルの検索値が未登録 → 都道府県レベルにフォールバック
CREATE TABLE IF NOT EXISTS city_site_mappings (
    city_id  INT NOT NULL REFERENCES cities(id) ON DELETE CASCADE PRIMARY KEY,
    suumo    VARCHAR(500),
    homes    VARCHAR(500),
    athome   VARCHAR(500),
    goo      VARCHAR(500),
    able     VARCHAR(500),
    minimini VARCHAR(500),
    eheya    VARCHAR(500),
    nifty    VARCHAR(500),
    apaman   VARCHAR(500),
    smocca   VARCHAR(500)
);

COMMENT ON COLUMN city_site_mappings.suumo    IS 'SUUMO の URL パスセグメント (例: sc_shinjuku)';
COMMENT ON COLUMN city_site_mappings.homes    IS 'LIFULL HOME''S の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.athome   IS 'athome の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.goo      IS 'goo不動産 の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.able     IS 'エイブル の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.minimini IS 'minimini の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.eheya    IS 'いい部屋ネット の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.nifty    IS 'ニフティ不動産 の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.apaman   IS 'アパマンショップ の検索パラメータ値';
COMMENT ON COLUMN city_site_mappings.smocca   IS 'スモッカ の検索パラメータ値';