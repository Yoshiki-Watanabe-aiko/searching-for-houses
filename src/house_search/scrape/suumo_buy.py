"""SUUMO 中古マンション（売買）の取得と解析（→ 課題#4・Phase 6）。

⚠⚠ **一覧は SEOパスでしか取れない。** robots.txt が検索フォームの action
（``/jj/bukken/ichiran/JJ010FJ001/``）と ``JJ012FC001/`` を**明示的に禁じている**。
賃貸が ``/jj/chintai/ichiran/`` なので売買も ``/jj/bukken/ichiran/`` だろう、
という連想は**そこがピンポイントで禁止されている**ので誤り（2026-09-06 実測）。

⚠ そのため**市区はスラグで指定する**（賃貸は JIS5桁）。``m_city_site_values`` に
行が無い市区は ``resolve_areas`` が**黙って落とす**ので、収集が前提になる
（→ 課題#4「SEOパス方式には市区スラグの収集が前提作業として要る」）。

⚠ **クエリの選択肢は賃貸と別体系。** ``md`` は売買では部屋数だけ（``2`` が
2K・2DK・2LDK をまとめて指す）で、賃貸の ``md=04``（1LDK）は存在しない値。
サイト側フィルタは実測値を丸めごと配線してから有効にする（→ ADR 0015）。
"""

from __future__ import annotations

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
    parse_yen,
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

SITE_CODE = "SUUMO"
BASE_URL = "https://suumo.jp"
# 1ページ20件（実測 2026-09-06）。賃貸の 30 とは違う
PAGE_SIZE = 20
# 中古マンションのSEOパス。``{pref}`` は都道府県ローマ字、``{city}`` は ``sc_`` スラグ
LIST_PATH = "/ms/chuko/{pref}/{city}/"


def _fields(unit) -> dict[str, str]:
    """掲載ブロックの定義リストを「ラベル → 値」にする。

    ⚠ ``dl`` に class は付いていない（``dottable-line`` のような名前で探すと0件）。
    """
    values: dict[str, str] = {}
    for dl in unit.cssselect("dl"):
        labels = dl.cssselect("dt")
        data = dl.cssselect("dd")
        if not labels or not data:
            continue
        key = " ".join(labels[0].text_content().split())
        if key and key not in values:
            values[key] = " ".join(data[0].text_content().split())
    return values


def _label(text: str) -> str:
    """見出しセルの文字列をラベルへ正規化する。

    ⚠ **``th`` にはツールチップの「ヒント」が同居する**（``管理費 ヒント``）。
    ``text_content()`` は子孫を全部拾うので、落とさないと引けない
    （課題#44 の「見出しの語が値に混入する」の裏返し）。
    """
    return " ".join(text.split()).removesuffix("ヒント").strip()


def _external_id(href: str) -> str | None:
    """詳細URLから物件ID（``nc_21457575``）を取り出す。"""
    for part in href.split("/"):
        if part.startswith("nc_") and part[3:].isdigit():
            return part
    return None


def _station_info(value: str | None) -> str | None:
    """交通欄を駅同定できる形へ整える。

    ⚠ **鉤括弧の「前に空白」と「後ろに駅」の両方が要る**（→ 課題#41・D-room）。
    ``東京メトロ丸ノ内線「淡路町」徒歩2分`` をそのまま渡すと駅が1件も取れず、
    ``東京メトロ丸ノ内線「淡路町」駅 徒歩2分`` では**路線名ごと駅名**になる。
    どちらも**マスタに当たらず通勤時間が unknown になるだけで例外にならない**。
    """
    if not value:
        return None
    text = value.replace("「", " 「").replace("」", "」駅 ")
    return " ".join(text.split())


def _walk_minutes(value: str | None) -> int | None:
    """駅からの徒歩分。**バス便は採らない**。

    ⚠ ``京王高尾線「めじろ台」バス5分停歩5分`` の「5分」は**バス停からの徒歩**で、
    駅徒歩として採ると ``walk_minutes_max`` を不当に通過する
    （UR・ホームメイト・D-room・レオパレスで踏んだのと同じ罠）。
    ⚠ **千代田区の一覧では検出できない**（バス便0件）ので、郊外のフィクスチャで
    回帰テストしている。
    """
    if not value or "バス" in value:
        return None
    return parse_walk_minutes(value)


