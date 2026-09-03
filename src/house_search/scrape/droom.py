"""D-room（大和リビング）のサイトアダプタ。

一覧URLは ``/{都道府県スラグ}/list/?city[]={JIS5桁}``。
実測は詳細設計書 §12（2026-09-04・課題#37 のチェックリスト10項目）。

D-room 固有の注意点:

* **市区の検索値が JIS5桁そのもの**なので ``m_cities.jis_code`` から導ける。
  スラグ収集（HOMES・ATHOME・レオパレスで必要だったもの）が要らない
* ⚠⚠ **ページ送りは ``page_num`` で 0 始まり**（→ §12.6）。ページャは ``href`` が空で
  ``onClick="pager(N)"`` しか持たないが、``pager.js`` が ``page_num`` へ入れて
  GET で submit する。**1始まりだと思って組むと1ページ目を永久に取り逃す**
  （2ページ目が返るだけでエラーにならない）
* ⚠⚠ **レスポンシブで同じ住戸が2つの形で並ぶ**（→ §12.8）。
  PC用テーブル（``room-list__tr`` > ``td``）と SP用カード（``room-list__bottom`` >
  ``room-list__content``）で、**どちらか一方だけを使わないと住戸が2倍になる**。
  しかも持っている項目が違う: **管理費は SP用にしかなく、詳細URLは PC用にしかない**。
  そのため**号室をキーに突き合わせる**（98棟で順序も号室も一致し重複0件を実測）
* ⚠ **``room-list__card`` は「棟」であって住戸ではない。** 名前で選ぶと棟あたり
  1件しか拾えず母集団が 334 → 98 に化けるが**エラーにならない**（→ §12.3）
* ⚠ **交通欄の駅名が鉤括弧つきで「駅」の字が無い**（``常磐緩行線「綾瀬」徒歩8分``）。
  課題#41 の修正は「鉤括弧の直後が『駅』のとき」に限るので**これでは拾えない**。
  こちらで ``「綾瀬」駅`` の形に直してから ``station_info`` に入れる
  （UR で「駅」を残したのと同じ対応）。⚠ **詳細ページは「駅」が付く**ので変換不要
* ⚠⚠ **交通欄にバス経由が混ざる**（``常磐緩行線「北千住」バス15分「本木新道」停徒歩2分``）。
  その「徒歩2分」は**バス停からの徒歩**なので駅徒歩にすると ``walk_minutes_max`` を
  不当に通過する（UR・レオパレスと同じ罠）。⚠ **1行に徒歩経路とバス経路が並ぶ**ので
  行ごと捨てると本物の駅徒歩を落とす。**経路ごとに割ってから1つずつ見る**
* ⚠ **掲載終了が 404 にならない。** HTTP 200 で ``title`` が
  「現在、空室はございません。」になる（→ §12.9）
* ⚠ **設備原文に「同じエリアの似た物件」を混ぜない**（→ §12.8）。同じページに
  他住戸が20件並んでおり、混ぜると**他物件の設備が自分の設備として載る**。
  「注意事項等」「その他」（保証料・室内清掃費用）も設備ではない
* **連続取得の上限は無い**（2.5秒間隔で20市区すべて正常 → §12.7）ので
  市区ローテーション（課題#36）は宣言しない
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlencode, urlparse

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    clean_address,
    parse_area_sqm,
    parse_built_on,
    parse_fee,
    parse_floor,
    parse_months_fee,
    parse_yen,
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "DROOM"
BASE_URL = "https://www.droom-daiwaliving.net"

# 賃料上限 rcu の選択肢（円）。実測で一覧の検索フォームから採った。
# ⚠ **等間隔ではない**（2〜20万は5,000円刻み・その上は30万と50万しかない）。
RENT_UPPER_CHOICES: tuple[int, ...] = (
    20_000, 25_000, 30_000, 35_000, 40_000, 45_000, 50_000, 55_000, 60_000,
    65_000, 70_000, 75_000, 80_000, 85_000, 90_000, 95_000, 100_000,
    105_000, 110_000, 115_000, 120_000, 125_000, 130_000, 135_000, 140_000,
    145_000, 150_000, 155_000, 160_000, 165_000, 170_000, 175_000, 180_000,
    185_000, 190_000, 195_000, 200_000, 300_000, 500_000,
)

# 1ページあたりの表示件数。⚠ **これは「棟」の数**なので住戸はこれより多い。
AMOUNT_PER_PAGE = "100"

# 掲載終了ページのタイトル。⚠ **404 にならない**ので文言で判別する（→ §12.9）。
_SOLD_TITLE = "現在、空室はございません。"
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)

_SPACES = re.compile(r"\s+")
# 「1K(25.91m²)」。㎡の表記ゆれは parse_area_sqm が NFKC で吸収する。
_LAYOUT_AREA = re.compile(r"^([0-9A-Za-z]+)[(（]([^)）]+)[)）]")
# 「賃料9.50万円」。⚠ 万円単位の小数で来る。
_RENT_MAN = re.compile(r"([\d.]+)万円")
# 「地上2階」。⚠ 共通の parse_total_floors は「階建」を前提にしていて当たらない。
_TOTAL_FLOORS = re.compile(r"地上\s*(\d+)\s*階")
# 交通欄を「「駅名」その後」の単位へ割る。⚠ 1行に複数の経路が並ぶため。
_ACCESS_PART = re.compile(r"「([^」]+)」([^「]*)")
_WALK = re.compile(r"徒歩\s*(\d+)\s*分")

# PC用テーブルの列位置（0起点）。ヘッダ行は th なので td の数で弾ける。
_TD_ROOM_NO = 2
_TD_COUNT = 8

# 設備原文に載せる詳細ページの行。⚠ **ここを絞らないと**「注意事項等」の保証料や
# 「その他」の室内清掃費用まで設備として辞書照合に載る（レオパレスの「諸費用」と同型）。
_FEATURE_ROWS = frozenset({"設備"})


def _squash(text: str | None) -> str:
    """空白をすべて落とす。

    ⚠ D-room はラベルにも値にも空白が入る（``交　通`` / ``東京都 足立区綾瀬５丁目 2-11``）。
    住所の空白を残すと他サイトと表記が揃わず ``dedup_key`` が一致しなくなる。
    """
    return _SPACES.sub("", text or "")


def rent_upper_value(price_max_hint: int | None) -> str | None:
    """賃料上限を選択肢へ**切り上げ**る。選択肢外の値は0件事故のもと（→ 課題#29）。"""
    if not price_max_hint:
        return None
    for choice in RENT_UPPER_CHOICES:
        if choice >= price_max_hint:
            return str(choice)
    return None  # 上限を超える希望額はサイト側へ渡さない（ローカル判定に任せる）


