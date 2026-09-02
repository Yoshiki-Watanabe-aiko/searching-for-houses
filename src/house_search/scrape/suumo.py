"""SUUMO（賃貸）のサイトアダプタ。

URL構成・DOM構造は v1（Go実装）で実績のあるものを引き継ぎ、v2 の方針に合わせて
**設備条件のクエリパラメータ（tc=）を一切送らない**ようにしてある（→ ADR 0003）。
サイトへ渡すのはエリア（ar/ta）・種別（bs=040 が賃貸）・価格上限（ct）だけ。

v1 が取りこぼしていた管理費・敷金・礼金・所在階も一覧から取る。
``rent_total``（賃料＋管理費）が MUST の1段目判定に要るため、管理費は必須。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlencode, urljoin

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.base import (
    EMPTY_MARKERS,
    ScrapedDetail,
    ScrapedListing,
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

SITE_CODE = "SUUMO"
BASE_URL = "https://suumo.jp"
# 1ページあたりの掲載数。これ未満しか返らなければ最終ページ。
PAGE_SIZE = 30
# bs=040 が賃貸。売買は Phase 6 で別コードを使う。
RENTAL_BS = "040"

# 都道府県名 → SUUMO の地方コード（ar）
PREFECTURE_REGION: dict[str, str] = {
    "北海道": "010",
    "青森県": "020", "岩手県": "020", "宮城県": "020",
    "秋田県": "020", "山形県": "020", "福島県": "020",
    "茨城県": "030", "栃木県": "030", "群馬県": "030", "埼玉県": "030",
    "千葉県": "030", "東京都": "030", "神奈川県": "030",
    "新潟県": "040", "富山県": "040", "石川県": "040",
    "福井県": "040", "山梨県": "040", "長野県": "040",
    "岐阜県": "050", "静岡県": "050", "愛知県": "050", "三重県": "050",
    "滋賀県": "060", "京都府": "060", "大阪府": "060",
    "兵庫県": "060", "奈良県": "060", "和歌山県": "060",
    "鳥取県": "070", "島根県": "070", "岡山県": "070", "広島県": "070", "山口県": "070",
    "徳島県": "080", "香川県": "080", "愛媛県": "080", "高知県": "080",
    "福岡県": "090", "佐賀県": "090", "長崎県": "090", "熊本県": "090",
    "大分県": "090", "宮崎県": "090", "鹿児島県": "090", "沖縄県": "090",
}

# 都道府県名 → JIS 2桁コード（ta）
PREFECTURE_JIS: dict[str, str] = {
    "北海道": "01", "青森県": "02", "岩手県": "03", "宮城県": "04", "秋田県": "05",
    "山形県": "06", "福島県": "07", "茨城県": "08", "栃木県": "09", "群馬県": "10",
    "埼玉県": "11", "千葉県": "12", "東京都": "13", "神奈川県": "14", "新潟県": "15",
    "富山県": "16", "石川県": "17", "福井県": "18", "山梨県": "19", "長野県": "20",
    "岐阜県": "21", "静岡県": "22", "愛知県": "23", "三重県": "24", "滋賀県": "25",
    "京都府": "26", "大阪府": "27", "兵庫県": "28", "奈良県": "29", "和歌山県": "30",
    "鳥取県": "31", "島根県": "32", "岡山県": "33", "広島県": "34", "山口県": "35",
    "徳島県": "36", "香川県": "37", "愛媛県": "38", "高知県": "39", "福岡県": "40",
    "佐賀県": "41", "長崎県": "42", "熊本県": "43", "大分県": "44", "宮崎県": "45",
    "鹿児島県": "46", "沖縄県": "47",
}

# 詳細URLに付く物件ID。一覧のチェックボックス value と同じ値になる
_BC_PARAM = re.compile(r"[?&]bc=(\d+)")
# 成約・掲載終了ページに出る文言
_SOLD_MARKERS = ("この物件は掲載が終了", "掲載を終了", "ご覧いただけません", "お探しの物件は")

# 詳細ページの th ラベル → 取り出したい項目
_DETAIL_LABELS = {
    "所在地": "address",
    "駅徒歩": "station",
    "構造": "structure",
    "階建": "floors",
    "階": "floor",
    "築年月": "built",
    "向き": "facing",
    "駐車場": "parking",
    "入居": "movein",
    "契約期間": "contract",
    "条件": "conditions",
    "建物種別": "building_type",
}


class SuumoScraper:
    """SUUMO 賃貸の取得と解析。"""

    site_code = SITE_CODE
    # 都道府県だけでも一覧が返る（市区指定は任意）
    requires_city = False
    # 市区は検索URLでは JIS5桁（sc=13101）を使う。m_city_site_values の
    # ``sc_chiyoda`` は SEO パス用の別表現なので検索URLには使わない
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    ignore_robots = False
    # MUST の数値条件と間取りを検索URLへ載せられる（→ ADR 0015）。
    # 実測でキーと選択肢を確定済み（data/site_search_params.yaml）
    supports_site_filters = True

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """検索パターンと対象エリアから一覧ページのURL（1ページ目）を組み立てる。"""
        search = pattern.search  # type: ignore[attr-defined]
        params = {"sort": "2"}  # 新着順
        if search.price_max_hint:
            # ct は万円単位だが**選択肢が決まっており、端数を渡すと
            # HTTP 200 のまま掲載0件になる**（実測: ct=15.6 で0件 /
            # ct=16.0 で100件 / ct無しで160件）。エラーにならないので
            # 「取れているつもり」で気づけない。整数の万円へ切り上げる
            params["ct"] = f"{-(-search.price_max_hint // 10_000):.1f}"

        urls: list[str] = []
        for area in areas:
            region = PREFECTURE_REGION.get(area.prefecture)
            jis = PREFECTURE_JIS.get(area.prefecture)
            if not region or not jis:
                raise ValueError(f"SUUMO: 未知の都道府県です: {area.prefecture}")
            query: dict[str, str] = {"ar": region, "bs": RENTAL_BS, "ta": jis, **params}
            if area.value:
                query["sc"] = area.value
            urls.append(f"{BASE_URL}/jj/chintai/ichiran/FR301FC001/?{urlencode(query)}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """一覧URLへページ番号を付ける。"""
        return f"{base_url}&pc={PAGE_SIZE}&pn={page}"

    def is_last_page(self, count: int) -> bool:
        """1ページに満たない件数しか返らなければ最終ページ。"""
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。

        SUUMO は「1建物 = 複数住戸」の入れ子構造で、住戸ごとに1掲載になる。
        建物側（住所・駅・築年）を読んでから住戸行を回す。
        """
        doc = lxml_html.fromstring(html_text)
        _reject_error_page(doc)
        listings: list[ScrapedListing] = []

        for building in doc.cssselect("div.cassetteitem"):
            title = _first_text(building, ".cassetteitem_content-title")
            address = _first_text(building, ".cassetteitem_detail-col1")
            stations = [
                node.text_content().strip()
                for node in building.cssselect(".cassetteitem_detail-text")
            ]
            station_info = " / ".join(s for s in stations if s) or None
            col3 = _first_text(building, ".cassetteitem_detail-col3")
            age_years = parse_age_years(col3)
            total_floors = parse_total_floors(col3)
            image_url = _building_image(building)

            for room in building.cssselect(".js-cassette_link"):
                listing = self._parse_room(
                    room,
                    title=title,
                    address=address,
                    station_info=station_info,
                    age_years=age_years,
                    total_floors=total_floors,
                    image_url=image_url,
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
        image_url: str | None,
    ) -> ScrapedListing | None:
        anchors = room.cssselect("a.js-cassette_link_href")
        if not anchors:
            return None
        href = anchors[0].get("href") or ""
        if not href:
            return None
        url = urljoin(BASE_URL, href)

        external_id = _external_id(room, url)
        if not external_id:
            return None

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=url,
            title=title,
            price=parse_yen(_first_text(room, ".cassetteitem_price--rent")),
            mgmt_fee_monthly=parse_fee(_first_text(room, ".cassetteitem_price--administration")),
            deposit_amount=parse_fee(_first_text(room, ".cassetteitem_price--deposit")),
            key_money_amount=parse_fee(_first_text(room, ".cassetteitem_price--gratuity")),
            area_sqm=parse_area_sqm(_first_text(room, ".cassetteitem_menseki")),
            layout=_first_text(room, ".cassetteitem_madori"),
            floor_num=_room_floor(room),
            total_floors=total_floors,
            age_years=age_years,
            address=address,
            station_info=station_info,
            walk_minutes=parse_walk_minutes(station_info),
            image_url=image_url,
        )

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        fields = _detail_fields(doc)
        equipment = _equipment_block(doc)

        # 設備リストに現れない構造化項目を、辞書が照合できる形へ正規化して足す。
        # 欄の意味づけ（「駐車場: -」＝無し、「入居: 即」＝即入居可）はサイト固有なので
        # ここで吸収し、辞書側は語彙の対応だけを持つ。
        derived_tokens: list[str] = []
        if structure := fields.get("structure"):
            derived_tokens.append(structure)
        facing = fields.get("facing")
        if facing and facing not in EMPTY_MARKERS:
            derived_tokens.append(facing if facing.endswith("向き") else f"{facing}向き")
        parking = fields.get("parking")
        if parking and parking not in EMPTY_MARKERS:
            derived_tokens.append("駐車場あり")
            if "敷地内" in parking:
                derived_tokens.append("敷地内駐車場")
        if (movein := fields.get("movein")) and movein.startswith("即"):
            derived_tokens.append("即入居可")
        # 建物種別（アパート/マンション等）は対応する条件が無く、未知表記を
        # 汚すだけなので raw_features_text には載せない（type_specific_attrs には残す）
        for key in ("contract", "conditions"):
            value = fields.get(key)
            if value and value not in EMPTY_MARKERS:
                derived_tokens.append(value)

        parts = [part for part in (equipment, "、".join(derived_tokens)) if part]
        raw_features_text = "\n".join(parts) or None

        floors_text = fields.get("floors")
        return ScrapedDetail(
            raw_features_text=raw_features_text,
            built_on=parse_built_on(fields.get("built")),
            floor_num=parse_floor(fields.get("floor")),
            total_floors=parse_total_floors(floors_text),
            address=fields.get("address"),
            walk_minutes=parse_walk_minutes(fields.get("station")),
            type_specific_attrs={
                key: value
                for key, value in fields.items()
                if key in ("structure", "facing", "building_type", "contract")
                and value
                and value not in EMPTY_MARKERS
            },
        )

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """詳細URLが成約/掲載終了ページに変わっていないかを見る。"""
        try:
            response = fetcher.get(url)
        except Exception:
            # 取得できない＝掲載終了とは限らないため、判定を保留する
            return False
        if response.status_code == 404:
            return True
        return any(marker in response.text for marker in _SOLD_MARKERS)


