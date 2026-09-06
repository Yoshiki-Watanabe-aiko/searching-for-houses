"""SUUMO 新築マンション（売買）の取得と解析（→ 課題#4・Phase 6 手順6）。

⚠⚠ **一覧に「プロジェクト（棟）」と「個別住戸」が混在する**（ユーザー判断
2026-09-06: 両方取り込む）。実測（板橋11件・港30件）では棟28・住戸13で、
**同じ建物が両方に出る**（リビオタワー品川＝棟1＋住戸6）。

⚠ **DOM は棟と住戸で完全に同一**なので1つのパーサで扱える。違うのは中身だけ。

| | プロジェクト（棟） | 個別住戸 |
|---|---|---|
| 価格 | ``価格未定`` / ``8410万円～1億4690万円`` | ``8980万円`` |
| 間取り・面積 | ``1LDK～3LDK / 37.01m2～152.01m2`` | ``1LDK / 38.14m2（11.53坪）（壁芯）`` |
| 所在地 | ``港区芝公園２``（都道府県なし） | ``東京都港区芝浦４`` |
| 販売期の行 | **複数ありうる** | 1つ |

⚠ **棟は ``dedup_key`` を作れない**（面積レンジ・間取りレンジ・階数なし）ので
名寄せされず単独で残る。同じ建物がランキングに複数出ることは承知のうえ。

⚠ 一覧URLは中古と同じく **SEOパス**（robots が ``/jj/bukken/ichiran/`` を
明示的に禁じている）。市区は**スラグ**で指定する。
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
    parse_floor,
    parse_total_floors,
    parse_walk_minutes,
    parse_yen,
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI

# ⚠ 詳細ページの ``th``/``td`` と設備ブロックは**中古マンションと同じ構造**
# （実測 2026-09-07・個別住戸のみ）。別々に書くと片方を直したとき
# もう片方が黙って古くなるので共用する
from house_search.scrape.suumo_buy import features_text, read_spec_table

SITE_CODE = "SUUMO"
BASE_URL = "https://suumo.jp"
# 1ページ30件（実測 2026-09-06）。⚠ **中古マンションは20件**で違う
PAGE_SIZE = 30
# 新築マンションのSEOパス。``{pref}`` は都道府県ローマ字、``{city}`` は ``sc_`` スラグ
LIST_PATH = "/ms/shinchiku/{pref}/{city}/"

# 価格欄の注記（``／予定``・``※1000万円単位``）。金額の後ろに付く
_PRICE_NOTE = re.compile(r"[／/※].*$")
# 価格の区切り。``～``（U+FF5E）・``~``・``〜``（U+301C）とレンジではない列挙 ``・``
_PRICE_SEPARATOR = re.compile(r"[～~〜・]")
# 詳細ページのパス。⚠ ``nc_.../rooms/?suit=`` のリンクが同じ掲載に同居するので
# 「``/nc_数字/`` で終わる」ことまで見る
_DETAIL_PATH = re.compile(r"/nc_\d+/$")
_UNDECIDED = "価格未定"
# 詳細ページの見出しクラス。⚠ **中古は ``secTitleInnerR`` で別物**（実測 2026-09-07）。
# 流用すると設備原文が空になるだけで例外にならない
_FEATURE_HEADING_CLASS = "secTitleInnerK"
# 棟の所在地に付く注記（``板橋１-3001、3002（地番）``）。⚠ **地番は住居表示ではない**
# ので残すと実在しない住所として正規化される（→ ADR 0020 の番地誤認と同型）
_CHIBAN_NOTE = re.compile(r"[（(]地番[）)].*$")


def _detail_address(value: str | None) -> str | None:
    """詳細ページの所在地から導線リンクと地番の注記を落とす。"""
    cleaned = clean_address(value)
    if not cleaned:
        return None
    return _CHIBAN_NOTE.sub("", cleaned).strip() or None


def parse_price_range(text: str | None) -> tuple[int | None, int | None]:
    """価格欄から下限と上限を読む。

    ⚠⚠ **レンジをそのまま ``parse_yen`` へ渡してはいけない。**
    ``9448万円～2億5498万円`` は **254,980,000（＝上限）** を返す（実測 2026-09-06）。
    億・万・円を1つの正規表現で連結して読むようにした（→ 課題#53）副作用で、
    「億」を含む側に先にマッチするため。⚠ **例外にならず値だけが約2.7倍に狂う**
    ので、必ず区切りで割ってから各辺を読む。

    実測された表記（すべて ``span.cassette_price-accent`` の中身）::

        価格未定
        8290万円
        6558万円～9898万円
        6400万円台～9900万円台／予定
        8590万円・8790万円
        1億3000万円台～1億9000万円台※1000万円単位／予定
    """
    if not text:
        return None, None
    head = _PRICE_NOTE.sub("", text)
    values = [
        value
        for value in (parse_yen(part) for part in _PRICE_SEPARATOR.split(head))
        if value is not None
    ]
    if not values:
        return None, None
    return min(values), max(values)


def split_description(text: str | None) -> tuple[str | None, str | None]:
    """``1LDK～3LDK / 37.01m2～152.01m2`` を間取りと面積に割る。

    ⚠ **間取りのレンジは潰さない。** 下限へ丸めると実態を過小に表現するので、
    MUST 判定側が unknown に落とす（→ 課題#4・``must._check_layouts``）。
    """
    if not text:
        return None, None
    layout, separator, area = text.rpartition("/")
    if not separator:
        return None, None
    return " ".join(layout.split()) or None, " ".join(area.split()) or None


def _basic_fields(unit) -> dict[str, str]:
    """「所在地 / 交通 / 引渡時期」のペアを読む。

    ⚠ **見出しと値を位置で対応づけない**（→ 課題#44）。項目が欠ける掲載が
    あったとき、ラベルと値がずれたまま静かに保存される。``list_item`` の中で
    対にする。
    """
    values: dict[str, str] = {}
    for item in unit.cssselect(".cassette_basic-list_item"):
        labels = item.cssselect(".cassette_basic-title")
        data = item.cssselect(".cassette_basic-value")
        if not labels or not data:
            continue
        key = " ".join(labels[0].text_content().split())
        if key and key not in values:
            values[key] = " ".join(data[0].text_content().split())
    return values


def _detail_href(unit) -> str | None:
    """掲載の詳細ページへのリンクを取り出す。"""
    for anchor in unit.cssselect("h2 a[href]"):
        href = anchor.get("href") or ""
        if _DETAIL_PATH.search(href):
            return href
    for anchor in unit.cssselect("a[href]"):
        href = anchor.get("href") or ""
        if _DETAIL_PATH.search(href):
            return href
    return None


def _external_id(href: str) -> str | None:
    """詳細URLから物件ID（``nc_67734880``）を取り出す。

    ⚠ 棟は ``nc_67…``、個別住戸は ``nc_20/21/78…`` と接頭が違うが**同じ体系**。
    """
    for part in href.split("/"):
        if part.startswith("nc_") and part[3:].isdigit():
            return part
    return None


def _title(unit) -> str | None:
    """物件名を読む。

    ⚠ **広告のキャッチコピーは別要素**（``.cassette_description_header-description``）
    で、そこには価格が混じる（``【70㎡超 3LDK 7098万円、…``・実測で板橋4/11）。
    中古の ``h2.property_unit-title`` が広告文だったのとは逆で、**新築の h2 は
    物件名**である。⚠ どちらにせよ**価格を本文の正規表現で拾わない**。
    """
    for heading in unit.cssselect("h2.cassette_header-title, h2"):
        text = " ".join(heading.text_content().split())
        if text:
            return text
    return None


def _station_info(value: str | None) -> str | None:
    """交通欄を駅同定できる形へ整える。

    新築の交通欄は ``ＪＲ埼京線/板橋 徒歩1分`` で、**「駅」の字も鉤括弧も無い**
    （中古の ``東京メトロ丸ノ内線「淡路町」徒歩2分`` とは別の形）。そのまま渡すと
    第1パス（「◯◯駅」のアンカー）が空振りする（→ 課題#41・D-room）。
    ⚠ 第2パス（時間表記の直前）では拾えるが、**マスタに当たらなければ
    ``t_listing_stations`` に行すら残らず「同定できなかった表記」の一覧にも
    出ない**ので、確実に取れる形へ直してから渡す。
    """
    if not value:
        return None
    text = " ".join(value.split())
    line, separator, rest = text.partition("/")
    if not separator:
        return text
    parts = rest.split(None, 1)
    if not parts:
        return text
    tail = f" {parts[1]}" if len(parts) > 1 else ""
    return f"{line} {parts[0]}駅{tail}"


def _walk_minutes(value: str | None) -> int | None:
    """駅からの徒歩分。**バス便は採らない**。

    ⚠ バス経由の「徒歩N分」は**バス停からの徒歩**で、駅徒歩として採ると
    ``walk_minutes_max`` を不当に通過する（UR・ホームメイト・D-room・
    レオパレス・SUUMO 中古で踏んだのと同じ罠）。
    ⚠ **板橋・港のフィクスチャにバス便は0件**なので、この分岐は実データで
    検証できていない（都心・準都心のため）。弾く側に倒してあるので、
    表記が違っても「駅徒歩を取り逃す」だけで誤って通すことはない。
    ⚠ 徒歩がレンジの掲載がある（``徒歩5分～6分``）。``parse_walk_minutes`` は
    先頭＝下限を採る（UR の団地と同じ扱い）。
    """
    if not value or "バス" in value:
        return None
    return parse_walk_minutes(value)


class SuumoNewMansionScraper:
    """SUUMO 新築マンションの取得と解析。"""

    site_code = SITE_CODE
    property_type = "SHINCHIKU_MANSION"
    # SEOパスは市区まで含めた形でしか測っていない
    requires_city = True
    # ⚠ 賃貸は JIS5桁だが、売買は SEOパスなのでスラグを引く（中古と同じ）
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False
    city_rotation_limit = None
    # ⚠ **サイト側MUST は未測定なので送らない。** 中古の ``mb``/``et`` が新築でも
    # 同じ選択肢かは分からない（同じサイト・同じキー名でも種別が違えば選択肢は
    # 別物 → 課題#4 手順4後半で ``mb`` の刻みが賃貸と売買で違った）。
    # 推測で書くと「0件になる／黙って無視される／向きが逆」のいずれかになり、
    # **どれも例外にならない**（→ ADR 0015・課題#29）
    supports_site_filters = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """対象エリアから一覧ページ（1ページ目）のURLを組み立てる。"""
        urls: list[str] = []
        for area in areas:
            pref = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref:
                raise ValueError(f"SUUMO新築: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                # スラグの無い市区は取りに行けない（``resolve_areas`` が落とす）
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

        ⚠⚠ **1掲載に価格の行が複数ある**（販売期ごと）。バウス加賀は
        「価格未定（第3期5次）」と「8410万円～1億4690万円（先着順）」が同居する。
        → **価格が付いた行が1つでもあれば、その最小値を ``price``**、
        最小〜最大を ``price_min``/``price_max`` にする。全部未定なら
        ``price`` は NULL で ``price_undecided`` を立てる（→ 要件定義書 §11.4）。

        ⚠ **価格は ``span.cassette_price-accent`` からしか取らない。**
        ``div.cassette_price-value`` だと「（先着順）」「（（一般定期借地権））」が
        混ざり、広告のキャッチコピーにも価格が書いてある（→ ``_title``）。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for unit in doc.cssselect("li.cassette_list-item"):
            href = _detail_href(unit)
            if not href:
                continue
            external_id = _external_id(href)
            if not external_id:
                continue

            price, price_min, price_max, description, undecided = _read_prices(unit)
            layout, area_text = split_description(description)
            fields = _basic_fields(unit)
            access = fields.get("交通")

            attrs: dict[str, object] = {"price_undecided": undecided}
            if fields.get("引渡時期"):
                attrs["引渡時期"] = fields["引渡時期"]

            listings.append(
                ScrapedListing(
                    site_code=SITE_CODE,
                    external_id=external_id,
                    url=BASE_URL + href if href.startswith("/") else href,
                    title=_title(unit),
                    price=price,
                    price_min=price_min,
                    price_max=price_max,
                    area_sqm=parse_area_sqm(area_text),
                    layout=layout,
                    # ⚠ 新築なので築年は無い（``age_years`` metric は
                    # 新築マンションに適用されない）
                    address=clean_address(fields.get("所在地")),
                    station_info=_station_info(access),
                    walk_minutes=_walk_minutes(access),
                    type_specific_attrs=attrs,
                )
            )
        return listings

    def detail_url(self, listing_url: str) -> str:
        """一覧のリンクをそのまま使う。"""
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページHTMLから追加情報を取り出す。

        ⚠⚠ **一覧と違い、詳細ページは棟と個別住戸で構造がまったく別物**
        （実測 2026-09-07）。一覧が同一だったからといって詳細も同じとは限らない。

        | | 棟 ``nc_67734880`` | 個別住戸 ``nc_21371763`` |
        |---|---|---|
        | 見出し | ``section_h2-title`` | ``secTitleInnerK`` |
        | 設備ブロック | **無い**（``secTitleInner*`` が0件） | 特徴ピックアップ35タグ |
        | 管理費・修繕積立金・所在階・築年月 | **無い** | ある |
        | ``th``/``td`` | 9項目だけ | 中古マンションとほぼ同じ |

        ⚠⚠ **棟の h2「建物の特徴」「室内の特徴」を設備原文に入れてはいけない。**
        中身は ``JR「板橋」駅直結徒歩1分 × 三大副都心直通`` のような広告の
        キャッチコピーで設備名ではない。辞書照合は本文全体への部分一致なので、
        入れると**その棟に無い設備が拾われて設備数が黙って水増しされる**
        （CHINTAI.net の用語集展開・HOMES の ``sr-only`` と同型 → 課題#37）。
        棟は ``secTitleInnerK`` が0件なので、何もしなくても原文なしになる。

        ⚠ 管理費は ``1万9500円／月`` の形。**「万」の後ろの下位桁を落とすと
        10,000 になる**（課題#53 で ``parse_yen`` を直してある）。
        """
        doc = lxml_html.fromstring(html_text)
        values = read_spec_table(doc)
        access = values.get("交通")
        return ScrapedDetail(
            raw_features_text=features_text(doc, _FEATURE_HEADING_CLASS),
            # ⚠ **括弧が全角と半角の2種類ある**（同じページに両方が出る）
            built_on=parse_built_on(
                values.get("完成時期（築年月）") or values.get("完成時期(築年月)")
            ),
            mgmt_fee_monthly=parse_yen(values.get("管理費")),
            repair_reserve_monthly=parse_yen(values.get("修繕積立金")),
            # ⚠ **棟には無い。** マンションファミリの ``dedup_key`` の構成要素なので、
            # 棟はキーを作れず名寄せされずに単独で残る（クラス docstring のとおり設計どおり）
            floor_num=parse_floor(values.get("所在階")),
            total_floors=parse_total_floors(values.get("構造・階建て")),
            address=_detail_address(values.get("所在地")),
            walk_minutes=_walk_minutes(access),
            type_specific_attrs={
                key: values[key]
                for key in ("敷地の権利形態", "用途地域", "構造・階建て", "総戸数")
                if values.get(key)
            },
        )

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """掲載終了は **HTTP 404**（実測 2026-09-07・中古マンションと同じ）。

        ⚠ 賃貸の ``_SOLD_MARKERS``（本文の文言）は流用しない。404 のページの
        ``title`` は「エラー｜SUUMO(スーモ)」で賃貸のエラーページと同じ文字列に
        なるため、本文で判定すると**正常なページの解析失敗と区別できない**。
        """
        response = fetcher.get(url)
        return response.status_code == 404


