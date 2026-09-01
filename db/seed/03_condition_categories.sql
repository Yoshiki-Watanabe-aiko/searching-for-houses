-- ============================================================
-- m_condition_categories: 条件カテゴリマスタ（19カテゴリ）
--
-- v1 の17カテゴリに、売買固有の CERT（証明書・性能評価）と
-- LAND（土地・法規制）を追加した。
-- ============================================================

INSERT INTO m_condition_categories (code, name, sort_order) VALUES
    ('AREA',          'エリア・アクセス',    1),
    ('PRICE_RENT',    '価格・費用（賃貸）',  2),
    ('PRICE_BUY',     '価格・費用（購入）',  3),
    ('BUILDING',      '建物・間取り',        4),
    ('STRUCTURE',     '構造',                5),
    ('LOCATION',      '位置・向き',          6),
    ('INTERIOR',      '室内設備',            7),
    ('HEATING',       '冷暖房',              8),
    ('BATHROOM',      'バス・トイレ',        9),
    ('KITCHEN',       'キッチン',           10),
    ('BLDG_EQUIP',    '建物設備',           11),
    ('SECURITY',      'セキュリティ',       12),
    ('COMMUNICATION', 'テレビ・通信',       13),
    ('STORAGE',       '収納',               14),
    ('MOVE_IN',       '入居条件（賃貸）',   15),
    ('FEAT',          '物件特性・表示',     16),
    ('NEARBY',        '周辺施設',           17),
    ('CERT',          '証明書・性能評価',   18),
    ('LAND',          '土地・法規制',       19)
ON CONFLICT (code) DO UPDATE SET
    name       = EXCLUDED.name,
    sort_order = EXCLUDED.sort_order,
    updated_at = now();
