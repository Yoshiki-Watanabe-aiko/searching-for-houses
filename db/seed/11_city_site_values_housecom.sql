-- ============================================================
-- m_city_site_values: ハウスコムの市区スラグ（Phase 5H・→ 課題#37）
--
-- 収集元は各都道府県の索引ページ（`/tokyo/` 等）で **1都道府県1リクエスト**。
-- `scripts/tools/probe_portals.py --stage slugs` の生成物なので**手で編集しない**。
--
-- ⚠⚠ **政令指定都市の行政区はアンダースコア区切り**（`saitamashi_minamiku`）。
-- 収集の正規表現を `[a-z0-9-]+` にすると**27市区が黙って落ちる**
-- （さいたま市・川崎市・横浜市の全区＝エリア帯2の主要エリア）。
-- `resolve_areas` は検索値の無い市区を黙って対象から外すので、
-- 歯抜けのまま「一巡した」ことになる（→ 課題#36・詳細設計書 §13.8）。
--
-- ⚠ **値は都道府県を含めない**（`adachiku`）。ハウスコムの都道府県スラグは
-- `PREFECTURE_ROMAJI` と一致するのでアダプタ側で導出する。
--
-- ⚠ **市区名で m_cities と突き合わせている**（レオパレスと違いスラグにJIS5桁が
-- 埋まっていないため）。⚠ 郡部は**マスタが郡名なし**（`日の出町`）で
-- **サイトが郡名つき**（`西多摩郡日の出町`）なので、郡名を落とした形でも照合する。
-- ⚠ ただし **同一都道府県内で一意なときだけ**採用する
-- （全国での部分文字列一致は他市のコード混入を招く → ADR 0014）。
--
-- 1都3県の4リクエストで 224 市区を収集し、エリア帯の 82/82 市区をカバーする。
-- ============================================================

