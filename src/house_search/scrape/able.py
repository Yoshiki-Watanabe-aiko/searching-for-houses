"""エイブル（賃貸）のサイトアダプタ。

一覧URLは ``/{都道府県}/area/{JIS5}/list/``。市区の指定値は JIS5桁コードそのもので、
``m_cities.jis_code`` から導出できる。

ABLE 固有の注意点:

* **市区の指定が必須**（都道府県だけでは一覧が出ない → 課題#1）。
  ``search.cities`` が空でも都道府県内の全市区へ自動展開する
* **賃料上限のクエリ（``ct``）が効かない。** 実測で ``ct=9`` を付けても
  23.9万円の掲載が返る。代わりに ``o=1``（賃料が安い順）で並べ、
  1ページ目に MUST を通る価格帯が集まるようにする。上限判定はローカルで行う
* 詳細リンクが ``href="javascript:void(0)"`` で、住戸ごとの実URLは
  ``onclick`` の ``clickBukkenInfoArea('...')`` の中にしか無い
"""

from __future__ import annotations

import re
from collections.abc import Sequence

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

SITE_CODE = "ABLE"
BASE_URL = "https://www.able.co.jp"
# 1ページあたりの掲載（住戸）数（実測）
PAGE_SIZE = 30
# 並び順: 1 = 賃料が安い順。賃料上限のクエリが効かないための代替
SORT_RENT_ASC = "1"

_SOLD_MARKERS = ("掲載を終了", "掲載が終了", "お探しの物件", "見つかりませんでした")

# 「詳細を見る」の href は javascript:void(0) で、実URLは onclick の中にある
_ONCLICK_URL = re.compile(r"""['"](https?://[^'"]*Detail\.do[^'"]*)['"]""")
# 「23.9万円10,000円」のように連結された金額列を1つずつ拾う
_AMOUNT = re.compile(r"[\d,.]+\s*万?円")

# 建物ヘッダの th ラベル
_BUILDING_LABELS = {
    "住所": "address",
    "交通": "station",
    "築年": "built",
    "階建": "floors",
    "構造": "structure",
}

# 詳細ページ th のうち原文に載せたい設備系ラベル
_FEATURE_LABELS = (
    "キッチン/バス・トイレ",
    "お部屋の設備サービス",
    "セキュリティ",
    "共有スペースの特徴・設備",
    "入居条件",
    "備考",
)

# 詳細ページ th のうち構造化して取り出したい項目
# 「築年」は「築20年」で年月が無いため built には使わない
# （年月が入るのは「築年/築年月」＝「築20年/2006年02月」のほう）
_DETAIL_LABELS = {
    "住所": "address",
    "交通": "station",
    "築年/築年月": "built",
    "階/階建": "floors",
    "向き": "facing",
    "建物種別/構造": "structure",
    "家賃管理費": "rent_mgmt",
    "敷金/保証金": "deposit",
    "礼金/償却": "key_money",
    "駐車場": "parking",
    "入居時期": "movein",
}


