"""レオパレス21の市区スラグを集めてシードSQLを生成する（→ 課題#37・Phase 5G）。

⚠ **エリア索引を舐める必要が無い。** サイトマップ1本（1リクエスト）に
市区一覧URLが全国ぶん入っている。ATHOME で47都道府県を連続取得して
ボット検知を発動させた失敗（→ 課題#21）を繰り返さずに済む。

⚠ **市区の同定はURLに埋まっている JIS5桁で行う**（`adachi-ku-13121`）。
部分文字列一致は使わない（Phase 2 で名古屋市に北名古屋市の 23234 が
混入した原因 → ADR 0014）。

⚠ **取得したサイトマップは保存する**（`--from-cache` で解析だけやり直せる）。
ATHOME の市区リンクが単一引用符で71市区中10市区しか拾えなかったとき、
これがあったので取得予算を1回も使わずに直せた（→ 課題#36）。

使い方（PowerShell 5.1。``&&`` は使えないので1行ずつ）:

    uv run python scripts/tools/collect_leopalace_slugs.py
    uv run python scripts/tools/collect_leopalace_slugs.py --from-cache
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from house_search.config.settings import load_settings  # noqa: E402
from house_search.scrape.prefectures import PREFECTURE_ROMAJI  # noqa: E402

SITEMAP = "https://www.leopalace21.com/sitemap_rent_room_list_map_ja.xml"
CACHE = Path("data/probe/leopalace/sitemap_rent_room_list.xml")
SEED = Path("db/seed/10_city_site_values_leopalace.sql")

# /properties/chintai/area/{都道府県スラグ}/{市区スラグ}-{JIS5桁}
_CITY_URL = re.compile(r"/properties/chintai/area/([a-z]+)/([a-z0-9-]+-(\d{5}))$")


def fetch_sitemap(*, from_cache: bool) -> str:
    if from_cache:
        return CACHE.read_text(encoding="utf-8")
    settings = load_settings()
    response = httpx.get(
        SITEMAP,
        headers={"User-Agent": settings.user_agent},
        timeout=60,
        follow_redirects=True,
    )
    response.raise_for_status()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(response.text, encoding="utf-8")
    return response.text


def collect(xml: str) -> dict[str, tuple[str, str]]:
    """JIS5桁 → (都道府県スラグ, 市区スラグ)。"""
    found: dict[str, tuple[str, str]] = {}
    known_prefs = set(PREFECTURE_ROMAJI.values())
    for url in re.findall(r"<loc>([^<]+)</loc>", xml):
        match = _CITY_URL.search(url)
        if not match:
            continue
        pref_slug, city_slug, jis = match.groups()
        # ⚠ 都道府県スラグが既存定義と食い違う行は捨てる。アダプタは
        # PREFECTURE_ROMAJI からパスを組むので、ずれていると 200 のまま
        # 別のエリアを取りに行く（エラーにならない）
        if pref_slug not in known_prefs:
            print(f"  ⚠ 未知の都道府県スラグ {pref_slug!r} を捨てた: {url}")
            continue
        found[jis] = (pref_slug, city_slug)
    return found


def render_seed(found: dict[str, tuple[str, str]]) -> str:
    rows = ",\n".join(
        f"    ('{jis}', 'LEOPALACE', '{slug}')" for jis, (_, slug) in sorted(found.items())
    )
    return f"""-- ============================================================
-- m_city_site_values: レオパレス21の市区スラグ（Phase 5G・→ 課題#37）
--
-- 収集元は `sitemap_rent_room_list_map_ja.xml`（**1リクエスト**）。
-- `scripts/tools/collect_leopalace_slugs.py` の生成物なので**手で編集しない**。
--
-- ⚠ **市区の同定はURLに埋まっている JIS5桁で行う**（`adachi-ku-13121`）。
-- 部分文字列一致は使わない（→ ADR 0014）。ここでも `m_cities.jis_code` で
-- 突き合わせているので、市区名の表記ゆれ（鎌ヶ谷/鎌ケ谷）に影響されない。
--
-- ⚠ **値は都道府県を含めない**（`adachi-ku-13121`）。レオパレスの都道府県
-- スラグは47件すべて `PREFECTURE_ROMAJI` と一致することを実測で確認したので、
-- アダプタ側で導出する。
--
-- ⚠ **掲載のある市区しか載らない**（自社物件のみのサイトのため全国{len(found)}件）。
-- エリア帯83市区のうち82市区をカバーする（千代田区にはレオパレスの物件が無い）。
-- ============================================================

INSERT INTO m_city_site_values (city_id, site_id, value)
SELECT c.id, s.id, v.value
FROM (VALUES
{rows}
) AS v(jis_code, site_code, value)
JOIN m_cities c ON c.jis_code = v.jis_code
JOIN m_sites s ON s.code = v.site_code
ON CONFLICT (city_id, site_id) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-cache", action="store_true", help="保存済みのサイトマップを使う")
    args = parser.parse_args()

    xml = fetch_sitemap(from_cache=args.from_cache)
    found = collect(xml)
    print(f"市区URL: {len(found)}件")
    SEED.write_text(render_seed(found), encoding="utf-8")
    print(f"生成: {SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