def _access_routes(access: str | None) -> list[tuple[str, str]]:
    """交通欄を「(駅名またはバス停名, その後の記述)」の並びへ割る。

    ⚠ 1行に ``伊勢崎線「西新井」徒歩6分 常磐緩行線「北千住」バス15分「西新井駅西口」停徒歩5分``
    のように複数の経路が並ぶので、**行単位で判断してはいけない**。
    """
    return [(m.group(1), m.group(2)) for m in _ACCESS_PART.finditer(access or "")]


def walk_minutes_from_access(access: str | None) -> int | None:
    """交通欄から**駅徒歩**（分）の最短を取る。

    ⚠⚠ 除くものが2種類ある。

    * **バス停からの徒歩**（``「西新井駅西口」停徒歩5分``）… 直後が「停」で始まる
    * **バスに乗る駅からの記述**（``「北千住」バス15分``）… その先の徒歩はバス停から
    """
    minutes: list[int] = []
    for _, rest in _access_routes(access):
        if rest.startswith("停") or "バス" in rest:
            continue
        if matched := _WALK.search(rest):
            minutes.append(int(matched.group(1)))
    return min(minutes) if minutes else None


def mark_stations(access: str | None) -> str | None:
    """駅名の鉤括弧の直後に「駅」を補う（→ 課題#41）。

    ``常磐緩行線「綾瀬」徒歩8分`` → ``常磐緩行線「綾瀬」駅 徒歩8分``

    ⚠ ``matcher`` は「◯◯駅」というアンカーで駅を拾い、鉤括弧で囲まれていても
    **直後が「駅」のときだけ**囲みを外す。D-room の一覧は「駅」が無いので
    そのままでは1件も同定できない。
    ⚠ **バス停には付けない**（直後が「停」）。付けると存在しない駅として扱われる。

    ⚠⚠ **鉤括弧の「前」にも空白を入れる。** ``matcher`` は囲みを外して
    ``常磐緩行線綾瀬駅`` にしてから「駅」の左を遡るので、路線名との間に区切りが無いと
    **路線名ごと駅名として拾ってしまう**（``常磐緩行線綾瀬`` という駅は存在せず、
    駅マスタに当たらないので通勤時間が unknown になる）。ATHOME は
    ``ＪＲ京浜東北線 「北浦和」駅`` と元から空白があるので表面化しなかった。
    """
    if not access:
        return None

    def _repl(matched: re.Match[str]) -> str:
        name, rest = matched.group(1), matched.group(2)
        if rest.startswith("停"):
            return matched.group(0)
        return f" 「{name}」駅 {rest}"

    return _SPACES.sub(" ", _ACCESS_PART.sub(_repl, access)).strip()


