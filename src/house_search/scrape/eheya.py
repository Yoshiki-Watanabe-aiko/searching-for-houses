"""いい部屋ネット（賃貸）のサイトアダプタ。

一覧URLは都道府県単位が ``/{都道府県}/search/``、市区単位が
``/{都道府県}/area/{JIS5}/search/``。市区の検索値は **JIS5桁コード**で、
``m_cities.jis_code`` から導出できる（Phase 3 の実測で確定。
``m_city_site_values`` は東京23区のブロックが JIS、それ以外がスラグという
矛盾した状態だったが、正しいのは JIS のほう）。

EHEYA 固有の注意点:

* **Playwright は要らない。** Next.js の Pages Router で、掲載データが
  ``<script id="__NEXT_DATA__">`` の JSON にそのまま入っている（→ ADR 0010）
* **HTMLのクラス名は CSS Modules のハッシュ付き**（``styles_cassette__UCHok``）で
  ビルドのたびに変わる。DOM ではなく **JSON を正典として読む**
* **賃料上限をサイト側へ渡せない。** 検索条件はURLクエリでは受け取らず
  （``?detail.priceMax=`` も ``?priceMax=`` も無視されることを実測）、
  ``serializedCondition`` に反映されない。上限判定はローカルで行う
* 詳細ページも同じ JSON 構造で、``propertyFeatures`` に設備がラベル付きで並ぶ。
  **``remarks`` と ``salesPoint`` は生成文なので設備原文に載せない**
  （賃貸EX で未知表記が文断片で埋まった 課題#19 と同じ轍を踏むため）
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    clean_address,
    parse_age_years,
    parse_built_on,
    parse_floor,
    parse_walk_minutes,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "EHEYA"
BASE_URL = "https://www.eheya.net"
# 1ページあたりの建物数（``pageInfo.limit`` の実測値）
PAGE_SIZE = 20

_SOLD_MARKERS = ("掲載が終了", "この物件は成約", "お探しの物件は見つかり")

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# 値が入っていないことを表す列挙値。
_UNKNOWN = "UNKNOWN"

# 真のときだけ設備原文へ足すフラグ → 語彙の対応。
# 所在階・築年から導ける条件（2階以上・最上階・新築）はここに入れない。
# 型付き列からの導出（``extract`` の ``SOURCE_DERIVED``）と二重になるため。
_FLAG_TOKENS = {
    "isAutolock": "オートロック",
    "isSeparatedBathAndToilet": "バス・トイレ別",
    "isWashingMachinePlace": "室内洗濯機置場",
    "isFreeInternet": "インターネット無料",
    "isFreeWashRoom": "独立洗面台",
    "isPet": "ペット相談",
    "isPetFriendly": "ペット相談",
    "isParking": "駐車場あり",
    "isAirConditioner": "エアコン",
    "isMoveInStatusAvailable": "即入居可",
}


class EheyaScraper:
    """いい部屋ネット 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = False
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    ignore_robots = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/{都道府県}/search/`` または ``/{都道府県}/area/{JIS5}/search/``。

        賃料上限は渡さない（クエリで受け取らないことを実測済み）。
        """
        urls: list[str] = []
        for area in areas:
            slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not slug:
                raise ValueError(f"EHEYA: 未知の都道府県です: {area.prefecture}")
            if area.value:
                urls.append(f"{BASE_URL}/{slug}/area/{area.value}/search/")
            else:
                urls.append(f"{BASE_URL}/{slug}/search/")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        return f"{base_url}?page={page}"

    def is_last_page(self, count: int) -> bool:
        """建物20件で1ページ。住戸数は建物数を下回らないので下限として使える。"""
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """``buildingSearchResult.buildings`` から掲載（住戸）を取り出す。"""
        page_props = _page_props(html_text)
        buildings = _dig(page_props, "buildingSearchResult", "buildings") or []
        listings: list[ScrapedListing] = []
        for building in buildings:
            station_info = building.get("mainTransportationText")
            # ⚠ "properties" は __NEXT_DATA__ のJSONキー。用語統一（物件→掲載）の
            # 一括置換で壊しやすいので、サイト側の名前であることを明示しておく
            for room in building.get("properties") or []:
                listing = _parse_room(building, room, station_info=station_info)
                if listing is not None:
                    listings.append(listing)
        return listings

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページの ``property`` から設備原文と補足項目を取り出す。"""
        prop = _dig(_page_props(html_text), "property") or {}

        tokens = [
            label
            for feature in prop.get("propertyFeatures") or []
            if (label := (feature.get("label") or "").strip())
        ]
        tokens.extend(
            token for flag, token in _FLAG_TOKENS.items() if prop.get(flag) is True
        )
        if (structure := prop.get("buildingStructure")) and structure != _UNKNOWN:
            tokens.append(structure)
        if (direction := prop.get("windowDirection")) and direction != _UNKNOWN:
            tokens.append(direction if direction.endswith("向き") else f"{direction}向き")

        return ScrapedDetail(
            raw_features_text="、".join(dict.fromkeys(tokens)) or None,
            built_on=parse_built_on(prop.get("constructionDate")),
            floor_num=parse_floor(prop.get("floor")),
            total_floors=_as_int(prop.get("story")),
            mgmt_fee_monthly=_yen(prop.get("manageCost")),
            deposit_amount=_yen(prop.get("securityDeposit")),
            key_money_amount=_yen(prop.get("keyMoney")),
            address=clean_address(prop.get("address")),
            walk_minutes=_walk_minutes(prop.get("transportations")),
            type_specific_attrs={
                key: value
                for key in ("buildingKind", "buildingStructure", "transactionStyle")
                if (value := prop.get(key)) and value != _UNKNOWN
            },
        )

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        try:
            response = fetcher.get(url)
        except Exception:
            return False
        if response.status_code == 404:
            return True
        return any(marker in response.text for marker in _SOLD_MARKERS)


