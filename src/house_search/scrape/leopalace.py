"""レオパレス21（賃貸）のサイトアダプタ。

一覧URLは ``/properties/chintai/area/{都道府県スラグ}/{市区スラグ}-{JIS5桁}``。
実測は詳細設計書 §11（2026-09-04・課題#37 のチェックリスト10項目）。

レオパレス21 固有の注意点:

* **自社物件のみ**なので既存11サイトと在庫が重ならない見込みが高い。
  採用可否は ``dedup-stats`` のユニーク率で判断する（賃貸EX と同じ手順 → 課題#5）
* **市区スラグはサイトマップ1本で全国1,000件採れる**（``collect_leopalace_slugs.py``）。
  ⚠ 末尾に JIS5桁が埋まっているので**部分文字列一致を使わずに**同定できる（→ ADR 0014）。
  都道府県スラグは47件すべて ``PREFECTURE_ROMAJI`` と一致するのでアダプタ側で導出する
* **一覧は「建物カードの中に住戸が並ぶ」**（ATHOME と同じ形）。UR と違い建物と住戸が
  同じHTMLに載るので任意フック（→ ADR 0019）は要らない
* ⚠ **CSS Modules のハッシュ名**（``ApartmentItem_apartment-item__cWhGE``）。EHEYA と
  同じ不安定さだが ``__`` より前は安定するので接頭辞で拾う。⚠ **より安定なのは
  住戸URLの形**なので、住戸の同定はリンクで行う
* ⚠ **駅名が鉤括弧つき**（``つくばエクスプレス「八潮駅」徒歩28分``）。
  囲みを外さないと駅が1件も同定できない（→ 課題#41 で ``matcher`` を修正済み）
* ⚠⚠ **交通欄の約1割がバス経由**（``京葉線「蘇我駅」バス6分 生実学校入口下車 徒歩7分``）。
  その「徒歩7分」は**バス停からの徒歩**なので駅徒歩にすると ``walk_minutes_max`` を
  不当に通過する（UR と同じ罠 → 課題#37）。バスを含む行からは徒歩分を採らない。
  ⚠ **駅名は拾ってよい**（通勤時間の算出に使えるし、``matcher`` がバス停名を消す）
* ⚠ **総件数の表示をページ判定に使わない。** 「209件」（銚子市）に対し
  1ページ4〜8住戸×11ページで積が合わない（満室を含む総戸数の可能性が高いが未確定）。
  最終ページは**そのページの住戸が0件**で判定する（``?page=12`` は HTTP 200・0件）
* ⚠ **掲載終了が 404 にならない。** 存在しないURLも HTTP 200 で検索トップ相当を返すので、
  タイトルで判別する
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlencode

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_MAPPING, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    age_years_from_built,
    clean_address,
    parse_area_sqm,
    parse_built_on,
    parse_fee,
    parse_months_fee,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "LEOPALACE"
BASE_URL = "https://www.leopalace21.com"

# 賃料上限 rentTo の選択肢（円）。実測で一覧の検索フォームから採った。
# ⚠ **等間隔ではない**（2〜10万は5,000円・10〜20万は1万円・以降 25/30/50万）。
RENT_TO_CHOICES: tuple[int, ...] = (
    20_000, 25_000, 30_000, 35_000, 40_000, 45_000, 50_000, 55_000, 60_000,
    65_000, 70_000, 75_000, 80_000, 85_000, 90_000, 95_000, 100_000,
    110_000, 120_000, 130_000, 140_000, 150_000, 160_000, 170_000, 180_000,
    190_000, 200_000, 250_000, 300_000, 500_000,
)

# 掲載が無いURLで返る検索トップのタイトル。⚠ 404 にならないのでこれで判別する。
_NOT_FOUND_TITLE = "賃貸マンション・アパート・マンスリーマンション検索"
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)

# 住戸URL /properties/chintai/{県}/{市区}-{JIS}/{建物スラグ}-{建物ID}/{号室}
_ROOM_URL = re.compile(r"/properties/chintai/[a-z]+/[a-z0-9-]+-\d{5}/[a-z0-9-]+-(\d+)/([^/?#]+)$")

# 建物の caption は常に3件（住所 / 交通 / 構造・築年）。実測148建物で例外なし。
_CAPTION_ADDRESS, _CAPTION_STATION, _CAPTION_BUILDING = 0, 1, 2

# バス経由の行。ここに当たったら徒歩分を駅徒歩として採らない。
_BUS = re.compile(r"バス\s*\d+\s*分")

# 設備原文に載せてよいセクションの見出し。⚠ **ここを絞らないと**「お部屋の詳細」
# 「諸費用」「お問い合わせ先」まで同じ ``TitleTextItem`` で組まれているため、
# 火災保険料・免許番号・電話番号が設備として辞書照合に載る。
_FEATURE_SECTIONS = frozenset({"設備", "おすすめポイント"})


def _texts(node, prefix: str, *, items: bool = False) -> list[str]:
    """CSS Modules の接頭辞で拾ったノードの文字列（重複を除く）。

    ⚠ レスポンシブ対応でPC用とSP用の同じ ``li`` が並ぶので**必ず重複を除く**。
    ``items=True`` なら配下の ``li`` を1件ずつ返す（建物の caption はこの形で、
    容器の ``text_content()`` を読むと住所・交通・築年が1本に繋がってしまう）。
    """
    selector = f".//*[starts-with(@class,'{prefix}')]"
    if items:
        selector += "//li"
    values = [
        " ".join(el.text_content().split()).rstrip("｜") for el in node.xpath(selector)
    ]
    return list(dict.fromkeys(v for v in values if v))


def rent_to_value(price_max_hint: int | None) -> str | None:
    """賃料上限を選択肢へ**切り上げ**る。選択肢外の値は0件事故のもと（→ 課題#29）。"""
    if not price_max_hint:
        return None
    for choice in RENT_TO_CHOICES:
        if choice >= price_max_hint:
            return str(choice)
    return None  # 上限を超える希望額はサイト側へ渡さない（ローカル判定に任せる）


