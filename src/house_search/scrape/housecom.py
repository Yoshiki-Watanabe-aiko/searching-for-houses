"""ハウスコムのサイトアダプタ。

一覧URLは ``/{都道府県スラグ}/{市区スラグ}-city/``。
実測は詳細設計書 §13（2026-09-04・課題#37 のチェックリスト10項目）。

ハウスコム固有の注意点:

* ⚠⚠ **既定の並びの1ページ目だけを見て分布を判断してはいけない**（→ §13.4）。
  足立区の既定の並びは1ページ目127住戸が **rent_total 中央 155,000円・10万円以下0%** で、
  そのまま測ると「賃料が高すぎて使えない」という結論になる。実際は
  ``cc_mdr[]``（間取り）＋ ``sort=0``（家賃が安い順）にすると11住戸中**9件が
  MUST 1段目を通過**した。**MUST を通る掲載は必ず賃料上限以下なので、
  安い順にすれば1ページ目に必ず載る**（ATHOME の課題#39 と同じ論法）
* ⚠ **``?sort=0`` は棟あたり最安の1住戸だけを返す**（既定は棟の全住戸）。
  同じ棟の別住戸は取り逃すが、名寄せで同じグループになるので実害は小さい
* ⚠ **間取りの複数指定は「クエリ配列」でしかできない**（→ §13.9）。
  ``?cc_mdr[]=4&cc_mdr[]=7`` は効く（件数 1,019 → 304・返る間取りは指定した2種だけ）が、
  パス形式の ``cc_mdr-4-7/`` は **HTTP 404**。1つずつURLを分けると
  MUST 6種でリクエストが6倍になるので、必ず配列で送る
* ⚠ **``.property_build`` は「棟」で住戸は ``.property_room``**（D-room の §12.3 と同型）。
  棟と住戸は同じ ``<article>`` にぶら下がる
* ⚠⚠ **号室の伏字（``***号室``）を掲載終了と解釈してはいけない**（→ §13.6）。
  調査段階ではそう解釈したが、実地の取り込みで**本番413掲載のうち100件（24%）が伏字**で、
  いずれも一覧に載る**募集中**の住戸だった。掲載終了の判定は 404 のみで行う
* ⚠ **詳細ページに費用の項目が同居する**（初期費用・更新料・鍵交換費用・
  損保火災保険・保証会社）。設備原文へ入れると辞書が費用の文言を設備として拾う
  （レオパレスの「諸費用」→ §11.5 と同型）
* **連続取得の上限は無い**（2.5秒間隔で20市区すべて正常 → §13.3）ので
  市区ローテーション（課題#36）は宣言しない
* 市区の検索値は**サイト固有スラグ**。都道府県索引1本で全市区が採れる。
  ⚠ **政令市の行政区はアンダースコア区切り**（``saitamashi_minamiku``）で、
  収集の正規表現に ``_`` を含めないと27市区が黙って落ちる（→ §13.8）
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_MAPPING, AreaTarget
from house_search.scrape.base import (
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
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "HOUSECOM"
BASE_URL = "https://www.housecom.jp"

_SPACES = re.compile(r"\s+")
_ROOM_HREF = re.compile(r"^/room_(\d+)/$")
_RENT = re.compile(r"([\d.]+)\s*万円")
_MGMT = re.compile(r"共益費\s*([\d,]+)\s*円")
_DEPOSIT = re.compile(r"敷\s*(\S+)")
_KEY_MONEY = re.compile(r"礼\s*(\S+)")
_SPEC = re.compile(r"(地下\d+階|\d+階)\s*/\s*([0-9A-Za-zＳ]+)\s*/\s*([\d.]+\s*㎡)")
# ⚠ 棟の階数は「地上4階」で ``parse_total_floors`` が読めない（あちらは「4階建」を見る）。
_TOTAL_FLOORS = re.compile(r"地上\s*(\d+)\s*階")
_BUILDING_KIND = re.compile(r"\s*賃貸(マンション|アパート|一戸建て|テラスハウス).*$")
# ⚠ ``.room-equip`` のうち設備ではないカテゴリ。落とさないと未知表記が
# 「一人暮らしに人気の設備」「服やおしゃれが好き」で埋まる。
_NON_FEATURE_CATEGORIES: frozenset[str] = frozenset({"ライフステージ", "こだわり", "趣味"})

# 設備として扱う詳細ページの見出し。⚠ ここに費用の項目を足さない（→ §13.7）。
_FEATURE_ROWS: tuple[str, ...] = (
    "設備",
    "部屋設備",
    "建物設備",
    "こだわり条件",
    "室内設備",
    "その他設備",
    "構造",
    "物件種別",
    "方位・位置",
    "駐車場",
)


def _squash(text: str | None) -> str:
    return _SPACES.sub("", text or "")


def _flat(text: str | None) -> str:
    return _SPACES.sub(" ", text or "").strip()


def walk_minutes_from_access(access: str | None) -> int | None:
    """最寄駅欄から駅徒歩の分数を取る。

    ⚠ **バス経由の「徒歩N分」はバス停からの徒歩**なので駅徒歩に使わない
    （UR・D-room・レオパレスで踏んだ罠）。「バス」より前だけを見る。
    """
    if not access:
        return None
    head = access.split("バス")[0]
    minutes = [
        value
        for part in re.findall(r"徒歩\s*\d+\s*分", head)
        if (value := parse_walk_minutes(part)) is not None
    ]
    return min(minutes) if minutes else None


class HousecomScraper:
    """ハウスコム賃貸の取得と解析。"""

    site_code = SITE_CODE
    # 都道府県ページ（/tokyo/）は市区の索引で掲載一覧ではない
    requires_city = True
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False
    # 連続取得の上限は実測で見つからなかった（2.5秒間隔で20市区すべて正常 → §13.3）
    city_rotation_limit = None
    # MUST をサイト側へ渡す（→ ADR 0015）。間取りだけを配線する。
    # ⚠ 賃料・面積のクエリキーは確認できていないので送らない（推測で書かない）
    supports_site_filters = True

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/{都道府県}/{市区}-city/?sort=0`` を組み立てる。

        ⚠ **``sort=0``（家賃が安い順）を必ず付ける。** 既定の並びは1ページ目が
        高い住戸で埋まり、MUST を1件も通さない（→ §13.4）。
        """
        urls: list[str] = []
        for area in areas:
            pref_slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref_slug:
                raise ValueError(f"HOUSECOM: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                # 市区の検索値が無い市区は対象にできない（→ 課題#36）
                continue
            urls.append(f"{BASE_URL}/{pref_slug}/{area.value}-city/?sort=0")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りは ``?page=N``（**1始まり**。重なり0件を実測 → §13.3）。"""
        return f"{base_url}{query_separator(base_url)}page={page}"

    def is_last_page(self, count: int) -> bool:
        """⚠ **件数表記は「棟」の数**なので住戸数の閾値では判定できない。

        最終ページを超えると住戸0件になるので、**0件になったら終わり**とする。
        """
        return count == 0

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for build in doc.cssselect(".property_build"):
            article = build.getparent()
            for _ in range(3):
                if article is None or article.cssselect(".property_room"):
                    break
                article = article.getparent()
            if article is None:
                continue
            listings.extend(self._parse_article(build, article))
        return self._dedupe(listings)

    def _dedupe(self, listings: list[ScrapedListing]) -> list[ScrapedListing]:
        """同じ住戸が2度出るのを1件にまとめる。

        ⚠ **棟の代表表示と住戸一覧の両方に同じ住戸が載る**ことがあり、
        代表表示のほうは ``h4``（号室）を持たない。除かないと**号室なしの掲載**が
        同じ ``external_id`` で並ぶ（D-room の PC用/SP用の重複 → §12.3 と同型）。
        **情報の多い（号室のある）ほうを残す。**
        """
        best: dict[str, ScrapedListing] = {}
        for listing in listings:
            current = best.get(listing.external_id)
            if current is None or len(listing.title or "") > len(current.title or ""):
                best[listing.external_id] = listing
        return list(best.values())

    def _rooms(self, article) -> list:
        """住戸カードを取り出す。

        ⚠⚠ **``.property_room`` は入れ子になっていることがある**（外側の要素と
        内側の ``<article class="property_room">``）。両方拾うと**同じ住戸が2件**になり、
        しかも外側は ``h4``（号室）を持たないので**号室なしの掲載が並ぶ**。
        エラーにならず件数が増えるだけなので、**子孫に ``.property_room`` を
        持つものを捨てて最も内側だけを残す**。
        """
        rooms = article.cssselect(".property_room")
        # ⚠ ``cssselect`` は **自分自身にもマッチする**（descendant-or-self）ので、
        #    自分を除いてから子孫の有無を見る。除かないと全件が「外側」と判定され
        #    **掲載0件になる**（実際に踏んだ）。
        return [
            room
            for room in rooms
            if not [inner for inner in room.cssselect(".property_room") if inner is not room]
        ]

    def _parse_article(self, build, article) -> list[ScrapedListing]:
        """棟1つを住戸へ展開する。"""
        info = self._build_info(build)
        access = self._access_text(info.get("最寄駅"))
        built_on = parse_built_on(info.get("築年月"))
        # ⚠ 住所は空白を落として他サイトと表記を揃える（dedup_key を一致させるため）。
        #    ⚠ **最寄駅の空白は落とさない**。落とすと matcher が路線名ごと駅名にする
        #    （D-room で踏んだ罠 → §12.3）
        address = clean_address(_squash(info.get("所在地")))
        building_name = self._building_name(article)
        total_floors = self._total_floors(info.get("階数"))
        listings: list[ScrapedListing] = []
        for room in self._rooms(article):
            listing = self._parse_room(
                room,
                address=address,
                access=access,
                built_on=built_on,
                total_floors=total_floors,
                building_name=building_name,
            )
            if listing is not None:
                listings.append(listing)
        return listings

    def _total_floors(self, value: str | None) -> int | None:
        """棟の階数。⚠ 一覧は「地上4階」・詳細は「4階部分（地上4階建）」と表記が違う。"""
        if not value:
            return None
        matched = _TOTAL_FLOORS.search(value)
        return int(matched.group(1)) if matched else parse_total_floors(value)

    def _access_text(self, value: str | None) -> str | None:
        """最寄駅欄を他サイトと同じ「◯◯駅 徒歩N分」の形へ揃える。

        ⚠ ハウスコムは「竹ノ塚駅 （徒歩12分）」と**徒歩をカッコで囲む**ため、
        ``commute/matcher`` の第2パス（時間表記の直前を駅名とみなす経路）が
        「徒」という語を拾ってしまう。第1パスが効くので同定自体は通るが、
        余計な表記が ``unmatched`` として残るのでカッコだけ外す。
        ⚠ **空白は落とさない**（落とすと路線名ごと駅名になる → §12.3）。
        """
        if not value:
            return None
        unwrapped = value
        for bracket in ("（", "）", "(", ")"):
            unwrapped = unwrapped.replace(bracket, " ")
        return _flat(unwrapped)

    def _build_info(self, build) -> dict[str, str]:
        """棟カードの ``dl`` から「見出し → 値」を作る。"""
        info: dict[str, str] = {}
        for dl in build.cssselect("dl"):
            terms = dl.cssselect("dt")
            values = dl.cssselect("dd")
            for term, value in zip(terms, values, strict=False):
                info.setdefault(_squash(term.text_content()), _flat(value.text_content()))
        return info

    def _building_name(self, article) -> str | None:
        for link in article.cssselect("a.c--link"):
            name = _flat(link.text_content())
            if name:
                # 「セレスティア東保木間 賃貸マンション」の種別部分を落とす
                return _BUILDING_KIND.sub("", name) or None
        return None

    def _parse_room(
        self,
        room,
        *,
        address: str | None,
        access: str | None,
        built_on,
        total_floors: int | None,
        building_name: str | None,
    ) -> ScrapedListing | None:
        detail_path = self._detail_path(room)
        if detail_path is None:
            return None
        matched_id = _ROOM_HREF.match(detail_path)
        if matched_id is None:
            return None
        text = _flat(room.text_content())
        rent = _RENT.search(text)
        spec = _SPEC.search(text)
        if not (rent and spec):
            return None
        price = int(float(rent.group(1)) * 10_000)
        mgmt = _MGMT.search(text)
        heads = room.cssselect("h4")
        room_no = _squash(heads[0].text_content()) if heads else ""
        title = " ".join(part for part in [building_name, room_no] if part) or None
        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=matched_id.group(1),
            url=f"{BASE_URL}{detail_path}",
            title=title,
            price=price,
            mgmt_fee_monthly=int(mgmt.group(1).replace(",", "")) if mgmt else 0,
            deposit_amount=parse_months_fee(self._fee(text, _DEPOSIT), price),
            key_money_amount=parse_months_fee(self._fee(text, _KEY_MONEY), price),
            area_sqm=parse_area_sqm(spec.group(3)),
            layout=spec.group(2),
            floor_num=parse_floor(spec.group(1)),
            total_floors=total_floors,
            address=address,
            station_info=access,
            walk_minutes=walk_minutes_from_access(access),
        )

    def _fee(self, text: str, pattern: re.Pattern[str]) -> str | None:
        matched = pattern.search(text)
        return matched.group(1) if matched else None

    def _detail_path(self, room) -> str | None:
        for link in room.cssselect("a[href]"):
            href = link.get("href") or ""
            if _ROOM_HREF.match(href):
                return href
        return None

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と補足項目を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        rows = self._detail_rows(doc)
        rent = parse_yen(rows.get("家賃"))
        floors = rows.get("階数")
        return ScrapedDetail(
            raw_features_text="、".join(self._detail_features(doc, rows)) or None,
            built_on=parse_built_on(rows.get("築年数")),
            floor_num=parse_floor(floors),
            total_floors=self._total_floors(floors),
            mgmt_fee_monthly=parse_fee(rows.get("共益費")),
            deposit_amount=parse_months_fee(self._slash(rows.get("敷金/礼金"), 0), rent),
            key_money_amount=parse_months_fee(self._slash(rows.get("敷金/礼金"), 1), rent),
            address=clean_address(_squash(rows.get("所在地"))),
            walk_minutes=walk_minutes_from_access(self._access_text(rows.get("最寄駅"))),
        )

    def _slash(self, value: str | None, index: int) -> str | None:
        if not value:
            return None
        parts = [part.strip() for part in value.split("/")]
        return parts[index] if index < len(parts) else None

    def _detail_rows(self, doc) -> dict[str, str]:
        """詳細ページの ``dl`` / ``table`` から「見出し → 値」を作る。"""
        rows: dict[str, str] = {}
        for dl in doc.cssselect("dl"):
            for term, value in zip(dl.cssselect("dt"), dl.cssselect("dd"), strict=False):
                rows.setdefault(_squash(term.text_content()), _flat(value.text_content()))
        for tr in doc.cssselect("tr"):
            heads = tr.cssselect("th")
            cells = tr.cssselect("td")
            if heads and cells:
                rows.setdefault(_squash(heads[0].text_content()), _flat(cells[0].text_content()))
        return rows

    def _detail_features(self, doc, rows: dict[str, str]) -> list[str]:
        """設備を集める。

        設備の本体は ``.room-equip``（カテゴリ別のタグ列が11ブロック）。
        型付き列から導けない「構造」「物件種別」などは ``dl`` の行から補う。

        ⚠⚠ **費用の項目を混ぜない**（→ §13.7）。同じ表に「初期費用」「更新料」
        「鍵交換費用」「室内清掃費用」「損保・火災保険」「保証会社」が並んでおり、
        混ぜると辞書が費用の文言を設備として拾う。**見出しを明示的に選ぶ**。
        ⚠ **``.room-tag-info`` は宣伝の生成文**（「洗濯に便利な設備として、室内洗濯機置場が
        あります。」）なので使わない。設備の照合には効くが未知表記を文断片で汚す
        （賃貸EX で踏んだ罠 → 課題#19）。
        ⚠ **「ライフステージ」「こだわり」「趣味」は設備ではない**
        （「一人暮らしに人気の設備」「服やおしゃれが好き」）ので落とす。
        """
        values: list[str] = []
        for block in doc.cssselect(".room-equip"):
            text = _flat(block.text_content())
            if not text or text.split(" ")[0] in _NON_FEATURE_CATEGORIES:
                continue
            values.append(text)
        # ⚠ **非該当の「－」を載せない**（HOMES の sr-only・goo の "-" と同型）。
        #    そのまま原文に入れると辞書が非該当の条件を拾う。
        values.extend(
            value for key in _FEATURE_ROWS if (value := rows.get(key, "")) not in ("", "－", "-")
        )
        seen: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.append(value)
        return seen

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """掲載終了は **404 でのみ**判定する（→ §13.6）。

        ⚠⚠ **号室の伏字（``***号室``）を掲載終了の印にしてはいけない。**
        調査段階では ``/room_1/`` が ``RenoasHAKATA ***号室`` を HTTP 200 で返したため
        「募集を終えた住戸」と解釈したが、**実地の取り込みで覆った**。
        一覧（家賃が安い順）の1ページ目にも伏字の住戸が並んでおり、
        本番の413掲載のうち **100件（24%）が伏字**だった。
        つまり伏字は「号室を公開していない」だけで**募集中**である。
        伏字で ``sold`` と判定すると、**募集中の掲載の4分の1を
        ランキングから消す**ことになる。
        ⚠ 実際に掲載が終了したときの挙動は**未確認**（終了済みのIDを持っていないため）。
        存在しないIDが 404 になることだけ実測できている（``/room_99999999/``）。
        """
        return fetcher.get(url).status_code == 404
