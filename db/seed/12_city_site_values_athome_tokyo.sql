-- ============================================================
-- m_city_site_values: ATHOME 東京都の市区スラグ追補（→ 課題#36・#21）
--
-- 09 が「東京都府中市の ATHOME スラグだけ未収集のまま残っている」と記録して
-- いた分を埋めた（2026-09-06）。ATHOME の東京都索引を **1リクエスト**だけ取得し、
-- 52リンクすべてを JIS5桁で同定した（不一致0件）。これで帯82市区は 82/82 になる。
--
-- ⚠ **既存48件と値が食い違うものは0件だった**（Phase 3 の収集そのものは正しく、
-- 単に4市区が漏れていただけ）。新規はここに挙げた4件のみ。
--
-- ⚠⚠ **スラグの形は一様ではない。** 通常は `tokyo/adachi-city` だが、この4件は
-- **郡名・都県名が前置される**（`tokyo_fuchu-city` / `nishitama_mizuho-city`）。
-- 府中市は広島県にも同名市があり、残る3町は西多摩郡に属する。
-- **規則を推測してスラグを組み立ててはいけない**——索引から採るしかない
-- （組み立てた値は 404 にならず別の市区の一覧を返す恐れがある → ADR 0014）。
--
-- 生成は `scripts/tools/collect_city_slugs.py --site ATHOME --prefectures 東京都`。
-- 取得HTMLは保存してあり `--from-cache` で解析だけやり直せる。**手で編集しない。**
--
-- ⚠ ATHOME は1回の実行で4リクエストが上限（→ 課題#36）。この収集で1本使った。
-- ============================================================

INSERT INTO m_city_site_values (city_id, site_id, value)
SELECT c.id, s.id, v.value
FROM (VALUES
    ('東京都', '府中市', 'ATHOME', 'tokyo/tokyo_fuchu-city'),
    ('東京都', '瑞穂町', 'ATHOME', 'tokyo/nishitama_mizuho-city'),
    ('東京都', '日の出町', 'ATHOME', 'tokyo/nishitama_hinode-city'),
    ('東京都', '奥多摩町', 'ATHOME', 'tokyo/nishitama_okutama-city')
) AS v(prefecture, city_name, site_code, value)
JOIN m_cities c ON c.prefecture = v.prefecture AND c.canonical_name = v.city_name
JOIN m_sites  s ON s.code = v.site_code
ON CONFLICT (city_id, site_id) DO UPDATE SET
    value      = EXCLUDED.value,
    updated_at = now();
