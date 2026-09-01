"""goo不動産（賃貸）のサイトアダプタ。

一覧URLは ``/rent/{地方}_ap/area_{都道府県}/{JIS5}.html`` の形で、
市区の指定値は **JIS5桁コードそのもの**。``m_city_site_values`` に行が無くても
``m_cities.jis_code`` から導出できる（→ ``scrape/area.py``）。

goo 固有の注意点:

* 詳細ページは条件を ``th``＝条件名 / ``td``＝``○`` or ``-`` の表で持つ。
  ``-`` の行まで原文に載せると辞書が非該当の条件を拾ってしまう
* 同じページに市区の統計情報（ごみ収集・病院数など）が大量に載る。
  設備の原文はラベルの白名簿で絞らないと未知表記が汚染される
* 掲載元が LIFULL HOME'S と重なるため掲載重複が多い（名寄せは Phase 4）
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode, urljoin

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.base import (
    EMPTY_MARKERS,
    ScrapedDetail,
    ScrapedListing,
    clean_address,
    parse_age_years,
    parse_area_sqm,
    parse_built_on,
    parse_fee,
    parse_floor,
    parse_months_fee,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "GOO"
BASE_URL = "https://house.goo.ne.jp"
# 1ページあたりの掲載数（実測）
PAGE_SIZE = 72

_SOLD_MARKERS = ("掲載が終了", "掲載を終了", "お探しの物件", "見つかりませんでした")

# goo の地方区分。実装対象の4都県はすべて首都圏なので、まず首都圏だけを持つ。
# 他地方を扱うときは実URLを確認してから足す（推測で綴ると404になるため）。
PREFECTURE_REGION: dict[str, str] = {
    "東京都": "shuto",
    "神奈川県": "shuto",
    "埼玉県": "shuto",
    "千葉県": "shuto",
    "茨城県": "shuto",
    "栃木県": "shuto",
    "群馬県": "shuto",
}

# goo だけ綴りが違う都道府県スラグ
_PREFECTURE_SLUG_OVERRIDES: dict[str, str] = {"茨城県": "ibaragi"}

# 詳細ページ th のうち原文に載せたい設備系ラベル
_FEATURE_LABELS = ("設備", "特記事項", "リノベーション履歴")

# 詳細ページ th のうち構造化して取り出したい項目
_DETAIL_LABELS = {
    "所在地": "address",
    "交通": "station",
    "築年月": "built",
    "築年月（築年数）": "built",
    "所在階/階数": "floors",
    "建物構造": "structure",
    "方位": "facing",
    "現況": "status",
    "管理費等": "mgmt_fee",
    "敷金": "deposit",
    "礼金": "key_money",
    "入居可能時期": "movein",
    "駐車場": "parking",
}


class GooScraper:
    """goo不動産 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = True
    city_value_source = CITY_VALUE_JIS
    user_agent = None

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/rent/{地方}_ap/area_{都道府県}/{JIS5}.html`` を組み立てる。"""
        search = pattern.search  # type: ignore[attr-defined]
        params: dict[str, str] = {}
        if search.price_max_hint:
            # ru は万円単位の整数。切り上げて取りこぼさない
            params["ru"] = str(-(-search.price_max_hint // 10_000))

        urls: list[str] = []
        for area in areas:
            region = PREFECTURE_REGION.get(area.prefecture)
            if not region:
                raise ValueError(
                    f"GOO: 地方区分が未登録の都道府県です: {area.prefecture}"
                    "（実URLを確認して PREFECTURE_REGION に足すこと）"
                )
            slug = _PREFECTURE_SLUG_OVERRIDES.get(
                area.prefecture, PREFECTURE_ROMAJI.get(area.prefecture, "")
            )
            if not area.value:
                raise ValueError(f"GOO: 市区の指定が要ります: {area.prefecture}")
            url = f"{BASE_URL}/rent/{region}_ap/area_{slug}/{area.value}.html"
            urls.append(f"{url}?{urlencode(params)}" if params else url)
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}p={page}"

    def is_last_page(self, count: int) -> bool:
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。

        ``div.name_id`` が建物1件、その中の ``table.property`` が住戸1件。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for building in doc.cssselect("div.name_id"):
            heading = _first_text(building, ".name_id_group-data h2")
            address = _first_text(building, ".name_id_group-data p") or heading
            sub_cells = building.cssselect(".name_id_group-dataSub td")
            # 1セル目に複数路線が <br> 区切りで入るので、区切りを見える形に畳む
            station_info = (
                " / ".join(" ".join(t.split()) for t in sub_cells[0].itertext() if t.strip())
                if sub_cells
                else None
            ) or None
            age_text = " ".join(cell.text_content() for cell in sub_cells)

            for room in building.cssselect("table.property"):
                listing = self._parse_room(
                    room,
                    title=heading,
                    address=address,
                    station_info=station_info,
                    age_years=parse_age_years(age_text) or parse_age_years(heading),
                    total_floors=parse_total_floors(heading),
                )
                if listing is not None:
                    listings.append(listing)
        return listings

    def _parse_room(
        self,
        room,
        *,
        title: str | None,
        address: str | None,
        station_info: str | None,
        age_years: int | None,
        total_floors: int | None,
    ) -> ScrapedListing | None:
        anchors = room.cssselect("a.linkurl")
        if not anchors:
            return None
        href = anchors[0].get("href") or ""
        if not href:
            return None

        external_id = _external_id(room)
        if not external_id:
            return None

        price = parse_yen(_first_text(room, "td.property-price"))
        deposit, key_money = _deposit_and_key(room, price)
        layout, area_sqm = _room_layout(room)

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=urljoin(BASE_URL, href),
            title=title,
            price=price,
            mgmt_fee_monthly=parse_fee(_first_text(room, "td.property-kanri")),
            deposit_amount=deposit,
            key_money_amount=key_money,
            area_sqm=area_sqm,
            layout=layout,
            floor_num=_room_floor(room),
            total_floors=total_floors,
            age_years=age_years,
            address=address,
            station_info=station_info,
            walk_minutes=parse_walk_minutes(station_info),
            image_url=_room_image(room),
        )

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        fields, conditions, features = _detail_tables(doc)

        blocks = [block for block in features if block]
        if conditions:
            blocks.append("、".join(conditions))

        derived: list[str] = []
        if structure := fields.get("structure"):
            derived.append(structure)
        facing = fields.get("facing")
        if facing and facing not in EMPTY_MARKERS:
            derived.append(facing if facing.endswith("向き") else f"{facing}向き")
        parking = fields.get("parking")
        if parking and parking not in EMPTY_MARKERS:
            derived.append("駐車場あり")
            if "敷地内" in parking:
                derived.append("敷地内駐車場")
        if (movein := fields.get("movein")) and movein.startswith("即"):
            derived.append("即入居可")
        if derived:
            blocks.append("、".join(derived))

        floors_text = fields.get("floors")
        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(fields.get("built")),
            floor_num=parse_floor(floors_text),
            total_floors=parse_total_floors(floors_text),
            mgmt_fee_monthly=parse_fee(fields.get("mgmt_fee")),
            deposit_amount=parse_fee(fields.get("deposit")),
            key_money_amount=parse_fee(fields.get("key_money")),
            address=clean_address(fields.get("address")),
            walk_minutes=parse_walk_minutes(fields.get("station")),
            type_specific_attrs={
                key: value
                for key, value in fields.items()
                if key in ("structure", "facing", "status")
                and value
                and value not in EMPTY_MARKERS
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


def _first_text(node, selector: str) -> str | None:
    found = node.cssselect(selector)
    if not found:
        return None
    return " ".join(found[0].text_content().split()) or None


def _external_id(room) -> str | None:
    for checkbox in room.cssselect("input[name='ai[]']"):
        if value := (checkbox.get("value") or "").strip():
            return value
    return None


def _room_floor(room) -> int | None:
    """``td.property-floor`` の ``li`` から所在階を拾う。

    同じ ``ul`` に「NEW」「閲覧済」も並ぶため、階として読める要素だけを採る。
    """
    for item in room.cssselect("td.property-floor li"):
        text = " ".join(item.text_content().split())
        if text and (floor := parse_floor(text)) is not None:
            return floor
    return None


def _room_layout(room) -> tuple[str | None, float | None]:
    """``td.property-madori`` の ``ワンルーム<br>30.57m²`` を分ける。"""
    cells = room.cssselect("td.property-madori")
    if not cells:
        return None, None
    cell = cells[0]
    texts = [" ".join(t.split()) for t in cell.itertext() if t.strip()]
    return (texts[0] if texts else None), parse_area_sqm(cell.text_content())


def _deposit_and_key(room, price: int | None) -> tuple[int | None, int | None]:
    """``td.property-shiki`` の ``なし / なし`` を敷金・礼金へ分ける。

    goo は1行目が「敷金 / 礼金」、2行目が「保証 / 敷引・償却」。
    """
    cells = room.cssselect("td.property-shiki")
    if not cells:
        return None, None
    lines = [" ".join(t.split()) for t in cells[0].itertext() if t.strip()]
    if not lines:
        return None, None
    parts = [part.strip() for part in lines[0].split("/")]
    deposit = parts[0] if parts else None
    key_money = parts[1] if len(parts) > 1 else None
    # goo は敷礼を金額でも月数でも書くため、両方を読める parse_months_fee を通す
    return parse_months_fee(deposit, price), parse_months_fee(key_money, price)


def _room_image(room) -> str | None:
    for image in room.cssselect("td.property-img img"):
        value = image.get("src")
        if value and not value.startswith("data:"):
            return value
    return None


def _detail_tables(doc) -> tuple[dict[str, str], list[str], list[str]]:
    """詳細ページの ``th/td`` を、構造化項目・該当条件・設備原文へ振り分ける。

    条件行は ``td`` が ``○`` なら該当、``-`` なら非該当。
    非該当まで原文に載せると辞書が拾ってしまうため、ここで落とす。
    """
    fields: dict[str, str] = {}
    conditions: list[str] = []
    features: list[str] = []

    for th in doc.cssselect("th"):
        label = "".join(th.text_content().split())
        sibling = th.getnext()
        if not label or sibling is None or sibling.tag != "td":
            continue
        value = " ".join(sibling.text_content().split())
        if not value:
            continue

        if value == "○":
            if label not in conditions:
                conditions.append(label)
            continue
        if label in _FEATURE_LABELS:
            if value not in EMPTY_MARKERS:
                features.append(value)
            continue
        key = _DETAIL_LABELS.get(label)
        if key and key not in fields:
            fields[key] = value
    return fields, conditions, features
