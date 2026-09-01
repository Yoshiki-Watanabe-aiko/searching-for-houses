"""賃貸EX（賃貸）のサイトアダプタ。

Phase 2 の調査で確定した仕様:

* ``base_url`` は ``https://chintai-ex.jp``
* 市区の一覧は ``/search/city/{JIS5}``、ページ送りは ``/page/{N}`` の**パス形式**
* 詳細は ``/dwelling/show/{ハッシュ}``
* **同じURLでも一覧レイアウトが3通り返る**（実測）。項目名つきの表を
  ``table`` で包む形・``tr`` で包む形・項目名の無い圧縮行の3種。
  ``parse_list`` はタグではなくクラスで拾い、中身で分岐する

**robots.txt が ``Disallow: *?*`` でクエリ付きURLを全面的に禁じている**
（``/search/results`` も禁止）。そのため賃料上限をサイト側へ渡せず、
市区の全掲載を取ってローカルで判定することになる。パス形式のページ送りは
この制約に触れないので、ページングだけは通常どおり使える。

詳細ページには同一物件の他社掲載と近隣物件が続けて並ぶため、
``th/td`` は**最初の出現だけ**を対象の掲載として採る。

観測モードで運用する（``m_sites.is_active=false``）。ユニーク物件率を
Phase 4 の名寄せで実測してから通知を有効化する（→ 課題#5・#11）。
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
    age_years_from_built,
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

SITE_CODE = "CHINTAI_EX"
BASE_URL = "https://chintai-ex.jp"
# 1ページあたりの掲載数（実測。PR枠は除いた本体カードの数）
PAGE_SIZE = 20

_SOLD_MARKERS = ("掲載が終了", "掲載を終了", "お探しの物件", "見つかりませんでした")
# 「敷 無料 礼 1ヶ月」形式の敷金・礼金
_SHIKIREI = re.compile(r"敷\s*(\S+?)\s*礼\s*(\S+)")
_DETAIL_ID = re.compile(r"/dwelling/show/([\w-]+)")

# 一覧・詳細に共通する th ラベル
_LABELS = {
    "賃料": "rent",
    "共益費": "mgmt_fee",
    "敷金/礼金": "deposit_key",
    "階層 / 方位": "floors_facing",
    "階層/方位": "floors_facing",
    "間取 / 面積": "layout_area",
    "間取/面積": "layout_area",
    "種別 / 構造": "type_structure",
    "種別/構造": "type_structure",
    "築年月": "built",
    "築年数": "built",
    "特徴": "features",
    "本物件について": "description",
}


class ChintaiExScraper:
    """賃貸EX の取得と解析。"""

    site_code = SITE_CODE
    requires_city = True
    city_value_source = CITY_VALUE_JIS
    user_agent = None

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/search/city/{JIS5}`` を組み立てる。

        ``price_max_hint`` は使わない。robots.txt がクエリ付きURLを禁じており、
        価格上限を渡す手段がサイト側に無いため上限判定はすべてローカルで行う。
        """
        urls: list[str] = []
        for area in areas:
            if not area.value:
                raise ValueError(f"CHINTAI_EX: 市区の指定が要ります: {area.prefecture}")
            urls.append(f"{BASE_URL}/search/city/{area.value}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りはパス形式（``?page=`` は robots.txt で禁止されている）。"""
        return base_url if page <= 1 else f"{base_url}/page/{page}"

    def is_last_page(self, count: int) -> bool:
        """1件も取れなくなったら終わり。

        件数でページ末尾を判定しない。賃貸EX は**同じURLでも一覧レイアウトが
        3通り返る**（後述）ため、1ページあたりの掲載数が一定にならない。
        """
        return count == 0

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。

        検索結果のカードは ``js-bukken`` クラスを持つ。**同じURLでも複数の
        レイアウトが返る**（実測）ので、タグではなくクラスで拾ってから中身で分岐する。

        * ``th`` を持つカード: 項目名つきの表。``table`` のことも ``tr`` のこともある
        * ``th`` を持たないカード: 建物ごとにまとめた圧縮行。値はセルのクラスで引く

        PR枠（``li.swiper-slide`` 内の ``table.bukken``）は ``js-bukken`` を
        持たないので自然に外れる。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for card in doc.cssselect(".js-bukken"):
            listing = (
                self._parse_card(card) if card.cssselect("th") else self._parse_compact(card)
            )
            if listing is not None:
                listings.append(listing)
        return listings

    def _parse_compact(self, row) -> ScrapedListing | None:
        """建物ごとにまとめた圧縮行レイアウトを読む。

        項目名が無く、値はセルのクラス（``group-bukken-*``）で引く。
        住所・駅・階建・築年月は建物ヘッダ側にしか無いので親を辿って拾う。
        """
        external_id = (row.get("id") or "").strip()
        anchors = row.cssselect('a[href*="/dwelling/show"]')
        if not external_id or not anchors:
            return None

        header = _building_header(row)
        building = _icon_text(header, "icon_home06") if header is not None else None
        price = parse_yen(_text(row, "span.group-bukken-chinryou"))
        deposit, key_money = _compact_deposit_key(_text(row, "span.group-bukken-shikirei"))
        # 間取りと面積は <br> で分かれた別のテキストノード。連結して読むと
        # 「1R 12.66m²」がまるごと間取り扱いになる
        plan_cells = row.cssselect("td.group-bukken-plan-cell")
        plan_parts = (
            [" ".join(t.split()) for t in plan_cells[0].itertext() if t.strip()]
            if plan_cells
            else []
        )
        layout = plan_parts[0] if plan_parts else None
        area_sqm = parse_area_sqm(plan_cells[0].text_content()) if plan_cells else None

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=urljoin(BASE_URL, anchors[0].get("href") or ""),
            title=_text(header, "a") if header is not None else None,
            price=price,
            mgmt_fee_monthly=parse_fee(_strip_separator(_text(row, "span.group-bukken-kanri"))),
            deposit_amount=parse_months_fee(deposit, price),
            key_money_amount=parse_months_fee(key_money, price),
            area_sqm=area_sqm,
            layout=layout,
            floor_num=parse_floor(_text(row, "td.group-bukken-room-cell")),
            total_floors=parse_total_floors(building),
            age_years=age_years_from_built(building),
            address=_icon_text(header, "icon_address06") if header is not None else None,
            station_info=_icon_text(header, "icon_train06") if header is not None else None,
            walk_minutes=parse_walk_minutes(
                _icon_text(header, "icon_train06") if header is not None else None
            ),
            image_url=None,
        )

    def _parse_card(self, card) -> ScrapedListing | None:
        external_id = (card.get("id") or "").strip()
        anchors = card.cssselect('a[href*="/dwelling/show"]')
        if not external_id or not anchors:
            return None
        url = urljoin(BASE_URL, anchors[0].get("href") or "")

        fields = _labelled_fields(card)
        price = parse_yen(fields.get("rent"))
        deposit, key_money = _split_pair(fields.get("deposit_key"))
        layout, area_sqm = _split_layout_area(fields.get("layout_area"))
        floors_text = (fields.get("floors_facing") or "").split("/")

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=url,
            title=_card_title(card),
            price=price,
            mgmt_fee_monthly=parse_fee(fields.get("mgmt_fee")),
            deposit_amount=parse_months_fee(deposit, price),
            key_money_amount=parse_months_fee(key_money, price),
            area_sqm=area_sqm,
            layout=layout,
            floor_num=parse_floor(floors_text[0] if floors_text else None),
            total_floors=parse_total_floors(fields.get("floors_facing")),
            age_years=age_years_from_built(fields.get("built")),
            address=_icon_text(card, "icon_address06"),
            station_info=_icon_text(card, "icon_train06"),
            walk_minutes=parse_walk_minutes(_icon_text(card, "icon_train06")),
            image_url=None,
        )

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。

        同一物件の他社掲載と近隣物件が続けて並ぶため、``th/td`` は
        最初の出現だけを対象の掲載として採る。
        """
        doc = lxml_html.fromstring(html_text)
        fields = _labelled_fields(doc)

        blocks: list[str] = []
        if features := fields.get("features"):
            blocks.append(features)
        # 「本物件について」は設備を説明する生成文。このサイトで最も語彙が多い一方、
        # 文章なので誤検出の温床にもなる。観測モードのうちに未知表記と
        # 抽出数を実測して採否を判断する（→ 課題#11）
        if description := fields.get("description"):
            blocks.append(description)

        type_structure = fields.get("type_structure")
        floors_facing = fields.get("floors_facing")
        derived: list[str] = []
        if type_structure:
            # 「アパート / 鉄骨造」。構造だけが辞書の語彙に対応する
            parts = [part.strip() for part in type_structure.split("/")]
            if len(parts) > 1 and parts[1] not in EMPTY_MARKERS:
                derived.append(parts[1])
        facing = _facing(floors_facing)
        if facing:
            derived.append(facing if facing.endswith("向き") else f"{facing}向き")
        if derived:
            blocks.append("、".join(derived))

        floor_part = (floors_facing or "").split("/")
        rent = parse_yen(fields.get("rent"))
        deposit, key_money = _split_pair(fields.get("deposit_key"))
        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(fields.get("built")),
            floor_num=parse_floor(floor_part[0] if floor_part else None),
            total_floors=parse_total_floors(floors_facing),
            mgmt_fee_monthly=parse_fee(fields.get("mgmt_fee")),
            deposit_amount=parse_months_fee(deposit, rent),
            key_money_amount=parse_months_fee(key_money, rent),
            address=_icon_text(doc, "icon_address06"),
            walk_minutes=parse_walk_minutes(_icon_text(doc, "icon_train06")),
            type_specific_attrs={
                key: value
                for key, value in (("type_structure", type_structure), ("facing", facing))
                if value and value not in EMPTY_MARKERS
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


def _labelled_fields(node) -> dict[str, str]:
    """``th/td`` を項目名で引ける形にする。最初の出現だけを採る。"""
    fields: dict[str, str] = {}
    for th in node.cssselect("th"):
        label = " ".join(th.text_content().split())
        key = _LABELS.get(label) or _LABELS.get(label.replace(" ", ""))
        sibling = th.getnext()
        if key is None or key in fields or sibling is None or sibling.tag != "td":
            continue
        value = " ".join(sibling.text_content().split())
        if value:
            fields[key] = value
    return fields


def _card_title(card) -> str | None:
    """カードには建物名の見出しが無いので、詳細リンクの画像 alt から拾う。"""
    for image in card.cssselect("img[alt]"):
        alt = (image.get("alt") or "").strip()
        if alt and "画像" in alt and not alt.startswith("物件の"):
            return alt.replace("の画像", "")
    return None


def _icon_text(node, icon_class: str) -> str | None:
    """アイコンと対になっているセルの文字列を返す（住所・交通）。"""
    for icon in node.cssselect(f"span.{icon_class}"):
        cell = icon.getparent()
        sibling = cell.getnext() if cell is not None else None
        if sibling is not None:
            value = " ".join(sibling.text_content().split())
            if value:
                return value
    return None


def _split_pair(text: str | None) -> tuple[str | None, str | None]:
    """「1ヶ月 / 1ヶ月」を敷金・礼金へ分ける。"""
    if not text:
        return None, None
    parts = [part.strip() for part in re.split(r"[/／]", text)]
    first = parts[0] if parts else None
    second = parts[1] if len(parts) > 1 else None
    return first or None, second or None


def _split_layout_area(text: str | None) -> tuple[str | None, float | None]:
    """「1R / 9.72m²」を間取りと面積へ分ける。"""
    if not text:
        return None, None
    parts = [part.strip() for part in text.split("/")]
    layout = parts[0] if parts and parts[0] not in EMPTY_MARKERS else None
    return layout, parse_area_sqm(text)


def _facing(floors_facing: str | None) -> str | None:
    """「5階/地上5階建 / 南西」の方位部分を返す。"""
    if not floors_facing:
        return None
    parts = [part.strip() for part in floors_facing.split("/")]
    if len(parts) < 3:
        return None
    return parts[-1] if parts[-1] not in EMPTY_MARKERS else None


def _text(node, selector: str) -> str | None:
    """セレクタで最初に当たった要素のテキスト。"""
    if node is None:
        return None
    found = node.cssselect(selector)
    if not found:
        return None
    return " ".join(found[0].text_content().split()) or None


def _building_header(row):
    """圧縮行から建物ヘッダ（住所・駅・階建・築年月）へ遡る。"""
    node = row
    while node is not None:
        if "building-group-container" in (node.get("class") or ""):
            headers = node.cssselect("div.group-building-header")
            title = node.cssselect("div.group-building-title-bar")
            if headers:
                # タイトルは別ブロックなので、両方を見られるようコンテナを返す
                return node if title else headers[0]
        node = node.getparent()
    return None


def _strip_separator(value: str | None) -> str | None:
    """「/ 3,000円」の先頭区切りを落とす。"""
    if value is None:
        return None
    return value.strip().lstrip("/／").strip() or None


def _compact_deposit_key(value: str | None) -> tuple[str | None, str | None]:
    """「敷 無料 礼 1ヶ月」から敷金・礼金を取り出す。"""
    if not value:
        return None, None
    match = _SHIKIREI.search(value)
    if not match:
        return None, None
    return match.group(1).strip() or None, match.group(2).strip() or None
