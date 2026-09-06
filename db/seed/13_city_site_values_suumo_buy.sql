-- ============================================================
-- m_city_site_values: SUUMO の市区スラグ（売買のSEOパス用 → 課題#4・Phase 6）
--
-- ⚠ **この表を引くのは売買だけ。** 賃貸の一覧は JIS5桁（`sc=13121`）で組み立てる
-- ので不要だが、売買は robots が `/jj/bukken/ichiran/` を**明示的に禁じており**、
-- SEOパス（`/ms/chuko/{pref}/sc_{slug}/`）でしか一覧を取れない。
-- ⚠ `resolve_areas` は検索値の無い市区を**黙って落とす**ので、これが無いまま
-- 組むと帯の大半が空のまま「取得できている」ことになる（→ 課題#36 と同型）。
--
-- 生成は `scripts/tools/collect_city_slugs.py --site SUUMO`（1都3県で4リクエスト）。
-- 取得HTMLは保存してあり `--from-cache` で解析だけやり直せる。**手で編集しない。**
--
-- ⚠ **同定は JIS5桁で行った**（→ ADR 0014）。市区選択ページは JIS とスラグが
-- **別の要素**にあり、リンク `<a href=".../sc_chiyoda/" id="js-linkSc101">` の id は
-- **JIS の下3桁**、実体 `<input name="sc" value="13101">` が5桁を持つ。下3桁で
-- 突き合わせて5桁を得ている。⚠ 課題#4 は「`<option value="13101">千代田区(585)</option>`」
-- と記録していたが、実測（2026-09-06）では **checkbox** でラベルに件数も付かない。
--
-- 実測（2026-09-06）:
--   * リンク 187件 → 一致 173件。⚠ 捨てた14件は**すべて「郡」単位**のリンク
--     （北足立郡・印旛郡など）で、`m_cities` は町村単位なので一致しないのが正常
--   * **既存23行（賃貸由来）と食い違い0件** ＝ スラグは種別によらず共通
--   * **エリア帯82市区を 82/82 カバー**
--   * ⚠ 掲載が無い市区にはリンクが無いので、`m_cities` より少なくなる
--
-- ここに挙げるのは**新規分のみ**（既存23行は 07/08 が持つ。同値を確認済み）。
-- ============================================================

