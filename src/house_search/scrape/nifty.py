"""ニフティ不動産（賃貸）のサイトアダプタ。

一覧URLは ``/rent/{都道府県}/{市区スラグ}_ct/``、ページ送りは ``/{N}/`` の
パス形式。市区の検索値は**サイト固有スラグ**（``adachiku``）で
``m_city_site_values`` から引く。JIS コードからは導出できない。

NIFTY 固有の注意点:

* **Playwright は要らない。** 一覧・詳細ともサーバレンダリング済み（→ ADR 0010）
* **市区の指定が必須。** 都道府県ページ（``/rent/tokyo/``）はエリア索引で、
  掲載は載らない（実測で詳細リンク6本のみ）
* **他社サイトの掲載を集約するポータル。** 詳細リンクが外部ドメイン
  （``sumaisagashi-madoguchi.com`` 等）へ向く掲載が混ざるので**取り込まない**。
  自社ドメインの ``detail_{ハッシュ}`` だけを対象にする
* **1ページの掲載数では最終ページを判定できない。** 建物40件/ページだが
  上記の理由で掲載数が建物数を下回るため、0件のときだけ打ち切る
* **賃料上限はサイト側へ渡さない。** robots.txt がクエリ付きURLを広く禁じており
  （``/*/?sort=*`` ``/*/?r3=*`` など）、賃料上限のパラメータは未検証のため
* 築年数の表記が ``1年7ヶ月`` で「築」が付かない
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urljoin

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_MAPPING, AreaTarget
from house_search.scrape.base import (
    EMPTY_MARKERS,
    ScrapedDetail,
    ScrapedListing,
    clean_address,
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

SITE_CODE = "NIFTY"
BASE_URL = "https://myhome.nifty.com"

_SOLD_MARKERS = ("掲載が終了", "この物件は成約", "お探しの物件は見つかり")

# 自社ドメインの詳細URL。``detail_{ハッシュ}`` の部分が物件ID
_DETAIL_PATH = re.compile(r"^/rent/[^/]+/[^/]+/detail_([0-9a-f]+)/")
# 「1年7ヶ月」「新築」。共通の parse_age_years は「築」を要求するので使えない
_AGE_YEARS = re.compile(r"(\d+)\s*年")
# 「無 質問 駐車場について…」のように案内文言が値に続く
_QUESTION_NOISE = re.compile(r"\s*質問\s.*$")

# 建物ヘッダのアイコン（svg の title）で項目を見分ける
_ICON_ADDRESS = "地図マーカー"

# 建物ヘッダの dl ラベル
_BUILDING_LABELS = {"総階数": "floors", "築年数": "age", "建物構造": "structure"}

# 詳細ページ dt/dd のラベル
_DD_LABELS = {
    "賃料": "rent",
    "間取り": "layout",
    "敷金/礼金": "deposit_key",
    "所在地": "address",
    "交通": "station",
    "築年月": "built",
    "階数/階建": "floors",
}

# 詳細ページ th のうち設備原文に載せたいラベル
# 「その他の情報」は備考の生成文なので載せない（未知表記が文断片で埋まるため）
_FEATURE_LABELS = ("設備", "条件等")

# 詳細ページ th のうち構造化して取り出したい項目
_TH_LABELS = {
    "建物構造": "structure",
    "採光向き": "facing",
    "駐車場": "parking",
    "現況": "status",
    "取引態様": "transaction",
    "総戸数": "units",
}


class NiftyScraper:
    """ニフティ不動産 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = True
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/rent/{都道府県}/{市区スラグ}_ct/`` を組み立てる。"""
        urls: list[str] = []
        for area in areas:
            slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not slug:
                raise ValueError(f"NIFTY: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                raise ValueError(f"NIFTY: 市区の指定が要ります: {area.prefecture}")
            urls.append(f"{BASE_URL}/rent/{slug}/{area.value}_ct/")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りは ``/{N}/`` のパス形式（1ページ目は番号を付けない）。"""
        return base_url if page <= 1 else f"{base_url.rstrip('/')}/{page}/"

    def is_last_page(self, count: int) -> bool:
        """掲載数では判定できないため、0件のときだけ最終ページとみなす。

        1ページの建物数は40だが、外部ドメインへ飛ぶ掲載を取り込まないので
        掲載数は建物数を下回りうる。件数を閾値にすると早すぎる打ち切りになる。
        """
        return count == 0

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for card in doc.cssselect("li.result-bukken-list"):
            spec = _building_spec(card)
            title = _building_title(card)
            station_info = _station_info(card)
            age_text = spec.get("age")

            for room in card.cssselect("tbody.click-area"):
                listing = _parse_room(
                    room,
                    title=title,
                    address=_building_address(card),
                    station_info=station_info,
                    age_years=_age_years(age_text),
                    total_floors=parse_total_floors(spec.get("floors")),
                )
                if listing is not None:
                    listings.append(listing)
        return listings

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。

        主要スペックが ``dt/dd``、細目が ``th/td`` と2系統に分かれている。
        """
        doc = lxml_html.fromstring(html_text)
        pairs = _definition_pairs(doc)
        fields, features = _detail_tables(doc)

        blocks = list(features)
        if derived := _derived_tokens(fields):
            blocks.append("、".join(derived))

        rent = parse_yen(pairs.get("rent"))
        deposit, key_money = _deposit_and_key(pairs.get("deposit_key"), rent)
        floors_text = pairs.get("floors")
        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(pairs.get("built")),
            floor_num=parse_floor(floors_text),
            total_floors=parse_total_floors(floors_text),
            mgmt_fee_monthly=_mgmt_fee(pairs.get("rent")),
            deposit_amount=deposit,
            key_money_amount=key_money,
            address=clean_address(pairs.get("address")),
            walk_minutes=parse_walk_minutes(pairs.get("station")),
            type_specific_attrs={
                key: value
                for key in ("structure", "facing", "status", "transaction", "units")
                if (value := fields.get(key)) and value not in EMPTY_MARKERS
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


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    return _QUESTION_NOISE.sub("", " ".join(value.split())).strip() or None


def _building_title(card) -> str | None:
    """建物名。見出しは「◯◯の賃貸物件」なので接尾辞を落とす。

    建物情報ページを持たない掲載では見出しがリンクにならないため、
    ``h2`` そのものへフォールバックする。
    """
    heads = card.cssselect("h2 a") or card.cssselect("h2")
    if not heads:
        return None
    text = " ".join(heads[0].text_content().split())
    return text.removesuffix("の賃貸物件") or None


def _station_info(card) -> str | None:
    """``li[data-transport-access]`` を連結する（徒歩分数は最短を採る）。"""
    texts = [
        " ".join(node.text_content().split())
        for node in card.cssselect("li[data-transport-access]")
    ]
    return " / ".join(t for t in texts if t) or None


def _building_address(card) -> str | None:
    """住所。ラベルが無く、地図マーカーのアイコンが目印になっている。"""
    for block in card.cssselect("div.box.is-flex"):
        titles = [t.text_content().strip() for t in block.cssselect("svg title")]
        if _ICON_ADDRESS in titles:
            paragraphs = block.cssselect("p")
            if paragraphs:
                return clean_address(" ".join(paragraphs[0].text_content().split()))
    return None


def _building_spec(card) -> dict[str, str]:
    """``div.bukken-info-items`` の dt/dd を項目名で引ける形にする。"""
    spec: dict[str, str] = {}
    for dl in card.cssselect("div.bukken-info-items dl"):
        terms = dl.cssselect("dt")
        values = dl.cssselect("dd")
        if not terms or not values:
            continue
        key = _BUILDING_LABELS.get("".join(terms[0].text_content().split()))
        value = " ".join(values[0].text_content().split())
        if key and value and key not in spec:
            spec[key] = value
    return spec


def _age_years(value: str | None) -> int | None:
    """``1年7ヶ月`` ``新築`` を築年数へ。「築」が付かないので専用に読む。"""
    if not value:
        return None
    if "新築" in value:
        return 0
    match = _AGE_YEARS.search(value)
    return int(match.group(1)) if match else None


def _parse_room(
    room,
    *,
    title: str | None,
    address: str | None,
    station_info: str | None,
    age_years: int | None,
    total_floors: int | None,
) -> ScrapedListing | None:
    external_id, url = _detail_link(room)
    if not external_id:
        return None

    cells = room.cssselect("td[data-link-wrap-item]")
    price, mgmt_fee = _rent_and_mgmt(room)
    deposit, key_money = _room_deposit_and_key(room, price)
    layout, area_sqm = _layout_and_area(cells)

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
        floor_num=_room_floor(cells),
        total_floors=total_floors,
        age_years=age_years,
        address=address,
        station_info=station_info,
        walk_minutes=parse_walk_minutes(station_info),
        image_url=_room_image(room),
    )


def _detail_link(room) -> tuple[str | None, str]:
    """自社ドメインの詳細リンクだけを採る。

    他社サイトへ飛ぶ掲載（``sumaisagashi-madoguchi.com`` 等）は
    別ドメインのため取り込まない。
    """
    for anchor in room.cssselect("a[data-detail-link]"):
        href = (anchor.get("href") or "").strip()
        if match := _DETAIL_PATH.match(href):
            return match.group(1), urljoin(BASE_URL, href)
    return None, ""


def _rent_and_mgmt(room) -> tuple[int | None, int | None]:
    """``11.8万円`` と ``3,000円``（管理費）を分ける。"""
    cells = room.cssselect("td.bukken-info-rent")
    if not cells:
        return None, None
    paragraphs = cells[0].cssselect("p")
    if not paragraphs:
        return None, None
    price = parse_yen(" ".join(paragraphs[0].text_content().split()))
    mgmt = (
        parse_fee(" ".join(paragraphs[1].text_content().split()))
        if len(paragraphs) > 1
        else None
    )
    return price, mgmt


def _room_deposit_and_key(room, price: int | None) -> tuple[int | None, int | None]:
    """``敷 不要`` ``礼 不要`` の dl 2つを敷金・礼金へ分ける。"""
    values: dict[str, str] = {}
    for dl in room.cssselect("dl"):
        terms, defs = dl.cssselect("dt"), dl.cssselect("dd")
        if terms and defs:
            label = "".join(terms[0].text_content().split())
            values.setdefault(label, " ".join(defs[0].text_content().split()))
    return (
        parse_months_fee(_normalize_fee(values.get("敷")), price),
        parse_months_fee(_normalize_fee(values.get("礼")), price),
    )


def _normalize_fee(value: str | None) -> str | None:
    """``不要`` を共通パーサが「無し」と読める表記へ寄せる。"""
    if value is None:
        return None
    return "なし" if value.strip() in ("不要", "無", "なし") else value


def _layout_and_area(cells) -> tuple[str | None, float | None]:
    """``1LDK`` と ``45.0㎡`` が同じセルに縦に並ぶ。"""
    for cell in cells:
        paragraphs = cell.cssselect("p")
        if len(paragraphs) == 2 and (area := parse_area_sqm(cell.text_content())):
            return " ".join(paragraphs[0].text_content().split()), area
    return None, None


def _room_floor(cells) -> int | None:
    """所在階のセル（``1階`` だけが入る）を探す。"""
    for cell in cells:
        text = " ".join(cell.text_content().split())
        if re.fullmatch(r"(?:地下)?\d+階", text):
            return parse_floor(text)
    return None


def _room_image(room) -> str | None:
    for image in room.cssselect("img.thumbnail, img.lazyload"):
        value = image.get("data-src") or image.get("src")
        if value and not value.startswith("data:") and "lazy-load" not in value:
            return value
    return None


def _definition_pairs(doc) -> dict[str, str]:
    """詳細ページの ``dt/dd`` を項目名で引ける形にする（最初の出現だけ）。"""
    pairs: dict[str, str] = {}
    for term in doc.cssselect("dt"):
        sibling = term.getnext()
        if sibling is None or sibling.tag != "dd":
            continue
        key = _DD_LABELS.get("".join(term.text_content().split()))
        value = _clean(sibling.text_content())
        if key and value and key not in pairs:
            pairs[key] = value
    return pairs


def _detail_tables(doc) -> tuple[dict[str, str], list[str]]:
    """詳細ページの ``th/td`` を構造化項目と設備原文へ振り分ける。"""
    fields: dict[str, str] = {}
    features: list[str] = []
    seen: set[str] = set()
    for th in doc.cssselect("th"):
        label = "".join(th.text_content().split())
        sibling = th.getnext()
        if not label or sibling is None or sibling.tag != "td":
            continue
        value = _clean(sibling.text_content())
        if not value or value in EMPTY_MARKERS:
            continue
        if label in _FEATURE_LABELS:
            if label not in seen:
                seen.add(label)
                features.append(value)
            continue
        key = _TH_LABELS.get(label)
        if key and key not in fields:
            fields[key] = value
    return fields, features


def _mgmt_fee(rent_text: str | None) -> int | None:
    """``10.1万円＋ 管理費等8,000円`` から管理費だけを取り出す。"""
    if not rent_text:
        return None
    _, _, tail = rent_text.partition("管理費等")
    return parse_fee(tail) if tail else None


def _deposit_and_key(value: str | None, rent: int | None) -> tuple[int | None, int | None]:
    """``無 / 2ヶ月`` を敷金・礼金へ分ける。"""
    if not value:
        return None, None
    parts = value.split("/")
    deposit = _normalize_fee(parts[0]) if parts else None
    key_money = _normalize_fee(parts[1]) if len(parts) > 1 else None
    return parse_months_fee(deposit, rent), parse_months_fee(key_money, rent)


def _derived_tokens(fields: dict[str, str]) -> list[str]:
    """型付きの欄から辞書が照合できる語へ寄せる。"""
    derived: list[str] = []
    if structure := fields.get("structure"):
        derived.append(structure)
    if (facing := fields.get("facing")) and facing not in EMPTY_MARKERS:
        derived.append(facing if facing.endswith("向き") else f"{facing}向き")
    parking = fields.get("parking")
    if parking and not parking.startswith("無") and parking not in EMPTY_MARKERS:
        derived.append("駐車場あり")
    return derived
