-- ============================================================
-- m_city_site_values: SUUMO の市区スラグの追加13件（→ 課題#61・2026-09-11）
--
-- ⚠ 13番（中古マンションの市区選択ページ・173件）は**掲載の無い市区にリンクが無い**ので、
-- `m_cities` にあるのにスラグが未登録の市町が残っていた。土地の市区選択ページ
-- （`/tochi/{pref}/city/`）は市区が多く（リンク207件）、そこから埋めた。
--
-- 生成は `scripts/tools/collect_city_slugs.py --site SUUMO --tochi --from-cache`
-- （2026-09-11 の着手前実測で保存したHTMLを使い、**取得は0本**）。**手で編集しない。**
--
-- ⚠ **同定は JIS5桁で行った**（→ ADR 0014）。リンクの id（JIS の下3桁）と
-- `<input name="sc" value="…">`（5桁）を突き合わせている。
-- ⚠ **スラグは推測で作れない**（八丈町は `sc_hachijojimahachijo`）。
--
-- 実測（2026-09-11）:
--   * リンク207件 → 一致186件。⚠ 捨てた21件は**すべて「郡」単位**（`m_cities` は町村単位。
--     郡の扱いは論点10 のとおり別の課題）
--   * **既存173件と食い違い0件** ＝ スラグは種別によらず共通（13番の前提どおり）
--   * ここに挙げるのは**新規13件のみ**
--
-- ⚠⚠ **入れた瞬間から売買4パターン（`cities: []`＝4都県全域）の取得対象に広がる。**
-- 次の ScanBuy が13市町の掲載を一斉に「新着」として通知しないよう、**先に `scan --seed`
-- を流す**（→ ADR 0006）。2026-09-11 は検証用の別名パターン（13市町だけ）で seed した。
-- ⚠ 実パターンと同じ名前・同じ市区の絞り込みで seed してはいけない——`prune_scores` が
-- 対象外になった他の市区のスコア行を消す。
-- ⚠ 八丈町・大島町は島嶼部（ユーザー判断で含める・2026-09-11）。
-- ============================================================

INSERT INTO m_city_site_values (city_id, site_id, value)
SELECT c.id, s.id, v.value
FROM (VALUES
    ('東京都', '大島町', 'SUUMO', 'sc_oshima'),
    ('東京都', '八丈町', 'SUUMO', 'sc_hachijojimahachijo'),
    ('埼玉県', '行田市', 'SUUMO', 'sc_gyoda'),
    ('埼玉県', '秩父市', 'SUUMO', 'sc_chichibu'),
    ('埼玉県', '羽生市', 'SUUMO', 'sc_hanyu'),
    ('埼玉県', '日高市', 'SUUMO', 'sc_hidaka'),
    ('千葉県', '旭市', 'SUUMO', 'sc_asahi'),
    ('千葉県', '富津市', 'SUUMO', 'sc_futtsu'),
    ('千葉県', '八街市', 'SUUMO', 'sc_yachimata'),
    ('千葉県', '匝瑳市', 'SUUMO', 'sc_sosa'),
    ('千葉県', '香取市', 'SUUMO', 'sc_katori'),
    ('千葉県', '大網白里市', 'SUUMO', 'sc_oamishirasato'),
    ('神奈川県', '南足柄市', 'SUUMO', 'sc_minamiashigara')
) AS v(prefecture, city_name, site_code, value)
JOIN m_cities c ON c.prefecture = v.prefecture AND c.canonical_name = v.city_name
JOIN m_sites  s ON s.code = v.site_code
ON CONFLICT (city_id, site_id) DO UPDATE SET
    value      = EXCLUDED.value,
    updated_at = now();
