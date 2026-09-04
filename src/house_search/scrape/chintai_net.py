"""CHINTAI.net のサイトアダプタ。

一覧URLは ``/{都道府県スラグ}/area/{JIS5桁}/list/``。
実測は詳細設計書 §14（2026-09-04・課題#37 のチェックリスト10項目）。

CHINTAI.net 固有の注意点:

* ⚠⚠ **一覧に返る住戸は実行のたびに入れ替わる**（→ §14.3）。同じURLを続けて取っても
  中身が違う（住戸50〜52件・面積の範囲も動く）。そのため GOO・APAMAN・ATHOME で
  使った「返る掲載の中身でフィルタの効きを確かめる」（→ §13.9）が**このサイトでは
  使えない**。**総件数（本文の「N件」）だけが安定した判定材料**だった。
  ⚠ 並び順（``o``）を指定しても揺れは止まらない。``o=2``（家賃が安い順）だけは
  決定的だが**棟あたり最安1住戸しか返さず母集団が半減する**（ハウスコムの
  ``?sort=0`` → §13.4 と同じ挙動）ので、**並び順は指定しない**。
  ⚠ 揺れるおかげで繰り返し実行すれば母集団を舐められる（SMOCCA → 課題#22 と同じ扱い）が、
  **「新着を確実に拾う」ことはできない**。
* ⚠⚠ **``.cassette_item`` は「棟」で、住戸は ``tbody[data-bkkey]``**（→ §14.5）。
  棟だけ数えると23件になり、**住戸49件のうち26件を黙って落とす**
  （D-room で母集団が 334 → 98 に化けたのと同じ罠 → §12.3）。
  住戸のデータは ``tbody`` の内側の hidden input（``bkName`` / ``chinRyo`` /
  ``madori`` / ``senMenseki`` / ``ekiName`` / ``ekiToho``）にあり、
  **D-room のような突き合わせは要らない**。
* ⚠ **PR枠（``.cassette_detail_pr``）は取り込まない**（→ §14.5）。
  ``tbody[data-bkkey]`` を持たず構造が違ううえ、``data-detailurl`` が
  **``?vm=0`` を含み robots.txt が ``/detail/*/?vm=`` を禁じている**。
* ⚠⚠ **詳細の ``.detail_specTable`` を設備原文に使ってはいけない**（→ §14.6）。
  用語集の解説文が本文に展開されており、「システムキッチン…**レンジフード**…」
  「**IHクッキングヒーター**よりも火力が強い」のように**その住戸に無い設備名**が出てくる。
  辞書照合は本文全体への部分一致なので**設備数が黙って水増しされる**
  （HOMES の ``sr-only`` と同型）。``.mod_equipmentBox`` のタグ列だけを使う。
* ⚠ **都道府県ページ ``/tokyo/list/`` は HTTP 404**。市区必須。
* 市区の検索値は **JIS5桁そのもの**（D-room・ホームメイトと同じ）。スラグ収集が要らない。
* **連続取得の上限は無い**（2.5秒間隔で20市区すべて正常 → §14.1）ので
  市区ローテーション（課題#36）は宣言しない。
* 掲載終了は **HTTP 404**（D-room・ハウスコムと違い素直 → §14.1）。
* ⚠ robots.txt が ``User-agent: *`` を**2グループ**持つ。課題#43 の修正
  （``merge_robots_groups``）が入っていないと ``/api/`` や ``/list/?b=`` を
  許可と誤判定する。実装で使う ``/{pref}/area/{jis}/list/`` 系は許可されている。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    clean_address,
    parse_area_sqm,
    parse_built_on,
    parse_floor,
    parse_months_fee,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "CHINTAI_NET"
BASE_URL = "https://www.chintai.net"

_SPACES = re.compile(r"\s+")

# 賃料上限 ``ct`` の選択肢。一覧ページの検索フォームから採った（→ §14.4）。
#
# ⚠⚠ **値の単位は「千円」であって万円ではない**（option が `30=3万円` / `130=13万円`）。
# 万円だと思って `13` を送ると選択肢外なので黙って無視され、
# **絞れていないのに絞れたつもりになる**（実測で `ct=13` は基準と同じ 11,109件）。
# ⚠ SUUMO の `ct` のように0件にはならないぶん、かえって気づきにくい。
CT_CHOICES_SEN: tuple[int, ...] = (
    *range(30, 100, 5),  # 3万円〜9.5万円（0.5万円刻み）
    *range(100, 210, 10),  # 10万円〜20万円（1万円刻み）
    250,  # 25万円
    300,  # 30万円
    400,
    500,
    1000,  # 100万円
)
# 管理費は「11.8万円10,000円」の後半。⚠ 「32.5万円--」のように `--` のこともある。
_MGMT_FEE = re.compile(r"([\d,]+)\s*円")
# 「1993年08月（築33年）」から築年月を取る。
_BUILT_ON = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


def _flat(text: str | None) -> str:
    return _SPACES.sub(" ", text or "").strip()


def _squash(text: str | None) -> str:
    return _SPACES.sub("", text or "")


def rent_max_code(price_max_hint: int | None) -> str | None:
    """``price_max_hint``（円）を ``ct`` の選択肢へ**切り上げる**。

    ⚠⚠ **選択肢の単位は千円**（13万円 = ``ct=130``）。万円と取り違えると
    ``ct=13`` を送ることになり、選択肢外なので**黙って無視される**。
    ⚠ **上限なので切り上げる。** 切り下げると MUST を通る掲載を
    サイト側で落としてしまい、ADR 0015 の不変条件を破る。
    ⚠ 選択肢の最大（100万円）を超えるときは送らない（絞る意味が無いため）。
    """
    if not price_max_hint or price_max_hint <= 0:
        return None
    sen = price_max_hint / 1_000
    for choice in CT_CHOICES_SEN:
        if choice >= sen:
            return str(choice)
    return None


def walk_minutes_from_access(access: str | None) -> int | None:
    """交通欄から駅徒歩の分数を取る（複数駅のうち最短）。

    ⚠ **バス経由の「徒歩N分」はバス停からの徒歩**なので駅徒歩に使わない
    （UR・D-room・レオパレス・ハウスコムで踏んだ罠）。「バス」より前だけを見る。
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