def walk_minutes_from_access(access: str | None) -> int | None:
    """交通欄から**駅徒歩**（分）を取る。

    ⚠⚠ **バス経由の行から徒歩分を採ってはいけない。**
    ``京葉線「蘇我駅」バス6分 生実学校入口下車 徒歩7分`` の徒歩7分は
    バス停からの徒歩で、他サイトの ``walk_minutes``（駅徒歩）とは意味が違う。
    実測で161建物中18件（11%）がバス経由だった。
    """
    if not access or _BUS.search(access):
        return None
    return parse_walk_minutes(access)


def _common_fee(info: str) -> int | None:
    """「（共益費 6,500円）」を円へ。欄そのものが無いときだけ None。"""
    matched = re.search(r"共益費[^\d]*([\d,]+\s*円|無料|なし)", info)
    return parse_fee(matched.group(1)) if matched else None


def _named_fee(info: str, label: str, rent: int | None) -> int | None:
    """「敷金 不要」「礼金 1ヶ月」を円へ。月数表記が混ざるので rent が要る。"""
    matched = re.search(rf"{label}\s*([^/｜|]+)", info)
    if not matched:
        return None
    value = matched.group(1).strip()
    if value.startswith(("不要", "なし", "無")):
        return 0
    return parse_months_fee(value, rent)


