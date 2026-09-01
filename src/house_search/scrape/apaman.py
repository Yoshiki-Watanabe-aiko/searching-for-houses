"""アパマンショップ（賃貸）のサイトアダプタ。

一覧URLは ``/{都道府県}/{市区コード}/``。市区コードは **JIS5桁の下3桁**で
（新宿区 13104 → ``104``）、``m_cities.jis_code`` から導出できる。

⚠ **このサイトだけ robots.txt を尊重していない。**
アパマンショップの robots.txt は ``User-agent: * / Disallow: /`` で全パスを
クローラに禁じているが、2026-09-01 にユーザーが取得する判断を明示したため
実装している（→ ADR 0011）。取得間隔・日次上限は他サイトと同じかそれ以上に
控えめなままにしてあり、緩めてはいけない。

APAMAN 固有の注意点:

* **Playwright は要らない。** 一覧・詳細ともサーバレンダリング済み（→ ADR 0010）
* **市区の指定が必須**（都道府県ページはエリア索引で掲載が載らない）
* 詳細ページは項目名が ``th`` ではなく ``span.heading`` の系統と、
  ``th/td`` の系統に**二分されている**。所在地・交通・築年月・建物階は前者、
  賃料・設備・構造は後者
* 「お問い合わせ」リンク（``/inquiry/bukkenentry/{ID}/``）は詳細ページではない
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urljoin

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

SITE_CODE = "APAMAN"
BASE_URL = "https://www.apamanshop.com"
# 1ページあたりの建物数（実測。表示件数の既定が20）
PAGE_SIZE = 20
# 市区コードは JIS5桁の下3桁
_CITY_CODE_LENGTH = 3

_SOLD_MARKERS = ("掲載が終了", "この物件は成約", "お探しの物件は見つかり", "ご成約済")

# 住戸の詳細URL ``/tokyo/121/b{建物CD}/{住戸CD}/``
_DETAIL_PATH = re.compile(r"^/[a-z]+/\d+/b\d+/(\d+)/")

# 詳細ページ ``span.heading`` のラベル
_HEADING_LABELS = {
    "所在地": "address",
    "交通": "station",
    "築年月": "built",
    "建物階": "floors",
    "部屋向き": "facing",
}

# 詳細ページ th のうち設備原文に載せたいラベル
# 「備考」「PRコメント」は生成文なので載せない（→ 課題#19 と同じ理由）
_FEATURE_LABELS = ("設備", "条件等")

# 詳細ページ th のうち構造化して取り出したい項目
_TH_LABELS = {
    "賃料": "rent",
    "管理費": "mgmt_fee",
    "敷金・保証金/礼金・権利金": "deposit_key",
    "構造・種別": "structure",
    "駐車場": "parking",
    "総戸数": "units",
    "入居可能日": "movein",
}


class ApamanScraper:
    """アパマンショップ 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = True
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    # robots.txt が全パスを禁じているサイト。ユーザーの明示的な判断で取得する
    # （→ ADR 0011）。他のサイトでこのフラグを立ててはいけない
    ignore_robots = True

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/{都道府県}/{JIS下3桁}/`` を組み立てる。

        賃料上限はサイト側へ渡さない（パラメータが未検証のため）。
        """
        urls: list[str] = []
        for area in areas:
            slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not slug:
                raise ValueError(f"APAMAN: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                raise ValueError(f"APAMAN: 市区の指定が要ります: {area.prefecture}")
            urls.append(f"{BASE_URL}/{slug}/{area.value[-_CITY_CODE_LENGTH:]}/")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        return base_url if page <= 1 else f"{base_url}?page={page}"

    def is_last_page(self, count: int) -> bool:
        """建物20件で1ページ。住戸数は建物数を下回らないので下限として使える。"""
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。

        同じ住戸がPR枠と通常枠の両方に現れることがあるため、
        物件IDで重複を落とす（実測で1ページに1件あった）。
        """
        doc = lxml_html.fromstring(html_text)
        listings: dict[str, ScrapedListing] = {}

        for building in doc.cssselect("article.mod_box_section_bdt"):
            spec = _first_text(building, "div.box_head_result p.info")
            title = _first_text(building, "div.box_head_result h2.name")
            address = clean_address(_first_text(building, "div.box_head_result p.address"))
            station_info = _station_info(building)

            for room in _room_rows(building):
                listing = _parse_room(
                    room,
                    title=title,
                    address=address,
                    station_info=station_info,
                    age_years=parse_age_years(spec),
                    total_floors=parse_total_floors(spec),
                )
                if listing is not None:
                    listings.setdefault(listing.external_id, listing)
        return list(listings.values())

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        headings = _heading_fields(doc)
        fields, features = _detail_tables(doc)

        blocks = list(features)
        if derived := _derived_tokens(fields, headings):
            blocks.append("、".join(derived))

        rent = parse_yen(fields.get("rent"))
        deposit, key_money = _deposit_and_key(fields.get("deposit_key"), rent)
        floors_text = headings.get("floors")
        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(headings.get("built")),
            floor_num=_floor_from_pair(floors_text),
            total_floors=parse_total_floors(floors_text),
            mgmt_fee_monthly=parse_fee(fields.get("mgmt_fee")),
            deposit_amount=deposit,
            key_money_amount=key_money,
            address=clean_address(headings.get("address")),
            walk_minutes=parse_walk_minutes(headings.get("station")),
            type_specific_attrs={
                key: value
                for key in ("structure", "units")
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


def _first_text(node, selector: str) -> str | None:
    found = node.cssselect(selector)
    if not found:
        return None
    return " ".join(found[0].text_content().split()) or None


def _station_info(building) -> str | None:
    """``ul.list_info`` の路線行を連結する（徒歩分数は最短を採る）。"""
    texts = [
        " ".join(node.text_content().split())
        for node in building.cssselect("div.box_head_result ul.list_info li")
    ]
    return " / ".join(t for t in texts if t) or None


def _room_rows(building) -> list[dict[str, object]]:
    """住戸テーブルを項目名 → セルの辞書の並びに直す。

    ⚠ **ヘッダ行の列位置は使えない。** ヘッダには先頭に余分な空セルが1つ入り、
    データ行より1列多い（実測: ヘッダ8列 / データ行7列）。列位置で対応させると
    間取りの欄に「お気に入り」を読んでしまう。

    クラス名が付いているのは賃料の ``td.chinryo`` だけなので、これを起点にして
    前後の相対位置で読む（部屋階 → 賃料 → 敷金礼金 → 間取り面積の並び）。
    """
    tables = building.cssselect("table")
    if not tables:
        return []

    parsed: list[dict[str, object]] = []
    for row in tables[0].cssselect("tr.tr_mid"):
        external_id, url = _detail_link(row)
        if not external_id:
            continue
        cells = row.cssselect("td")
        anchor = next(
            (i for i, cell in enumerate(cells) if "chinryo" in (cell.get("class") or "")),
            None,
        )
        values: dict[str, object] = {"external_id": external_id, "url": url, "row": row}
        if anchor is not None:
            for offset, key in ((-1, "floor"), (0, "rent"), (1, "deposit_key"), (2, "layout_area")):
                index = anchor + offset
                if 0 <= index < len(cells):
                    values[key] = cells[index]
        parsed.append(values)
    return parsed


def _detail_link(row) -> tuple[str | None, str]:
    """住戸の詳細URL。「お問い合わせ」リンクは詳細ページではないので除く。"""
    for anchor in row.cssselect("a[href]"):
        href = (anchor.get("href") or "").strip()
        if match := _DETAIL_PATH.match(href):
            return match.group(1), urljoin(BASE_URL, href.split("?")[0])
    return None, ""


def _lines(cell) -> list[str]:
    """1セルに ``<p>`` で縦積みされた値を行の並びに直す。"""
    if cell is None:
        return []
    paragraphs = cell.cssselect("p")
    if paragraphs:
        return [" ".join(p.text_content().split()) for p in paragraphs if p.text_content().strip()]
    text = " ".join(cell.text_content().split())
    return [text] if text else []


def _parse_room(
    room: dict[str, object],
    *,
    title: str | None,
    address: str | None,
    station_info: str | None,
    age_years: int | None,
    total_floors: int | None,
) -> ScrapedListing | None:
    external_id = room.get("external_id")
    if not external_id:
        return None

    rent_lines = _lines(room.get("rent"))
    fee_lines = _lines(room.get("deposit_key"))
    layout_lines = _lines(room.get("layout_area"))
    floor_lines = _lines(room.get("floor"))
    price = parse_yen(rent_lines[0]) if rent_lines else None

    return ScrapedListing(
        site_code=SITE_CODE,
        external_id=str(external_id),
        url=str(room.get("url") or ""),
        title=title,
        price=price,
        mgmt_fee_monthly=parse_fee(rent_lines[1]) if len(rent_lines) > 1 else None,
        deposit_amount=parse_months_fee(fee_lines[0], price) if fee_lines else None,
        key_money_amount=parse_months_fee(fee_lines[1], price) if len(fee_lines) > 1 else None,
        area_sqm=parse_area_sqm(" ".join(layout_lines)),
        layout=layout_lines[0] if layout_lines else None,
        floor_num=parse_floor(floor_lines[0]) if floor_lines else None,
        total_floors=total_floors,
        age_years=age_years,
        address=address,
        station_info=station_info,
        walk_minutes=parse_walk_minutes(station_info),
        image_url=_room_image(room),
    )


def _room_image(room: dict[str, object]) -> str | None:
    row = room.get("row")
    if row is None:
        return None
    for image in row.cssselect("img"):
        value = image.get("src")
        if value and not value.startswith("data:"):
            return value
    return None


def _heading_fields(doc) -> dict[str, str]:
    """``<li><span class="heading">所在地</span>…値…</li>`` を項目名で引ける形にする。"""
    fields: dict[str, str] = {}
    for heading in doc.cssselect("span.heading"):
        key = _HEADING_LABELS.get("".join(heading.text_content().split()))
        item = heading.getparent()
        if key is None or item is None or key in fields:
            continue
        texts = [" ".join(t.split()) for t in item.itertext() if t.strip()]
        # 先頭は見出しそのものなので落とす
        value = " ".join(texts[1:]).strip()
        if value:
            fields[key] = value
    return fields


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
        value = " ".join(sibling.text_content().split())
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


def _floor_from_pair(value: str | None) -> int | None:
    """``3階建/2階`` の後半（所在階）を返す。"""
    if not value:
        return None
    parts = value.split("/")
    return parse_floor(parts[-1] if len(parts) > 1 else None)


def _deposit_and_key(value: str | None, rent: int | None) -> tuple[int | None, int | None]:
    """``1ヶ月/－`` を敷金・礼金へ分ける。"""
    if not value:
        return None, None
    parts = value.split("/")
    deposit = parts[0] if parts else None
    key_money = parts[1] if len(parts) > 1 else None
    return parse_months_fee(deposit, rent), parse_months_fee(key_money, rent)


def _derived_tokens(fields: dict[str, str], headings: dict[str, str]) -> list[str]:
    """型付きの欄から辞書が照合できる語へ寄せる。"""
    derived: list[str] = []
    if structure := fields.get("structure"):
        # 「木造/アパート」の前半が構造
        derived.append(structure.split("/")[0].strip())
    if (facing := headings.get("facing")) and facing not in EMPTY_MARKERS:
        derived.append(facing if facing.endswith("向き") else f"{facing}向き")
    parking = fields.get("parking")
    if parking and not parking.startswith("無") and parking not in EMPTY_MARKERS:
        derived.append("駐車場あり")
    if (movein := fields.get("movein")) and movein.startswith("即"):
        derived.append("即入居可")
    return derived