class ChintaiNetScraper:
    """CHINTAI.net 賃貸の取得と解析。"""

    site_code = SITE_CODE
    # ⚠ 都道府県ページ（/tokyo/list/）は HTTP 404（→ §14.1）
    requires_city = True
    # 市区の検索値は JIS5桁そのもの。スラグ収集が要らない
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    ignore_robots = False
    # 連続取得の上限は実測で見つからなかった（2.5秒間隔で20市区すべて正常 → §14.1）
    city_rotation_limit = None
    # MUST をサイト側へ渡す（→ ADR 0015）。面積・賃料・駅徒歩・間取りの4軸すべて効く
    supports_site_filters = True

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """``/{都道府県}/area/{JIS5桁}/list/`` を組み立てる。

        ⚠ **並び順（``o``）は指定しない**（→ §14.3）。既定でも新着順でも一覧は揺れ、
        決定的な ``o=2``（安い順）は棟あたり最安1住戸しか返さず母集団が半減する。

        賃料上限（``ct``）はここで ``price_max_hint`` から組み立てる
        （ATHOME の ``PRICETO``・レオパレスの ``rentTo`` と同じ扱いで、
        正典YAML には載せない）。
        """
        search = pattern.search  # type: ignore[attr-defined]
        query = ""
        if code := rent_max_code(search.price_max_hint):
            query = f"?ct={code}"

        urls: list[str] = []
        for area in areas:
            pref_slug = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref_slug:
                raise ValueError(f"CHINTAI_NET: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                continue
            urls.append(f"{BASE_URL}/{pref_slug}/area/{area.value}/list/{query}")
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """ページ送りは**パス形式** ``list/page{N}/``（2始まり）。

        ⚠ **クエリの前に入れる**（``/list/page2/?sf=30`` が正しく2ページ目を返すことを
        実測済み → §14.1）。クエリ側に付けると robots の ``/list/?`` 系の制約に
        近づくうえ、そもそもページ送りとして効かない。
        """
        if page <= 1:
            return base_url
        head, sep, query = base_url.partition("?")
        if not head.endswith("/"):
            head += "/"
        return f"{head}page{page}/{sep}{query}"

    def is_last_page(self, count: int) -> bool:
        """最終ページを超えると住戸0件になるので、**0件になったら終わり**とする。"""
        return count == 0

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載（住戸）を取り出す。

        ⚠ **走査の単位は棟（``.cassette_item``）ではなく住戸（``tbody[data-bkkey]``）**。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for tbody in doc.cssselect("tbody[data-bkkey]"):
            listing = self._parse_room(tbody)
            if listing is not None:
                listings.append(listing)
        return listings

    def _building(self, tbody):
        """住戸が属する棟（``.cassette_item``）を辿る。"""
        node = tbody
        for _ in range(8):
            node = node.getparent()
            if node is None:
                return None
            if "cassette_item" in (node.get("class") or ""):
                return node
        return None

    def _cell_value(self, key: str, cell) -> str:
        """情報テーブルのセルを文字列にする。

        ⚠⚠ **住所だけは「セルの直接テキスト」を採る。** 住所のセルには地図への導線が
        子要素として同居しており（一覧は ``周辺地図``、詳細は
        ``地図で物件の周辺環境をチェック！``）、``text_content()`` で取ると
        住所の末尾に惹句が繋がる。``dedup_key`` は住所から作るので
        **名寄せが黙って失敗する**（例外にならず、グループ化されないだけ）。
        ``clean_address`` の文言リストを増やして追うより、リンクを含まない
        直接テキストを採るほうが確実。
        """
        if key == "住所" and (cell.text or "").strip():
            return cell.text.strip()
        return _flat(cell.text_content())

    def _table_info(self, rows) -> dict[str, str]:
        """``th`` / ``td`` が交互に並ぶ情報テーブルを辞書にする。"""
        info: dict[str, str] = {}
        for row in rows:
            cells = row.cssselect("th, td")
            for index in range(0, len(cells) - 1, 2):
                if cells[index].tag != "th":
                    continue
                key = _squash(cells[index].text_content())
                if key and key not in info:
                    info[key] = self._cell_value(key, cells[index + 1])
        return info

    def _building_info(self, building) -> dict[str, str]:
        """棟の情報テーブル（住所・交通・築年・階建・構造）を辞書にする。"""
        if building is None:
            return {}
        return self._table_info(building.cssselect(".bukken_information tr"))

    def _parse_room(self, tbody) -> ScrapedListing | None:
        external_id = (tbody.get("data-bkkey") or "").strip()
        if not external_id:
            return None
        detail_url = (tbody.get("data-detailurl") or "").strip()
        if not detail_url:
            detail_url = f"/detail/bk-{external_id}/"
        # ⚠ 一覧の detailurl にクエリが付くことがある。robots.txt が
        #   `/detail/*/?vm=` を禁じているので**クエリは落とす**
        detail_url = detail_url.split("?")[0]

        values = self._hidden_values(tbody)
        rent = self._rent(values, tbody)
        building = self._building(tbody)
        info = self._building_info(building)
        # ⚠ 交通欄は複数駅がスペース区切りで並ぶ。**空白を落とさない**
        #   （落とすと matcher が路線名ごと駅名にする → D-room §12.3）
        access = info.get("交通")
        row = tbody.cssselect("tr.detail-inner")
        cells = row[0] if row else tbody

        return ScrapedListing(
            site_code=SITE_CODE,
            external_id=external_id,
            url=BASE_URL + detail_url,
            title=values.get("bkName"),
            price=rent,
            mgmt_fee_monthly=self._mgmt_fee(cells),
            deposit_amount=self._other_price(cells, 0, rent),
            key_money_amount=self._other_price(cells, 1, rent),
            area_sqm=parse_area_sqm(f"{values.get('senMenseki') or ''}㎡"),
            layout=values.get("madori"),
            floor_num=self._floor(cells),
            total_floors=parse_total_floors(info.get("階建")),
            address=clean_address(_squash(info.get("住所"))),
            station_info=access,
            walk_minutes=walk_minutes_from_access(access),
        )

    def _rent(self, values: dict[str, str], tbody) -> int | None:
        """賃料（円）。

        ⚠⚠ **hidden input の ``chinRyo`` は「118000」という円の生値**で、
        ``parse_yen`` は「円」の文字を要求するため**読めない**。
        素朴に ``parse_yen(values["chinRyo"])`` と書くと **49件すべて price が None**
        になり、``rent_total`` が NULL → MUST が ``unknown`` へ落ちて
        ``unknown_policy: keep`` の下で**賃料不明の掲載がランキングに並ぶ**
        （UR の割引住戸で踏んだのと同じ形 → 課題#37）。⚠ **例外にならない。**

        表示の ``td.price``（「11.8万円」）は万円単位で丸められているので、
        生値のほうを正典にし、無いときだけ表示から読む。
        """
        raw = values.get("chinRyo") or ""
        if raw.isdigit():
            return int(raw)
        found = tbody.cssselect("td.price span.num")
        return parse_yen(f"{_squash(found[0].text_content())}万円") if found else None

    def _hidden_values(self, tbody) -> dict[str, str]:
        """住戸の hidden input を辞書にする。"""
        values: dict[str, str] = {}
        for name in ("bkName", "chinRyo", "madori", "senMenseki", "ekiName", "ekiToho"):
            found = tbody.cssselect(f"input.{name}")
            if found:
                value = (found[0].get("value") or "").strip()
                if value:
                    values[name] = value
        return values

    def _mgmt_fee(self, cells) -> int | None:
        """管理費。「11.8万円10,000円」の後半を読む。

        ⚠ **「32.5万円--」のように ``--`` のことがある。** SUUMO の「-」と同じく
        0円として扱う（→ CLAUDE.md）。⚠ 「非公開」の意味かは**測れていない**。
        """
        found = cells.cssselect("td.price")
        if not found:
            return None
        text = _squash(found[0].text_content())
        # 「11.8万円」の万円表記を落としてから円表記を探す
        tail = re.sub(r"^[\d.]+万円", "", text)
        if not tail or set(tail) <= {"-", "‐", "―", "−"}:
            return 0
        matched = _MGMT_FEE.search(tail)
        return int(matched.group(1).replace(",", "")) if matched else None

    def _other_price(self, cells, index: int, rent: int | None) -> int | None:
        """敷金（index=0）・礼金（index=1）。

        ⚠ **「1ヶ月」形式が最多**（実測 `1ヶ月1ヶ月` 167件・`なし1ヶ月` 77件）で、
        実額（`135,000円`）も混じる。``parse_months_fee`` が両方を読む。
        """
        found = cells.cssselect("td.other_price")
        if not found:
            return None
        parts = [_squash(span.text_content()) for span in found[0].cssselect("span")]
        if index >= len(parts):
            return None
        return parse_months_fee(parts[index], rent)

    def _floor(self, cells) -> int | None:
        """所在階。⚠ 「2階即入居可」のように**惹句が続く**ことがある。"""
        found = cells.cssselect("td.floar")
        if not found:
            return None
        return parse_floor(_squash(found[0].text_content()))

    def detail_url(self, listing_url: str) -> str:
        """一覧で得たURLをそのまま詳細URLに使う。

        ⚠ ``ScrapedListing.url`` は既に ``/detail/bk-{bkkey}/`` の形で、
        robots が禁じるクエリ（``?vm=``）も落としてある。
        """
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページから設備原文と築年を取る。

        ⚠⚠ **``.detail_specTable`` は使わない**（→ §14.6）。用語集の解説文が
        本文に展開されており、**その住戸に無い設備名**（レンジフード・
        IHクッキングヒーター）が辞書に拾われる。
        """
        doc = lxml_html.fromstring(html_text)
        features = [_flat(box.text_content()) for box in doc.cssselect(".mod_equipmentBox")]
        raw_features = "\n".join(part for part in features if part) or None

        info = self._detail_info(doc)
        built_on = None
        if age := info.get("築年"):
            matched = _BUILT_ON.search(age)
            if matched:
                built_on = parse_built_on(f"{matched.group(1)}年{matched.group(2)}月")
        return ScrapedDetail(
            raw_features_text=raw_features,
            built_on=built_on,
            floor_num=parse_floor(info.get("物件階層")),
            address=clean_address(_squash(info.get("住所"))),
        )

    def _detail_info(self, doc) -> dict[str, str]:
        """詳細ページの「物件概要」テーブルを辞書にする。"""
        return self._table_info(doc.cssselect("table tr"))

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """掲載終了は **HTTP 404**（→ §14.1）。

        ⚠ ``fetcher.get`` は 404 を例外にせず ``response`` を返す（→ 課題#25）。
        """
        response = fetcher.get(url)
        return response.status_code == 404
