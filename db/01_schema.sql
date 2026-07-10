-- ============================================================
-- 物件検索通知システム - マスタテーブルスキーマ
-- Created: 2026-06-16
-- ============================================================

-- 物件種別マスタ
CREATE TABLE IF NOT EXISTS property_type_master (
    id         SERIAL PRIMARY KEY,
    code       VARCHAR(50)  UNIQUE NOT NULL,
    name       VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
);

-- サイトマスタ
CREATE TABLE IF NOT EXISTS site_master (
    id         SERIAL PRIMARY KEY,
    code       VARCHAR(50)  UNIQUE NOT NULL,
    name       VARCHAR(100) NOT NULL,
    base_url   VARCHAR(255),
    is_active  BOOLEAN      DEFAULT TRUE,
    notes      TEXT,
    created_at TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
);

-- 条件カテゴリマスタ
CREATE TABLE IF NOT EXISTS condition_category_master (
    id         SERIAL PRIMARY KEY,
    code       VARCHAR(50)  UNIQUE NOT NULL,
    name       VARCHAR(100) NOT NULL,
    sort_order INT          DEFAULT 0,
    created_at TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
);

-- 条件マスタ
-- data_type: boolean=チェックボックス系, range=下限・上限指定, enum=選択肢, number=数値単体
CREATE TABLE IF NOT EXISTS condition_master (
    id          SERIAL PRIMARY KEY,
    category_id INT          NOT NULL REFERENCES condition_category_master(id),
    code        VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    data_type   VARCHAR(50)  DEFAULT 'boolean',
    sort_order  INT          DEFAULT 0,
    created_at  TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP
);

-- 条件×物件種別マッピング（どの条件がどの物件種別に適用されるか）
CREATE TABLE IF NOT EXISTS condition_property_type_map (
    id               SERIAL PRIMARY KEY,
    condition_id     INT NOT NULL REFERENCES condition_master(id),
    property_type_id INT NOT NULL REFERENCES property_type_master(id),
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(condition_id, property_type_id)
);

-- サイト×条件マッピング（サイトがその条件をサポートしているか・URLパラメータ名）
CREATE TABLE IF NOT EXISTS site_condition_map (
    id              SERIAL PRIMARY KEY,
    site_id         INT          NOT NULL REFERENCES site_master(id),
    condition_id    INT          NOT NULL REFERENCES condition_master(id),
    is_supported    BOOLEAN      DEFAULT TRUE,
    site_param_name VARCHAR(200),
    notes           TEXT,
    created_at      TIMESTAMPTZ  DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(site_id, condition_id)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_condition_master_category ON condition_master(category_id);
CREATE INDEX IF NOT EXISTS idx_cpt_map_condition         ON condition_property_type_map(condition_id);
CREATE INDEX IF NOT EXISTS idx_cpt_map_property_type     ON condition_property_type_map(property_type_id);
CREATE INDEX IF NOT EXISTS idx_site_cond_map_site        ON site_condition_map(site_id);
CREATE INDEX IF NOT EXISTS idx_site_cond_map_condition   ON site_condition_map(condition_id);