class AbleScraper:
    """エイブル 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = True
    city_value_source = CITY_VALUE_JIS
    user_agent = None

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/{都道府県}/area/{JIS5}/list/`` を組み立てる。

        ``price_max_hint`` はサイト側へ渡さない（``ct`` が効かないことを実測済み）。
        代わりに賃料が安い順へ並べ、限られたページ数でも MUST を通る価格帯が
        1ページ目に載るようにする。
        """
        urls: list[str] = []
        for area in areas:
            slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not slug:
                raise ValueError(f"ABLE: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                raise ValueError(f"ABLE: 市区の指定が要ります: {area.prefecture}")
            urls.append(f"{BASE_URL}/{slug}/area/{area.value}/list/?o={SORT_RENT_ASC}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        return f"{base_url}&i={page}"

    def is_last_page(self, count: int) -> bool:
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。

        ``section.m-list_cassette`` が建物1件、``tr.detail-inner`` が住戸1件。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for building in doc.cssselect("section.m-list_cassette"):
            spec = _building_spec(building)
            title = _first_text(building, "h2")
            built = spec.get("built")
            floors = spec.get("floors")

            for room in building.cssselect("tr.detail-inner"):
                listing = self._parse_room(
                    room,
                    title=title,
                    address=spec.get("address"),
                    station_info=spec.get("station"),
                    age_years=parse_age_years(built),
                    total_floors=parse_total_floors(floors),
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
        external_id = _external_id(room)
        url = _detail_href(room)
        if not external_id or not url:
            return None

        cells = room.cssselect("td.price")
        price, mgmt_fee = _rent_and_mgmt(cells[0] if cells else None)
        deposit, key_money = _deposit_and_key(cells[1] if len(cells) > 1 else None, price)
        layout, area_sqm = _room_layout(room)

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            mgmt_fee_monthly=mgmt_fee,
            deposit_amount=deposit,
            key_money_amount=key_money,
            area_sqm=area_sqm,
            layout=layout,
            floor_num=parse_floor(_first_text(room, "td.floar li")),
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
        fields, features = _detail_tables(doc)

        blocks = list(features)
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
        rent, mgmt_fee = _split_rent_mgmt(fields.get("rent_mgmt"))
        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(fields.get("built")),
            floor_num=parse_floor(floors_text),
            total_floors=parse_total_floors(floors_text),
            mgmt_fee_monthly=mgmt_fee,
            deposit_amount=parse_months_fee(_first_part(fields.get("deposit")), rent),
            key_money_amount=parse_months_fee(_first_part(fields.get("key_money")), rent),
            address=clean_address(fields.get("address")),
            walk_minutes=parse_walk_minutes(fields.get("station")),
            type_specific_attrs={
                key: value
                for key, value in fields.items()
                if key in ("structure", "facing")
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


def _building_spec(building) -> dict[str, str]:
    """建物ヘッダの ``th/td`` を項目名で引ける形にする。"""
    spec: dict[str, str] = {}
    for th in building.cssselect("th"):
        label = "".join(th.text_content().split())
        key = _BUILDING_LABELS.get(label)
        sibling = th.getnext()
        if key is None or key in spec or sibling is None or sibling.tag != "td":
            continue
        value = " ".join(sibling.text_content().split())
        if value:
            spec[key] = value
    return spec


def _external_id(room) -> str | None:
    """住戸の物件ID（bkkey）。詳細リンクの ``data-bkkey`` が正典。"""
    for anchor in room.cssselect("a.js-detailLink, a.js-detailLinkUrl"):
        if value := (anchor.get("data-bkkey") or "").strip():
            return value
    for checkbox in room.cssselect("input[name='inquiryAll']"):
        if value := (checkbox.get("value") or "").strip():
            return value
    return None


def _detail_href(room) -> str | None:
    """住戸ごとの詳細ページURL。

    「詳細を見る」の ``href`` は ``javascript:void(0)`` なので使えない。
    実URLは ``onclick`` の ``clickBukkenInfoArea('...')`` の中にある。
    建物見出しの ``a.js-detailLinkUrl`` にも同形のURLが入るが建物あたり1本しか
    無いため、住戸が複数ある建物では住戸ごとの ``onclick`` を優先する。
    """
    for anchor in room.cssselect("a.js-detailLink"):
        if match := _ONCLICK_URL.search(anchor.get("onclick") or ""):
            return match.group(1)
    for anchor in room.cssselect("a.js-detailLinkUrl"):
        href = (anchor.get("href") or "").strip()
        if href.startswith("http"):
            return href
    return None


def _rent_and_mgmt(cell) -> tuple[int | None, int | None]:
    """``<span class="num">23.9</span>万円<br>10,000円`` を賃料と管理費へ分ける。

    賃料は ``23.9`` と ``万円`` に要素が割れるためセル全体から読む
    （``parse_yen`` は先頭の万円表記に当たる）。管理費は ``<br>`` の後ろ。
    ``-`` のときも ``<br>`` の tail には入るので、「円を含む行」を探すより確実。
    """
    if cell is None:
        return None, None
    price = parse_yen(cell.text_content())
    breaks = cell.cssselect("br")
    return price, parse_fee(breaks[0].tail if breaks else None)


def _deposit_and_key(cell, price: int | None) -> tuple[int | None, int | None]:
    """``23.9万<br>なし`` を敷金・礼金へ分ける。"""
    if cell is None:
        return None, None
    lines = [" ".join(t.split()) for t in cell.itertext() if t.strip()]
    deposit = lines[0] if lines else None
    key_money = lines[1] if len(lines) > 1 else None
    return parse_months_fee(deposit, price), parse_months_fee(key_money, price)


def _room_layout(room) -> tuple[str | None, float | None]:
    """``td.layout`` の ``1LDK<br>42.9㎡`` を分ける。"""
    cells = room.cssselect("td.layout")
    if not cells:
        return None, None
    cell = cells[0]
    texts = [" ".join(t.split()) for t in cell.itertext() if t.strip()]
    return (texts[0] if texts else None), parse_area_sqm(cell.text_content())


def _room_image(room) -> str | None:
    for image in room.cssselect("td.madori img"):
        value = image.get("src")
        if value and not value.startswith("data:"):
            return value
    return None


def _first_part(value: str | None) -> str | None:
    """「23.9万/--」「なし/--」の前半（敷金・礼金の本体）を返す。"""
    if not value:
        return None
    return value.split("/")[0].strip() or None


def _split_rent_mgmt(value: str | None) -> tuple[int | None, int | None]:
    """「23.9万円10,000円」を賃料と管理費へ分ける。

    詳細ページでは <br> が入らず連結されるため、金額表記を順に拾う。
    """
    if not value:
        return None, None
    amounts = _AMOUNT.findall(value)
    rent = parse_yen(amounts[0]) if amounts else None
    mgmt = parse_fee(amounts[1]) if len(amounts) > 1 else None
    return rent, mgmt


def _first_part(value: str | None) -> str | None:
    """「23.9万/--」「なし/--」の前半（敷金・礼金の本体）を返す。"""
    if not value:
        return None
    return value.split("/")[0].strip() or None


def _split_rent_mgmt(value: str | None) -> tuple[int | None, int | None]:
    """「23.9万円10,000円」を賃料と管理費へ分ける。

    詳細ページでは ``<br>`` が入らず連結されるため、金額表記を順に拾う。
    """
    if not value:
        return None, None
    amounts = _AMOUNT.findall(value)
    rent = parse_yen(amounts[0]) if amounts else None
    mgmt = parse_fee(amounts[1]) if len(amounts) > 1 else None
    return rent, mgmt


def _detail_tables(doc) -> tuple[dict[str, str], list[str]]:
    """詳細ページの ``th/td`` を構造化項目と設備原文へ振り分ける。"""
    fields: dict[str, str] = {}
    features: list[str] = []
    for th in doc.cssselect("th"):
        label = "".join(th.text_content().split())
        sibling = th.getnext()
        if not label or sibling is None or sibling.tag != "td":
            continue
        value = " ".join(sibling.text_content().split())
        if not value or value in EMPTY_MARKERS:
            continue
        if label in _FEATURE_LABELS:
            features.append(value)
            continue
        key = _DETAIL_LABELS.get(label)
        if key and key not in fields:
            fields[key] = value
    return fields, features
