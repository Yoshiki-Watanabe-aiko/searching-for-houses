"""サイト別の取得と解析。

Phase 1 で実装済みなのは SUUMO（賃貸）のみ。未実装サイトは
``SCRAPERS`` に載らないため、scan が明示的にスキップを報告する。
"""

from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    SiteScraper,
    parse_age_years,
    parse_area_sqm,
    parse_floor,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
)
from house_search.scrape.fetch import (
    RateLimit,
    RobotsDisallowed,
    SiteAborted,
    SiteFetcher,
    build_client,
)
from house_search.scrape.suumo import SuumoScraper

# サイトコード → アダプタ。Phase 2/3 でここへ追加していく。
SCRAPERS: dict[str, type] = {SuumoScraper.site_code: SuumoScraper}


def get_scraper(site_code: str) -> SiteScraper | None:
    """サイトコードからアダプタを作る。未実装なら None。"""
    factory = SCRAPERS.get(site_code)
    return factory() if factory else None


__all__ = [
    "SCRAPERS",
    "RateLimit",
    "RobotsDisallowed",
    "ScrapedDetail",
    "ScrapedListing",
    "SiteAborted",
    "SiteFetcher",
    "SiteScraper",
    "SuumoScraper",
    "build_client",
    "get_scraper",
    "parse_age_years",
    "parse_area_sqm",
    "parse_floor",
    "parse_total_floors",
    "parse_walk_minutes",
    "parse_yen",
]