def _page_props(html_text: str) -> dict[str, Any]:
    """``__NEXT_DATA__`` の ``props.pageProps`` を取り出す。

    見つからない場合は黙って空を返さずエラーにする。
    Next.js の App Router へ移行するとこの経路は消えるため、
    「取れているつもりで0件」を最も避けたいところだから。
    """
    match = _NEXT_DATA.search(html_text)
    if not match:
        raise ValueError("EHEYA: __NEXT_DATA__ が見つかりません（ページ構造の変更）")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"EHEYA: __NEXT_DATA__ を読めません: {exc}") from exc
    return _dig(data, "props", "pageProps") or {}


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _yen(money: Any) -> int | None:
    """``{"number": 15000, "unit": "YEN"}`` を円へ。"""
    if not isinstance(money, dict):
        return None
    return _as_int(money.get("number"))


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _walk_minutes(transportations: Any) -> int | None:
    """``transportations`` の徒歩分数の最小値（複数路線があるため最短を採る）。"""
    if not isinstance(transportations, list):
        return None
    values = [
        minutes
        for item in transportations
        if isinstance(item, dict)
        and (minutes := _as_int(item.get("walkingMinutesFromStation"))) is not None
    ]
    return min(values) if values else None


def _parse_room(building: dict, room: dict, *, station_info: str | None) -> ScrapedListing | None:
    external_id = (room.get("propertyFullId") or "").strip()
    if not external_id:
        return None
    return ScrapedListing(
        site_code=SITE_CODE,
        external_id=external_id,
        url=f"{BASE_URL}/detail/{external_id}/",
        title=room.get("buildingName") or building.get("name"),
        price=_yen(room.get("price")),
        mgmt_fee_monthly=_yen(room.get("manageCost")),
        deposit_amount=_yen(room.get("securityDeposit")),
        key_money_amount=_yen(room.get("keyMoney")),
        area_sqm=_as_float(room.get("roomArea")),
        layout=room.get("housePlan"),
        floor_num=parse_floor(room.get("floor")),
        total_floors=_as_int(room.get("story") or building.get("story")),
        age_years=parse_age_years(room.get("age") or building.get("age")),
        address=clean_address(room.get("address") or building.get("address")),
        station_info=station_info,
        walk_minutes=parse_walk_minutes(station_info),
        image_url=_image_url(room),
    )


def _image_url(room: dict) -> str | None:
    for key in ("housePlanImage", "exteriorImage"):
        image = room.get(key)
        if isinstance(image, dict) and (url := image.get("url")):
            return url
    return None
