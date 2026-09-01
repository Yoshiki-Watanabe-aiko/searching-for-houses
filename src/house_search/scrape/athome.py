"""アットホーム（賃貸）のサイトアダプタ。

一覧URLは ``/chintai/{エリアスラグ}/list/``。エリアスラグは都道府県ローマ字
（``tokyo``）か、市区まで含めたスラグ（``tokyo/adachi-city``）。

ATHOME 固有の注意点:

* **Playwright は要らない。** Phase 3 の実測で、一覧・詳細とも素のHTTPで
  サーバレンダリング済みのHTMLが返ることを確認した（→ ADR 0010）。
  v1 が go-rod を使っていたのは検索フォームを操作していたためで、
  URLを直接組み立てる v2 では不要
* **1建物 = 複数住戸**。住戸ごとに1掲載になる（SUUMO・HOME'S と同じ）
* 「部屋番号・階」の欄は掲載によって号室（``２０５``）だったり階（``2階``）
  だったりする。階として読めるときだけ所在階に使う
* 敷金・礼金が**月数表記**（``1ヶ月``）。円へ直すには賃料が要る
* robots.txt に ``User-agent: *`` のブロックが無い（名指しのクローラにだけ
  規則がある）ため、当サイトは全パスが許可されている
* ⚠ **ただし robots.txt とは別に、能動的なボット検知（パズル認証）がある。**
  3秒間隔で47件続けて取得したところ発動し、以後 200 のまま
  「認証にご協力ください」のページが返るようになった（→ 課題#20）。
  突破はしないので、取得間隔を6秒へ広げたうえで、
  認証ページを掴んだら**黙って0件にせずエラーにする**
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
    age_years_from_built,
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

SITE_CODE = "ATHOME"
BASE_URL = "https://www.athome.co.jp"
# 1ページあたりの建物数（実測。TATEMONONUM の既定が30）
PAGE_SIZE = 30
# 並び順: 7 = 賃料が安い順
SORT_RENT_ASC = "7"

_SOLD_MARKERS = (
    "この物件は掲載が終了",
    "掲載が終了しました",
    "お探しの物件は見つかりません",
    "ご指定の物件は見つかりません",
)

# ボット検知（パズル認証）のページに現れる文言。ページ冒頭だけを見る
_CHALLENGE_MARKERS = ("認証にご協力ください", "onProtectionInitialized", "reeseSkipExpirationCheck")
_CHALLENGE_SCAN_CHARS = 4000

# 詳細URL ``/chintai/1110265930/`` の数字が物件ID
_DETAIL_ID = re.compile(r"/chintai/(\d+)/")

# 賃料上限セレクト（PRICETO）のコード体系。3万円の kc101 から 0.5万円刻みで
# 連番になっている（kc109 = 7万円 を実測で確認）。2万・2.5万だけ別体系
# （kc141 / kc142）だが、上限として使う帯ではないので下限を3万円で丸める。
_PRICE_TO_BASE_CODE = 101
_PRICE_TO_BASE_MAN = 3.0
_PRICE_TO_STEP_MAN = 0.5
_PRICE_TO_MAX_MAN = 100.0

# 建物ヘッダの dl は dt のアイコンで種類が決まる
_HINT_ICONS = {
    "u-icon--map-mini": "address",
    "u-icon--train-mini": "station",
    "u-icon--home-mini": "building",
}

# 詳細ページ th のうち設備原文に載せたいラベル
_FEATURE_LABELS = (
    "バス・トイレ",
    "キッチン",
    "セキュリティー",
    "収納",
    "設備・サービス",
    "TV・通信",
    "冷暖房",
    "その他",
)

# 詳細ページ th のうち構造化して取り出したい項目（最初の出現だけ採る）
_DETAIL_LABELS = {
    "住所": "address",
    "交通": "station",
    "築年月": "built",
    "階建/階": "floors",
    "管理費等": "mgmt_fee",
    "敷金": "deposit",
    "礼金": "key_money",
    "賃料": "rent",
    "主要採光面": "facing",
    "建物構造・工法": "structure",
    "種目": "kind",
    "駐車場": "parking",
    "駐輪場": "bicycle",
    "現況": "status",
    "総戸数": "units",
}


class AthomeScraper:
    """アットホーム 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = False
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/chintai/{エリアスラグ}/list/`` を組み立てる。

        市区の値（``tokyo/adachi-city``）は都道府県スラグを含んでいるので
        そのままパスに嵌まる。都道府県単位のときはローマ字スラグを使う。
        """
        search = pattern.search  # type: ignore[attr-defined]
        params: dict[str, str] = {"SORT": SORT_RENT_ASC}
        if code := _price_to_code(search.price_max_hint):
            params["PRICETO"] = code

        urls: list[str] = []
        for area in areas:
            slug = area.value or PREFECTURE_ROMAJI.get(area.prefecture)
            if not slug:
                raise ValueError(f"ATHOME: 未知の都道府県です: {area.prefecture}")
            urls.append(f"{BASE_URL}/chintai/{slug}/list/?{urlencode(params)}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りは ``/list/page{N}/`` のパス形式。"""
        head, _, query = base_url.partition("?")
        paged = f"{head.rstrip('/')}/page{page}/"
        return f"{paged}?{query}" if query else paged

    def is_last_page(self, count: int) -> bool:
        """建物30件で1ページ。住戸数は建物数を下回らないので下限として使える。"""
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        _reject_bot_challenge(html_text)
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for building in doc.cssselect("div.p-property--building"):
            hints = _building_hints(building)
            title = _first_text(building, "h2.p-property__title--building")
            spec = hints.get("building")

            for room in building.cssselect("div.p-property__room--detailbox"):
                listing = _parse_room(
                    room,
                    title=title,
                    address=clean_address(hints.get("address")),
                    station_info=hints.get("station"),
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
        _reject_bot_challenge(html_text)
        doc = lxml_html.fromstring(html_text)
        fields, features = _detail_tables(doc)

        blocks = list(features)
        if derived := _derived_tokens(fields):
            blocks.append("、".join(derived))

        floors_text = fields.get("floors")
        rent = parse_yen(fields.get("rent"))
        return ScrapedDetail(
            raw_features_text="\n".join(blocks) or None,
            built_on=parse_built_on(fields.get("built")),
            floor_num=_floor_from_pair(floors_text),
            total_floors=parse_total_floors(floors_text),
            mgmt_fee_monthly=parse_fee(fields.get("mgmt_fee")),
            deposit_amount=parse_months_fee(fields.get("deposit"), rent),
            key_money_amount=parse_months_fee(fields.get("key_money"), rent),
            address=clean_address(fields.get("address")),
            walk_minutes=parse_walk_minutes(fields.get("station")),
            type_specific_attrs={
                key: value
                for key in ("structure", "facing", "kind", "status", "units")
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


def _price_to_code(price_max_hint: int | None) -> str | None:
    """賃料上限（円）を ``PRICETO`` のコードへ。範囲外なら None（＝上限を渡さない）。"""
    if not price_max_hint:
        return None
    man = price_max_hint / 10_000
    if man > _PRICE_TO_MAX_MAN:
        return None
    # 選択肢に無い額は「1つ上の帯」へ切り上げる（取りこぼしを作らないため）
    steps = max(0, -(-(man - _PRICE_TO_BASE_MAN) // _PRICE_TO_STEP_MAN))
    return f"kc{_PRICE_TO_BASE_CODE + int(steps)}"


def _reject_bot_challenge(html_text: str) -> None:
    """ボット検知のパズル認証ページなら黙って0件にせずエラーにする。

    ATHOME は短時間にリクエストを重ねると、200 のまま
    「認証にご協力ください」のパズル認証ページを返すようになる（実測）。
    そのまま解析すると掲載0件になり、**取れているつもりで気づけない**。
    scan は1ページの失敗ではサイト全体を止めないので、
    エラーとして記録させるのが正しい（→ 課題#20）。
    """
    head = html_text[:_CHALLENGE_SCAN_CHARS]
    if any(marker in head for marker in _CHALLENGE_MARKERS):
        raise ValueError("ATHOME: ボット検知の認証ページが返りました（→ 課題#20）")


def _building_age(spec: str | None) -> int | None:
    """``賃貸アパート 3階建 1989年12月 (築36年10ヶ月)`` から築年数を求める。

    完成前の建物は括弧の築年数が付かず年月だけになる（実測で「2026年12月」）。
    その場合は年月から数える（未来なら0年＝新築）。
    """
    if (years := parse_age_years(spec)) is not None:
        return years
    return age_years_from_built(spec)


def _first_text(node, selector: str) -> str | None:
    found = node.cssselect(selector)
    if not found:
        return None
    return " ".join(found[0].text_content().split()) or None


def _building_hints(building) -> dict[str, str]:
    """建物ヘッダの ``dl.p-property__information-hint`` を種類で引ける形にする。

    項目名がテキストではなく ``dt`` のアイコンのクラスで表されるため、
    ラベル文字列ではなくアイコンで振り分ける。
    """
    hints: dict[str, str] = {}
    for hint in building.cssselect("dl.p-property__information-hint"):
        key = None
        for icon in hint.cssselect("dt i"):
            for name in (icon.get("class") or "").split():
                if name in _HINT_ICONS:
                    key = _HINT_ICONS[name]
                    break
        value = _first_text(hint, "dd")
        if key and value and key not in hints:
            hints[key] = value
    return hints


def _parse_room(
    room,
    *,
    title: str | None,
    address: str | None,
    station_info: str | None,
    age_years: int | None,
    total_floors: int | None,
) -> ScrapedListing | None:
    anchors = [a.get("href") or "" for a in room.cssselect("a[href]")]
    external_id = next(
        (m.group(1) for href in anchors if (m := _DETAIL_ID.search(href))), None
    )
    if not external_id:
        return None

    price, mgmt_fee = _rent_and_mgmt(room)
    deposit, key_money = _deposit_and_key(room, price)
    layout, area_sqm = _layout_and_area(room)

    return ScrapedListing(
        site_code=SITE_CODE,
        external_id=external_id,
        url=f"{BASE_URL}/chintai/{external_id}/",
        title=title,
        price=price,
        mgmt_fee_monthly=mgmt_fee,
        deposit_amount=deposit,
        key_money_amount=key_money,
        area_sqm=area_sqm,
        layout=layout,
        floor_num=parse_floor(_first_text(room, "li.p-property__room-number")),
        total_floors=total_floors,
        age_years=age_years,
        address=address,
        station_info=station_info,
        walk_minutes=parse_walk_minutes(station_info),
        image_url=_room_image(room),
    )


def _rent_and_mgmt(room) -> tuple[int | None, int | None]:
    """``10.8万円`` と ``10,000円``（管理費）を分ける。

    賃料は ``<b>10.8</b>万円`` と要素が割れるためセル全体から読む。
    管理費は同じ ``li`` の ``span``。欄が ``-`` のときは0円として扱う。
    """
    cells = room.cssselect("li.p-property__room-rent")
    if not cells:
        return None, None
    price = parse_yen(" ".join(cells[0].text_content().split()))
    spans = cells[0].cssselect("span")
    mgmt = parse_fee(" ".join(spans[0].text_content().split())) if spans else None
    return price, mgmt


def _deposit_and_key(room, price: int | None) -> tuple[int | None, int | None]:
    """``1ヶ月`` / ``なし`` の2つ組を敷金・礼金へ分ける。"""
    cells = room.cssselect("li.p-property__room-keymoney")
    if not cells:
        return None, None
    texts = [" ".join(t.split()) for t in cells[0].itertext() if t.strip()]
    deposit = texts[0] if texts else None
    key_money = texts[1] if len(texts) > 1 else None
    return parse_months_fee(deposit, price), parse_months_fee(key_money, price)


def _layout_and_area(room) -> tuple[str | None, float | None]:
    """``1LDK`` と ``46.40m²`` を分ける。"""
    cells = room.cssselect("li.p-property__room-floorplan")
    if not cells:
        return None, None
    cell = cells[0]
    return _first_text(cell, "div.p-property__floor"), parse_area_sqm(cell.text_content())


def _room_image(room) -> str | None:
    for image in room.cssselect("li.p-property__room-image img, div.madori_img img"):
        value = image.get("src")
        if value and not value.startswith("data:"):
            return urljoin(BASE_URL, value)
    return None


def _floor_from_pair(value: str | None) -> int | None:
    """``3階建 / 2階`` の後半（所在階）を返す。"""
    if not value:
        return None
    parts = value.split("/")
    return parse_floor(parts[-1] if len(parts) > 1 else None)


def _detail_tables(doc) -> tuple[dict[str, str], list[str]]:
    """詳細ページの ``th/td`` を構造化項目と設備原文へ振り分ける。

    同じラベルが問い合わせフォームなどで再出現するため**最初の出現だけ**採る。
    """
    fields: dict[str, str] = {}
    features: list[str] = []
    seen_features: set[str] = set()
    for th in doc.cssselect("th"):
        label = "".join(th.text_content().split())
        sibling = th.getnext()
        if not label or sibling is None or sibling.tag != "td":
            continue
        value = " ".join(sibling.text_content().split())
        if not value or value in EMPTY_MARKERS:
            continue
        if label in _FEATURE_LABELS:
            if label not in seen_features:
                seen_features.add(label)
                features.append(value)
            continue
        key = _DETAIL_LABELS.get(label)
        if key and key not in fields:
            fields[key] = value
    return fields, features


def _derived_tokens(fields: dict[str, str]) -> list[str]:
    """型付きの欄から辞書が照合できる語へ寄せる。

    「駐車場: 有」のような ``有/無`` 欄は語彙が無く辞書に当たらないため、
    値の意味づけ（``無`` は無し 等）をアダプタ側で吸収して派生トークンにする。
    """
    derived: list[str] = []
    if structure := fields.get("structure"):
        derived.append(structure)
    if (facing := fields.get("facing")) and facing not in EMPTY_MARKERS:
        derived.append(facing if facing.endswith("向き") else f"{facing}向き")
    for key, token in (("parking", "駐車場あり"), ("bicycle", "駐輪場あり")):
        value = fields.get(key)
        if value and not value.startswith("無") and value not in EMPTY_MARKERS:
            derived.append(token)
    return derived