def total_floors_from_text(text: str | None) -> int | None:
    """「地上2階」「1階部分（地上2階）」から総階数を取る。

    ⚠ **共通の ``parse_total_floors`` は使えない。** 「◯階建」という表記を
    前提にしているが、D-room は「地上◯階」と書く（レオパレスは「2/2」だった）。
    """
    matched = _TOTAL_FLOORS.search(text or "")
    return int(matched.group(1)) if matched else None


def _dedupe(values: Sequence[str]) -> list[str]:
    """順序を保った重複除去。"""
    return list(dict.fromkeys(v for v in values if v))


class DroomScraper:
    """D-room（大和リビング）賃貸の取得と解析。"""

    site_code = SITE_CODE
    requires_city = False
    # 市区の検索値は JIS5桁そのもの。⚠ スラグ収集が要らない数少ないサイト
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    ignore_robots = False
    # 連続取得の上限は実測で見つからなかった（2.5秒間隔で20市区すべて正常 → §12.7）
    city_rotation_limit = None
    # MUST をサイト側へ渡す（→ ADR 0015）。キーと選択肢は一覧の検索フォームから採り、
    # 間取り軸だけを動かして件数が変わることを対照実験で確かめた（→ §12.11）。
    # ⚠ walk は配線しない（選択肢が15分までで MUST の20分を表現できず、
    # 送るとサイト側フィルタが MUST より厳しくなる）
    supports_site_filters = True

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/{都道府県スラグ}/list/?city[]={JIS5桁}`` を組み立てる。

        ⚠ **``cff=Y``（共益費/管理費を含む）を必ず ``rcu`` と対で送る。**
        無いと ``rcu`` は賃料だけに掛かり、管理費を足すと上限を超える住戸が混ざる。
        付ければ ``rent_total`` そのもので絞れる（他サイトには無い利点）。
        """
        search = pattern.search  # type: ignore[attr-defined]
        base_params: list[tuple[str, str]] = []
        if rent_upper := rent_upper_value(search.price_max_hint):
            base_params.append(("rcu", rent_upper))
            base_params.append(("cff", "Y"))
        base_params.append(("amount", AMOUNT_PER_PAGE))

        urls: list[str] = []
        for area in areas:
            pref_slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref_slug:
                raise ValueError(f"DROOM: 未知の都道府県です: {area.prefecture}")
            params = list(base_params)
            if area.value:
                params.append(("city[]", area.value))
            urls.append(f"{BASE_URL}/{pref_slug}/list/?{urlencode(params)}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りは ``page_num``。

        ⚠⚠ **0 始まり。** 呼び出し側の ``page`` は1始まりなので1を引く。
        ここを間違えると**1ページ目を永久に取り逃す**（2ページ目が返るだけで
        エラーにならない → §12.6）。
        """
        return f"{base_url}{query_separator(base_url)}page_num={page - 1}"

    def is_last_page(self, count: int) -> bool:
        """⚠ **``amount`` は「棟」の数**なので住戸数の閾値では判定できない。

        最終ページを超えると住戸0件で返るので、**0件になったら終わり**とする。
        """
        return count == 0

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for card in doc.cssselect(".result__card"):
            listings.extend(self._parse_card(card))
        return listings

    def _parse_card(self, card) -> list[ScrapedListing]:
        """棟カード1つを住戸へ展開する。"""
        info = self._building_info(card)
        access = info.get("交通")
        names = card.cssselect(".result__subtitle")
        building_name = _SPACES.sub(" ", names[0].text_content()).strip() if names else None
        images = card.cssselect(".result__img img")
        image_url = images[0].get("src") if images else None

        detail_urls = self._detail_urls(card)
        listings: list[ScrapedListing] = []
        for bottom in card.cssselect(".room-list__bottom"):
            listing = self._parse_room(
                bottom,
                detail_urls=detail_urls,
                building_name=building_name,
                address=clean_address(_squash(info.get("所在地"))),
                access=access,
                built=info.get("築年月"),
                floors=info.get("階数"),
                image_url=image_url,
            )
            if listing is not None:
                listings.append(listing)
        return listings

    def _building_info(self, card) -> dict[str, str]:
        """棟カードの「所在地 / 築年月 / 交通 / 階数」。

        ⚠ **ラベルに全角スペースが入る**（``交　通`` / ``階　数``）ので、
        キーは空白を落として突き合わせる。

        ⚠⚠ **値の空白は落とさない。** 交通欄の空白は路線名と駅名の区切りとして
        効いており、消すと ``matcher`` が ``常磐緩行線綾瀬`` のように路線名ごと
        駅名として拾ってしまう（→ ``mark_stations``）。空白を落としたいのは
        住所だけなので、それは呼び出し側で行う。
        """
        info: dict[str, str] = {}
        for item in card.cssselect(".result__item"):
            labels = item.cssselect(".result__label")
            if not labels:
                continue
            label = _SPACES.sub(" ", labels[0].text_content()).strip()
            body = _SPACES.sub(" ", item.text_content()).strip()
            value = body[len(label) :] if body.startswith(label) else body
            info[_squash(label)] = value.strip()
        return info

    def _detail_urls(self, card) -> dict[str, str]:
        """PC用テーブルから「号室 → 詳細URL」を作る。

        ⚠ **詳細URLは PC用テーブルにしかなく、管理費は SP用カードにしかない**
        （→ §12.8）。号室で突き合わせるのは、98棟で順序も号室も一致し
        棟内の号室重複が0件であることを実測したうえでの選択で、
        **順序に依存しないぶん安全側**である。
        """
        urls: dict[str, str] = {}
        for row in card.cssselect(".room-list__tr"):
            cells = row.cssselect("td")
            if len(cells) < _TD_COUNT:
                continue  # ヘッダ行は th なのでここで落ちる
            room_no = _squash(cells[_TD_ROOM_NO].text_content())
            links = row.cssselect(".room-list__btn--rarrow a")
            if room_no and links and (href := links[0].get("href")):
                urls.setdefault(room_no, href)
        return urls

    def _parse_room(
        self,
        bottom,
        *,
        detail_urls: dict[str, str],
        building_name: str | None,
        address: str | None,
        access: str | None,
        built: str | None,
        floors: str | None,
        image_url: str | None,
    ) -> ScrapedListing | None:
        contents = bottom.cssselect(".room-list__content")
        if not contents:
            return None
        content = contents[0]

        # 号室と「間取り(専有面積)」は dt を持たない dl に2つ並ぶ
        multi = content.cssselect(".room-list__dl--multi dd")
        if len(multi) < 2:
            return None
        room_no = _squash(multi[0].text_content())
        layout, area_sqm = self._layout_area(_squash(multi[1].text_content()))

        url = detail_urls.get(room_no)
        if not url:
            return None  # 詳細URLの無い住戸は掲載として同定できない
        external_id = urlparse(url).path.strip("/").split("/")[-1]
        if not external_id:
            return None

        cost = content.cssselect(".room-list__dl--cost")
        price = self._rent(_squash(cost[0].text_content())) if cost else None
        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=url,
            title=f"{building_name} {room_no}" if building_name else room_no,
            price=price,
            mgmt_fee_monthly=self._mgmt_fee(content),
            area_sqm=area_sqm,
            layout=layout,
            total_floors=total_floors_from_text(floors),
            address=address,
            station_info=mark_stations(access),
            walk_minutes=walk_minutes_from_access(access),
            image_url=image_url,
        )

    def _layout_area(self, text: str) -> tuple[str | None, float | None]:
        """「1K(25.91m²)」を (間取り, 面積) へ。"""
        matched = _LAYOUT_AREA.match(text)
        if not matched:
            return None, None
        return matched.group(1), parse_area_sqm(matched.group(2))

    def _rent(self, text: str) -> int | None:
        """「賃料9.50万円」を円へ。⚠ **万円単位の小数**で来る。"""
        matched = _RENT_MAN.search(text)
        return round(float(matched.group(1)) * 10_000) if matched else None

    def _mgmt_fee(self, content) -> int | None:
        """管理費。

        ⚠ **欄そのものが無い住戸がある**（実測で 112室中2室）。SUUMO の「-」＝0円とは
        違い欄が存在しないので、ここでは **None（欠損）**にして詳細で確定させる。
        0円と決め打つと、実際は管理費のある住戸で ``rent_total`` を過小評価する。
        """
        for dl in content.cssselect(".room-list__dl"):
            terms = dl.cssselect("dt")
            if terms and _squash(terms[0].text_content()) == "管理費":
                values = "".join(_squash(dd.text_content()) for dd in dl.cssselect("dd"))
                return parse_fee(values)
        return None

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        rows = self._detail_rows(doc)
        rent = parse_yen(rows.get("賃料"))
        floor_text = rows.get("建物階数", "")
        return ScrapedDetail(
            raw_features_text="、".join(self._detail_features(doc, rows)) or None,
            built_on=parse_built_on(rows.get("築年月")),
            floor_num=parse_floor(floor_text),
            total_floors=total_floors_from_text(floor_text),
            mgmt_fee_monthly=parse_fee(rows.get("共益費/管理費")),
            deposit_amount=parse_months_fee(rows.get("敷金"), rent),
            key_money_amount=parse_months_fee(rows.get("礼金"), rent),
            address=clean_address(_squash(rows.get("所在地"))),
            walk_minutes=walk_minutes_from_access(rows.get("交通")),
        )

    def _detail_rows(self, doc) -> dict[str, str]:
        """``infomation__row`` の「見出し → 値」。29行に MUST・採点の材料が揃う。"""
        rows: dict[str, str] = {}
        for row in doc.cssselect(".infomation__row"):
            heads = row.cssselect(".infomation__head")
            data = row.cssselect(".infomation__data")
            if not (heads and data):
                continue
            key = _squash(heads[0].text_content())
            rows.setdefault(key, _SPACES.sub(" ", data[0].text_content()).strip())
        return rows

    def _detail_features(self, doc, rows: dict[str, str]) -> list[str]:
        """設備のタグ列。

        ⚠⚠ **「同じエリアの似た物件」を混ぜない**（→ §12.8）。同じページに他住戸が
        20件並んでおり、混ぜると**他物件の設備が自分の設備として載る**。
        ``infomation__row`` の「設備」行と ``point__facility`` に限れば自然に除ける。
        ⚠ 「注意事項等」「その他」も設備ではない（保証料・室内清掃費用）ので
        ``_FEATURE_ROWS`` で絞る。
        ⚠ **区切りは空白のみ**。中黒で割ると「バス・トイレ別」を取りこぼす。
        """
        values: list[str] = []
        for key in _FEATURE_ROWS:
            values.extend(rows.get(key, "").split())
        values.extend(
            _SPACES.sub(" ", node.text_content()).strip()
            for node in doc.cssselect(".point__facility")
        )
        return _dedupe(values)

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """⚠ **404 にならない。** HTTP 200 で「現在、空室はございません。」が返る。"""
        response = fetcher.get(url)
        if response.status_code == 404:
            return True
        title = _TITLE.search(response.text)
        return bool(title) and _SOLD_TITLE in title.group(1)
