"""LIFULL HOME'S（賃貸）のサイトアダプタ。

サイトへ渡すのはエリア・種別・価格上限だけで、設備条件（``cond[mcf]``）は
一切送らない（→ ADR 0003）。

HOME'S 固有の注意点:

* 敷金・礼金が**月数表記**（「1ヶ月」）。円へ直すには賃料が要る
* 詳細ページの人気条件アイコンは**非該当の条件も並ぶ**。
  ``<span class="sr-only">(該当)</span>`` を見て該当だけを拾わないと、
  「オートロック(非該当)」から辞書が「オートロック」を拾ってしまう
* レイアウトが Tailwind のユーティリティクラスで組まれており class 名に意味が無い。
  設備ブロックは**ラベル文字列**（「設備・サービス」など）で引き当てる
* **自己申告の User-Agent を 403 で拒否する。** robots.txt は当該パスを
  ``User-agent: *`` に許可しているのに、既定のUAだと 403 が返る（実測）。
  このサイトにだけブラウザ相当のUAを使う。取得間隔と robots.txt の尊重は変えない
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlencode, urljoin

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
from house_search.scrape.fetch import BROWSER_USER_AGENT, SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "HOMES"
BASE_URL = "https://www.homes.co.jp"
# 1ページあたりの建物数（実測。住戸数はこれより多い）
PAGE_SIZE = 30

_SOLD_MARKERS = ("掲載が終了", "掲載を終了", "お探しの物件は", "ページが見つかりません")

# 「8年 / 8階建」。HOME'S は築年数に「築」を付けないため、
# 「築N年」を要求する共通の parse_age_years では読めない
_AGE_PLAIN = re.compile(r"(\d+)\s*年")

# 設備ブロックのラベル。値は読点区切りの span 群として並ぶ
_FEATURE_LABELS = ("入居条件", "キッチン/バス・トイレ", "設備・サービス", "その他")

# 詳細ページ dt/dd のうち取り出したい項目。口コミも dt/dd を使うため
# ラベルの白名簿で絞り、最初の出現だけを採る
_DETAIL_LABELS = {
    "管理費等": "mgmt_fee",
    "敷金/礼金": "deposit_key",
    "交通": "station",
    "所在地": "address",
    "築年月": "built",
    "所在階/階数": "floors",
    "主要採光面": "facing",
    "現況": "status",
    "入居可能時期": "movein",
    "建物構造": "structure",
}


class HomesScraper:
    """LIFULL HOME'S 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = False
    city_value_source = CITY_VALUE_MAPPING
    # 既定の自己申告UAだと robots.txt で許可されているパスでも 403 になる（実測）
    user_agent = BROWSER_USER_AGENT
    ignore_robots = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/chintai/{エリアスラグ}/list/`` を組み立てる。

        市区の値（``tokyo/chiyoda-city``）は都道府県スラグを含んでいるので
        そのままパスに嵌まる。都道府県単位のときはローマ字スラグを使う。
        """
        search = pattern.search  # type: ignore[attr-defined]
        params: dict[str, str] = {"cond[sortby]": "newdate"}
        if search.price_max_hint:
            # 万円単位・小数1桁。セレクトの選択肢が 0.5 刻みのため丸める
            params["cond[monthmoneyroomh]"] = f"{search.price_max_hint / 10_000:.1f}"

        urls: list[str] = []
        for area in areas:
            slug = area.value or PREFECTURE_ROMAJI.get(area.prefecture)
            if not slug:
                raise ValueError(f"HOMES: 未知の都道府県です: {area.prefecture}")
            urls.append(f"{BASE_URL}/chintai/{slug}/list/?{urlencode(params)}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        return f"{base_url}&page={page}"

    def is_last_page(self, count: int) -> bool:
        """建物30件で1ページ。住戸数で判定できないため建物換算はしない。

        ``parse_list`` が返すのは住戸なので、住戸数が建物数を下回ることはない。
        1ページ分の建物が埋まっていなければ最終ページとみなす。
        """
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for building in doc.cssselect("div.moduleInner.prg-building"):
            spec = _building_spec(building)
            title = _first_text(building, ".bukkenName")
            stations = [
                " ".join(node.text_content().split())
                for node in building.cssselect(".prg-stationText")
            ]
            station_info = " / ".join(s for s in stations if s) or spec.get("交通")
            age_floors = spec.get("築年数/階数")

            for room in building.cssselect("tr.prg-room"):
                listing = self._parse_room(
                    room,
                    title=title,
                    address=spec.get("所在地"),
                    station_info=station_info,
                    age_years=_building_age(age_floors),
                    total_floors=parse_total_floors(age_floors),
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
        external_id = (room.get("data-kykey") or "").strip()
        href = room.get("data-href") or ""
        if not href:
            anchors = room.cssselect("a.prg-detailAnchor")
            href = anchors[0].get("href") if anchors else ""
        if not external_id or not href:
            return None

        price, mgmt_fee, deposit, key_money = _room_money(room)
        layout, area_sqm = _room_layout(room)

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=urljoin(BASE_URL, href),
            title=title,
            price=price,
            mgmt_fee_monthly=mgmt_fee,
            deposit_amount=deposit,
            key_money_amount=key_money,
            area_sqm=area_sqm,
            layout=layout,
            floor_num=parse_floor(_first_text(room, "li.roomKaisuu")),
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
        fields = _detail_fields(doc)

        blocks = [
            value
            for value in (_feature_blocks(doc), "、".join(_matched_conditions(doc)))
            if value
        ]
        # 構造化フィールドは辞書が照合できる語へ寄せてから足す。
        derived: list[str] = []
        if structure := fields.get("structure"):
            derived.append(structure)
        facing = fields.get("facing")
        if facing and facing not in EMPTY_MARKERS:
            derived.append(facing if facing.endswith("向き") else f"{facing}向き")
        if (movein := fields.get("movein")) and movein.startswith("即"):
            derived.append("即入居可")
        if derived:
            blocks.append("、".join(derived))

        floors_text = fields.get("floors")
        rent = parse_yen(fields.get("rent"))
        deposit_key = fields.get("deposit_key")
        deposit, key_money = _split_pair(deposit_key)

        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(fields.get("built")),
            floor_num=parse_floor(floors_text),
            total_floors=parse_total_floors(floors_text),
            mgmt_fee_monthly=parse_fee(fields.get("mgmt_fee")),
            deposit_amount=parse_months_fee(deposit, rent),
            key_money_amount=parse_months_fee(key_money, rent),
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
            # 取得できない＝掲載終了とは限らないため判定を保留する
            return False
        if response.status_code == 404:
            return True
        return any(marker in response.text for marker in _SOLD_MARKERS)


def _building_age(text: str | None) -> int | None:
    """「8年 / 8階建」から築年数を返す。「新築」は0年として扱う。"""
    if not text:
        return None
    if "新築" in text:
        return 0
    match = _AGE_PLAIN.search(text)
    return int(match.group(1)) if match else None


def _first_text(node, selector: str) -> str | None:
    found = node.cssselect(selector)
    if not found:
        return None
    return " ".join(found[0].text_content().split()) or None


def _building_spec(building) -> dict[str, str]:
    """建物ヘッダの ``th/td`` を項目名で引ける形にする。"""
    spec: dict[str, str] = {}
    for th in building.cssselect("div.bukkenSpec th"):
        label = "".join(th.text_content().split())
        sibling = th.getnext()
        if label and label not in spec and sibling is not None and sibling.tag == "td":
            value = " ".join(sibling.text_content().split())
            if value:
                spec[label] = value
    return spec


def _room_money(room) -> tuple[int | None, int | None, int | None, int | None]:
    """``td.price`` から賃料・管理費・敷金・礼金を取り出す。

    HTML は ``<span class="priceLabel">8.4万円</span>/2,000円<br>無/無/-/-`` の形。
    管理費は priceLabel の tail、敷金/礼金は ``<br>`` の tail に入る。
    """
    cells = room.cssselect("td.price")
    if not cells:
        return None, None, None, None
    cell = cells[0]

    labels = cell.cssselect("span.priceLabel")
    if labels:
        price = parse_yen(labels[0].text_content())
        mgmt_fee = parse_fee(_strip_separator(labels[0].tail))
    else:
        price = parse_yen(cell.text_content())
        mgmt_fee = None

    breaks = cell.cssselect("br")
    deposit_line = breaks[0].tail if breaks else None
    deposit, key_money = _split_pair(deposit_line)
    return price, mgmt_fee, parse_months_fee(deposit, price), parse_months_fee(key_money, price)


def _strip_separator(text: str | None) -> str | None:
    """「/ 4,000円」の先頭区切りを落とす。"""
    if text is None:
        return None
    return text.strip().lstrip("/／").strip()


def _split_pair(text: str | None) -> tuple[str | None, str | None]:
    """「1ヶ月/1ヶ月/-/-」から先頭2つ（敷金・礼金）を返す。"""
    if not text:
        return None, None
    parts = [part.strip() for part in re.split(r"[/／]", text)]
    first = parts[0] if parts else None
    second = parts[1] if len(parts) > 1 else None
    return first or None, second or None


def _room_layout(room) -> tuple[str | None, float | None]:
    """``td.layout`` の ``1LDK<br>48.29m²`` を間取りと面積へ分ける。"""
    cells = room.cssselect("td.layout")
    if not cells:
        return None, None
    cell = cells[0]
    texts = [" ".join(t.split()) for t in cell.itertext() if t.strip()]
    layout = texts[0] if texts else None
    return layout, parse_area_sqm(cell.text_content())


def _room_image(room) -> str | None:
    for image in room.cssselect("td.floarPlan img"):
        for attribute in ("data-original", "src"):
            value = image.get(attribute)
            if value and not value.startswith(("data:", "/search/assets")):
                return value
    return None


def _detail_fields(doc) -> dict[str, str]:
    """詳細ページの ``dt/dd`` を白名簿で拾う。

    口コミも ``dt/dd`` を使うため、ラベルを限定したうえで最初の出現だけを採る。
    賃料だけは ``dt`` が「賃料」単独で口コミと衝突しないので別扱いにしない。
    """
    fields: dict[str, str] = {}
    for dt in doc.cssselect("dt"):
        label = "".join(dt.text_content().split())
        key = _DETAIL_LABELS.get(label)
        if label == "賃料" and "rent" not in fields:
            key = "rent"
        if key is None or key in fields:
            continue
        sibling = dt.getnext()
        if sibling is not None and sibling.tag == "dd":
            value = " ".join(sibling.text_content().split())
            if value:
                fields[key] = value
    return fields


def _feature_blocks(doc) -> str:
    """設備ブロック（ラベル + 読点区切りの span 群）を原文として集める。"""
    parts: list[str] = []
    for li in doc.cssselect("li"):
        children = [child for child in li if child.tag in ("p", "div")]
        if len(children) < 2 or children[0].tag != "p" or children[1].tag != "div":
            continue
        label = " ".join(children[0].text_content().split())
        if label not in _FEATURE_LABELS:
            continue
        spans = children[1].cssselect("span")
        value = "".join(" ".join(span.text_content().split()) for span in spans)
        if value:
            parts.append(value)
    return "\n".join(parts)


def _matched_conditions(doc) -> list[str]:
    """人気条件アイコンのうち**該当するものだけ**を返す。

    非該当も同じマークアップで並ぶため、``sr-only`` の「(該当)」で選別する。
    ここを省くと辞書が非該当の条件まで拾ってしまう。
    """
    matched: list[str] = []
    for li in doc.cssselect("li"):
        flags = li.cssselect("span.sr-only")
        if not flags or "(該当)" not in flags[0].text_content():
            continue
        names = li.cssselect("span.tracking-tight")
        if names:
            name = " ".join(names[0].text_content().split())
            if name and name not in matched:
                matched.append(name)
    return matched
