"""SUUMO 一戸建て（新築・中古）の取得と解析（→ 課題#4 手順8）。

⚠ **一覧の構造は中古マンションと同じ**（``div.property_unit``・``?page=N``・
``nc_`` のID）ので、掲載ブロックの読み取りと詳細の仕様表は
:mod:`house_search.scrape.suumo_buy` の公開関数を共用する。
別々に書くと片方を直したときもう片方が黙って古くなる。

⚠⚠ **中古マンションと決定的に違うのは次の3点**（2026-09-07 実測・八王子市）。

1. **土地面積・建物面積の2軸**になる。⚠ ``area_sqm``（専有面積）は使わない
   （→ 要件定義書 §5.3）
2. ⚠⚠ **バス便が過半数**（中古 11/20・新築 17/20）。
   ``ＪＲ中央線「八王子」バス18分停歩5分`` の「停歩5分」は**バス停からの徒歩**で、
   駅徒歩にすると ``walk_minutes_max`` を不当に通過する（→ 課題#58）。
   さらに ⚠ **バス停名も鉤括弧に入る**（``京王バス「館ヶ丘団地」徒歩1分``）ので、
   駅として変換すると ``館ヶ丘団地駅`` という**実在しない駅名**になり、
   マスタに当たらず**通勤時間が unknown になるだけで例外にならない**
   （→ 課題#41・D-room）
3. **新築は分譲地（全5区画）単位**で、価格・土地面積・建物面積・間取りが
   すべてレンジまたは中黒列挙になる（``4960万円～4980万円`` /
   ``120.17m2・120.18m2`` / ``3LDK・3LDK+S（納戸）``）
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_MAPPING, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    age_years_from_built,
    clean_address,
    parse_area_sqm,
    parse_built_on,
    parse_walk_minutes,
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI
from house_search.scrape.suumo_buy import (
    features_text,
    list_fields,
    read_spec_table,
)
from house_search.scrape.suumo_shinchiku import parse_price_range

SITE_CODE = "SUUMO"
BASE_URL = "https://suumo.jp"
# 1ページ20件（実測 2026-09-07・中古586件/新築1,007件の八王子市で確認）
PAGE_SIZE = 20

#: バス会社名＋鉤括弧のバス停名（``京王バス「館ヶ丘団地」``）。
#: ⚠ 駅の鉤括弧（``ＪＲ中央線「八王子」``）と**同じ形**なので、
#: 落としてからでないと駅名として変換してしまう。
_BUS_STOP_QUOTED = re.compile(r"[^\s「]*バス「[^」]*」")


def station_info(value: str | None) -> str | None:
    """交通欄を駅同定できる形へ整える。

    ⚠ **鉤括弧の「前に空白」と「後ろに駅」の両方が要る**（→ 課題#41）。
    中古マンションと同じだが、⚠ **戸建てはバス停名も鉤括弧に入る**ので
    先にバス停を落とす。

    ⚠ **駅の鉤括弧が1つも残らなければ None を返す。** 残った
    ``徒歩1分`` のような断片を渡すと、駅名の第2パスが「徒」のような
    語を拾ってしまう（→ 課題#58 の「第1パスが取れたら第2パスは使わない」）。
    """
    if not value:
        return None
    stripped = _BUS_STOP_QUOTED.sub(" ", value)
    if "「" not in stripped:
        return None
    text = stripped.replace("「", " 「").replace("」", "」駅 ")
    return " ".join(text.split()) or None


def walk_minutes_of(value: str | None) -> int | None:
    """駅からの徒歩分。**バス便は採らない**。

    ⚠ ``ＪＲ中央線「八王子」バス18分停歩5分`` の「停歩5分」は
    **バス停からの徒歩**で、駅徒歩として採ると ``walk_minutes_max`` を
    不当に通過する（→ 課題#58・UR・ホームメイト・D-room で踏んだ罠）。
    ⚠ **実測で中古 11/20・新築 17/20 がバス便**なので、
    戸建てではこれが多数派である。
    """
    if not value or "バス" in value:
        return None
    return parse_walk_minutes(value)


def _external_id(href: str) -> str | None:
    """詳細URLから物件ID（``nc_21516154``）を取り出す。"""
    for part in href.split("/"):
        if part.startswith("nc_") and part[3:].isdigit():
            return part
    return None


class _SuumoKodateScraper:
    """SUUMO 一戸建ての共通実装。種別ごとの差は下のサブクラスが宣言する。"""

    site_code = SITE_CODE
    # SEOパスは市区まで含めた形でしか測っていない（robots が
    # ``/jj/bukken/ichiran/`` を禁じているのでこの経路しかない → 課題#4）
    requires_city = True
    # ⚠ 賃貸は JIS5桁だが、売買は SEOパスなのでスラグを引く
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False
    city_rotation_limit = None
    # ⚠ **サイト側MUST は1軸も測っていないので送らない**（→ ADR 0015・課題#29）。
    # 同じサイト・同じキー名でも種別が違えば選択肢は別物で、推測で書くと
    # 「0件になる／黙って無視される／向きが逆」のいずれかになり**どれも例外にならない**
    supports_site_filters = False

    #: SEOパス。``{pref}`` は都道府県ローマ字、``{city}`` は ``sc_`` スラグ
    list_path = ""
    #: 新築（分譲地）は価格・面積・間取りがレンジになる
    is_new_build = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """対象エリアから一覧ページ（1ページ目）のURLを組み立てる。"""
        urls: list[str] = []
        for area in areas:
            pref = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref:
                raise ValueError(f"SUUMO戸建て: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                # スラグの無い市区は取りに行けない（``resolve_areas`` が落とす）
                continue
            urls.append(BASE_URL + self.list_path.format(pref=pref, city=area.value))
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """一覧URLへページ番号を付ける（``?page=N``・**1始まり**）。"""
        if page <= 1:
            return base_url
        return f"{base_url}{query_separator(base_url)}page={page}"

    def is_last_page(self, count: int) -> bool:
        """1ページに満たない件数しか返らなければ最終ページ。"""
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。

        ⚠ **``property_unit--osusume`` を除外してはいけない**（中古マンションと同じ）。
        中身は通常の掲載で、除外すると大半が消える（件数が減るだけでエラーにならない）。
        ⚠ **``nc_`` を本文への正規表現で拾わない。** 一覧の外の別枠が混ざる。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for unit in doc.cssselect("div.property_unit"):
            links = unit.cssselect("h2.property_unit-title a")
            if not links:
                continue
            href = links[0].get("href") or ""
            external_id = _external_id(href)
            if not external_id:
                continue
            # ⚠ **新築一戸建ての索引には土地（``/tochi/``）の掲載が混ざる**
            #   （建築条件付き土地）。種別が違うので取り込まない
            if "/tochi/" in href:
                continue
            fields = list_fields(unit)
            access = fields.get("沿線・駅")
            price_min, price_max = parse_price_range(fields.get("販売価格"))
            listings.append(
                ScrapedListing(
                    site_code=SITE_CODE,
                    external_id=external_id,
                    url=BASE_URL + href if href.startswith("/") else href,
                    # ⚠ **物件名が無い掲載がある**（実測で中古 4/20）。
                    #   必須にすると黙って落ちる
                    title=fields.get("物件名"),
                    # レンジは下限を ``price`` に入れる（→ 要件定義書 §11.4）
                    price=price_min,
                    price_min=price_min if self.is_new_build else None,
                    price_max=price_max if self.is_new_build else None,
                    # ⚠ **戸建てに ``area_sqm`` を使わない**（→ 要件定義書 §5.3）。
                    #   ``parse_area_sqm`` は中黒・レンジとも**先頭（下限）**を採る
                    land_area_sqm=parse_area_sqm(fields.get("土地面積")),
                    building_area_sqm=parse_area_sqm(fields.get("建物面積")),
                    # ⚠ ``3LDK・3LDK+S（納戸）`` は MUST で **unknown** になる
                    #   （レンジ内に対象が実在するか断定できない → 課題#4）
                    layout=fields.get("間取り") or None,
                    # ⚠ 新築の一覧に築年月は無い（完成時期は詳細ページ）
                    age_years=age_years_from_built(fields.get("築年月")),
                    address=clean_address(fields.get("所在地")),
                    station_info=station_info(access),
                    walk_minutes=walk_minutes_of(access),
                )
            )
        return listings

    def detail_url(self, listing_url: str) -> str:
        """一覧のリンクをそのまま使う。"""
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページHTMLから追加情報を取り出す。

        ⚠ 見出しクラスは中古マンションと同じ **``secTitleInnerR``**
        （新築マンションだけが ``secTitleInnerK`` → 課題#4 手順6-1）。
        流用を間違えると**設備原文が空になるだけで例外にならない**。
        """
        doc = lxml_html.fromstring(html_text)
        values = read_spec_table(doc)

        access = values.get("交通")
        return ScrapedDetail(
            raw_features_text=features_text(doc),
            # ⚠ **括弧が全角と半角の2種類ある**（同じページに両方が出る）
            built_on=parse_built_on(
                values.get("完成時期（築年月）") or values.get("完成時期(築年月)")
            ),
            # ⚠ 戸建てに管理費・修繕積立金は無い（マンションの項目）
            address=clean_address(values.get("所在地")),
            walk_minutes=walk_minutes_of(access),
            # ⚠ 接道・建ぺい率・用途地域は表記揺れが激しく正規化が未確立なので
            #   JSONB へ入れる（列にすると ``t_listings`` の再作成が要る
            #   → 要件定義書 §11.3）
            type_specific_attrs={
                key: values[key]
                for key in (
                    "土地の権利形態",
                    "私道負担・道路",
                    "建ぺい率・容積率",
                    "用途地域",
                    "地目",
                    "構造・工法",
                    "総戸数",
                )
                if values.get(key)
            },
        )

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """掲載終了は **HTTP 404**（実測 2026-09-07）。

        ⚠ 賃貸の ``_SOLD_MARKERS``（本文の文言）は流用しない。
        """
        response = fetcher.get(url)
        return response.status_code == 404


class SuumoChukoKodateScraper(_SuumoKodateScraper):
    """SUUMO 中古一戸建て。1掲載＝1戸。"""

    property_type = "CHUKO_KODATE"
    list_path = "/chukoikkodate/{pref}/{city}/"


class SuumoShinchikuKodateScraper(_SuumoKodateScraper):
    """SUUMO 新築一戸建て。

    ⚠ **1掲載＝分譲地（全5区画）**で、価格・土地面積・建物面積・間取りが
    すべてレンジまたは中黒列挙になる。新築マンションの「棟」と同じ粒度。
    """

    property_type = "SHINCHIKU_KODATE"
    list_path = "/ikkodate/{pref}/{city}/"
    is_new_build = True