INSERT INTO m_city_site_values (city_id, site_id, value)
SELECT c.id, s.id, v.value
FROM (VALUES
    ('11101', 'HOUSECOM', 'saitamashi_nishiku'),  -- 埼玉県さいたま市西区
    ('11102', 'HOUSECOM', 'saitamashi_kitaku'),  -- 埼玉県さいたま市北区
    ('11103', 'HOUSECOM', 'saitamashi_omiyaku'),  -- 埼玉県さいたま市大宮区
    ('11104', 'HOUSECOM', 'saitamashi_minumaku'),  -- 埼玉県さいたま市見沼区
    ('11105', 'HOUSECOM', 'saitamashi_chuoku'),  -- 埼玉県さいたま市中央区
    ('11106', 'HOUSECOM', 'saitamashi_sakuraku'),  -- 埼玉県さいたま市桜区
    ('11107', 'HOUSECOM', 'saitamashi_urawaku'),  -- 埼玉県さいたま市浦和区
    ('11108', 'HOUSECOM', 'saitamashi_minamiku'),  -- 埼玉県さいたま市南区
    ('11109', 'HOUSECOM', 'saitamashi_midoriku'),  -- 埼玉県さいたま市緑区
    ('11110', 'HOUSECOM', 'saitamashi_iwatsukiku'),  -- 埼玉県さいたま市岩槻区
    ('11201', 'HOUSECOM', 'kawagoeshi'),  -- 埼玉県川越市
    ('11202', 'HOUSECOM', 'kumagayashi'),  -- 埼玉県熊谷市
    ('11203', 'HOUSECOM', 'kawaguchishi'),  -- 埼玉県川口市
    ('11206', 'HOUSECOM', 'gyodashi'),  -- 埼玉県行田市
    ('11207', 'HOUSECOM', 'chichibushi'),  -- 埼玉県秩父市
    ('11208', 'HOUSECOM', 'tokorozawashi'),  -- 埼玉県所沢市
    ('11209', 'HOUSECOM', 'hannoshi'),  -- 埼玉県飯能市
    ('11210', 'HOUSECOM', 'kazoshi'),  -- 埼玉県加須市
    ('11211', 'HOUSECOM', 'honjoshi'),  -- 埼玉県本庄市
    ('11212', 'HOUSECOM', 'higashimatsuyamashi'),  -- 埼玉県東松山市
    ('11214', 'HOUSECOM', 'kasukabeshi'),  -- 埼玉県春日部市
    ('11215', 'HOUSECOM', 'sayamashi'),  -- 埼玉県狭山市
    ('11216', 'HOUSECOM', 'hanyushi'),  -- 埼玉県羽生市
    ('11217', 'HOUSECOM', 'konosushi'),  -- 埼玉県鴻巣市
    ('11218', 'HOUSECOM', 'fukayashi'),  -- 埼玉県深谷市
    ('11219', 'HOUSECOM', 'ageoshi'),  -- 埼玉県上尾市
    ('11221', 'HOUSECOM', 'sokashi'),  -- 埼玉県草加市
    ('11222', 'HOUSECOM', 'koshigayashi'),  -- 埼玉県越谷市
    ('11223', 'HOUSECOM', 'warabishi'),  -- 埼玉県蕨市
    ('11224', 'HOUSECOM', 'todashi'),  -- 埼玉県戸田市
    ('11225', 'HOUSECOM', 'irumashi'),  -- 埼玉県入間市
    ('11227', 'HOUSECOM', 'asakashi'),  -- 埼玉県朝霞市
    ('11228', 'HOUSECOM', 'shikishi'),  -- 埼玉県志木市
    ('11229', 'HOUSECOM', 'wakoshi'),  -- 埼玉県和光市
    ('11230', 'HOUSECOM', 'niizashi'),  -- 埼玉県新座市
    ('11231', 'HOUSECOM', 'okegawashi'),  -- 埼玉県桶川市
    ('11232', 'HOUSECOM', 'kukishi'),  -- 埼玉県久喜市
    ('11233', 'HOUSECOM', 'kitamotoshi'),  -- 埼玉県北本市
    ('11234', 'HOUSECOM', 'yashioshi'),  -- 埼玉県八潮市
    ('11235', 'HOUSECOM', 'fujimishi'),  -- 埼玉県富士見市
    ('11237', 'HOUSECOM', 'misatoshi'),  -- 埼玉県三郷市
    ('11238', 'HOUSECOM', 'hasudashi'),  -- 埼玉県蓮田市
    ('11239', 'HOUSECOM', 'sakadoshi'),  -- 埼玉県坂戸市
    ('11240', 'HOUSECOM', 'satteshi'),  -- 埼玉県幸手市
    ('11241', 'HOUSECOM', 'tsurugashimashi'),  -- 埼玉県鶴ヶ島市
    ('11242', 'HOUSECOM', 'hidakashi'),  -- 埼玉県日高市
    ('11243', 'HOUSECOM', 'yoshikawashi'),  -- 埼玉県吉川市
    ('11245', 'HOUSECOM', 'fujiminoshi'),  -- 埼玉県ふじみ野市
    ('11246', 'HOUSECOM', 'shiraokashi'),  -- 埼玉県白岡市
    ('11301', 'HOUSECOM', 'kitaadachigun_inamachi'),  -- 埼玉県北足立郡伊奈町
    ('11324', 'HOUSECOM', 'irumagun_miyoshimachi'),  -- 埼玉県入間郡三芳町
    ('11326', 'HOUSECOM', 'irumagun_moroyamamachi'),  -- 埼玉県入間郡毛呂山町
    ('11327', 'HOUSECOM', 'irumagun_ogosemachi'),  -- 埼玉県入間郡越生町
    ('11341', 'HOUSECOM', 'hikigun_namegawamachi'),  -- 埼玉県比企郡滑川町
    ('11342', 'HOUSECOM', 'hikigun_ranzanmachi'),  -- 埼玉県比企郡嵐山町
    ('11343', 'HOUSECOM', 'hikigun_ogawamachi'),  -- 埼玉県比企郡小川町
    ('11346', 'HOUSECOM', 'hikigun_kawajimamachi'),  -- 埼玉県比企郡川島町
    ('11347', 'HOUSECOM', 'hikigun_yoshimimachi'),  -- 埼玉県比企郡吉見町
    ('11348', 'HOUSECOM', 'hikigun_hatoyamamachi'),  -- 埼玉県比企郡鳩山町
    ('11361', 'HOUSECOM', 'chichibugun_yokozemachi'),  -- 埼玉県秩父郡横瀬町
    ('11362', 'HOUSECOM', 'chichibugun_minanomachi'),  -- 埼玉県秩父郡皆野町
    ('11363', 'HOUSECOM', 'chichibugun_nagatoromachi'),  -- 埼玉県秩父郡長瀞町
    ('11365', 'HOUSECOM', 'chichibugun_oganomachi'),  -- 埼玉県秩父郡小鹿野町
    ('11381', 'HOUSECOM', 'kodamagun_misatomachi'),  -- 埼玉県児玉郡美里町
    ('11383', 'HOUSECOM', 'kodamagun_kamikawamachi'),  -- 埼玉県児玉郡神川町
    ('11385', 'HOUSECOM', 'kodamagun_kamisatomachi'),  -- 埼玉県児玉郡上里町
    ('11408', 'HOUSECOM', 'osatogun_yoriimachi'),  -- 埼玉県大里郡寄居町
    ('11442', 'HOUSECOM', 'minamisaitamagun_miyashiromachi'),  -- 埼玉県南埼玉郡宮代町
    ('11464', 'HOUSECOM', 'kitakatsushikagun_sugitomachi'),  -- 埼玉県北葛飾郡杉戸町
    ('11465', 'HOUSECOM', 'kitakatsushikagun_matsubushimachi'),  -- 埼玉県北葛飾郡松伏町
    ('12101', 'HOUSECOM', 'chibashi_chuoku'),  -- 千葉県千葉市中央区
    ('12102', 'HOUSECOM', 'chibashi_hanamigawaku'),  -- 千葉県千葉市花見川区
    ('12103', 'HOUSECOM', 'chibashi_inageku'),  -- 千葉県千葉市稲毛区
    ('12104', 'HOUSECOM', 'chibashi_wakabaku'),  -- 千葉県千葉市若葉区
    ('12105', 'HOUSECOM', 'chibashi_midoriku'),  -- 千葉県千葉市緑区
    ('12106', 'HOUSECOM', 'chibashi_mihamaku'),  -- 千葉県千葉市美浜区
    ('12202', 'HOUSECOM', 'choshishi'),  -- 千葉県銚子市
    ('12203', 'HOUSECOM', 'ichikawashi'),  -- 千葉県市川市
    ('12204', 'HOUSECOM', 'funabashishi'),  -- 千葉県船橋市
    ('12205', 'HOUSECOM', 'tateyamashi'),  -- 千葉県館山市
    ('12206', 'HOUSECOM', 'kisarazushi'),  -- 千葉県木更津市
    ('12207', 'HOUSECOM', 'matsudoshi'),  -- 千葉県松戸市
    ('12208', 'HOUSECOM', 'nodashi'),  -- 千葉県野田市
    ('12210', 'HOUSECOM', 'mobarashi'),  -- 千葉県茂原市
    ('12211', 'HOUSECOM', 'naritashi'),  -- 千葉県成田市
    ('12212', 'HOUSECOM', 'sakurashi'),  -- 千葉県佐倉市
    ('12213', 'HOUSECOM', 'toganeshi'),  -- 千葉県東金市
    ('12215', 'HOUSECOM', 'asahishi'),  -- 千葉県旭市
    ('12216', 'HOUSECOM', 'narashinoshi'),  -- 千葉県習志野市
    ('12217', 'HOUSECOM', 'kashiwashi'),  -- 千葉県柏市
    ('12219', 'HOUSECOM', 'ichiharashi'),  -- 千葉県市原市
    ('12220', 'HOUSECOM', 'nagareyamashi'),  -- 千葉県流山市
    ('12221', 'HOUSECOM', 'yachiyoshi'),  -- 千葉県八千代市
    ('12222', 'HOUSECOM', 'abikoshi'),  -- 千葉県我孫子市
    ('12223', 'HOUSECOM', 'kamogawashi'),  -- 千葉県鴨川市
    ('12224', 'HOUSECOM', 'kamagayashi'),  -- 千葉県鎌ケ谷市
    ('12225', 'HOUSECOM', 'kimitsushi'),  -- 千葉県君津市
    ('12226', 'HOUSECOM', 'futtsushi'),  -- 千葉県富津市
    ('12227', 'HOUSECOM', 'urayasushi'),  -- 千葉県浦安市
    ('12228', 'HOUSECOM', 'yotsukaidoshi'),  -- 千葉県四街道市
    ('12229', 'HOUSECOM', 'sodegaurashi'),  -- 千葉県袖ケ浦市
    ('12230', 'HOUSECOM', 'yachimatashi'),  -- 千葉県八街市
    ('12231', 'HOUSECOM', 'inzaishi'),  -- 千葉県印西市
    ('12232', 'HOUSECOM', 'shiroishi'),  -- 千葉県白井市
    ('12233', 'HOUSECOM', 'tomisatoshi'),  -- 千葉県富里市
    ('12234', 'HOUSECOM', 'minamibososhi'),  -- 千葉県南房総市
    ('12235', 'HOUSECOM', 'sosashi'),  -- 千葉県匝瑳市
    ('12236', 'HOUSECOM', 'katorishi'),  -- 千葉県香取市
    ('12237', 'HOUSECOM', 'sammushi'),  -- 千葉県山武市
    ('12239', 'HOUSECOM', 'oamishirasatoshi'),  -- 千葉県大網白里市
    ('12322', 'HOUSECOM', 'imbagun_shisuimachi'),  -- 千葉県印旛郡酒々井町
    ('12329', 'HOUSECOM', 'imbagun_sakaemachi'),  -- 千葉県印旛郡栄町
    ('12409', 'HOUSECOM', 'sambugun_shibayamamachi'),  -- 千葉県山武郡芝山町
    ('12410', 'HOUSECOM', 'sambugun_yokoshibahikarimachi'),  -- 千葉県山武郡横芝光町
    ('12421', 'HOUSECOM', 'choseigun_ichinomiyamachi'),  -- 千葉県長生郡一宮町
    ('12423', 'HOUSECOM', 'choseigun_choseimura'),  -- 千葉県長生郡長生村
    ('12424', 'HOUSECOM', 'choseigun_shirakomachi'),  -- 千葉県長生郡白子町
    ('13101', 'HOUSECOM', 'chiyodaku'),  -- 東京都千代田区
    ('13102', 'HOUSECOM', 'chuoku'),  -- 東京都中央区
    ('13103', 'HOUSECOM', 'minatoku'),  -- 東京都港区
    ('13104', 'HOUSECOM', 'shinjukuku'),  -- 東京都新宿区
    ('13105', 'HOUSECOM', 'bunkyoku'),  -- 東京都文京区
    ('13106', 'HOUSECOM', 'taitoku'),  -- 東京都台東区
    ('13107', 'HOUSECOM', 'sumidaku'),  -- 東京都墨田区
    ('13108', 'HOUSECOM', 'kotoku'),  -- 東京都江東区
    ('13109', 'HOUSECOM', 'shinagawaku'),  -- 東京都品川区
    ('13110', 'HOUSECOM', 'meguroku'),  -- 東京都目黒区
    ('13111', 'HOUSECOM', 'otaku'),  -- 東京都大田区
    ('13112', 'HOUSECOM', 'setagayaku'),  -- 東京都世田谷区
    ('13113', 'HOUSECOM', 'shibuyaku'),  -- 東京都渋谷区
    ('13114', 'HOUSECOM', 'nakanoku'),  -- 東京都中野区
    ('13115', 'HOUSECOM', 'suginamiku'),  -- 東京都杉並区
    ('13116', 'HOUSECOM', 'toshimaku'),  -- 東京都豊島区
    ('13117', 'HOUSECOM', 'kitaku'),  -- 東京都北区
    ('13118', 'HOUSECOM', 'arakawaku'),  -- 東京都荒川区
    ('13119', 'HOUSECOM', 'itabashiku'),  -- 東京都板橋区
    ('13120', 'HOUSECOM', 'nerimaku'),  -- 東京都練馬区
    ('13121', 'HOUSECOM', 'adachiku'),  -- 東京都足立区
    ('13122', 'HOUSECOM', 'katsushikaku'),  -- 東京都葛飾区
    ('13123', 'HOUSECOM', 'edogawaku'),  -- 東京都江戸川区
    ('13201', 'HOUSECOM', 'hachiojishi'),  -- 東京都八王子市
    ('13202', 'HOUSECOM', 'tachikawashi'),  -- 東京都立川市
    ('13203', 'HOUSECOM', 'musashinoshi'),  -- 東京都武蔵野市
    ('13204', 'HOUSECOM', 'mitakashi'),  -- 東京都三鷹市
    ('13205', 'HOUSECOM', 'omeshi'),  -- 東京都青梅市
    ('13206', 'HOUSECOM', 'fuchushi'),  -- 東京都府中市
    ('13207', 'HOUSECOM', 'akishimashi'),  -- 東京都昭島市
    ('13208', 'HOUSECOM', 'chofushi'),  -- 東京都調布市
    ('13209', 'HOUSECOM', 'machidashi'),  -- 東京都町田市
    ('13210', 'HOUSECOM', 'koganeishi'),  -- 東京都小金井市
    ('13211', 'HOUSECOM', 'kodairashi'),  -- 東京都小平市
    ('13212', 'HOUSECOM', 'hinoshi'),  -- 東京都日野市
    ('13213', 'HOUSECOM', 'higashimurayamashi'),  -- 東京都東村山市
    ('13214', 'HOUSECOM', 'kokubunjishi'),  -- 東京都国分寺市
    ('13215', 'HOUSECOM', 'kunitachishi'),  -- 東京都国立市
    ('13218', 'HOUSECOM', 'fussashi'),  -- 東京都福生市
    ('13219', 'HOUSECOM', 'komaeshi'),  -- 東京都狛江市
    ('13220', 'HOUSECOM', 'higashiyamatoshi'),  -- 東京都東大和市
    ('13221', 'HOUSECOM', 'kiyoseshi'),  -- 東京都清瀬市
    ('13222', 'HOUSECOM', 'higashikurumeshi'),  -- 東京都東久留米市
    ('13223', 'HOUSECOM', 'musashimurayamashi'),  -- 東京都武蔵村山市
    ('13224', 'HOUSECOM', 'tamashi'),  -- 東京都多摩市
    ('13225', 'HOUSECOM', 'inagishi'),  -- 東京都稲城市
    ('13227', 'HOUSECOM', 'hamurashi'),  -- 東京都羽村市
    ('13228', 'HOUSECOM', 'akirunoshi'),  -- 東京都あきる野市
    ('13229', 'HOUSECOM', 'nishitokyoshi'),  -- 東京都西東京市
    ('13303', 'HOUSECOM', 'nishitamagun_mizuhomachi'),  -- 東京都西多摩郡瑞穂町
    ('13305', 'HOUSECOM', 'nishitamagun_hinodemachi'),  -- 東京都西多摩郡日の出町
    ('14101', 'HOUSECOM', 'yokohamashi_tsurumiku'),  -- 神奈川県横浜市鶴見区
    ('14102', 'HOUSECOM', 'yokohamashi_kanagawaku'),  -- 神奈川県横浜市神奈川区
    ('14103', 'HOUSECOM', 'yokohamashi_nishiku'),  -- 神奈川県横浜市西区
    ('14104', 'HOUSECOM', 'yokohamashi_nakaku'),  -- 神奈川県横浜市中区
    ('14105', 'HOUSECOM', 'yokohamashi_minamiku'),  -- 神奈川県横浜市南区
    ('14106', 'HOUSECOM', 'yokohamashi_hodogayaku'),  -- 神奈川県横浜市保土ケ谷区
    ('14107', 'HOUSECOM', 'yokohamashi_isogoku'),  -- 神奈川県横浜市磯子区
    ('14108', 'HOUSECOM', 'yokohamashi_kanazawaku'),  -- 神奈川県横浜市金沢区
    ('14109', 'HOUSECOM', 'yokohamashi_kohokuku'),  -- 神奈川県横浜市港北区
    ('14110', 'HOUSECOM', 'yokohamashi_totsukaku'),  -- 神奈川県横浜市戸塚区
    ('14111', 'HOUSECOM', 'yokohamashi_konanku'),  -- 神奈川県横浜市港南区
    ('14112', 'HOUSECOM', 'yokohamashi_asahiku'),  -- 神奈川県横浜市旭区
    ('14113', 'HOUSECOM', 'yokohamashi_midoriku'),  -- 神奈川県横浜市緑区
    ('14114', 'HOUSECOM', 'yokohamashi_seyaku'),  -- 神奈川県横浜市瀬谷区
    ('14115', 'HOUSECOM', 'yokohamashi_sakaeku'),  -- 神奈川県横浜市栄区
    ('14116', 'HOUSECOM', 'yokohamashi_izumiku'),  -- 神奈川県横浜市泉区
    ('14117', 'HOUSECOM', 'yokohamashi_aobaku'),  -- 神奈川県横浜市青葉区
    ('14118', 'HOUSECOM', 'yokohamashi_tsuzukiku'),  -- 神奈川県横浜市都筑区
    ('14131', 'HOUSECOM', 'kawasakishi_kawasakiku'),  -- 神奈川県川崎市川崎区
    ('14132', 'HOUSECOM', 'kawasakishi_saiwaiku'),  -- 神奈川県川崎市幸区
    ('14133', 'HOUSECOM', 'kawasakishi_nakaharaku'),  -- 神奈川県川崎市中原区
    ('14134', 'HOUSECOM', 'kawasakishi_takatsuku'),  -- 神奈川県川崎市高津区
    ('14135', 'HOUSECOM', 'kawasakishi_tamaku'),  -- 神奈川県川崎市多摩区
    ('14136', 'HOUSECOM', 'kawasakishi_miyamaeku'),  -- 神奈川県川崎市宮前区
    ('14137', 'HOUSECOM', 'kawasakishi_asaoku'),  -- 神奈川県川崎市麻生区
    ('14151', 'HOUSECOM', 'sagamiharashi_midoriku'),  -- 神奈川県相模原市緑区
    ('14152', 'HOUSECOM', 'sagamiharashi_chuoku'),  -- 神奈川県相模原市中央区
    ('14153', 'HOUSECOM', 'sagamiharashi_minamiku'),  -- 神奈川県相模原市南区
    ('14201', 'HOUSECOM', 'yokosukashi'),  -- 神奈川県横須賀市
    ('14203', 'HOUSECOM', 'hiratsukashi'),  -- 神奈川県平塚市
    ('14204', 'HOUSECOM', 'kamakurashi'),  -- 神奈川県鎌倉市
    ('14205', 'HOUSECOM', 'fujisawashi'),  -- 神奈川県藤沢市
    ('14206', 'HOUSECOM', 'odawarashi'),  -- 神奈川県小田原市
    ('14207', 'HOUSECOM', 'chigasakishi'),  -- 神奈川県茅ヶ崎市
    ('14208', 'HOUSECOM', 'zushishi'),  -- 神奈川県逗子市
    ('14210', 'HOUSECOM', 'miurashi'),  -- 神奈川県三浦市
    ('14211', 'HOUSECOM', 'hadanoshi'),  -- 神奈川県秦野市
    ('14212', 'HOUSECOM', 'atsugishi'),  -- 神奈川県厚木市
    ('14213', 'HOUSECOM', 'yamatoshi'),  -- 神奈川県大和市
    ('14214', 'HOUSECOM', 'iseharashi'),  -- 神奈川県伊勢原市
    ('14215', 'HOUSECOM', 'ebinashi'),  -- 神奈川県海老名市
    ('14216', 'HOUSECOM', 'zamashi'),  -- 神奈川県座間市
    ('14217', 'HOUSECOM', 'minamiashigarashi'),  -- 神奈川県南足柄市
    ('14218', 'HOUSECOM', 'ayaseshi'),  -- 神奈川県綾瀬市
    ('14301', 'HOUSECOM', 'miuragun_hayamamachi'),  -- 神奈川県三浦郡葉山町
    ('14321', 'HOUSECOM', 'kozagun_samukawamachi'),  -- 神奈川県高座郡寒川町
    ('14341', 'HOUSECOM', 'nakagun_oisomachi'),  -- 神奈川県中郡大磯町
    ('14342', 'HOUSECOM', 'nakagun_ninomiyamachi'),  -- 神奈川県中郡二宮町
    ('14361', 'HOUSECOM', 'ashigarakamigun_nakaimachi'),  -- 神奈川県足柄上郡中井町
    ('14362', 'HOUSECOM', 'ashigarakamigun_oimachi'),  -- 神奈川県足柄上郡大井町
    ('14363', 'HOUSECOM', 'ashigarakamigun_matsudamachi'),  -- 神奈川県足柄上郡松田町
    ('14364', 'HOUSECOM', 'ashigarakamigun_yamakitamachi'),  -- 神奈川県足柄上郡山北町
    ('14366', 'HOUSECOM', 'ashigarakamigun_kaiseimachi'),  -- 神奈川県足柄上郡開成町
    ('14382', 'HOUSECOM', 'ashigarashimogun_hakonemachi'),  -- 神奈川県足柄下郡箱根町
    ('14384', 'HOUSECOM', 'ashigarashimogun_yugawaramachi'),  -- 神奈川県足柄下郡湯河原町
    ('14401', 'HOUSECOM', 'aikogun_aikawamachi')  -- 神奈川県愛甲郡愛川町
) AS v(jis_code, site_code, value)
JOIN m_cities c ON c.jis_code = v.jis_code
JOIN m_sites s ON s.code = v.site_code
ON CONFLICT (city_id, site_id) DO UPDATE SET
    value      = EXCLUDED.value,
    updated_at = now();
