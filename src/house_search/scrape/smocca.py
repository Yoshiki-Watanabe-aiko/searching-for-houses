"""スモッカ（賃貸）のサイトアダプタ。

一覧URLは ``/search/{都道府県}/city/{JIS5}``。市区の検索値は **JIS5桁コード**で
``m_cities.jis_code`` から導出できる（Phase 3 の実測で確定。
``m_city_site_values`` は東京23区のブロックが JIS、それ以外がスラグという
矛盾した状態だったが、正しいのは JIS のほう）。

SMOCCA 固有の注意点:

* **Playwright は要らない。** 一覧・詳細ともサーバレンダリング済み（→ ADR 0010）
* **市区の指定が必須。**
* **robots.txt がページ送りを禁じている**（``Disallow: /*/page/``）。
  ``/search/{都道府県}/city/{JIS5}/page/2`` は取りに行かず、**1ページ目だけ**を扱う。
  1ページは90件なので、市区あたり上位90件が取得範囲になる
* ``/search/results``（条件検索の結果）も禁止されているため、
  **賃料上限をサイト側へ渡せない**。上限判定はローカルで行う（賃貸EXと同じ）
* **一覧のレイアウトが2種類ある**（実測）。通常表示は1住戸=1行の表で、
  列位置をヘッダ行のラベルから決めて読む。グループ表示
  （``building-group-container``）は同一建物の住戸を1つの ``td`` に畳み、
  賃料・管理費・敷金・礼金まで同じセルへ入れるため別経路で読む
* **建物欄に築年数が載らない**（``地上3階建 / 2014年04月 / 賃貸アパート``）。
  築年月から数える
* 詳細の「備考」は生成文なので設備原文に載せない（→ 課題#19 と同じ理由）
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
    age_years_from_built,
    clean_address,
    parse_age_years,
    parse_area_sqm,
    parse_built_on,
    parse_fee,
    parse_floor,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "SMOCCA"
BASE_URL = "https://smocca.jp"
# 1ページあたりの掲載数（``data-per-page`` の実測値）。
# ページ送りが robots.txt で禁止されているため、実質これが市区あたりの上限になる
PAGE_SIZE = 90

_SOLD_MARKERS = ("掲載を終了", "掲載が終了", "お探しの物件は見つかり", "成約済")

# 住戸行のヘッダラベル → 読み取るキー。列位置は掲載によって変わりうる
_COLUMN_LABELS = {
    "階部屋番号": "floor",
    "賃料管理費": "rent",
    "敷金礼金": "deposit_key",
    "間取面積": "layout_area",
    "方位": "facing",
}

# 建物ヘッダの行は項目名を持たず、アイコンのクラスで種類が決まる
_ROW_ICONS = {
    "icon_20_address01": "address",
    "icon_20_train01": "station",
    "icon_20_house01": "building",
}

# 詳細ページ th のうち設備原文に載せたいラベル
_FEATURE_LABELS = ("特徴", "設備/条件")

# 詳細ページ th のうち構造化して取り出したい項目
_DETAIL_LABELS = {
    "賃料/管理費等": "rent_mgmt",
    "敷金": "deposit",
    "礼金": "key_money",
    "種別/構造": "structure",
    "築年月": "built",
    "所在地": "address",
    "主要交通機関": "station",
    "方位": "facing",
    "階数/部屋番号": "floors",
    "駐車場": "parking",
    "取引態様": "transaction",
    "入居可能時期": "movein",
}

# 所在地の欄に付く導線リンクの文言
_ADDRESS_NOISE = re.compile(r"\s*[^\s]*の賃貸を探す.*$")


class SmoccaScraper:
    """スモッカ 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = True
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    ignore_robots = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/search/{都道府県}/city/{JIS5}`` を組み立てる。

        賃料上限は渡さない（``/search/results`` が robots.txt で禁止のため）。
        """
        urls: list[str] = []
        for area in areas:
            slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not slug:
                raise ValueError(f"SMOCCA: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                raise ValueError(f"SMOCCA: 市区の指定が要ります: {area.prefecture}")
            urls.append(f"{BASE_URL}/search/{slug}/city/{area.value}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """1ページ目だけを扱う。2ページ目以降は robots.txt が禁じている。"""
        if page > 1:
            raise ValueError("SMOCCA: robots.txt がページ送り（/*/page/）を禁じています")
        return base_url

    def is_last_page(self, count: int) -> bool:
        """常に最終ページとして扱い、2ページ目を取りに行かせない。"""
        return True

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for card in doc.cssselect("div.item_list01"):
            rows = _card_rows(card)
            title = _first_text(card, "div.item_list01_title h3")
            spec = rows.get("building")

            for room in _room_rows(card):
                listing = _parse_room(
                    room,
                    title=title,
                    address=rows.get("address"),
                    station_info=rows.get("station"),
                    age_years=_building_age(spec),
                    total_floors=parse_total_floors(spec),
                )
                if listing is not None:
                    listings.append(listing)
        return listings

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        fields, features = _detail_tables(doc)

        blocks = list(features)
        if derived := _derived_tokens(fields):
            blocks.append("、".join(derived))

        floors_text = fields.get("floors")
        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(fields.get("built")),
            floor_num=parse_floor(floors_text),
            total_floors=parse_total_floors(floors_text),
            mgmt_fee_monthly=_mgmt_fee(fields.get("rent_mgmt")),
            deposit_amount=parse_fee(fields.get("deposit")),
            key_money_amount=parse_fee(fields.get("key_money")),
            address=_clean_address(fields.get("address")),
            walk_minutes=parse_walk_minutes(fields.get("station")),
            type_specific_attrs={
                key: value
                for key in ("structure", "facing", "transaction")
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


def _clean_address(value: str | None) -> str | None:
    """「東京都足立区西新井３ 足立区の賃貸を探す」から導線リンクを落とす。"""
    if not value:
        return None
    return clean_address(_ADDRESS_NOISE.sub("", " ".join(value.split())))


def _card_rows(card) -> dict[str, str]:
    """建物ヘッダの行をアイコンのクラスで振り分ける。

    住所・交通・建物スペックの3行はいずれも項目名を持たず、
    行頭のアイコン（``icon_20_address01`` 等）だけが種類を表している。
    """
    rows: dict[str, str] = {}
    for row in card.cssselect("ul.info_wrap > li"):
        key = None
        for span in row.cssselect("span"):
            for name in (span.get("class") or "").split():
                if name in _ROW_ICONS:
                    key = _ROW_ICONS[name]
                    break
            if key:
                break
        value = " ".join(row.text_content().split())
        if key and value and key not in rows:
            rows[key] = value
    if "address" in rows:
        rows["address"] = _clean_address(rows["address"]) or rows["address"]
    return rows


def _room_rows(card) -> list[dict[str, object]]:
    """住戸行を項目名で引ける形に正規化する。

    一覧のレイアウトは2種類ある（実測）。

    * **通常表示**: 1住戸=1行の表。列の並びは掲載によって変わりうるので、
      位置ではなく**ヘッダ行のラベル**でどの列が何かを決めてから読む
    * **グループ表示**（``building-group-container``）: 同一建物の住戸を
      1つの ``td`` に畳み、賃料・管理費・敷金・礼金まで同じセルへ入れる。
      ヘッダのラベルが列に対応しないため、セル内の意味づけされたクラス
      （``group-bukken-price-info`` 等）で読む
    """
    tables = card.cssselect("table")
    if not tables:
        return []
    rows = tables[0].cssselect("tr")
    if len(rows) < 2:
        return []

    columns = _header_columns(rows[0])
    parsed: list[dict[str, object]] = []
    for row in rows[1:]:
        anchors = row.cssselect("a[data-link-id]")
        if not anchors:
            continue
        grouped = row.cssselect("div.group-bukken-row-info-wrapper")
        values = _grouped_room(grouped[0]) if grouped else _tabular_room(row, columns)
        values["anchor"] = anchors[0]
        parsed.append(values)
    return parsed


def _header_columns(header_row) -> dict[int, str]:
    """ヘッダ行のラベルから「列位置 → 項目」の対応を作る。"""
    columns: dict[int, str] = {}
    for index, cell in enumerate(header_row.cssselect("th, td")):
        label = "".join(cell.text_content().split())
        if key := _COLUMN_LABELS.get(label):
            columns[index] = key
    return columns


def _tabular_room(row, columns: dict[int, str]) -> dict[str, object]:
    """通常表示の1行を項目名 → セルの辞書にする。"""
    cells = row.cssselect("th, td")
    return {key: cells[index] for index, key in columns.items() if index < len(cells)}


def _grouped_room(wrapper) -> dict[str, object]:
    """グループ表示の1住戸を項目名 → セルの辞書にする。

    賃料と敷礼が同じセルに同居するため、``div`` の意味づけで切り分ける。
    """
    values: dict[str, object] = {}
    for selector, key in (
        ("div.group-bukken-room-info", "floor"),
        ("div.group-bukken-plan-info", "layout_area"),
    ):
        found = wrapper.cssselect(selector)
        if found:
            values[key] = found[0]
    price_info = wrapper.cssselect("div.group-bukken-price-info > div")
    if price_info:
        values["rent"] = price_info[0]
    if len(price_info) > 1:
        values["deposit_key_inline"] = price_info[1]
    return values


def _lines(cell) -> list[str]:
    """1セルに ``<br>`` で縦積みされた値を行の並びに直す。

    ``itertext`` で分けると ``<span><span>12.8</span>万円</span>`` のような
    入れ子が「12.8」と「万円」に割れて金額として読めなくなる。
    区切りは ``<br>`` だけとして扱う。
    """
    if cell is None:
        return []
    lines: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = " ".join("".join(buffer).split())
        if text:
            lines.append(text)
        buffer.clear()

    if cell.text:
        buffer.append(cell.text)
    for child in cell:
        if child.tag == "br":
            flush()
        else:
            buffer.append(child.text_content())
        if child.tail:
            buffer.append(child.tail)
    flush()
    return lines


def _parse_room(
    room: dict[str, object],
    *,
    title: str | None,
    address: str | None,
    station_info: str | None,
    age_years: int | None,
    total_floors: int | None,
) -> ScrapedListing | None:
    anchor = room.get("anchor")
    external_id = (anchor.get("data-link-id") or "").strip() if anchor is not None else ""
    if not external_id:
        return None

    layout_lines = _lines(room.get("layout_area"))
    floor_lines = _lines(room.get("floor"))
    price, mgmt_fee = _rent_and_mgmt(_lines(room.get("rent")))
    deposit, key_money = _deposit_and_key(room)

    return ScrapedListing(
        site_code=SITE_CODE,
        external_id=external_id,
        url=(anchor.get("href") or f"{BASE_URL}/bukken/detail/{external_id}"),
        title=title,
        price=price,
        mgmt_fee_monthly=mgmt_fee,
        deposit_amount=deposit,
        key_money_amount=key_money,
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


def _rent_and_mgmt(lines: list[str]) -> tuple[int | None, int | None]:
    """賃料と管理費を分ける。

    通常表示は ``12.8万円`` と ``3,000円`` が ``<br>`` で2行、
    グループ表示は ``9.8万円 / 1.0万円`` と1行にまとまる。
    """
    if not lines:
        return None, None
    head = lines[0]
    if "/" in head:
        rent_text, _, mgmt_text = head.partition("/")
        return parse_yen(rent_text), parse_fee(mgmt_text)
    return parse_yen(head), (parse_fee(lines[1]) if len(lines) > 1 else None)


def _deposit_and_key(room: dict[str, object]) -> tuple[int | None, int | None]:
    """敷金と礼金を分ける。

    通常表示は専用の列、グループ表示は賃料と同じセルに
    ``敷 無料 礼 無料`` の形で同居する。
    """
    if (cell := room.get("deposit_key")) is not None:
        lines = _lines(cell)
        return (
            parse_fee(lines[0]) if lines else None,
            parse_fee(lines[1]) if len(lines) > 1 else None,
        )
    if (cell := room.get("deposit_key_inline")) is not None:
        text = " ".join(cell.text_content().split())
        return _labelled_fee(text, "敷"), _labelled_fee(text, "礼")
    return None, None


def _labelled_fee(text: str, label: str) -> int | None:
    """``敷 無料 礼 1ヶ月`` から指定ラベルの金額を取り出す。"""
    match = re.search(rf"{label}\s*(\S+)", text)
    return parse_fee(match.group(1)) if match else None


def _building_age(spec: str | None) -> int | None:
    """``地上3階建 / 2014年04月 / 賃貸アパート`` から築年数を求める。

    新築は「新築(2026年08月)」と書かれるが、既存の建物は築年数ではなく
    **築年月**しか載らないため、年月から数える。
    """
    if years := parse_age_years(spec):
        return years
    if spec and "新築" in spec:
        return 0
    return age_years_from_built(spec)


def _room_image(room: dict[str, object]) -> str | None:
    anchor = room.get("anchor")
    if anchor is None:
        return None
    row = anchor.getparent()
    while row is not None and row.tag != "tr":
        row = row.getparent()
    if row is None:
        return None
    for image in row.cssselect("img"):
        value = image.get("data-original") or image.get("src")
        if value and not value.startswith("data:") and "spinner" not in value:
            return value
    return None


def _detail_tables(doc) -> tuple[dict[str, str], list[str]]:
    """詳細ページの ``th/td`` を構造化項目と設備原文へ振り分ける。

    同一物件の他社掲載や近隣物件が続けて並ぶため**最初の出現だけ**採る。
    """
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
        key = _DETAIL_LABELS.get(label)
        if key and key not in fields:
            fields[key] = value
    return fields, features


def _mgmt_fee(value: str | None) -> int | None:
    """``10.9万円（管理費等 3,000円）`` から管理費だけを取り出す。"""
    if not value:
        return None
    _, _, tail = value.partition("管理費等")
    return parse_fee(tail) if tail else None


def _derived_tokens(fields: dict[str, str]) -> list[str]:
    """型付きの欄から辞書が照合できる語へ寄せる。"""
    derived: list[str] = []
    if structure := fields.get("structure"):
        # 「アパート/木造」の後半が構造
        derived.append(structure.split("/")[-1].strip())
    if (facing := fields.get("facing")) and facing not in EMPTY_MARKERS:
        derived.append(facing if facing.endswith("向き") else f"{facing}向き")
    parking = fields.get("parking")
    if parking and not parking.startswith("無") and parking not in EMPTY_MARKERS:
        derived.append("駐車場あり")
    if (movein := fields.get("movein")) and movein.startswith("即"):
        derived.append("即入居可")
    return derived
