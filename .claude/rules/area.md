---
paths:
  - "src/house_search/scrape/area.py"
  - "src/house_search/scrape/prefectures.py"
  - "data/city_master/**"
  - "scripts/tools/generate_city_seed.py"
  - "scripts/tools/collect_city_slugs.py"
  - "scripts/tools/collect_leopalace_slugs.py"
---

# 市区の検索値・市区町村マスタ・エリア索引の注意

CLAUDE.md の「実装上の注意」から領域別に分けたもの（→ CLAUDE.md の表）。
実際に踏んだ落とし穴なので、該当する処理を書く・直す前に読む。

- **市区の検索値が JIS5桁のサイトは `m_cities.jis_code` から導く。**
  `m_city_site_values` に縛ると対象4都県で 67/253市区しか指定できない（東京は23区のみ）
- **市区町村マスタの正典は総務省の全国地方公共団体コード**（`data/city_master/`）。
  `db/seed/06_cities.sql` は `scripts/tools/generate_city_seed.py` の生成物なので手で編集しない。
  ⚠ **サイトのエリア索引から部分文字列一致で補完してはいけない。**
  実際に名古屋市へ北名古屋市の `23234`、大阪市へ東大阪市の `27227` が混入していた。
  **別の市の一覧が返るだけで取得は成功しエラーにもならない**（→ ADR 0014）
- **政令指定都市はマスタが市と行政区の両方を持つ。** 取得URLを組み立てるときは
  行政区を持つ市の親行を外さないと、同じ掲載を市と区で二重に取りに行く
- **サイトのエリア索引を解析するときは、取得HTMLを必ず保存する。**
  ATHOME の市区リンクは `href='...'` と**単一引用符**で、`href="..."` 決め打ちだと
  71市区のうち10市区しか拾えない。⚠ **エラーにならず件数が減るだけ**なので、
  保存が無いと取得予算を試行錯誤で使い切る（`collect_city_slugs.py --from-cache`）
- **`resolve_areas` は検索値の無い市区を黙って落とす。** スラグ系サイトは
  `m_city_site_values` に行が無いとその市区が対象から消えるだけでエラーにならない。
  実測で HOMES は帯82市区のうち32、ATHOME は49の検索値が無かった（→ 課題#36）。
  Phase 5E で収集して **HOMES 82/82・ATHOME 81/82** まで埋めた
  （`scripts/tools/collect_city_slugs.py`）。⚠ **市区の同定は索引に埋まっている
  JIS5桁で行う**（部分文字列一致は他市のコードを混入させる → ADR 0014）
- **市区の検索値は3系統ある。** JIS5桁（SUUMO/GOO/ABLE/賃貸EX/EHEYA/SMOCCA）／
  JIS5桁の下3桁（APAMAN）／サイト固有スラグ（HOMES/ATHOME/NIFTY/MINIMINI）。
  スラグ系だけが `m_city_site_values` を引く。
  ⚠⚠ **SUUMO は賃貸と売買で系統が違う**（→ 課題#4）。売買は robots が
  `/jj/bukken/ichiran/` を禁じており SEOパス（`/ms/chuko/tokyo/sc_chiyoda/`）
  でしか取れないので**スラグを引く**。⚠ スラグは賃貸／売買で共通（実測で食い違い0）。
  ⚠ **同定はリンクの `id="js-linkSc101"`（JIS の下3桁）と
  `<input name="sc" value="13101">`（5桁）を突き合わせる**（→ ADR 0014）
