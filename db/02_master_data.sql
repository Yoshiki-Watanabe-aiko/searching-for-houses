-- ============================================================
-- 物件検索通知システム - マスタデータ初期投入
-- Created: 2026-06-16
-- ============================================================

-- ============================================================
-- 物件種別マスタ
-- ============================================================
INSERT INTO property_type_master (code, name) VALUES
    ('CHINTAI',           '賃貸'),
    ('SHINCHIKU_MANSION', '新築マンション'),
    ('CHUKO_MANSION',     '中古マンション'),
    ('SHINCHIKU_KODATE',  '新築一戸建て'),
    ('CHUKO_KODATE',      '中古一戸建て')
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- サイトマスタ（11サイト：LIFULL HOME'Sとホームズは同一）
-- ============================================================
INSERT INTO site_master (code, name, base_url, notes) VALUES
    ('SUUMO',     'SUUMO',          'https://suumo.jp',           NULL),
    ('HOMES',     'LIFULL HOME''S', 'https://www.homes.co.jp',    NULL),
    ('ATHOME',    'アットホーム',     'https://www.athome.co.jp',   NULL),
    ('NIFTY',     'ニフティ不動産',   'https://sumai.nifty.com',    '接続制限あり・スクレイピング実装時に要確認'),
    ('APAMAN',    'アパマンショップ', 'https://www.apaman.jp',      '接続制限あり・スクレイピング実装時に要確認'),
    ('SHAMAISON', 'シャーメゾン',     'https://www.shamaison.com',  '積水ハウス系・自社物件のみ'),
    ('GOO',       'goo不動産',       'https://house.goo.ne.jp',    NULL),
    ('EHEYA',     'いい部屋ネット',   'https://www.eheya.net',      '大東建託グループ・自社物件中心'),
    ('ABLE',      'エイブル',         'https://www.able.co.jp',     NULL),
    ('SMOCCA',    'スモッカ',         'https://smocca.jp',          '賃貸特化型集約サイト'),
    ('MINIMINI',  'minimini',        'https://minimini.jp',        NULL)
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- 条件カテゴリマスタ
-- ============================================================
INSERT INTO condition_category_master (code, name, sort_order) VALUES
    ('AREA',          'エリア・アクセス',      1),
    ('PRICE_RENT',    '価格・費用（賃貸）',    2),
    ('PRICE_BUY',     '価格・費用（購入）',    3),
    ('BUILDING',      '建物・間取り',          4),
    ('STRUCTURE',     '構造',                  5),
    ('LOCATION',      '位置・向き',            6),
    ('INTERIOR',      '室内設備',              7),
    ('HEATING',       '冷暖房',                8),
    ('BATHROOM',      'バス・トイレ',          9),
    ('KITCHEN',       'キッチン',             10),
    ('BLDG_EQUIP',    '建物設備',             11),
    ('SECURITY',      'セキュリティ',         12),
    ('COMMUNICATION', 'テレビ・通信',         13),
    ('STORAGE',       '収納',                 14),
    ('MOVE_IN',       '入居条件（賃貸）',     15),
    ('FEAT',          '物件特性・表示',        16),
    ('NEARBY',        '周辺施設',             17)
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- 条件マスタ
-- ============================================================
INSERT INTO condition_master (category_id, code, name, data_type, sort_order)
SELECT cat.id, v.code, v.name, v.data_type, v.sort_order
FROM (VALUES
    -- エリア・アクセス
    ('AREA', 'AREA_PREF',           '都道府県',                     'enum',    1),
    ('AREA', 'AREA_CITY',           '市区町村',                     'enum',    2),
    ('AREA', 'AREA_STATION_LINE',   '路線・駅',                     'enum',    3),
    ('AREA', 'AREA_WALK_MIN',       '駅徒歩（分以内）',             'number',  4),
    ('AREA', 'AREA_BUS_OK',         'バス利用可（含む）',           'boolean', 5),
    ('AREA', 'AREA_COMMUTE_MIN',    '通勤・通学時間（分以内）',     'number',  6),
    ('AREA', 'AREA_TRANSFER_COUNT', '乗換回数',                     'number',  7),
    -- 価格・費用（賃貸）
    ('PRICE_RENT', 'PRICE_RENT_MIN',      '家賃下限',               'range',   1),
    ('PRICE_RENT', 'PRICE_RENT_MAX',      '家賃上限',               'range',   2),
    ('PRICE_RENT', 'PRICE_INCL_MGMT',     '管理費・共益費込み',     'boolean', 3),
    ('PRICE_RENT', 'PRICE_INCL_PARKING',  '駐車場代込み',           'boolean', 4),
    ('PRICE_RENT', 'PRICE_NO_REIKIN',     '礼金なし',               'boolean', 5),
    ('PRICE_RENT', 'PRICE_NO_SHIKIKIN',   '敷金なし',               'boolean', 6),
    ('PRICE_RENT', 'PRICE_NO_HOSHOKIN',   '保証金なし',             'boolean', 7),
    ('PRICE_RENT', 'PRICE_FREE_RENT',     'フリーレント',           'boolean', 8),
    ('PRICE_RENT', 'PRICE_INITIAL_CARD',  '初期費用カード決済可',   'boolean', 9),
    ('PRICE_RENT', 'PRICE_RENT_CARD',     '家賃カード決済可',       'boolean',10),
    ('PRICE_RENT', 'PRICE_NO_GUARANTOR',  '保証人不要',             'boolean',11),
    -- 価格・費用（購入）
    ('PRICE_BUY', 'PRICE_BUY_MIN',    '物件価格下限',               'range',   1),
    ('PRICE_BUY', 'PRICE_BUY_MAX',    '物件価格上限',               'range',   2),
    ('PRICE_BUY', 'PRICE_MONTHLY',    '月々支払額',                 'range',   3),
    ('PRICE_BUY', 'PRICE_UNDECIDED',  '価格未定を含む',             'boolean', 4),
    -- 建物・間取り
    ('BUILDING', 'BLDG_TYPE',         '建物種別',                   'enum',    1),
    ('BUILDING', 'BLDG_LAYOUT',       '間取り',                     'enum',    2),
    ('BUILDING', 'BLDG_AREA_MIN',     '専有面積下限（㎡）',         'range',   3),
    ('BUILDING', 'BLDG_AREA_MAX',     '専有面積上限（㎡）',         'range',   4),
    ('BUILDING', 'BLDG_BUILD_AREA',   '建物面積（㎡）',             'range',   5),
    ('BUILDING', 'BLDG_LAND_AREA',    '土地面積（㎡）',             'range',   6),
    ('BUILDING', 'BLDG_BALCONY_AREA', 'バルコニー面積（㎡）',       'range',   7),
    ('BUILDING', 'BLDG_AGE_MAX',      '築年数（〇年以内）',         'number',  8),
    -- 構造
    ('STRUCTURE', 'STRUCT_RC',        '鉄筋コンクリート（RC）',     'boolean', 1),
    ('STRUCTURE', 'STRUCT_SRC',       '鉄筋鉄骨コンクリート（SRC）','boolean', 2),
    ('STRUCTURE', 'STRUCT_STEEL',     '鉄骨造',                     'boolean', 3),
    ('STRUCTURE', 'STRUCT_WOOD',      '木造',                       'boolean', 4),
    ('STRUCTURE', 'STRUCT_BLOCK',     'ブロック・その他',           'boolean', 5),
    ('STRUCTURE', 'STRUCT_QUAKE',     '耐震基準適合',               'boolean', 6),
    ('STRUCTURE', 'STRUCT_LONGEVITY', '長期優良住宅',               'boolean', 7),
    -- 位置・向き
    ('LOCATION', 'LOC_FLOOR_1',       '1階の物件',                  'boolean', 1),
    ('LOCATION', 'LOC_FLOOR_2UP',     '2階以上',                    'boolean', 2),
    ('LOCATION', 'LOC_TOP_FLOOR',     '最上階',                     'boolean', 3),
    ('LOCATION', 'LOC_CORNER',        '角部屋',                     'boolean', 4),
    ('LOCATION', 'LOC_SOUTH_FACING',  '南向き',                     'boolean', 5),
    ('LOCATION', 'LOC_CORNER_LOT',    '角地（戸建て）',             'boolean', 6),
    ('LOCATION', 'LOC_SOUTH_ROAD',    '南道路（戸建て）',           'boolean', 7),
    -- 室内設備
    ('INTERIOR', 'INT_LAUNDRY',       '室内洗濯機置場',             'boolean', 1),
    ('INTERIOR', 'INT_WASHROOM',      '洗面所独立',                 'boolean', 2),
    ('INTERIOR', 'INT_FLOORING',      'フローリング',               'boolean', 3),
    ('INTERIOR', 'INT_LOFT',          'ロフト',                     'boolean', 4),
    ('INTERIOR', 'INT_SOUNDPROOF',    '防音室',                     'boolean', 5),
    ('INTERIOR', 'INT_BASEMENT',      '地下室',                     'boolean', 6),
    ('INTERIOR', 'INT_FURNISHED',     '家具家電付き',               'boolean', 7),
    -- 冷暖房
    ('HEATING', 'HVAC_AC',            'エアコン付き',               'boolean', 1),
    ('HEATING', 'HVAC_FLOOR_HEAT',    '床暖房',                     'boolean', 2),
    ('HEATING', 'HVAC_KEROSENE',      '灯油暖房',                   'boolean', 3),
    ('HEATING', 'HVAC_GAS_HEAT',      'ガス暖房',                   'boolean', 4),
    -- バス・トイレ
    ('BATHROOM', 'BATH_SEPARATE',     'バス・トイレ別',             'boolean', 1),
    ('BATHROOM', 'BATH_WASHLET',      '温水洗浄便座',               'boolean', 2),
    ('BATHROOM', 'BATH_DRY',          '浴室乾燥機',                 'boolean', 3),
    ('BATHROOM', 'BATH_REHEATING',    '追い焚き風呂',               'boolean', 4),
    ('BATHROOM', 'BATH_SHOWER',       'シャワールーム',             'boolean', 5),
    -- キッチン
    ('KITCHEN', 'KITCHEN_GAS',        'ガスコンロ対応',             'boolean', 1),
    ('KITCHEN', 'KITCHEN_IH',         'IHコンロ',                   'boolean', 2),
    ('KITCHEN', 'KITCHEN_2BURNER',    'コンロ2口以上',              'boolean', 3),
    ('KITCHEN', 'KITCHEN_ALL_ELEC',   'オール電化',                 'boolean', 4),
    ('KITCHEN', 'KITCHEN_SYSTEM',     'システムキッチン',           'boolean', 5),
    ('KITCHEN', 'KITCHEN_COUNTER',    'カウンターキッチン',         'boolean', 6),
    ('KITCHEN', 'KITCHEN_DISPOSER',   'ディスポーザー',             'boolean', 7),
    ('KITCHEN', 'KITCHEN_DISHWASHER', '食器洗い乾燥機',             'boolean', 8),
    -- 建物設備
    ('BLDG_EQUIP', 'EQUIP_PARKING',         '駐車場あり',           'boolean', 1),
    ('BLDG_EQUIP', 'EQUIP_PARKING_2',       '駐車場2台以上',        'boolean', 2),
    ('BLDG_EQUIP', 'EQUIP_PARKING_ONSITE',  '敷地内駐車場',         'boolean', 3),
    ('BLDG_EQUIP', 'EQUIP_BICYCLE',         '駐輪場あり',           'boolean', 4),
    ('BLDG_EQUIP', 'EQUIP_BIKE',            'バイク置場あり',       'boolean', 5),
    ('BLDG_EQUIP', 'EQUIP_ELEVATOR',        'エレベーター',         'boolean', 6),
    ('BLDG_EQUIP', 'EQUIP_DELIVERY_BOX',    '宅配ボックス',         'boolean', 7),
    ('BLDG_EQUIP', 'EQUIP_TRASH',           '敷地内ゴミ置場',       'boolean', 8),
    ('BLDG_EQUIP', 'EQUIP_TRASH_24H',       '24時間ゴミ出し可',     'boolean', 9),
    ('BLDG_EQUIP', 'EQUIP_BALCONY',         'バルコニー付き',       'boolean',10),
    ('BLDG_EQUIP', 'EQUIP_ROOF_BALCONY',    'ルーフバルコニー付き', 'boolean',11),
    ('BLDG_EQUIP', 'EQUIP_PRIVATE_GARDEN',  '専用庭',               'boolean',12),
    ('BLDG_EQUIP', 'EQUIP_CITY_GAS',        '都市ガス',             'boolean',13),
    ('BLDG_EQUIP', 'EQUIP_LPG',             'プロパンガス',         'boolean',14),
    ('BLDG_EQUIP', 'EQUIP_BARRIER_FREE',    'バリアフリー',         'boolean',15),
    -- セキュリティ
    ('SECURITY', 'SEC_AUTOLOCK',          'オートロック',             'boolean', 1),
    ('SECURITY', 'SEC_CONCIERGE',         '管理人あり',               'boolean', 2),
    ('SECURITY', 'SEC_MONITOR_INTERCOM',  'TVモニタ付きインターホン', 'boolean', 3),
    ('SECURITY', 'SEC_CAMERA',            '防犯カメラ',               'boolean', 4),
    ('SECURITY', 'SEC_SECURITY_CO',       'セキュリティ会社加入済',   'boolean', 5),
    -- テレビ・通信
    ('COMMUNICATION', 'COMM_NET_AVAILABLE', 'インターネット接続可',  'boolean', 1),
    ('COMMUNICATION', 'COMM_NET_FREE',      'インターネット無料',    'boolean', 2),
    ('COMMUNICATION', 'COMM_FIBER',         '光ファイバー',          'boolean', 3),
    ('COMMUNICATION', 'COMM_BS',            'BSアンテナ',            'boolean', 4),
    ('COMMUNICATION', 'COMM_CS',            'CSアンテナ',            'boolean', 5),
    ('COMMUNICATION', 'COMM_CABLE_TV',      'ケーブルテレビ',        'boolean', 6),
    -- 収納
    ('STORAGE', 'STORAGE_WIC',          'ウォークインクローゼット',   'boolean', 1),
    ('STORAGE', 'STORAGE_SHOE',         'シューズボックス',           'boolean', 2),
    ('STORAGE', 'STORAGE_UNDER_FLOOR',  '床下収納',                   'boolean', 3),
    ('STORAGE', 'STORAGE_TRUNK',        'トランクルーム（専用）',     'boolean', 4),
    -- 入居条件（賃貸専用）
    ('MOVE_IN', 'MOVEIN_IMMEDIATE',     '即入居可',                   'boolean', 1),
    ('MOVE_IN', 'MOVEIN_FEMALE_ONLY',   '女性限定',                   'boolean', 2),
    ('MOVE_IN', 'MOVEIN_PET',           'ペット相談可',               'boolean', 3),
    ('MOVE_IN', 'MOVEIN_INSTRUMENT',    '楽器相談可',                 'boolean', 4),
    ('MOVE_IN', 'MOVEIN_OFFICE_USE',    '事務所利用可',               'boolean', 5),
    ('MOVE_IN', 'MOVEIN_ROOMSHARE',     'ルームシェア可',             'boolean', 6),
    ('MOVE_IN', 'MOVEIN_SENIOR',        '高齢者歓迎',                 'boolean', 7),
    ('MOVE_IN', 'MOVEIN_LGBT',          'LGBTフレンドリー',           'boolean', 8),
    ('MOVE_IN', 'MOVEIN_CUSTOMIZE',     'カスタマイズ可',             'boolean', 9),
    ('MOVE_IN', 'MOVEIN_DIY',           'DIY可',                      'boolean',10),
    ('MOVE_IN', 'MOVEIN_NO_FIXED_TERM', '定期借家を含まない',         'boolean',11),
    -- 物件特性・表示
    ('FEAT', 'FEAT_NEW',           '新築・築浅',                      'boolean', 1),
    ('FEAT', 'FEAT_REFORMED',      'リフォーム済み',                  'boolean', 2),
    ('FEAT', 'FEAT_RENOVATED',     'リノベーション物件',              'boolean', 3),
    ('FEAT', 'FEAT_DESIGNER',      'デザイナーズ物件',                'boolean', 4),
    ('FEAT', 'FEAT_SUBLEASE',      '分譲賃貸',                        'boolean', 5),
    ('FEAT', 'FEAT_TOWER',         'タワーマンション',                'boolean', 6),
    ('FEAT', 'FEAT_LARGE_SCALE',   '大規模マンション',                'boolean', 7),
    ('FEAT', 'FEAT_LOW_RISE',      '低層マンション',                  'boolean', 8),
    ('FEAT', 'FEAT_IT_CONTRACT',   'IT重説対応',                      'boolean', 9),
    ('FEAT', 'FEAT_ONLINE_CONSULT','オンライン相談可',                 'boolean',10),
    ('FEAT', 'FEAT_DUPLEX',        '二世帯向き（戸建て）',            'boolean',11),
    ('FEAT', 'FEAT_JAPANESE_ROOM', '和室あり',                        'boolean',12),
    ('FEAT', 'FEAT_TODAY_NEW',     '本日の新着',                      'boolean',13),
    ('FEAT', 'FEAT_WITH_PHOTO',    '写真付き',                        'boolean',14),
    ('FEAT', 'FEAT_WITH_LAYOUT',   '間取り図付き',                    'boolean',15),
    -- 周辺施設
    ('NEARBY', 'NEARBY_CONVENIENCE', 'コンビニ400m以内',              'boolean', 1),
    ('NEARBY', 'NEARBY_SUPERMARKET', 'スーパー800m以内',              'boolean', 2),
    ('NEARBY', 'NEARBY_SCHOOL',      '小学校800m以内',                'boolean', 3),
    ('NEARBY', 'NEARBY_PARK',        '公園400m以内',                  'boolean', 4)
) AS v(cat_code, code, name, data_type, sort_order)
JOIN condition_category_master cat ON cat.code = v.cat_code
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- 条件×物件種別マッピング
-- C=賃貸 SM=新築M CM=中古M SK=新築戸建 CK=中古戸建
-- ============================================================