class SuumoBuyMansionScraper:
    """SUUMO 中古マンションの取得と解析。"""

    site_code = SITE_CODE
    property_type = "CHUKO_MANSION"
    # SEOパスは市区まで含めた形でしか測っていない（都道府県だけのページは未確認）
    requires_city = True
    # ⚠ 賃貸は JIS5桁だが、売買は SEOパスなのでスラグを引く
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False
    city_rotation_limit = None
    # ⚠ キーと選択肢は実測済みだが、丸めの向きを ``AXIS_BOUND`` へ載せてから
    # 有効にする。推測で書くと「0件になる／黙って無視される／向きが逆」の
    # いずれかになり、**どれも例外にならない**（→ ADR 0015・課題#29）
    supports_site_filters = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """対象エリアから一覧ページ（1ページ目）のURLを組み立てる。"""
        urls: list[str] = []
        for area in areas:
            pref = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref:
                raise ValueError(f"SUUMO売買: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                # スラグの無い市区は取りに行けない。``resolve_areas`` が落とすので
                # ここへは来ない想定
                continue
            urls.append(BASE_URL + LIST_PATH.format(pref=pref, city=area.value))
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

        ⚠ **``property_unit--osusume`` を除外してはいけない。** 20件のうち16件に
        付いているが中身は通常の掲載で、除外すると**80%が消える**（件数が減る
        だけでエラーにならない）。
        ⚠ **``nc_`` を本文への正規表現で拾わない。** 一覧の外の別枠が8件混ざる。
        必ず ``div.property_unit`` を起点にする。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for unit in doc.cssselect("div.property_unit"):
            # ⚠ ``property_unit-title`` は **h2**。``div`` 決め打ちだと URL が 0/20 になる
            links = unit.cssselect("h2.property_unit-title a")
            if not links:
                continue
            href = links[0].get("href") or ""
            external_id = _external_id(href)
            if not external_id:
                continue
            fields = _fields(unit)
            access = fields.get("沿線・駅")
            listings.append(
                ScrapedListing(
                    site_code=SITE_CODE,
                    external_id=external_id,
                    url=BASE_URL + href if href.startswith("/") else href,
                    # ⚠ **h2 の中身は広告のキャッチコピー**（「◆現在空室◆…」）。
                    # 物件名は定義リスト側にある。取り違えると通知と
                    # ダイジェストに広告文が並ぶ（例外にならない）
                    title=fields.get("物件名"),
                    price=parse_yen(fields.get("販売価格")),
                    area_sqm=parse_area_sqm(fields.get("専有面積")),
                    layout=fields.get("間取り") or None,
                    age_years=age_years_from_built(fields.get("築年月")),
                    address=clean_address(fields.get("所在地")),
                    station_info=_station_info(access),
                    walk_minutes=_walk_minutes(access),
                )
            )
        return listings

    def detail_url(self, listing_url: str) -> str:
        """一覧のリンクをそのまま使う。"""
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページHTMLから追加情報を取り出す。

        ⚠ **``tr`` の中に ``th``/``td`` が複数対並ぶ**ので ``th.getnext()`` で読む。
        ``tr`` から ``td`` を拾うと**値が1つずれる**（実測で管理費に販売価格が入った）。
        ⚠ **設備の一覧に相当するブロックは見つかっていない**ので
        ``raw_features_text`` は埋めない（売買辞書 ``buy:`` も空 → 手順5以降）。
        """
        doc = lxml_html.fromstring(html_text)
        values: dict[str, str] = {}
        for th in doc.cssselect("th"):
            td = th.getnext()
            if td is None or td.tag != "td":
                continue
            label = _label(th.text_content())
            if label and label not in values:
                values[label] = " ".join(td.text_content().split())

        access = values.get("交通")
        return ScrapedDetail(
            # ⚠ **括弧が全角と半角の2種類ある**（同じページに両方が出る）
            built_on=parse_built_on(
                values.get("完成時期（築年月）") or values.get("完成時期(築年月)")
            ),
            mgmt_fee_monthly=parse_yen(values.get("管理費")),
            repair_reserve_monthly=parse_yen(values.get("修繕積立金")),
            address=clean_address(values.get("所在地")),
            walk_minutes=_walk_minutes(access),
            type_specific_attrs={
                key: values[key]
                for key in ("敷地の権利形態", "用途地域", "構造・階建て", "総戸数")
                if values.get(key)
            },
        )

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """掲載終了は **HTTP 404**（実測 2026-09-06）。

        ⚠ 賃貸の ``_SOLD_MARKERS``（本文の文言）は流用しない。404 のページの
        ``title`` は「エラー｜SUUMO(スーモ)」で賃貸のエラーページと同じ文字列に
        なるため、本文で判定すると**正常なページの解析失敗と区別できない**。
        """
        response = fetcher.get(url)
        return response.status_code == 404