def _detail_features(doc) -> list[str]:
    """設備のタグ列を取り出す。

    2か所から集める。

    - ``TitleTextList_item-list__`` … 設備表。``<p>ラベル</p><div>値、値</div>`` の形で、
      **ラベル（「バス・トイレ」）は落として値だけ**を採る。ラベルをそのまま載せると
      「バス・トイレ別」との区別が付かない語が辞書に当たってしまう
    - ``PointList_point-list__`` … おすすめポイント。``角部屋``・``モニター付インターホン``
      のように設備表に出ない条件が入る

    ⚠ **PC用とSP用で同じ内容が2組ある**ので重複を除く。

    ⚠⚠ **セクションの見出しで絞り込む。** ``TitleTextItem`` は「お部屋の詳細」
    「諸費用」「お問い合わせ先」でも使われており、絞らないと火災保険料や免許番号まで
    設備原文に載る。しかも金額のカンマで割れて ``16``・``500円（税込）`` のような
    断片になり、辞書の未知表記を汚す（課題#19 の別の形）。
    """
    seen: dict[str, None] = {}
    for section in doc.xpath("//*[starts-with(@class,'Section_section-block__')]"):
        heading = section.xpath(".//*[starts-with(@class,'Heading_heading-block__')]")
        title = " ".join(heading[0].text_content().split()) if heading else ""
        if title not in _FEATURE_SECTIONS:
            continue
        for item in section.xpath(".//*[starts-with(@class,'TitleTextItem_title-text-item__')]"):
            for node in item.xpath("./div"):
                for token in re.split(r"[、,／/]", " ".join(node.text_content().split())):
                    cleaned = token.strip("｜| ")
                    if cleaned and len(cleaned) <= 30:
                        seen.setdefault(cleaned, None)
        for point in section.xpath(".//*[starts-with(@class,'PointList_point-list__')]/li"):
            cleaned = " ".join(point.text_content().split())
            if cleaned and len(cleaned) <= 30:
                seen.setdefault(cleaned, None)
    return list(seen)


# 所在階/階数は「2/2」と**「階」を付けずに**並ぶので、共通の parse_floor が当たらない。
_FLOORS = re.compile(r"所在階/階数\s*(B?\d+)\s*/\s*(\d+)")


def _floors(body: str) -> tuple[int | None, int | None]:
    """「所在階/階数2/2」を (所在階, 階数) へ。

    ⚠ **共通の ``parse_floor`` / ``parse_total_floors`` は使えない。**
    どちらも「◯階」「◯階建」という表記を前提にしているが、レオパレスの詳細は
    数字をスラッシュで並べるだけ（``2/2``）なので当たらない。
    """
    matched = _FLOORS.search(body)
    if not matched:
        return None, None
    raw_floor = matched.group(1)
    floor = -int(raw_floor[1:]) if raw_floor.startswith("B") else int(raw_floor)
    return floor, int(matched.group(2))


def _between(body: str, start: str, end: str) -> str | None:
    """本文中の ``start`` と ``end`` に挟まれた部分。

    ⚠ 詳細ページに ``dt``/``th`` が1つも無い（すべて div で組まれている）ため、
    見出しの文字列で切り出すしかない。
    """
    head = body.find(start)
    if head < 0:
        return None
    head += len(start)
    tail = body.find(end, head)
    return body[head:tail] if tail > head else body[head : head + 60]


