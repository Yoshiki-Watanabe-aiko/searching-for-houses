-- ============================================================
-- m_property_types: 物件種別マスタ（6種別）
--
-- family は metric体系・dedup_key構成要素・YAMLスキーマの分岐単位。
-- 6種別を6クラスに割らないのは、新築/中古の差が age_years・価格未定・
-- リノベ関連の数項目だけでクラスを分けるほどの構造差がないため。
--
-- 土地（TOCHI / TOCHI_BUY）は Phase 9 で追加した（→ ADR 0024・課題#61）。
-- 建物を伴わないので、戸建て・マンションとは別のファミリにしている。
-- ⚠ ここへ行を足したら config/metrics.py の FAMILY_OF と db/seed.py の
--   EXPECTED_MIN_ROWS も直す（tests/test_property_type_seed.py が突き合わせる）
-- ============================================================

INSERT INTO m_property_types (code, name, family, sort_order) VALUES
    ('CHINTAI',           '賃貸',           'CHINTAI',     1),
    ('SHINCHIKU_MANSION', '新築マンション', 'MANSION_BUY', 2),
    ('CHUKO_MANSION',     '中古マンション', 'MANSION_BUY', 3),
    ('SHINCHIKU_KODATE',  '新築一戸建て',   'KODATE_BUY',  4),
    ('CHUKO_KODATE',      '中古一戸建て',   'KODATE_BUY',  5),
    ('TOCHI',             '土地',           'TOCHI_BUY',   6)
ON CONFLICT (code) DO UPDATE SET
    name       = EXCLUDED.name,
    family     = EXCLUDED.family,
    sort_order = EXCLUDED.sort_order,
    updated_at = now();
