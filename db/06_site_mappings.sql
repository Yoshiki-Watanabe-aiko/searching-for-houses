-- ============================================================
-- 06_site_mappings.sql: 全10サイト 市区町村マッピングデータ
-- ============================================================
-- 格納値の形式:
--   SUUMO    : URL パスセグメント (例: sc_shinjuku)
--   HOMES    : {pref-slug}/{city-slug}-city  (例: tokyo/shinjuku-city)
--   ATHOME   : 5桁JIS市区町村コード          (例: 13104)
--   GOO      : 5桁JIS市区町村コード          (例: 13104)
--   ABLE     : 5桁JIS市区町村コード          (例: 13104)
--   MINIMINI : 5桁JIS市区町村コード          (例: 13104)
--   EHEYA    : {pref-slug}/{city-slug}        (例: tokyo/shinjuku)
--   NIFTY    : {pref-slug}/{city-slug}        (例: tokyo/shinjuku)
--   APAMAN   : 5桁JIS市区町村コード          (例: 13104)
--   SMOCCA   : {pref-slug}/{city-slug}        (例: tokyo/shinjuku)
-- NULL = そのサイトでの市区町村絞り込みが未対応 → 都道府県レベルにフォールバック
-- ============================================================

-- ============================================================
-- 東京都 特別区 (23区) — 全10サイト
-- JIS: 13101(千代田区) 〜 13123(江戸川区)
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, suumo, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
SELECT c.id, v.suumo, v.homes, v.athome, v.goo, v.able, v.minimini, v.eheya, v.nifty, v.apaman, v.smocca
FROM (VALUES
  ('千代田区', 'sc_chiyoda',   'tokyo/chiyoda-city',   '13101', '13101', '13101', 'chiyodaku',    '13101', 'tokyo/chiyoda',   '13101', '13101'),
  ('中央区',   'sc_chuo',      'tokyo/chuo-city',      '13102', '13102', '13102', 'chuoku',       '13102', 'tokyo/chuo',      '13102', '13102'),
  ('港区',     'sc_minato',    'tokyo/minato-city',    '13103', '13103', '13103', 'minatoku',     '13103', 'tokyo/minato',    '13103', '13103'),
  ('新宿区',   'sc_shinjuku',  'tokyo/shinjuku-city',  '13104', '13104', '13104', 'shinjukuku',   '13104', 'tokyo/shinjuku',  '13104', '13104'),
  ('文京区',   'sc_bunkyo',    'tokyo/bunkyo-city',    '13105', '13105', '13105', 'bunkyoku',     '13105', 'tokyo/bunkyo',    '13105', '13105'),
  ('台東区',   'sc_taito',     'tokyo/taito-city',     '13106', '13106', '13106', 'taitoku',      '13106', 'tokyo/taito',     '13106', '13106'),
  ('墨田区',   'sc_sumida',    'tokyo/sumida-city',    '13107', '13107', '13107', 'sumidaku',     '13107', 'tokyo/sumida',    '13107', '13107'),
  ('江東区',   'sc_koto',      'tokyo/koto-city',      '13108', '13108', '13108', 'kotoku',       '13108', 'tokyo/koto',      '13108', '13108'),
  ('品川区',   'sc_shinagawa', 'tokyo/shinagawa-city', '13109', '13109', '13109', 'shinagawaku',  '13109', 'tokyo/shinagawa', '13109', '13109'),
  ('目黒区',   'sc_meguro',    'tokyo/meguro-city',    '13110', '13110', '13110', 'meguroku',     '13110', 'tokyo/meguro',    '13110', '13110'),
  ('大田区',   'sc_ota',       'tokyo/ota-city',       '13111', '13111', '13111', 'otaku',        '13111', 'tokyo/ota',       '13111', '13111'),
  ('世田谷区', 'sc_setagaya',  'tokyo/setagaya-city',  '13112', '13112', '13112', 'setagayaku',   '13112', 'tokyo/setagaya',  '13112', '13112'),
  ('渋谷区',   'sc_shibuya',   'tokyo/shibuya-city',   '13113', '13113', '13113', 'shibuyaku',    '13113', 'tokyo/shibuya',   '13113', '13113'),
  ('中野区',   'sc_nakano',    'tokyo/nakano-city',    '13114', '13114', '13114', 'nakanoku',     '13114', 'tokyo/nakano',    '13114', '13114'),
  ('杉並区',   'sc_suginami',  'tokyo/suginami-city',  '13115', '13115', '13115', 'suginamiku',   '13115', 'tokyo/suginami',  '13115', '13115'),
  ('豊島区',   'sc_toshima',   'tokyo/toshima-city',   '13116', '13116', '13116', 'toshimaku',    '13116', 'tokyo/toshima',   '13116', '13116'),
  ('北区',     'sc_kita',      'tokyo/kita-city',      '13117', '13117', '13117', 'kitaku',       '13117', 'tokyo/kita',      '13117', '13117'),
  ('荒川区',   'sc_arakawa',   'tokyo/arakawa-city',   '13118', '13118', '13118', 'arakawaku',    '13118', 'tokyo/arakawa',   '13118', '13118'),
  ('板橋区',   'sc_itabashi',  'tokyo/itabashi-city',  '13119', '13119', '13119', 'itabashiku',   '13119', 'tokyo/itabashi',  '13119', '13119'),
  ('練馬区',   'sc_nerima',    'tokyo/nerima-city',    '13120', '13120', '13120', 'nerimaku',     '13120', 'tokyo/nerima',    '13120', '13120'),
  ('足立区',   'sc_adachi',    'tokyo/adachi-city',    '13121', '13121', '13121', 'adachiku',     '13121', 'tokyo/adachi',    '13121', '13121'),
  ('葛飾区',   'sc_katsushika','tokyo/katsushika-city','13122', '13122', '13122', 'katsushikaku', '13122', 'tokyo/katsushika','13122', '13122'),
  ('江戸川区', 'sc_edogawa',   'tokyo/edogawa-city',   '13123', '13123', '13123', 'edogawaku',    '13123', 'tokyo/edogawa',   '13123', '13123')
) AS v(canonical_name, suumo, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
JOIN cities c ON c.prefecture = '東京都' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  suumo    = EXCLUDED.suumo,
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini,
  eheya    = EXCLUDED.eheya,
  nifty    = EXCLUDED.nifty,
  apaman   = EXCLUDED.apaman,
  smocca   = EXCLUDED.smocca;

-- ============================================================
-- 神奈川県 — 横浜市18区 / 川崎市7区 / 相模原市3区
-- JIS: 横浜14101-14118, 川崎14131-14137, 相模原14151-14153
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini, v.eheya, v.nifty, v.apaman, v.smocca
FROM (VALUES
  ('横浜市鶴見区',   'kanagawa/yokohama-tsurumi-city',    '14101','14101','14101','14101','kanagawa/yokohama-tsurumi',   'kanagawa/yokohama-tsurumi',   '14101','kanagawa/yokohama-tsurumi'),
  ('横浜市神奈川区', 'kanagawa/yokohama-kanagawa-city',   '14102','14102','14102','14102','kanagawa/yokohama-kanagawa',  'kanagawa/yokohama-kanagawa',  '14102','kanagawa/yokohama-kanagawa'),
  ('横浜市西区',     'kanagawa/yokohama-nishi-city',      '14103','14103','14103','14103','kanagawa/yokohama-nishi',     'kanagawa/yokohama-nishi',     '14103','kanagawa/yokohama-nishi'),
  ('横浜市中区',     'kanagawa/yokohama-naka-city',       '14104','14104','14104','14104','kanagawa/yokohama-naka',      'kanagawa/yokohama-naka',      '14104','kanagawa/yokohama-naka'),
  ('横浜市南区',     'kanagawa/yokohama-minami-city',     '14105','14105','14105','14105','kanagawa/yokohama-minami',    'kanagawa/yokohama-minami',    '14105','kanagawa/yokohama-minami'),
  ('横浜市保土ケ谷区','kanagawa/yokohama-hodogaya-city',  '14106','14106','14106','14106','kanagawa/yokohama-hodogaya',  'kanagawa/yokohama-hodogaya',  '14106','kanagawa/yokohama-hodogaya'),
  ('横浜市磯子区',   'kanagawa/yokohama-isogo-city',      '14107','14107','14107','14107','kanagawa/yokohama-isogo',     'kanagawa/yokohama-isogo',     '14107','kanagawa/yokohama-isogo'),
  ('横浜市金沢区',   'kanagawa/yokohama-kanazawa-city',   '14108','14108','14108','14108','kanagawa/yokohama-kanazawa',  'kanagawa/yokohama-kanazawa',  '14108','kanagawa/yokohama-kanazawa'),
  ('横浜市港北区',   'kanagawa/yokohama-kohoku-city',     '14109','14109','14109','14109','kanagawa/yokohama-kohoku',    'kanagawa/yokohama-kohoku',    '14109','kanagawa/yokohama-kohoku'),
  ('横浜市戸塚区',   'kanagawa/yokohama-totsuka-city',    '14110','14110','14110','14110','kanagawa/yokohama-totsuka',   'kanagawa/yokohama-totsuka',   '14110','kanagawa/yokohama-totsuka'),
  ('横浜市港南区',   'kanagawa/yokohama-konan-city',      '14111','14111','14111','14111','kanagawa/yokohama-konan',     'kanagawa/yokohama-konan',     '14111','kanagawa/yokohama-konan'),
  ('横浜市旭区',     'kanagawa/yokohama-asahi-city',      '14112','14112','14112','14112','kanagawa/yokohama-asahi',     'kanagawa/yokohama-asahi',     '14112','kanagawa/yokohama-asahi'),
  ('横浜市緑区',     'kanagawa/yokohama-midori-city',     '14113','14113','14113','14113','kanagawa/yokohama-midori',    'kanagawa/yokohama-midori',    '14113','kanagawa/yokohama-midori'),
  ('横浜市瀬谷区',   'kanagawa/yokohama-seya-city',       '14114','14114','14114','14114','kanagawa/yokohama-seya',      'kanagawa/yokohama-seya',      '14114','kanagawa/yokohama-seya'),
  ('横浜市栄区',     'kanagawa/yokohama-sakae-city',      '14115','14115','14115','14115','kanagawa/yokohama-sakae',     'kanagawa/yokohama-sakae',     '14115','kanagawa/yokohama-sakae'),
  ('横浜市泉区',     'kanagawa/yokohama-izumi-city',      '14116','14116','14116','14116','kanagawa/yokohama-izumi',     'kanagawa/yokohama-izumi',     '14116','kanagawa/yokohama-izumi'),
  ('横浜市青葉区',   'kanagawa/yokohama-aoba-city',       '14117','14117','14117','14117','kanagawa/yokohama-aoba',      'kanagawa/yokohama-aoba',      '14117','kanagawa/yokohama-aoba'),
  ('横浜市都筑区',   'kanagawa/yokohama-tsuzuki-city',    '14118','14118','14118','14118','kanagawa/yokohama-tsuzuki',   'kanagawa/yokohama-tsuzuki',   '14118','kanagawa/yokohama-tsuzuki'),
  ('川崎市川崎区',   'kanagawa/kawasaki-kawasaki-city',   '14131','14131','14131','14131','kanagawa/kawasaki-kawasaki',  'kanagawa/kawasaki-kawasaki',  '14131','kanagawa/kawasaki-kawasaki'),
  ('川崎市幸区',     'kanagawa/kawasaki-saiwai-city',     '14132','14132','14132','14132','kanagawa/kawasaki-saiwai',    'kanagawa/kawasaki-saiwai',    '14132','kanagawa/kawasaki-saiwai'),
  ('川崎市中原区',   'kanagawa/kawasaki-nakahara-city',   '14133','14133','14133','14133','kanagawa/kawasaki-nakahara',  'kanagawa/kawasaki-nakahara',  '14133','kanagawa/kawasaki-nakahara'),
  ('川崎市高津区',   'kanagawa/kawasaki-takatsu-city',    '14134','14134','14134','14134','kanagawa/kawasaki-takatsu',   'kanagawa/kawasaki-takatsu',   '14134','kanagawa/kawasaki-takatsu'),
  ('川崎市多摩区',   'kanagawa/kawasaki-tama-city',       '14135','14135','14135','14135','kanagawa/kawasaki-tama',      'kanagawa/kawasaki-tama',      '14135','kanagawa/kawasaki-tama'),
  ('川崎市宮前区',   'kanagawa/kawasaki-miyamae-city',    '14136','14136','14136','14136','kanagawa/kawasaki-miyamae',   'kanagawa/kawasaki-miyamae',   '14136','kanagawa/kawasaki-miyamae'),
  ('川崎市麻生区',   'kanagawa/kawasaki-aso-city',        '14137','14137','14137','14137','kanagawa/kawasaki-aso',       'kanagawa/kawasaki-aso',       '14137','kanagawa/kawasaki-aso'),
  ('相模原市緑区',   'kanagawa/sagamihara-midori-city',   '14151','14151','14151','14151','kanagawa/sagamihara-midori',  'kanagawa/sagamihara-midori',  '14151','kanagawa/sagamihara-midori'),
  ('相模原市中央区', 'kanagawa/sagamihara-chuo-city',     '14152','14152','14152','14152','kanagawa/sagamihara-chuo',    'kanagawa/sagamihara-chuo',    '14152','kanagawa/sagamihara-chuo'),
  ('相模原市南区',   'kanagawa/sagamihara-minami-city',   '14153','14153','14153','14153','kanagawa/sagamihara-minami',  'kanagawa/sagamihara-minami',  '14153','kanagawa/sagamihara-minami')
) AS v(canonical_name, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
JOIN cities c ON c.prefecture = '神奈川県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini,
  eheya    = EXCLUDED.eheya,
  nifty    = EXCLUDED.nifty,
  apaman   = EXCLUDED.apaman,
  smocca   = EXCLUDED.smocca;

-- ============================================================
-- 大阪府 — 大阪市24区
-- JIS: 27102〜27128 (飛び番あり)
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini, v.eheya, v.nifty, v.apaman, v.smocca
FROM (VALUES
  ('大阪市都島区',   'osaka/osaka-miyakojima-city',      '27102','27102','27102','27102','osaka/osaka-miyakojima',     'osaka/osaka-miyakojima',     '27102','osaka/osaka-miyakojima'),
  ('大阪市福島区',   'osaka/osaka-fukushima-city',       '27103','27103','27103','27103','osaka/osaka-fukushima',      'osaka/osaka-fukushima',      '27103','osaka/osaka-fukushima'),
  ('大阪市此花区',   'osaka/osaka-konohana-city',        '27104','27104','27104','27104','osaka/osaka-konohana',       'osaka/osaka-konohana',       '27104','osaka/osaka-konohana'),
  ('大阪市中央区',   'osaka/osaka-chuo-city',            '27106','27106','27106','27106','osaka/osaka-chuo',           'osaka/osaka-chuo',           '27106','osaka/osaka-chuo'),
  ('大阪市西区',     'osaka/osaka-nishi-city',           '27107','27107','27107','27107','osaka/osaka-nishi',          'osaka/osaka-nishi',          '27107','osaka/osaka-nishi'),
  ('大阪市港区',     'osaka/osaka-minato-city',          '27108','27108','27108','27108','osaka/osaka-minato',         'osaka/osaka-minato',         '27108','osaka/osaka-minato'),
  ('大阪市大正区',   'osaka/osaka-taisho-city',          '27109','27109','27109','27109','osaka/osaka-taisho',         'osaka/osaka-taisho',         '27109','osaka/osaka-taisho'),
  ('大阪市天王寺区', 'osaka/osaka-tennoji-city',         '27111','27111','27111','27111','osaka/osaka-tennoji',        'osaka/osaka-tennoji',        '27111','osaka/osaka-tennoji'),
  ('大阪市浪速区',   'osaka/osaka-naniwa-city',          '27113','27113','27113','27113','osaka/osaka-naniwa',         'osaka/osaka-naniwa',         '27113','osaka/osaka-naniwa'),
  ('大阪市西淀川区', 'osaka/osaka-nishiyodogawa-city',   '27114','27114','27114','27114','osaka/osaka-nishiyodogawa',  'osaka/osaka-nishiyodogawa',  '27114','osaka/osaka-nishiyodogawa'),
  ('大阪市淀川区',   'osaka/osaka-yodogawa-city',        '27115','27115','27115','27115','osaka/osaka-yodogawa',       'osaka/osaka-yodogawa',       '27115','osaka/osaka-yodogawa'),
  ('大阪市東淀川区', 'osaka/osaka-higashiyodogawa-city', '27116','27116','27116','27116','osaka/osaka-higashiyodogawa','osaka/osaka-higashiyodogawa','27116','osaka/osaka-higashiyodogawa'),
  ('大阪市東成区',   'osaka/osaka-higashinari-city',     '27117','27117','27117','27117','osaka/osaka-higashinari',    'osaka/osaka-higashinari',    '27117','osaka/osaka-higashinari'),
  ('大阪市生野区',   'osaka/osaka-ikuno-city',           '27118','27118','27118','27118','osaka/osaka-ikuno',          'osaka/osaka-ikuno',          '27118','osaka/osaka-ikuno'),
  ('大阪市旭区',     'osaka/osaka-asahi-city',           '27119','27119','27119','27119','osaka/osaka-asahi',          'osaka/osaka-asahi',          '27119','osaka/osaka-asahi'),
  ('大阪市城東区',   'osaka/osaka-joto-city',            '27120','27120','27120','27120','osaka/osaka-joto',           'osaka/osaka-joto',           '27120','osaka/osaka-joto'),
  ('大阪市鶴見区',   'osaka/osaka-tsurumi-city',         '27121','27121','27121','27121','osaka/osaka-tsurumi',        'osaka/osaka-tsurumi',        '27121','osaka/osaka-tsurumi'),
  ('大阪市阿倍野区', 'osaka/osaka-abeno-city',           '27122','27122','27122','27122','osaka/osaka-abeno',          'osaka/osaka-abeno',          '27122','osaka/osaka-abeno'),
  ('大阪市住之江区', 'osaka/osaka-suminoe-city',         '27123','27123','27123','27123','osaka/osaka-suminoe',        'osaka/osaka-suminoe',        '27123','osaka/osaka-suminoe'),
  ('大阪市住吉区',   'osaka/osaka-sumiyoshi-city',       '27124','27124','27124','27124','osaka/osaka-sumiyoshi',      'osaka/osaka-sumiyoshi',      '27124','osaka/osaka-sumiyoshi'),
  ('大阪市東住吉区', 'osaka/osaka-higashisumiyoshi-city','27125','27125','27125','27125','osaka/osaka-higashisumiyoshi','osaka/osaka-higashisumiyoshi','27125','osaka/osaka-higashisumiyoshi'),
  ('大阪市平野区',   'osaka/osaka-hirano-city',          '27126','27126','27126','27126','osaka/osaka-hirano',         'osaka/osaka-hirano',         '27126','osaka/osaka-hirano'),
  ('大阪市西成区',   'osaka/osaka-nishinari-city',       '27127','27127','27127','27127','osaka/osaka-nishinari',      'osaka/osaka-nishinari',      '27127','osaka/osaka-nishinari'),
  ('大阪市北区',     'osaka/osaka-kita-city',            '27128','27128','27128','27128','osaka/osaka-kita',           'osaka/osaka-kita',           '27128','osaka/osaka-kita')
) AS v(canonical_name, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
JOIN cities c ON c.prefecture = '大阪府' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini,
  eheya    = EXCLUDED.eheya,
  nifty    = EXCLUDED.nifty,
  apaman   = EXCLUDED.apaman,
  smocca   = EXCLUDED.smocca;

-- ============================================================
-- 埼玉県 — さいたま市10区
-- JIS: 11101〜11110
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini
FROM (VALUES
  ('さいたま市西区',   'saitama/saitama-nishi-city',    '11101','11101','11101','11101'),
  ('さいたま市北区',   'saitama/saitama-kita-city',     '11102','11102','11102','11102'),
  ('さいたま市大宮区', 'saitama/saitama-omiya-city',    '11103','11103','11103','11103'),
  ('さいたま市見沼区', 'saitama/saitama-minuma-city',   '11104','11104','11104','11104'),
  ('さいたま市中央区', 'saitama/saitama-chuo-city',     '11105','11105','11105','11105'),
  ('さいたま市桜区',   'saitama/saitama-sakura-city',   '11106','11106','11106','11106'),
  ('さいたま市浦和区', 'saitama/saitama-urawa-city',    '11107','11107','11107','11107'),
  ('さいたま市南区',   'saitama/saitama-minami-city',   '11108','11108','11108','11108'),
  ('さいたま市緑区',   'saitama/saitama-midori-city',   '11109','11109','11109','11109'),
  ('さいたま市岩槻区', 'saitama/saitama-iwatsuki-city', '11110','11110','11110','11110')
) AS v(canonical_name, homes, athome, goo, able, minimini)
JOIN cities c ON c.prefecture = '埼玉県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini;

-- ============================================================
-- 千葉県 — 千葉市6区
-- JIS: 12101〜12106
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini
FROM (VALUES
  ('千葉市中央区',  'chiba/chiba-chuo-city',      '12101','12101','12101','12101'),
  ('千葉市花見川区','chiba/chiba-hanamigawa-city', '12102','12102','12102','12102'),
  ('千葉市稲毛区',  'chiba/chiba-inage-city',      '12103','12103','12103','12103'),
  ('千葉市若葉区',  'chiba/chiba-wakaba-city',     '12104','12104','12104','12104'),
  ('千葉市緑区',    'chiba/chiba-midori-city',     '12105','12105','12105','12105'),
  ('千葉市美浜区',  'chiba/chiba-mihama-city',     '12106','12106','12106','12106')
) AS v(canonical_name, homes, athome, goo, able, minimini)
JOIN cities c ON c.prefecture = '千葉県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini;

-- ============================================================
-- 愛知県 — 名古屋市16区
-- JIS: 23101〜23116
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini
FROM (VALUES
  ('名古屋市千種区', 'aichi/nagoya-chikusa-city',  '23101','23101','23101','23101'),
  ('名古屋市東区',   'aichi/nagoya-higashi-city',  '23102','23102','23102','23102'),
  ('名古屋市北区',   'aichi/nagoya-kita-city',     '23103','23103','23103','23103'),
  ('名古屋市西区',   'aichi/nagoya-nishi-city',    '23104','23104','23104','23104'),
  ('名古屋市中村区', 'aichi/nagoya-nakamura-city', '23105','23105','23105','23105'),
  ('名古屋市中区',   'aichi/nagoya-naka-city',     '23106','23106','23106','23106'),
  ('名古屋市昭和区', 'aichi/nagoya-showa-city',    '23107','23107','23107','23107'),
  ('名古屋市瑞穂区', 'aichi/nagoya-mizuho-city',   '23108','23108','23108','23108'),
  ('名古屋市熱田区', 'aichi/nagoya-atsuta-city',   '23109','23109','23109','23109'),
  ('名古屋市中川区', 'aichi/nagoya-nakagawa-city', '23110','23110','23110','23110'),
  ('名古屋市港区',   'aichi/nagoya-minato-city',   '23111','23111','23111','23111'),
  ('名古屋市南区',   'aichi/nagoya-minami-city',   '23112','23112','23112','23112'),
  ('名古屋市守山区', 'aichi/nagoya-moriyama-city', '23113','23113','23113','23113'),
  ('名古屋市緑区',   'aichi/nagoya-midori-city',   '23114','23114','23114','23114'),
  ('名古屋市名東区', 'aichi/nagoya-meito-city',    '23115','23115','23115','23115'),
  ('名古屋市天白区', 'aichi/nagoya-tenpaku-city',  '23116','23116','23116','23116')
) AS v(canonical_name, homes, athome, goo, able, minimini)
JOIN cities c ON c.prefecture = '愛知県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini;

-- ============================================================
-- 京都府 — 京都市11区
-- JIS: 26101〜26111
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini
FROM (VALUES
  ('京都市北区',   'kyoto/kyoto-kita-city',       '26101','26101','26101','26101'),
  ('京都市上京区', 'kyoto/kyoto-kamigyo-city',    '26102','26102','26102','26102'),
  ('京都市左京区', 'kyoto/kyoto-sakyo-city',      '26103','26103','26103','26103'),
  ('京都市中京区', 'kyoto/kyoto-nakagyo-city',    '26104','26104','26104','26104'),
  ('京都市東山区', 'kyoto/kyoto-higashiyama-city','26105','26105','26105','26105'),
  ('京都市下京区', 'kyoto/kyoto-shimogyo-city',   '26106','26106','26106','26106'),
  ('京都市南区',   'kyoto/kyoto-minami-city',     '26107','26107','26107','26107'),
  ('京都市右京区', 'kyoto/kyoto-ukyo-city',       '26108','26108','26108','26108'),
  ('京都市伏見区', 'kyoto/kyoto-fushimi-city',    '26109','26109','26109','26109'),
  ('京都市山科区', 'kyoto/kyoto-yamashina-city',  '26110','26110','26110','26110'),
  ('京都市西京区', 'kyoto/kyoto-nishikyo-city',   '26111','26111','26111','26111')
) AS v(canonical_name, homes, athome, goo, able, minimini)
JOIN cities c ON c.prefecture = '京都府' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini;

-- ============================================================
-- 北海道 — 札幌市10区
-- JIS: 01101〜01110
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini, v.eheya, v.nifty, v.apaman, v.smocca
FROM (VALUES
  ('札幌市中央区', 'hokkaido/sapporo-chuo-city',      '01101','01101','01101','01101','hokkaido/sapporo-chuo',      'hokkaido/sapporo-chuo',      '01101','hokkaido/sapporo-chuo'),
  ('札幌市北区',   'hokkaido/sapporo-kita-city',      '01102','01102','01102','01102','hokkaido/sapporo-kita',      'hokkaido/sapporo-kita',      '01102','hokkaido/sapporo-kita'),
  ('札幌市東区',   'hokkaido/sapporo-higashi-city',   '01103','01103','01103','01103','hokkaido/sapporo-higashi',   'hokkaido/sapporo-higashi',   '01103','hokkaido/sapporo-higashi'),
  ('札幌市白石区', 'hokkaido/sapporo-shiroishi-city', '01104','01104','01104','01104','hokkaido/sapporo-shiroishi', 'hokkaido/sapporo-shiroishi', '01104','hokkaido/sapporo-shiroishi'),
  ('札幌市豊平区', 'hokkaido/sapporo-toyohira-city',  '01105','01105','01105','01105','hokkaido/sapporo-toyohira',  'hokkaido/sapporo-toyohira',  '01105','hokkaido/sapporo-toyohira'),
  ('札幌市南区',   'hokkaido/sapporo-minami-city',    '01106','01106','01106','01106','hokkaido/sapporo-minami',    'hokkaido/sapporo-minami',    '01106','hokkaido/sapporo-minami'),
  ('札幌市西区',   'hokkaido/sapporo-nishi-city',     '01107','01107','01107','01107','hokkaido/sapporo-nishi',     'hokkaido/sapporo-nishi',     '01107','hokkaido/sapporo-nishi'),
  ('札幌市厚別区', 'hokkaido/sapporo-atsubetsu-city', '01108','01108','01108','01108','hokkaido/sapporo-atsubetsu', 'hokkaido/sapporo-atsubetsu', '01108','hokkaido/sapporo-atsubetsu'),
  ('札幌市手稲区', 'hokkaido/sapporo-teine-city',     '01109','01109','01109','01109','hokkaido/sapporo-teine',     'hokkaido/sapporo-teine',     '01109','hokkaido/sapporo-teine'),
  ('札幌市清田区', 'hokkaido/sapporo-kiyota-city',    '01110','01110','01110','01110','hokkaido/sapporo-kiyota',    'hokkaido/sapporo-kiyota',    '01110','hokkaido/sapporo-kiyota')
) AS v(canonical_name, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
JOIN cities c ON c.prefecture = '北海道' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini,
  eheya    = EXCLUDED.eheya,
  nifty    = EXCLUDED.nifty,
  apaman   = EXCLUDED.apaman,
  smocca   = EXCLUDED.smocca;

-- ============================================================
-- 福岡県 — 福岡市7区
-- JIS: 40101〜40137 (飛び番あり)
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini, v.eheya, v.nifty, v.apaman, v.smocca
FROM (VALUES
  ('福岡市東区',   'fukuoka/fukuoka-higashi-city', '40101','40101','40101','40101','fukuoka/fukuoka-higashi', 'fukuoka/fukuoka-higashi', '40101','fukuoka/fukuoka-higashi'),
  ('福岡市博多区', 'fukuoka/fukuoka-hakata-city',  '40102','40102','40102','40102','fukuoka/fukuoka-hakata',  'fukuoka/fukuoka-hakata',  '40102','fukuoka/fukuoka-hakata'),
  ('福岡市中央区', 'fukuoka/fukuoka-chuo-city',    '40103','40103','40103','40103','fukuoka/fukuoka-chuo',    'fukuoka/fukuoka-chuo',    '40103','fukuoka/fukuoka-chuo'),
  ('福岡市南区',   'fukuoka/fukuoka-minami-city',  '40104','40104','40104','40104','fukuoka/fukuoka-minami',  'fukuoka/fukuoka-minami',  '40104','fukuoka/fukuoka-minami'),
  ('福岡市西区',   'fukuoka/fukuoka-nishi-city',   '40105','40105','40105','40105','fukuoka/fukuoka-nishi',   'fukuoka/fukuoka-nishi',   '40105','fukuoka/fukuoka-nishi'),
  ('福岡市城南区', 'fukuoka/fukuoka-jonan-city',   '40131','40131','40131','40131','fukuoka/fukuoka-jonan',   'fukuoka/fukuoka-jonan',   '40131','fukuoka/fukuoka-jonan'),
  ('福岡市早良区', 'fukuoka/fukuoka-sawara-city',  '40137','40137','40137','40137','fukuoka/fukuoka-sawara',  'fukuoka/fukuoka-sawara',  '40137','fukuoka/fukuoka-sawara')
) AS v(canonical_name, homes, athome, goo, able, minimini, eheya, nifty, apaman, smocca)
JOIN cities c ON c.prefecture = '福岡県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini,
  eheya    = EXCLUDED.eheya,
  nifty    = EXCLUDED.nifty,
  apaman   = EXCLUDED.apaman,
  smocca   = EXCLUDED.smocca;

-- ============================================================
-- 兵庫県 — 神戸市9区
-- JIS: 28101〜28111 (飛び番あり)
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini
FROM (VALUES
  ('神戸市東灘区', 'hyogo/kobe-higashinada-city', '28101','28101','28101','28101'),
  ('神戸市灘区',   'hyogo/kobe-nada-city',        '28102','28102','28102','28102'),
  ('神戸市兵庫区', 'hyogo/kobe-hyogo-city',       '28105','28105','28105','28105'),
  ('神戸市長田区', 'hyogo/kobe-nagata-city',      '28106','28106','28106','28106'),
  ('神戸市須磨区', 'hyogo/kobe-suma-city',        '28107','28107','28107','28107'),
  ('神戸市垂水区', 'hyogo/kobe-tarumi-city',      '28108','28108','28108','28108'),
  ('神戸市北区',   'hyogo/kobe-kita-city',        '28109','28109','28109','28109'),
  ('神戸市中央区', 'hyogo/kobe-chuo-city',        '28110','28110','28110','28110'),
  ('神戸市西区',   'hyogo/kobe-nishi-city',       '28111','28111','28111','28111')
) AS v(canonical_name, homes, athome, goo, able, minimini)
JOIN cities c ON c.prefecture = '兵庫県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini;

-- ============================================================
-- 静岡県 — 静岡市3区 / 浜松市3区 (2024年再編後)
-- JIS: 静岡22101-22103, 浜松22131-22133
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini
FROM (VALUES
  ('静岡市葵区',   'shizuoka/shizuoka-aoi-city',     '22101','22101','22101','22101'),
  ('静岡市駿河区', 'shizuoka/shizuoka-suruga-city',  '22102','22102','22102','22102'),
  ('静岡市清水区', 'shizuoka/shizuoka-shimizu-city', '22103','22103','22103','22103'),
  ('浜松市中央区', 'shizuoka/hamamatsu-chuo-city',   '22131','22131','22131','22131'),
  ('浜松市浜名区', 'shizuoka/hamamatsu-hamana-city', '22132','22132','22132','22132'),
  ('浜松市天竜区', 'shizuoka/hamamatsu-tenryu-city', '22133','22133','22133','22133')
) AS v(canonical_name, homes, athome, goo, able, minimini)
JOIN cities c ON c.prefecture = '静岡県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini;

-- ============================================================
-- 新潟県 — 新潟市8区
-- JIS: 15101〜15108
-- ============================================================
INSERT INTO city_site_mappings
  (city_id, homes, athome, goo, able, minimini)
SELECT c.id, v.homes, v.athome, v.goo, v.able, v.minimini
FROM (VALUES
  ('新潟市北区',   'niigata/niigata-kita-city',     '15101','15101','15101','15101'),
  ('新潟市東区',   'niigata/niigata-higashi-city',  '15102','15102','15102','15102'),
  ('新潟市中央区', 'niigata/niigata-chuo-city',     '15103','15103','15103','15103'),
  ('新潟市江南区', 'niigata/niigata-konan-city',    '15104','15104','15104','15104'),
  ('新潟市秋葉区', 'niigata/niigata-akiha-city',    '15105','15105','15105','15105'),
  ('新潟市南区',   'niigata/niigata-minami-city',   '15106','15106','15106','15106'),
  ('新潟市西区',   'niigata/niigata-nishi-city',    '15107','15107','15107','15107'),
  ('新潟市西蒲区', 'niigata/niigata-nishikan-city', '15108','15108','15108','15108')
) AS v(canonical_name, homes, athome, goo, able, minimini)
JOIN cities c ON c.prefecture = '新潟県' AND c.canonical_name = v.canonical_name
ON CONFLICT (city_id) DO UPDATE SET
  homes    = EXCLUDED.homes,
  athome   = EXCLUDED.athome,
  goo      = EXCLUDED.goo,
  able     = EXCLUDED.able,
  minimini = EXCLUDED.minimini;