-- エリア・アクセス（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('AREA_PREF','AREA_CITY','AREA_STATION_LINE','AREA_WALK_MIN',
                 'AREA_BUS_OK','AREA_COMMUTE_MIN','AREA_TRANSFER_COUNT')
ON CONFLICT DO NOTHING;

-- 賃貸専用 価格条件
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c
JOIN property_type_master pt ON pt.code = 'CHINTAI'
WHERE c.code IN ('PRICE_RENT_MIN','PRICE_RENT_MAX','PRICE_INCL_MGMT','PRICE_INCL_PARKING',
                 'PRICE_NO_REIKIN','PRICE_NO_SHIKIKIN','PRICE_NO_HOSHOKIN','PRICE_FREE_RENT',
                 'PRICE_INITIAL_CARD','PRICE_RENT_CARD','PRICE_NO_GUARANTOR')
ON CONFLICT DO NOTHING;

-- 購入系 価格条件
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('PRICE_BUY_MIN','PRICE_BUY_MAX','PRICE_MONTHLY')
  AND pt.code IN ('SHINCHIKU_MANSION','CHUKO_MANSION','SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 価格未定（新築のみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'PRICE_UNDECIDED'
  AND pt.code IN ('SHINCHIKU_MANSION','SHINCHIKU_KODATE')
ON CONFLICT DO NOTHING;

-- 建物・間取り（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('BLDG_TYPE','BLDG_LAYOUT','BLDG_AREA_MIN','BLDG_AREA_MAX','BLDG_AGE_MAX')
ON CONFLICT DO NOTHING;

-- 建物面積・土地面積（戸建てのみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('BLDG_BUILD_AREA','BLDG_LAND_AREA')
  AND pt.code IN ('SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- バルコニー面積（マンションのみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'BLDG_BALCONY_AREA'
  AND pt.code IN ('SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 構造（RC・SRC：マンション系）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('STRUCT_RC','STRUCT_SRC')
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 構造（鉄骨：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'STRUCT_STEEL'
ON CONFLICT DO NOTHING;

-- 構造（木造・ブロック：賃貸・戸建て）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('STRUCT_WOOD','STRUCT_BLOCK')
  AND pt.code IN ('CHINTAI','SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 構造（耐震：購入物件）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'STRUCT_QUAKE'
  AND pt.code IN ('SHINCHIKU_MANSION','CHUKO_MANSION','SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 構造（長期優良：新築のみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'STRUCT_LONGEVITY'
  AND pt.code IN ('SHINCHIKU_MANSION','SHINCHIKU_KODATE')
ON CONFLICT DO NOTHING;

-- 位置（1階：賃貸のみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c
JOIN property_type_master pt ON pt.code = 'CHINTAI'
WHERE c.code = 'LOC_FLOOR_1'
ON CONFLICT DO NOTHING;

-- 位置（2F以上・最上階・角部屋：マンション系）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('LOC_FLOOR_2UP','LOC_TOP_FLOOR','LOC_CORNER')
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 位置（南向き：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'LOC_SOUTH_FACING'
ON CONFLICT DO NOTHING;

-- 位置（角地・南道路：戸建てのみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('LOC_CORNER_LOT','LOC_SOUTH_ROAD')
  AND pt.code IN ('SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 室内設備（洗濯機置場：賃貸・マンション）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'INT_LAUNDRY'
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 室内設備（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('INT_WASHROOM','INT_FLOORING','INT_SOUNDPROOF')
ON CONFLICT DO NOTHING;

-- 室内設備（ロフト：賃貸・マンション）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'INT_LOFT'
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 室内設備（地下室：戸建て）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'INT_BASEMENT'
  AND pt.code IN ('SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 室内設備（家具家電：賃貸のみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c
JOIN property_type_master pt ON pt.code = 'CHINTAI'
WHERE c.code = 'INT_FURNISHED'
ON CONFLICT DO NOTHING;

-- 冷暖房（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('HVAC_AC','HVAC_FLOOR_HEAT')
ON CONFLICT DO NOTHING;

-- 冷暖房（灯油・ガス：賃貸のみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c
JOIN property_type_master pt ON pt.code = 'CHINTAI'
WHERE c.code IN ('HVAC_KEROSENE','HVAC_GAS_HEAT')
ON CONFLICT DO NOTHING;

-- バス・トイレ（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('BATH_SEPARATE','BATH_WASHLET','BATH_DRY','BATH_REHEATING','BATH_SHOWER')
ON CONFLICT DO NOTHING;

-- キッチン（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('KITCHEN_GAS','KITCHEN_IH','KITCHEN_2BURNER','KITCHEN_ALL_ELEC',
                 'KITCHEN_SYSTEM','KITCHEN_COUNTER')
ON CONFLICT DO NOTHING;

-- キッチン（ディスポーザー・食洗機：購入系）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('KITCHEN_DISPOSER','KITCHEN_DISHWASHER')
  AND pt.code IN ('SHINCHIKU_MANSION','CHUKO_MANSION','SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 建物設備（駐車場・自転車・バイク：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('EQUIP_PARKING','EQUIP_PARKING_ONSITE')
ON CONFLICT DO NOTHING;

-- 建物設備（駐車場2台：戸建て）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'EQUIP_PARKING_2'
  AND pt.code IN ('SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 建物設備（エレベーター・宅配BOX・バルコニー系：マンション・賃貸）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('EQUIP_ELEVATOR','EQUIP_DELIVERY_BOX','EQUIP_BICYCLE','EQUIP_BIKE',
                 'EQUIP_BALCONY','EQUIP_ROOF_BALCONY')
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 建物設備（ゴミ置場：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'EQUIP_TRASH'
ON CONFLICT DO NOTHING;

-- 建物設備（24Hゴミ：マンション）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'EQUIP_TRASH_24H'
  AND pt.code IN ('SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 建物設備（専用庭・都市ガス・LPG・バリアフリー：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('EQUIP_PRIVATE_GARDEN','EQUIP_CITY_GAS','EQUIP_LPG','EQUIP_BARRIER_FREE')
ON CONFLICT DO NOTHING;

-- セキュリティ（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('SEC_AUTOLOCK','SEC_CONCIERGE','SEC_MONITOR_INTERCOM','SEC_CAMERA','SEC_SECURITY_CO')
ON CONFLICT DO NOTHING;

-- テレビ・通信（ネット接続可・光・BS・CS・CATV：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('COMM_NET_AVAILABLE','COMM_FIBER','COMM_BS','COMM_CS','COMM_CABLE_TV')
ON CONFLICT DO NOTHING;

-- テレビ・通信（ネット無料：賃貸のみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c
JOIN property_type_master pt ON pt.code = 'CHINTAI'
WHERE c.code = 'COMM_NET_FREE'
ON CONFLICT DO NOTHING;

-- 収納（WIC・シューズBOX：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('STORAGE_WIC','STORAGE_SHOE')
ON CONFLICT DO NOTHING;

-- 収納（床下収納：戸建て）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'STORAGE_UNDER_FLOOR'
  AND pt.code IN ('SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 収納（トランクルーム：マンション・賃貸）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'STORAGE_TRUNK'
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 入居条件（賃貸専用）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c
JOIN property_type_master pt ON pt.code = 'CHINTAI'
WHERE c.code IN ('MOVEIN_IMMEDIATE','MOVEIN_FEMALE_ONLY','MOVEIN_PET','MOVEIN_INSTRUMENT',
                 'MOVEIN_OFFICE_USE','MOVEIN_ROOMSHARE','MOVEIN_SENIOR','MOVEIN_LGBT',
                 'MOVEIN_CUSTOMIZE','MOVEIN_DIY','MOVEIN_NO_FIXED_TERM')
ON CONFLICT DO NOTHING;

-- 物件特性（新築：賃貸・新築系）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'FEAT_NEW'
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','SHINCHIKU_KODATE')
ON CONFLICT DO NOTHING;

-- 物件特性（リフォーム：中古系）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'FEAT_REFORMED'
  AND pt.code IN ('CHUKO_MANSION','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 物件特性（リノベ：賃貸・中古系）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'FEAT_RENOVATED'
  AND pt.code IN ('CHINTAI','CHUKO_MANSION','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 物件特性（デザイナーズ：賃貸・マンション）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'FEAT_DESIGNER'
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 物件特性（分譲賃貸：賃貸のみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c
JOIN property_type_master pt ON pt.code = 'CHINTAI'
WHERE c.code = 'FEAT_SUBLEASE'
ON CONFLICT DO NOTHING;

-- 物件特性（タワー：賃貸・マンション）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'FEAT_TOWER'
  AND pt.code IN ('CHINTAI','SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 物件特性（大規模・低層：マンションのみ）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('FEAT_LARGE_SCALE','FEAT_LOW_RISE')
  AND pt.code IN ('SHINCHIKU_MANSION','CHUKO_MANSION')
ON CONFLICT DO NOTHING;

-- 物件特性（IT重説・オンライン：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('FEAT_IT_CONTRACT','FEAT_ONLINE_CONSULT')
ON CONFLICT DO NOTHING;

-- 物件特性（二世帯：戸建て）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'FEAT_DUPLEX'
  AND pt.code IN ('SHINCHIKU_KODATE','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 物件特性（和室：中古系）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code = 'FEAT_JAPANESE_ROOM'
  AND pt.code IN ('CHUKO_MANSION','CHUKO_KODATE')
ON CONFLICT DO NOTHING;

-- 物件特性（表示系：全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('FEAT_TODAY_NEW','FEAT_WITH_PHOTO','FEAT_WITH_LAYOUT')
ON CONFLICT DO NOTHING;

-- 周辺施設（全種別）
INSERT INTO condition_property_type_map (condition_id, property_type_id)
SELECT c.id, pt.id FROM condition_master c, property_type_master pt
WHERE c.code IN ('NEARBY_CONVENIENCE','NEARBY_SUPERMARKET','NEARBY_SCHOOL','NEARBY_PARK')
ON CONFLICT DO NOTHING;