def _read_prices(unit) -> tuple[int | None, int | None, int | None, str | None, bool]:
    """販売期ごとの価格行をまとめて、価格・レンジ・間取り面積・未定フラグを返す。

    ⚠ **``price_undecided`` は「価格未定と明記されている」ときだけ True。**
    「価格が取れなかった」と混ぜてはいけない（→ ADR 0021 決定4 と同じ形）。
    ⚠ **価格が付いたら False を明示的に書く**（保存は JSONB の ``||`` マージなので、
    書かないと以前立てたフラグが残り「価格があるのに価格未定」と表示される）。
    """
    best: tuple[int, int, str | None] | None = None
    fallback_description: str | None = None
    undecided = False
    for row in unit.cssselect("li.cassette_price-list_item"):
        accents = row.cssselect("span.cassette_price-accent")
        accent = " ".join(accents[0].text_content().split()) if accents else None
        descriptions = row.cssselect("p.cassette_price-description")
        description = (
            " ".join(descriptions[0].text_content().split()) if descriptions else None
        )
        if fallback_description is None:
            fallback_description = description
        if accent and _UNDECIDED in accent:
            undecided = True
        low, high = parse_price_range(accent)
        if low is None or high is None:
            continue
        # 価格の付いた行のうち最も安いものを採る（``price`` と説明を揃える）
        if best is None or low < best[0]:
            best = (low, high, description)
    if best is None:
        return None, None, None, fallback_description, undecided
    return best[0], best[0], best[1], best[2] or fallback_description, False