def _first_text(node, selector: str) -> str | None:
    found = node.cssselect(selector)
    if not found:
        return None
    return found[0].text_content().strip() or None


def _external_id(room, url: str) -> str | None:
    """住戸の物件ID。チェックボックスの value を優先し、無ければURLの bc= から。"""
    for checkbox in room.cssselect("input[name='bc']"):
        if value := (checkbox.get("value") or "").strip():
            return value
    match = _BC_PARAM.search(url)
    return match.group(1) if match else None


def _room_floor(room) -> int | None:
    """住戸の所在階。クラス名が無い td なので「N階」に一致するセルを拾う。"""
    for cell in room.cssselect("td"):
        text = cell.text_content().strip()
        if text and len(text) <= 12 and (floor := parse_floor(text)) is not None:
            return floor
    return None


def _building_image(building) -> str | None:
    for image in building.cssselect(".cassetteitem_object img"):
        for attribute in ("data-src", "rel", "src"):
            value = image.get(attribute)
            if value and not value.startswith("data:"):
                return value
    return None


def _equipment_block(doc) -> str | None:
    """設備タグの列。``ul.inline_list`` のうち区切り数が最も多いものを採る。

    ページ内には契約期間・備考など別用途の ``ul.inline_list`` も混ざるため、
    位置ではなく「読点区切りの語が最も多い」ことで設備ブロックを選ぶ。
    """
    best: tuple[int, str] | None = None
    for node in doc.cssselect("ul.inline_list"):
        text = " ".join(node.text_content().split())
        count = text.count("、")
        # 設備が2件だけ（読点1個）の掲載もあるため下限は1にする。
        # 備考・初期費用の欄は区切りが「/」「／」なので読点では競合しない
        if count >= 1 and (best is None or count > best[0]):
            best = (count, text)
    return best[1] if best else None