class LeopalaceScraper:
    """レオパレス21 賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = False
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False
    city_rotation_limit = None
    # MUST をサイト側へ渡す（→ ADR 0015）。キーと選択肢は一覧の検索フォームから採り、
    # zzz=1 の対照で判定方法の妥当性を担保してから実測した（→ 詳細設計書 §11.5）。
    # ⚠ walkTo と propertyAgeTo は配線しない（前者は徒歩23分を返し、
    # 後者は 99=新築 という特殊値を持つ）
    supports_site_filters = True

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/properties/chintai/area/{都道府県}/{市区スラグ}`` を組み立てる。

        ⚠ **市区の検索値に都道府県は含まれない**（``adachi-ku-13121``）。
        レオパレスの都道府県スラグは47件すべて ``PREFECTURE_ROMAJI`` と一致する
        ことを実測で確認したので、こちらで前置する。
        """
        search = pattern.search  # type: ignore[attr-defined]
        params: dict[str, str] = {}
        if rent_to := rent_to_value(search.price_max_hint):
            params["rentTo"] = rent_to
        query = f"?{urlencode(params)}" if params else ""

        urls: list[str] = []
        for area in areas:
            pref_slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref_slug:
                raise ValueError(f"LEOPALACE: 未知の都道府県です: {area.prefecture}")
            path = f"{pref_slug}/{area.value}" if area.value else pref_slug
            urls.append(f"{BASE_URL}/properties/chintai/area/{path}{query}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りは ``?page=N``（1始まり）。"""
        return f"{base_url}{query_separator(base_url)}page={page}"

    def is_last_page(self, count: int) -> bool:
        """⚠ **1ページの住戸数が一定でない**ので件数の閾値では判定できない。

        実測で足立区は18住戸/13建物、銚子市は4住戸/2建物。総件数の表示も
        ページ数と合わない。最終ページを超えると HTTP 200・0件で返るので、
        **0件になったら終わり**とするのが唯一測れている規則。
        """
        return count == 0

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []

        for card in doc.xpath("//*[starts-with(@class,'ApartmentItem_apartment-item__')]"):
            captions = _texts(card, "CaptionList_caption-list__", items=True)
            if len(captions) < 3:
                continue  # 想定と違う建物カードは飛ばす（1件で全体を止めない）
            address = clean_address(captions[_CAPTION_ADDRESS])
            access = captions[_CAPTION_STATION]
            building_spec = captions[_CAPTION_BUILDING]
            names = card.xpath(".//*[starts-with(@class,'Heading_heading-block__')]")
            building_name = " ".join(names[0].text_content().split()) if names else None

            for room in card.xpath(".//*[starts-with(@class,'RoomItem_room-item__')]"):
                listing = self._parse_room(
                    room,
                    building_name=building_name,
                    address=address,
                    access=access,
                    building_spec=building_spec,
                )
                if listing is not None:
                    listings.append(listing)
        return listings

    def _parse_room(
        self,
        room,
        *,
        building_name: str | None,
        address: str | None,
        access: str,
        building_spec: str,
    ) -> ScrapedListing | None:
        links = room.xpath(".//a[starts-with(@class,'RoomItem_link__')]/@href")
        if not links:
            return None
        href = links[0]
        matched = _ROOM_URL.search(href)
        if not matched:
            return None
        building_id, room_no = matched.groups()

        info = " ".join(_texts(room, "RoomItem_info__"))
        captions = _texts(room, "RoomItem_caption__")
        # caption は「1K｜23.18㎡｜201号室」を1つの塊で返すので分解する
        parts = [p for p in re.split(r"[｜|]", captions[0]) if p.strip()] if captions else []
        layout = parts[0].strip() if parts else None
        area_sqm = parse_area_sqm(parts[1]) if len(parts) > 1 else None
        price = parse_yen(info)

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=f"{building_id}_{room_no}",
            url=f"{BASE_URL}{href}",
            title=f"{building_name} {room_no}号室" if building_name else room_no,
            price=price,
            mgmt_fee_monthly=_common_fee(info),
            deposit_amount=_named_fee(info, "敷金", price),
            key_money_amount=_named_fee(info, "礼金", price),
            area_sqm=area_sqm,
            layout=layout,
            age_years=age_years_from_built(building_spec),
            total_floors=parse_total_floors(building_spec),
            address=address,
            station_info=access,
            walk_minutes=walk_minutes_from_access(access),
        )

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        # ⚠ **script を落としてから本文を読む。** RSC ペイロードが text_content() に
        # 混ざり、翻訳リソース（"searchByFeature":"特徴からお部屋を検索"）まで
        # 設備原文に載ってしまう（課題#19 の別の形）
        for node in doc.xpath("//script | //style | //noscript"):
            node.getparent().remove(node)

        features = _detail_features(doc)
        body = " ".join(doc.text_content().split())
        floor_num, total_floors = _floors(body)
        return ScrapedDetail(
            raw_features_text="、".join(features) or None,
            built_on=parse_built_on(_between(body, "築年数", "入居") or ""),
            floor_num=floor_num,
            total_floors=total_floors,
            address=clean_address(_between(body, "住所", "交通")),
        )

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """⚠ **404 にならない。** 存在しないURLも HTTP 200 で検索トップ相当を返す。"""
        response = fetcher.get(url)
        if response.status_code == 404:
            return True
        title = _TITLE.search(response.text)
        return bool(title) and _NOT_FOUND_TITLE in title.group(1)
