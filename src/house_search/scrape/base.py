"""サイトアダプタの共通型とパース補助。

v2 のサイト別コードは「取得と解析」だけを担う。設備条件の絞り込みはサイトへ
渡さず（→ ADR 0003）、フォームに載せるのはエリア・物件種別・価格上限だけ。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Protocol

from house_search.scrape.fetch import SiteFetcher

# 「3.5万円」「10万5000円」などの金額表記。
_MAN_YEN = re.compile(r"([\d,]+(?:\.\d+)?)\s*万円")
_YEN = re.compile(r"([\d,]+)\s*円")
# NFKC 後は「㎡」が「m2」になる。生HTMLでは <sup>2</sup> のため m2/m² 双方を見る。
_AREA = re.compile(r"([\d,]+(?:\.\d+)?)\s*m\s*(?:2|²)")
_AGE = re.compile(r"築\s*(\d+)\s*年")
_NEW_BUILDING = re.compile(r"新築")
_WALK = re.compile(r"歩\s*(\d+)\s*分")
_FLOOR = re.compile(r"(地下)?\s*(\d+)\s*階")
_TOTAL_FLOORS = re.compile(r"(\d+)\s*階建")
# 「1987年1月」「2024年12月」。日は取れないので1日に固定して格納する。
_BUILT_ON = re.compile(r"(\d{4})\s*年\s*(\d{1,2})?\s*月?")


@dataclass(frozen=True, slots=True)
class ScrapedListing:
    """一覧ページから取れる1掲載ぶんの情報。

    ここに載る項目だけで MUST の1段目判定を行い、``fail`` なら詳細を取りに行かない。
    """

    site_code: str
    external_id: str
    url: str
    title: str | None = None
    price: int | None = None
    mgmt_fee_monthly: int | None = None
    deposit_amount: int | None = None
    key_money_amount: int | None = None
    area_sqm: float | None = None
    layout: str | None = None
    floor_num: int | None = None
    total_floors: int | None = None
    age_years: int | None = None
    address: str | None = None
    station_info: str | None = None
    walk_minutes: int | None = None
    image_url: str | None = None


@dataclass(frozen=True, slots=True)
class ScrapedDetail:
    """詳細ページから取れる追加情報。

    ``raw_features_text`` は設備ブロックの原文。辞書を改善したとき
    再スクレイピングせず ``re-extract`` で全件やり直すための保存であり、
    詳細HTML全体は保存しない。
    """

    raw_features_text: str | None = None
    built_on: dt.date | None = None
    floor_num: int | None = None
    total_floors: int | None = None
    mgmt_fee_monthly: int | None = None
    deposit_amount: int | None = None
    key_money_amount: int | None = None
    address: str | None = None
    walk_minutes: int | None = None
    type_specific_attrs: dict = field(default_factory=dict)


class SiteScraper(Protocol):
    """サイトアダプタが満たすべき最小のインタフェース。"""

    site_code: str

    def list_urls(self, pattern: object, cities: dict[str, str]) -> list[str]:
        """検索パターンから一覧ページのURLを組み立てる。"""
        ...

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。"""
        ...

    def detail_url(self, listing_url: str) -> str:
        """詳細ページのURL（一覧のリンクをそのまま使えるなら同じ値）。"""
        ...

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページHTMLから追加情報を取り出す。"""
        ...

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """成約・掲載終了かどうか。"""
        ...


def parse_yen(text: str | None) -> int | None:
    """「3.5万円」「25000円」などを円の整数へ。取れなければ None。"""
    if not text:
        return None
    if match := _MAN_YEN.search(text):
        return int(round(float(match.group(1).replace(",", "")) * 10_000))
    if match := _YEN.search(text):
        return int(match.group(1).replace(",", ""))
    return None


def parse_area_sqm(text: str | None) -> float | None:
    """「18m2」「42.5m²」などを㎡の実数へ。"""
    if not text:
        return None
    match = _AREA.search(text)
    return float(match.group(1).replace(",", "")) if match else None


def parse_age_years(text: str | None) -> int | None:
    """「築40年」を年数へ。「新築」は0年として扱う。"""
    if not text:
        return None
    if match := _AGE.search(text):
        return int(match.group(1))
    return 0 if _NEW_BUILDING.search(text) else None


def parse_walk_minutes(text: str | None) -> int | None:
    """「歩50分」の最小値を返す。複数路線が併記されるため最短を採る。"""
    if not text:
        return None
    values = [int(m) for m in _WALK.findall(text)]
    return min(values) if values else None


def parse_floor(text: str | None) -> int | None:
    """「3階」「地下1階」を所在階へ（地下は負値）。

    「2-3階」のようなメゾネット表記は下の階を採る。
    """
    if not text:
        return None
    match = _FLOOR.search(text)
    if not match:
        return None
    floor = int(match.group(2))
    return -floor if match.group(1) else floor


def parse_total_floors(text: str | None) -> int | None:
    """「2階建」「1階/2階建」から建物の階数を返す。"""
    if not text:
        return None
    match = _TOTAL_FLOORS.search(text)
    return int(match.group(1)) if match else None


def parse_built_on(text: str | None) -> dt.date | None:
    """「1987年1月」を築年月へ。日は取れないので1日に固定する。"""
    if not text:
        return None
    match = _BUILT_ON.search(text)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else 1
    if not (1800 <= year <= 2200 and 1 <= month <= 12):
        return None
    return dt.date(year, month, 1)