INSERT INTO m_city_site_values (city_id, site_id, value)
SELECT c.id, s.id, v.value
FROM (VALUES
    ('東京都', '八王子市', 'SUUMO', 'sc_hachioji'),
    ('東京都', '立川市', 'SUUMO', 'sc_tachikawa'),
    ('東京都', '武蔵野市', 'SUUMO', 'sc_musashino'),
    ('東京都', '三鷹市', 'SUUMO', 'sc_mitaka'),
    ('東京都', '青梅市', 'SUUMO', 'sc_ome'),
    ('東京都', '府中市', 'SUUMO', 'sc_fuchu'),
    ('東京都', '昭島市', 'SUUMO', 'sc_akishima'),
    ('東京都', '調布市', 'SUUMO', 'sc_chofu'),
    ('東京都', '町田市', 'SUUMO', 'sc_machida'),
    ('東京都', '小金井市', 'SUUMO', 'sc_koganei'),
    ('東京都', '小平市', 'SUUMO', 'sc_kodaira'),
    ('東京都', '日野市', 'SUUMO', 'sc_hino'),
    ('東京都', '東村山市', 'SUUMO', 'sc_higashimurayama'),
    ('東京都', '国分寺市', 'SUUMO', 'sc_kokubunji'),
    ('東京都', '国立市', 'SUUMO', 'sc_kunitachi'),
    ('東京都', '福生市', 'SUUMO', 'sc_fussa'),
    ('東京都', '狛江市', 'SUUMO', 'sc_komae'),
    ('東京都', '東大和市', 'SUUMO', 'sc_higashiyamato'),
    ('東京都', '清瀬市', 'SUUMO', 'sc_kiyose'),
    ('東京都', '東久留米市', 'SUUMO', 'sc_higashikurume'),
    ('東京都', '武蔵村山市', 'SUUMO', 'sc_musashimurayama'),
    ('東京都', '多摩市', 'SUUMO', 'sc_tama'),
    ('東京都', '稲城市', 'SUUMO', 'sc_inagi'),
    ('東京都', '羽村市', 'SUUMO', 'sc_hamura'),
    ('東京都', 'あきる野市', 'SUUMO', 'sc_akiruno'),
    ('東京都', '西東京市', 'SUUMO', 'sc_nishitokyo'),
    ('埼玉県', 'さいたま市西区', 'SUUMO', 'sc_saitamashinishi'),
    ('埼玉県', 'さいたま市北区', 'SUUMO', 'sc_saitamashikita'),
    ('埼玉県', 'さいたま市大宮区', 'SUUMO', 'sc_saitamashiomiya'),
    ('埼玉県', 'さいたま市見沼区', 'SUUMO', 'sc_saitamashiminuma'),
    ('埼玉県', 'さいたま市中央区', 'SUUMO', 'sc_saitamashichuo'),
    ('埼玉県', 'さいたま市桜区', 'SUUMO', 'sc_saitamashisakura'),
    ('埼玉県', 'さいたま市浦和区', 'SUUMO', 'sc_saitamashiurawa'),
    ('埼玉県', 'さいたま市南区', 'SUUMO', 'sc_saitamashiminami'),
    ('埼玉県', 'さいたま市緑区', 'SUUMO', 'sc_saitamashimidori'),
    ('埼玉県', 'さいたま市岩槻区', 'SUUMO', 'sc_saitamashiiwatsuki'),
    ('埼玉県', '川越市', 'SUUMO', 'sc_kawagoe'),
    ('埼玉県', '熊谷市', 'SUUMO', 'sc_kumagaya'),
    ('埼玉県', '川口市', 'SUUMO', 'sc_kawaguchi'),
    ('埼玉県', '所沢市', 'SUUMO', 'sc_tokorozawa'),
    ('埼玉県', '飯能市', 'SUUMO', 'sc_hanno'),
    ('埼玉県', '加須市', 'SUUMO', 'sc_kazo'),
    ('埼玉県', '本庄市', 'SUUMO', 'sc_honjo'),
    ('埼玉県', '東松山市', 'SUUMO', 'sc_higashimatsuyama'),
    ('埼玉県', '春日部市', 'SUUMO', 'sc_kasukabe'),
    ('埼玉県', '狭山市', 'SUUMO', 'sc_sayama'),
    ('埼玉県', '鴻巣市', 'SUUMO', 'sc_konosu'),
    ('埼玉県', '深谷市', 'SUUMO', 'sc_fukaya'),
    ('埼玉県', '上尾市', 'SUUMO', 'sc_ageo'),
    ('埼玉県', '草加市', 'SUUMO', 'sc_soka'),
    ('埼玉県', '越谷市', 'SUUMO', 'sc_koshigaya'),
    ('埼玉県', '蕨市', 'SUUMO', 'sc_warabi'),
    ('埼玉県', '戸田市', 'SUUMO', 'sc_toda'),
    ('埼玉県', '入間市', 'SUUMO', 'sc_iruma'),
    ('埼玉県', '朝霞市', 'SUUMO', 'sc_asaka'),
    ('埼玉県', '志木市', 'SUUMO', 'sc_shiki'),
    ('埼玉県', '和光市', 'SUUMO', 'sc_wako'),
    ('埼玉県', '新座市', 'SUUMO', 'sc_niiza'),
    ('埼玉県', '桶川市', 'SUUMO', 'sc_okegawa'),
    ('埼玉県', '久喜市', 'SUUMO', 'sc_kuki'),
    ('埼玉県', '北本市', 'SUUMO', 'sc_kitamoto'),
    ('埼玉県', '八潮市', 'SUUMO', 'sc_yashio'),
    ('埼玉県', '富士見市', 'SUUMO', 'sc_fujimi'),
    ('埼玉県', '三郷市', 'SUUMO', 'sc_misato'),
    ('埼玉県', '蓮田市', 'SUUMO', 'sc_hasuda'),
    ('埼玉県', '坂戸市', 'SUUMO', 'sc_sakado'),
    ('埼玉県', '幸手市', 'SUUMO', 'sc_satte'),
    ('埼玉県', '鶴ヶ島市', 'SUUMO', 'sc_tsurugashima'),
    ('埼玉県', '吉川市', 'SUUMO', 'sc_yoshikawa'),
    ('埼玉県', 'ふじみ野市', 'SUUMO', 'sc_fujimino'),
    ('埼玉県', '白岡市', 'SUUMO', 'sc_shiraoka'),
    ('千葉県', '千葉市中央区', 'SUUMO', 'sc_chibashichuo'),
    ('千葉県', '千葉市花見川区', 'SUUMO', 'sc_chibashihanamigawa'),
    ('千葉県', '千葉市稲毛区', 'SUUMO', 'sc_chibashiinage'),
    ('千葉県', '千葉市若葉区', 'SUUMO', 'sc_chibashiwakaba'),
    ('千葉県', '千葉市緑区', 'SUUMO', 'sc_chibashimidori'),
    ('千葉県', '千葉市美浜区', 'SUUMO', 'sc_chibashimihama'),
    ('千葉県', '銚子市', 'SUUMO', 'sc_choshi'),
    ('千葉県', '市川市', 'SUUMO', 'sc_ichikawa'),
    ('千葉県', '船橋市', 'SUUMO', 'sc_funabashi'),
    ('千葉県', '館山市', 'SUUMO', 'sc_tateyama'),
    ('千葉県', '木更津市', 'SUUMO', 'sc_kisarazu'),
    ('千葉県', '松戸市', 'SUUMO', 'sc_matsudo'),
    ('千葉県', '野田市', 'SUUMO', 'sc_noda'),
    ('千葉県', '茂原市', 'SUUMO', 'sc_mobara'),
    ('千葉県', '成田市', 'SUUMO', 'sc_narita'),
    ('千葉県', '佐倉市', 'SUUMO', 'sc_sakura'),
    ('千葉県', '東金市', 'SUUMO', 'sc_togane'),
    ('千葉県', '習志野市', 'SUUMO', 'sc_narashino'),
    ('千葉県', '柏市', 'SUUMO', 'sc_kashiwa'),
    ('千葉県', '勝浦市', 'SUUMO', 'sc_katsuura'),
    ('千葉県', '市原市', 'SUUMO', 'sc_ichihara'),
    ('千葉県', '流山市', 'SUUMO', 'sc_nagareyama'),
    ('千葉県', '八千代市', 'SUUMO', 'sc_yachiyo'),
    ('千葉県', '我孫子市', 'SUUMO', 'sc_abiko'),
    ('千葉県', '鴨川市', 'SUUMO', 'sc_kamogawa'),
    ('千葉県', '鎌ケ谷市', 'SUUMO', 'sc_kamagaya'),
    ('千葉県', '君津市', 'SUUMO', 'sc_kimitsu'),
    ('千葉県', '浦安市', 'SUUMO', 'sc_urayasu'),
    ('千葉県', '四街道市', 'SUUMO', 'sc_yotsukaido'),
    ('千葉県', '袖ケ浦市', 'SUUMO', 'sc_sodegaura'),
    ('千葉県', '印西市', 'SUUMO', 'sc_inzai'),
    ('千葉県', '白井市', 'SUUMO', 'sc_shiroi'),
    ('千葉県', '富里市', 'SUUMO', 'sc_tomisato'),
    ('千葉県', '南房総市', 'SUUMO', 'sc_minamiboso'),
    ('千葉県', '山武市', 'SUUMO', 'sc_sammu'),
    ('千葉県', 'いすみ市', 'SUUMO', 'sc_isumi'),
    ('神奈川県', '横浜市鶴見区', 'SUUMO', 'sc_yokohamashitsurumi'),
    ('神奈川県', '横浜市神奈川区', 'SUUMO', 'sc_yokohamashikanagawa'),
    ('神奈川県', '横浜市西区', 'SUUMO', 'sc_yokohamashinishi'),
    ('神奈川県', '横浜市中区', 'SUUMO', 'sc_yokohamashinaka'),
    ('神奈川県', '横浜市南区', 'SUUMO', 'sc_yokohamashiminami'),
    ('神奈川県', '横浜市保土ケ谷区', 'SUUMO', 'sc_yokohamashihodogaya'),
    ('神奈川県', '横浜市磯子区', 'SUUMO', 'sc_yokohamashiisogo'),
    ('神奈川県', '横浜市金沢区', 'SUUMO', 'sc_yokohamashikanazawa'),
    ('神奈川県', '横浜市港北区', 'SUUMO', 'sc_yokohamashikohoku'),
    ('神奈川県', '横浜市戸塚区', 'SUUMO', 'sc_yokohamashitotsuka'),
    ('神奈川県', '横浜市港南区', 'SUUMO', 'sc_yokohamashikonan'),
    ('神奈川県', '横浜市旭区', 'SUUMO', 'sc_yokohamashiasahi'),
    ('神奈川県', '横浜市緑区', 'SUUMO', 'sc_yokohamashimidori'),
    ('神奈川県', '横浜市瀬谷区', 'SUUMO', 'sc_yokohamashiseya'),
    ('神奈川県', '横浜市栄区', 'SUUMO', 'sc_yokohamashisakae'),
    ('神奈川県', '横浜市泉区', 'SUUMO', 'sc_yokohamashiizumi'),
    ('神奈川県', '横浜市青葉区', 'SUUMO', 'sc_yokohamashiaoba'),
    ('神奈川県', '横浜市都筑区', 'SUUMO', 'sc_yokohamashitsuzuki'),
    ('神奈川県', '川崎市川崎区', 'SUUMO', 'sc_kawasakishikawasaki'),
    ('神奈川県', '川崎市幸区', 'SUUMO', 'sc_kawasakishisaiwai'),
    ('神奈川県', '川崎市中原区', 'SUUMO', 'sc_kawasakishinakahara'),
    ('神奈川県', '川崎市高津区', 'SUUMO', 'sc_kawasakishitakatsu'),
    ('神奈川県', '川崎市多摩区', 'SUUMO', 'sc_kawasakishitama'),
    ('神奈川県', '川崎市宮前区', 'SUUMO', 'sc_kawasakishimiyamae'),
    ('神奈川県', '川崎市麻生区', 'SUUMO', 'sc_kawasakishiasao'),
    ('神奈川県', '相模原市緑区', 'SUUMO', 'sc_sagamiharashimidori'),
    ('神奈川県', '相模原市中央区', 'SUUMO', 'sc_sagamiharashichuo'),
    ('神奈川県', '相模原市南区', 'SUUMO', 'sc_sagamiharashiminami'),
    ('神奈川県', '横須賀市', 'SUUMO', 'sc_yokosuka'),
    ('神奈川県', '平塚市', 'SUUMO', 'sc_hiratsuka'),
    ('神奈川県', '鎌倉市', 'SUUMO', 'sc_kamakura'),
    ('神奈川県', '藤沢市', 'SUUMO', 'sc_fujisawa'),
    ('神奈川県', '小田原市', 'SUUMO', 'sc_odawara'),
    ('神奈川県', '茅ヶ崎市', 'SUUMO', 'sc_chigasaki'),
    ('神奈川県', '逗子市', 'SUUMO', 'sc_zushi'),
    ('神奈川県', '三浦市', 'SUUMO', 'sc_miura'),
    ('神奈川県', '秦野市', 'SUUMO', 'sc_hadano'),
    ('神奈川県', '厚木市', 'SUUMO', 'sc_atsugi'),
    ('神奈川県', '大和市', 'SUUMO', 'sc_yamato'),
    ('神奈川県', '伊勢原市', 'SUUMO', 'sc_isehara'),
    ('神奈川県', '海老名市', 'SUUMO', 'sc_ebina'),
    ('神奈川県', '座間市', 'SUUMO', 'sc_zama'),
    ('神奈川県', '綾瀬市', 'SUUMO', 'sc_ayase')
) AS v(prefecture, city_name, site_code, value)
JOIN m_cities c ON c.prefecture = v.prefecture AND c.canonical_name = v.city_name
JOIN m_sites  s ON s.code = v.site_code
ON CONFLICT (city_id, site_id) DO UPDATE SET
    value      = EXCLUDED.value,
    updated_at = now();
