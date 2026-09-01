"""サイト別の取得と解析。

実装済みは SUUMO / HOME'S / goo不動産 / エイブル / 賃貸EX（HTTP取得）。
Playwright が要る5サイト（ATHOME・EHEYA・NIFTY・APAMAN・SMOCCA）と、
一覧が reCAPTCHA のボット判定下にある MINIMINI は Phase 3 で扱う。

``SCRAPERS`` に載らないサイトは scan が「スキップ（アダプタ未実装）」として
明示的に報告する。黙って無視すると「実装済みだが未配線」を見逃すため。
"""

from house_search.scrape.able import AbleScraper
from house_search.scrape.area import (
    CITY_VALUE_JIS,
    CITY_VALUE_MAPPING,
    AreaTarget,
    resolve_areas,
)
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    SiteScraper,
    age_years_from_built,
    parse_age_years,
    parse_area_sqm,
    parse_fee,
    parse_floor,
    parse_months_fee,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
)
from house_search.scrape.chintai_ex import ChintaiExScraper
from house_search.scrape.fetch import (
    RateLimit,
    RobotsDisallowed,
    SiteAborted,
    SiteFetcher,
    build_client,
)
from house_search.scrape.goo import GooScraper
from house_search.scrape.homes import HomesScraper
from house_search.scrape.suumo import SuumoScraper

# サイトコード → アダプタ。Phase 3 で Playwright サイトをここへ追加する。
SCRAPERS: dict[str, type] = {
    scraper.site_code: scraper
    for scraper in (
        SuumoScraper,
        HomesScraper,
        GooScraper,
        AbleScraper,
        ChintaiExScraper,
    )
}


def get_scraper(site_code: str) -> SiteScraper | None:
    """サイトコードからアダプタを作る。未実装なら None。"""
    factory = SCRAPERS.get(site_code)
    return factory() if factory else None


__all__ = [
    "CITY_VALUE_JIS",
    "CITY_VALUE_MAPPING",
    "SCRAPERS",
    "AbleScraper",
    "AreaTarget",
    "ChintaiExScraper",
    "GooScraper",
    "HomesScraper",
    "RateLimit",
    "RobotsDisallowed",
    "ScrapedDetail",
    "ScrapedListing",
    "SiteAborted",
    "SiteFetcher",
    "SiteScraper",
    "SuumoScraper",
    "age_years_from_built",
    "build_client",
    "get_scraper",
    "parse_age_years",
    "parse_area_sqm",
    "parse_fee",
    "parse_floor",
    "parse_months_fee",
    "parse_total_floors",
    "parse_walk_minutes",
    "parse_yen",
    "resolve_areas",
]
