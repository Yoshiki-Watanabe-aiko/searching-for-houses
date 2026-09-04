"""サイト別の取得と解析。

賃貸11サイトのうち **10サイトが実装済み**（SUUMO / HOME'S / goo不動産 /
エイブル / 賃貸EX / アットホーム / いい部屋ネット / ニフティ不動産 /
アパマンショップ / スモッカ）。**いずれも HTTP 取得**で、Phase 3 の実測により
Playwright が必要なサイトは1つも無いことが分かった（→ ADR 0010）。

未実装は MINIMINI だけ。一覧が reCAPTCHA のボット判定下にあり、
素のブラウザでも通らないため取得しない（→ 課題#18）。

**UR賃貸住宅（``UR``）だけは取得の形が違う。** JSON API への POST で、
団地と住戸の2段になっているため、``list_urls`` → ``parse_list`` の経路ではなく
任意フック ``collect_listings`` / ``fetch_detail`` で ``pipeline.scan`` から
委譲を受ける（→ ADR 0019）。**既存10アダプタには影響しない。**

``SCRAPERS`` に載らないサイトは scan が「スキップ（アダプタ未実装）」として
明示的に報告する。黙って無視すると「実装済みだが未配線」を見逃すため。
"""

from house_search.scrape.able import AbleScraper
from house_search.scrape.apaman import ApamanScraper
from house_search.scrape.area import (
    CITY_VALUE_JIS,
    CITY_VALUE_MAPPING,
    AreaTarget,
    resolve_areas,
)
from house_search.scrape.athome import AthomeScraper
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
from house_search.scrape.chintai_net import ChintaiNetScraper
from house_search.scrape.droom import DroomScraper
from house_search.scrape.eheya import EheyaScraper
from house_search.scrape.fetch import (
    RateLimit,
    RobotsDisallowed,
    SiteAborted,
    SiteFetcher,
    build_client,
)
from house_search.scrape.goo import GooScraper
from house_search.scrape.homemate import HomemateScraper
from house_search.scrape.homes import HomesScraper
from house_search.scrape.housecom import HousecomScraper
from house_search.scrape.leopalace import LeopalaceScraper
from house_search.scrape.nifty import NiftyScraper
from house_search.scrape.smocca import SmoccaScraper
from house_search.scrape.suumo import SuumoScraper
from house_search.scrape.ur import UrScraper

# サイトコード → アダプタ。既存11サイトのうち MINIMINI 以外の10サイト
# ＋ UR賃貸（Phase 5F）＋ レオパレス21（Phase 5G）
# ＋ D-room・ハウスコム・ホームメイト・CHINTAI.net（Phase 5H）。
SCRAPERS: dict[str, type] = {
    scraper.site_code: scraper
    for scraper in (
        SuumoScraper,
        HomesScraper,
        GooScraper,
        AbleScraper,
        ChintaiExScraper,
        AthomeScraper,
        EheyaScraper,
        NiftyScraper,
        ApamanScraper,
        SmoccaScraper,
        UrScraper,
        LeopalaceScraper,
        DroomScraper,
        HousecomScraper,
        HomemateScraper,
        ChintaiNetScraper,
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
    "ApamanScraper",
    "AreaTarget",
    "AthomeScraper",
    "ChintaiExScraper",
    "ChintaiNetScraper",
    "DroomScraper",
    "EheyaScraper",
    "GooScraper",
    "HomemateScraper",
    "HousecomScraper",
    "HomesScraper",
    "LeopalaceScraper",
    "NiftyScraper",
    "RateLimit",
    "RobotsDisallowed",
    "ScrapedDetail",
    "ScrapedListing",
    "SiteAborted",
    "SiteFetcher",
    "SiteScraper",
    "SmoccaScraper",
    "SuumoScraper",
    "UrScraper",
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
