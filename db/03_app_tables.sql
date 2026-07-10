-- ============================================================
-- 物件検索通知システム - アプリケーションテーブルスキーマ
-- Created: 2026-06-16
-- ============================================================

-- 物件テーブル
CREATE TABLE IF NOT EXISTS properties (
    id               SERIAL PRIMARY KEY,
    site_id          INT NOT NULL REFERENCES site_master(id),
    property_type_id INT NOT NULL REFERENCES property_type_master(id),
    external_id      VARCHAR(500) NOT NULL,
    url              TEXT NOT NULL,
    title            VARCHAR(500),
    price            BIGINT,
    price_prev       BIGINT,
    area_sqm         DECIMAL(8,2),
    layout           VARCHAR(50),
    address          TEXT,
    station_info     TEXT,
    age_years        INT,
    floor_num        INT,
    image_url        TEXT,
    address_hash     VARCHAR(64),
    status           VARCHAR(20) NOT NULL DEFAULT 'active',
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT properties_status_check CHECK (status IN ('active', 'sold', 'removed')),
    UNIQUE(site_id, external_id)
);

-- 通知履歴テーブル（UNIQUE制約なし：同一物件の価格変動を複数回記録可能）
CREATE TABLE IF NOT EXISTS notifications (
    id                SERIAL PRIMARY KEY,
    property_id       INT NOT NULL REFERENCES properties(id),
    pattern_name      VARCHAR(255) NOT NULL,
    notification_type VARCHAR(20) NOT NULL,
    price_at_notify   BIGINT,
    notified_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT notifications_type_check
        CHECK (notification_type IN ('new', 'sold', 'price_up', 'price_down'))
);

-- スクレイプログテーブル（全件永久保持）
CREATE TABLE IF NOT EXISTS scrape_logs (
    id           SERIAL PRIMARY KEY,
    level        VARCHAR(10) NOT NULL,
    site_code    VARCHAR(50),
    pattern_name VARCHAR(255),
    message      TEXT NOT NULL,
    detail       JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT scrape_logs_level_check CHECK (level IN ('INFO', 'WARN', 'ERROR'))
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_properties_status       ON properties(status);
CREATE INDEX IF NOT EXISTS idx_properties_site_ext     ON properties(site_id, external_id);
CREATE INDEX IF NOT EXISTS idx_properties_addr_hash    ON properties(address_hash) WHERE address_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_property  ON notifications(property_id);
CREATE INDEX IF NOT EXISTS idx_notifications_pattern   ON notifications(pattern_name);
CREATE INDEX IF NOT EXISTS idx_notifications_type      ON notifications(notification_type);
CREATE INDEX IF NOT EXISTS idx_notifications_notify_at ON notifications(notified_at);
CREATE INDEX IF NOT EXISTS idx_scrape_logs_created     ON scrape_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_scrape_logs_level       ON scrape_logs(level);
CREATE INDEX IF NOT EXISTS idx_scrape_logs_site        ON scrape_logs(site_code) WHERE site_code IS NOT NULL;
