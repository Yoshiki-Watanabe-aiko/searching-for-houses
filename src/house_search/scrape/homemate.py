"""ホームメイトのサイトアダプタ。

一覧URLは ``/pr-{都道府県スラグ}/{JIS5桁}/``。
実測は詳細設計書 §13（2026-09-04・課題#37 のチェックリスト10項目）。

ホームメイト固有の注意点:

* ⚠⚠ **robots.txt が ``User-agent: ClaudeBot`` を全面禁止している**（→ §13.2）。
  GPTBot・PerplexityBot・CCBot 等も同じグループにある。本システムが名乗るのは
  ``house-search/2.0`` で ``_can_fetch`` は名乗る UA で照合するため、
  適用されるのは ``User-agent: *``（``/ad`` 等のみ Disallow）である。
  **ユーザー判断（2026-09-04）で `*` の規則に従って取得する。**
  ⚠ ADR 0011（APAMAN の ``ignore_robots``）とは別の話で、あちらは robots を
  無視する判断、こちらは自分に適用される規則に従うという標準的な解釈
* **市区の検索値が JIS5桁そのもの**なので ``m_cities.jis_code`` から導ける
  （D-room と同じくスラグ収集が要らない）
* ⚠⚠ **``ekiw``（駅徒歩）を配線してはいけない**（→ §13.9）。選択肢が
  **3/5/10/15分しか無く** MUST の ``walk_minutes_max: 20`` を表現できない。
  送るとサイト側フィルタが MUST より厳しくなり、徒歩16〜20分の住戸を取りこぼす
  （ADR 0015 の不変条件違反。D-room の ``walk`` → §12.4 と同じ理由）
* ⚠ **``section.m_prpty_list_item`` が「棟」で、住戸は ``.m_prpty_list_room``**
  （D-room の §12.3・ハウスコムの §13 と同じ二層構造）
* ⚠⚠ **交通欄は ``ul.m_prpty_list_item_main_info_access`` の1つ目の ``li`` から取る**
  （→ §13.10）。本文から「◯◯駅まで徒歩N分」を探して切り出すと、その形を持たない
  **バス便**（「東武バス 東金町五丁目停まで徒歩3分、バス乗車してＪＲ常磐線 金町駅まで17分」）が
  交通欄ごと落ち、**駅が同定できず通勤時間が unknown になる**（実測 236掲載中45件＝19%）。
  ⚠ **例外にならない**うえ、交通欄が取れた掲載だけを分母にすると98.4%と健全に見える
* ⚠ **築年月ではなく築年数（「築49年」）しか一覧に無い**ので
  ``ScrapedListing.age_years`` へ渡す（UR と同じ → 課題#37）
* ⚠ **未知のクエリキーはリダイレクトで落とされる**（``?zzz=1`` を送ると
  クエリなしURLへ 302 され、応答が元と同一になる）。判定方法の妥当性の確認に使える
* **連続取得の上限は無い**（2.5秒間隔で20市区すべて正常 → §13.3）ので
  市区ローテーション（課題#36）は宣言しない
* **掲載終了は素直に 404**（→ §13.3）。ハウスコムのような伏字は無い
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlencode

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    clean_address,
    parse_area_sqm,
    parse_months_fee,
    parse_walk_minutes,
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "HOMEMATE"
BASE_URL = "https://www.homemate.co.jp"
# 賃料が安い順。⚠ **必ず付ける**（既定の「おすすめ順」は高い住戸が上に来る）。
SORT_CHEAPEST = "11"

_SPACES = re.compile(r"\s+")
# ⚠⚠ **物件番号に全角数字が混じることがある**（``C604040901１０``）。URLでは
# ``%EF%BC%91%EF%BC%90`` にエンコードされるので、``[0-9A-Za-z]+`` だと
# **エラーにならず件数が減るだけ**（実測26住戸のうち8件を落としていた）。
_DETAIL_HREF = re.compile(r"^/dtl-([^/]+)/$")
_RENT = re.compile(r"([\d.]+)\s*万円")
_MGMT = re.compile(r"共益費[：:]\s*([\d.]+)\s*万円")
_DEPOSIT = re.compile(r"敷\s*([\d.]+万円|無|なし|－)")
_KEY_MONEY = re.compile(r"礼\s*([\d.]+万円|無|なし|－)")
# ⚠⚠ **階の表記が無い住戸が実在する**（「敷5.5万円 礼5.5万円 1R 30.2m²」）。
# 階を必須にすると**エラーにならず件数が減るだけ**で、実測13住戸のうち8件を落としていた。
_SPEC = re.compile(r"(?:(\d+)\s*階\s+)?([0-9A-Za-zＳ]+)\s+([\d.]+\s*m²)")
_TOTAL_FLOORS = re.compile(r"(\d+)\s*階建")
_AGE_YEARS = re.compile(r"築\s*(\d+)\s*年")
_STATION = re.compile(r"([^\s]+?駅)まで徒歩(\d+)分")
_ADDRESS = re.compile(r"((?:東京都|北海道|(?:京都|大阪)府|[^\s]{2,3}県)[^\s]{2,24})")
_BUILDING_KIND = re.compile(r"^(アパート|マンション|一戸建て|テラスハウス|貸家)")
# 設備として扱う詳細ページの見出し。⚠ **ここに費用・案内文の項目を足さない**（→ §13.7）。
# 同じページに「その他費用」「家賃保証」「よくある質問はこちら」
# 「テーマ・条件別 賃貸物件検索」（＝他物件へのリンク）が並んでおり、混ぜると
# 他物件の条件が自分の設備として載る（D-room の §12.8 と同型）。
_FEATURE_ROWS: tuple[str, ...] = (
    "キッチン",
    "バス・トイレ",
    "室内設備",
    "建物設備・環境",
    "セキュリティー",
    "専用機能",
    "通信環境",
    "入居条件",
    "建築構造",
    "駐車場",
    "方角",
)
# ⚠ 非該当は「-」で返る。原文に載せると辞書が非該当の条件を拾う（HOMES・goo と同型）。
_EMPTY_VALUES: frozenset[str] = frozenset({"-", "－", "無し", ""})
# ⚠ 所在地は「〒123-0855東京都足立区本木南町 地図」。郵便番号と「地図」を落とさないと
#    dedup_key が他サイトと一致しない。
_POSTAL = re.compile(r"〒\s*\d{3}-?\d{4}\s*")


def _flat(text: str | None) -> str:
    return _SPACES.sub(" ", text or "").strip()


def _squash(text: str | None) -> str:
    return _SPACES.sub("", text or "")


def rent_upper_value(price_max_hint: int | None) -> str | None:
    """賃料上限（``ye``）。万円・0.5刻みへ**切り上げる**。

    ⚠ 端数をそのまま渡すと選択肢から外れる（SUUMO の ``ct`` で踏んだ罠 → 課題#29）。
    """
    if not price_max_hint:
        return None
    man = price_max_hint / 10_000
    stepped = -(-man * 2 // 1) / 2  # 0.5 刻みへ切り上げ
    return f"{stepped:g}"


def walk_minutes_from_access(access: str | None) -> int | None:
    """交通欄から駅徒歩の分数を取る。

    ⚠ **バス経由の「徒歩N分」はバス停からの徒歩**なので駅徒歩に使わない
    （UR・D-room・レオパレスで踏んだ罠）。
    """
    if not access:
        return None
    head = access.split("バス")[0]
    minutes = [int(match.group(2)) for match in _STATION.finditer(head)]
    if minutes:
        return min(minutes)
    fallback = parse_walk_minutes(head)
    return fallback


def station_info_from(access: str | None) -> str | None:
    """駅名を「◯◯駅」の形で残す。

    ⚠ **「駅」を落とさない**。落とすと ``commute/matcher`` のアンカーが消えて
    同定できなくなる（UR・D-room で踏んだ罠 → 課題#41）。
    ⚠ **路線名との間の空白も落とさない**（落とすと路線名ごと駅名になる → §12.3）。
    """
    if not access:
        return None
    return access or None


class HomemateScraper:
    """ホームメイト賃貸の取得と解析。"""

    site_code = SITE_CODE
    # 市区ページ（/pr-tokyo/13121/）が掲載一覧。都道府県ページは索引
    requires_city = True
    # ⚠ 市区の検索値は JIS5桁そのもの（スラグ収集が要らない）
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    ignore_robots = False
    # 連続取得の上限は実測で見つからなかった（2.5秒間隔で20市区すべて正常 → §13.3）
    city_rotation_limit = None
    # MUST をサイト側へ渡す（→ ADR 0015）。面積下限だけを ``m_site_search_params`` で扱う。
    # ⚠ ekiw（駅徒歩）は選択肢が15分までなので配線しない（→ §13.9）
    supports_site_filters = True

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/pr-{都道府県}/{JIS5桁}/?so=11&ye={賃料上限}`` を組み立てる。"""
        search = pattern.search  # type: ignore[attr-defined]
        base_params: list[tuple[str, str]] = [("so", SORT_CHEAPEST)]
        if rent_upper := rent_upper_value(search.price_max_hint):
            base_params.append(("ye", rent_upper))

        urls: list[str] = []
        for area in areas:
            pref_slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref_slug:
                raise ValueError(f"HOMEMATE: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                continue
            urls.append(f"{BASE_URL}/pr-{pref_slug}/{area.value}/?{urlencode(base_params)}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りは ``?pg=N``（**1始まり**。重なり0件を実測 → §13.3）。"""
        return f"{base_url}{query_separator(base_url)}pg={page}"

    def is_last_page(self, count: int) -> bool:
        """最終ページを超えると住戸0件になる。"""
        return count == 0

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for section in doc.cssselect("section.m_prpty_list_item"):
            listings.extend(self._parse_section(section))
        return listings

    def _parse_section(self, section) -> list[ScrapedListing]:
        """棟1つを住戸へ展開する。"""
        text = _flat(section.text_content())
        building_name = self._building_name(section)
        access = self._access(section)
        address_match = _ADDRESS.search(text)
        # ⚠ 住所は空白を落として他サイトと表記を揃える（dedup_key を一致させるため）。
        #    ⚠ **交通欄の空白は落とさない**（落とすと路線名ごと駅名になる → §12.3）
        address = clean_address(_squash(address_match.group(1))) if address_match else None
        total_floors = _TOTAL_FLOORS.search(text)
        age_years = _AGE_YEARS.search(text)
        listings: list[ScrapedListing] = []
        for room in section.cssselect(".m_prpty_list_room"):
            listing = self._parse_room(
                room,
                address=address,
                access=access,
                total_floors=int(total_floors.group(1)) if total_floors else None,
                age_years=int(age_years.group(1)) if age_years else None,
                building_name=building_name,
            )
            if listing is not None:
                listings.append(listing)
        return listings

    def _access(self, section) -> str | None:
        """交通欄を **DOM から**取る（``ul...._access`` の**1つ目の** ``li``）。

        ⚠⚠ **本文から「◯◯駅まで徒歩N分」を探して切り出してはいけない**（→ §13.10）。
        バス便はこの形を持たず、
        「東武バス 東金町五丁目停まで徒歩3分、バス乗車してＪＲ常磐線 金町駅まで17分」
        と書かれるため、**交通欄ごと落ちて駅が同定できず通勤時間が unknown になる**。
        実測で236掲載中45件（19%）がこれだった。⚠ **例外にならないので気づけない**
        （交通欄が取れた191件の同定率は98.4%で、一見すると健全に見える）。

        ⚠ **2つ目の ``li`` は住所**なので混ぜない。
        ⚠ **駅名の直前の空白を残す**。落とすと ``commute/matcher`` が路線名ごと
        駅名にしてしまい、実在しない駅名になって同定できない（→ §12.3）。
        """
        for block in section.cssselect("ul.m_prpty_list_item_main_info_access"):
            items = block.cssselect("li")
            if items and (access := _flat(items[0].text_content())):
                return access
        return None

    def _building_name(self, section) -> str | None:
        for head in section.cssselect("h2, h3, .m_prpty_list_ttl, .m_prpty_list_bill_ttl"):
            name = _flat(head.text_content())
            if name:
                return _BUILDING_KIND.sub("", name).strip() or None
        return None

    def _parse_room(
        self,
        room,
        *,
        address: str | None,
        access: str | None,
        total_floors: int | None,
        age_years: int | None,
        building_name: str | None,
    ) -> ScrapedListing | None:
        detail_path = self._detail_path(room)
        if detail_path is None:
            return None
        matched_id = _DETAIL_HREF.match(detail_path)
        if matched_id is None:
            return None
        text = _flat(room.text_content())
        rent = _RENT.search(text)
        spec = _SPEC.search(text)
        if not (rent and spec):
            return None
        price = int(float(rent.group(1)) * 10_000)
        mgmt = _MGMT.search(text)
        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=matched_id.group(1),
            url=f"{BASE_URL}{detail_path}",
            title=building_name,
            price=price,
            # ⚠ 「共益費：－」は 0円の意味（SUUMO の「-」と同じ）。None にすると
            #    rent_total が「管理費不明」になり MUST が unknown へ落ちる
            mgmt_fee_monthly=int(float(mgmt.group(1)) * 10_000) if mgmt else 0,
            deposit_amount=parse_months_fee(self._fee(text, _DEPOSIT), price),
            key_money_amount=parse_months_fee(self._fee(text, _KEY_MONEY), price),
            area_sqm=parse_area_sqm(spec.group(3)),
            layout=spec.group(2),
            floor_num=int(spec.group(1)) if spec.group(1) else None,
            total_floors=total_floors,
            age_years=age_years,
            address=address,
            station_info=station_info_from(access),
            walk_minutes=walk_minutes_from_access(access),
        )

    def _fee(self, text: str, pattern: re.Pattern[str]) -> str | None:
        """敷金・礼金の原文。

        ⚠ **「無」をここで 0 に置き換えない。** ``parse_months_fee`` が「無」を
        0円として解釈できる一方、文字列 ``"0"`` は解釈できず ``None`` を返す
        （＝金額不明になる）。原文のまま渡す。
        """
        matched = pattern.search(text)
        return matched.group(1) if matched else None

    def _detail_path(self, room) -> str | None:
        for link in room.cssselect("a[href]"):
            href = link.get("href") or ""
            if _DETAIL_HREF.match(href):
                return href
        return None

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文を取り出す。"""
        doc = lxml_html.fromstring(html_text)
        rows = self._detail_rows(doc)
        return ScrapedDetail(
            raw_features_text="、".join(self._detail_features(rows)) or None,
            address=self._detail_address(rows.get("所在地") or rows.get("住所")),
        )

    def _detail_address(self, value: str | None) -> str | None:
        """所在地から郵便番号と「地図」を落とす。"""
        if not value:
            return None
        cleaned = _POSTAL.sub("", value).replace("地図", "")
        return clean_address(_squash(cleaned))

    def _detail_rows(self, doc) -> dict[str, str]:
        rows: dict[str, str] = {}
        for table in doc.cssselect("table"):
            for row in table.cssselect("tr"):
                heads = row.cssselect("th")
                cells = row.cssselect("td")
                if heads and cells:
                    rows.setdefault(
                        _squash(heads[0].text_content()), _flat(cells[0].text_content())
                    )
        for dl in doc.cssselect("dl"):
            for term, value in zip(dl.cssselect("dt"), dl.cssselect("dd"), strict=False):
                rows.setdefault(_squash(term.text_content()), _flat(value.text_content()))
        return rows

    def _detail_features(self, rows: dict[str, str]) -> list[str]:
        """設備の行だけを集める。

        ⚠⚠ **見出しを明示的に選ぶ**（→ §13.7）。同じページに「その他費用」
        「家賃保証」「よくある質問はこちら」「テーマ・条件別 賃貸物件検索」（＝他物件への
        リンク）が並んでおり、拾う側を絞らないと**他物件の条件が自分の設備として載る**
        （D-room の「同じエリアの似た物件」→ §12.8 と同型）。
        """
        seen: list[str] = []
        for key in _FEATURE_ROWS:
            value = rows.get(key, "")
            if value in _EMPTY_VALUES or value in seen:
                continue
            seen.append(value)
        return seen

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """掲載終了は素直に 404（→ §13.3）。"""
        return fetcher.get(url).status_code == 404