def _detail_fields(doc) -> dict[str, str]:
    """詳細ページの th/td を項目名で引ける形にする。"""
    fields: dict[str, str] = {}
    for th in doc.cssselect("th"):
        label = "".join(th.text_content().split())
        key = _DETAIL_LABELS.get(label)
        if key is None or key in fields:
            continue
        sibling = th.getnext()
        if sibling is not None and sibling.tag == "td":
            value = " ".join(sibling.text_content().split())
            if value:
                fields[key] = value
    return fields


def _reject_error_page(doc) -> None:
    """SUUMO のエラーページを掴んだら例外にする。

    ⚠ **絞り込みパラメータに選択肢外の値を渡すと、SUUMO は HTTP 200 のまま
    エラーページを返す**（実測 2026-09-03: ``et=12`` で title が
    「エラー｜SUUMO(スーモ)」の 11KB のページ）。そのまま解析すると
    **掲載0件になるだけで例外にならない**ので、「取れているつもり」で気づけない
    （→ 課題#29）。ATHOME の認証ページと同じ扱いで例外にする。

    ``scan`` は1ページの失敗でサイト全体を止めないため、エラーとして記録され
    実行サマリに出る。
    """
    titles = doc.cssselect("title")
    if titles and "エラー" in (titles[0].text_content() or ""):
        raise ValueError(
            "SUUMO がエラーページを返しました（絞り込みパラメータに"
            "選択肢外の値を渡した可能性があります）"
        )
