-- ============================================================
-- m_property_types: 物件種別マスタ（5種別）
--
-- family は metric体系・dedup_key構成要素・YAMLスキーマの分岐単位。
-- 5種別を5クラスに割らないのは、新築/中古の差が age_years・価格未定・
-- リノベ関連の数項目だけでクラスを分けるほどの構造差がないため。
-- ============================================================

INSERT INTO m_property_types (code, name, family, sort_order) VALUES
    ('CHINTAI',           '賃貸',           'CHINTAI',     1),
    ('SHINCHIKU_MANSION', '新築マンション', 'MANSION_BUY', 2),
    ('CHUKO_MANSION',     '中古マンション', 'MANSION_BUY', 3),
    ('SHINCHIKU_KODATE',  '新築一戸建て',   'KODATE_BUY',  4),
    ('CHUKO_KODATE',      '中古一戸建て',   'KODATE_BUY',  5)
ON CONFLICT (code) DO UPDATE SET
    name       = EXCLUDED.name,
    family     = EXCLUDED.family,
    sort_order = EXCLUDED.sort_order,
    updated_at = now();
