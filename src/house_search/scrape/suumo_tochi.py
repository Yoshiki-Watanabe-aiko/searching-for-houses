"""SUUMO 土地の取得と解析（→ 課題#61・Phase 9b）。

⚠⚠ **戸建て（``suumo_kodate``）の派生にしない。** 戸建ては新築の索引に混ざる
``/tochi/`` を**捨てる**ので、継承すると土地の一覧が**0件になるだけで例外にならない**
（計画書 §6.5-1）。部品の関数（``list_fields``・``read_spec_table``・``features_text``・
``parse_price_range``・``station_info``・``walk_minutes_of``）だけを共用する。

一覧の構造は戸建て・中古マンションと同じ（``div.property_unit``・1ページ20件・
``?po=1&pj=2``・``&page=N``・``nc_`` のID。2026-09-11 に3市区で実測）。

⚠ 土地に固有のこと（いずれも実測・課題#61「9b の着手前実測」が正典）:

1. **物件名が無い掲載が多い**（八王子市 0/20・印西市 7/20）。必須にすると黙って落ちる
2. **価格・面積がレンジ／中黒の列挙になる**（分譲地・区画売り）。
   価格は下限を ``price``、最小〜最大を ``price_min``/``price_max`` に入れる
3. ⚠ **建築条件付きかどうかは詳細の仕様表の「建築条件」欄で判定する。**
   一覧の本文を「建築条件」で探すと ``建築条件なし`` のタグや
   ``建築条件付土地購入サポート`` のバッジに当たる（実測73件）
4. ⚠ **詳細の「プラン間取り」「プラン建物面積」は建築プラン例**で、その土地の建物ではない。
   間取り・建物面積として読まない（→ ADR 0024）
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lxml import html as lxml_html

from house_search.scrape.area import CITY_VALUE_MAPPING, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    leasehold_flag,
    parse_area_sqm,
    query_separator,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_ROMAJI
from house_search.scrape.suumo_buy import (
    BUY_LIST_QUERY,
    features_text,
    list_fields,
    read_spec_table,
)
from house_search.scrape.suumo_kodate import station_info, walk_minutes_of
from house_search.scrape.suumo_shinchiku import address_without_chiban, parse_price_range

SITE_CODE = "SUUMO"
BASE_URL = "https://suumo.jp"
#: 土地のSEOパス。``{pref}`` は都道府県ローマ字、``{city}`` は ``sc_`` スラグ。
#: ⚠ スラグは種別によらず共通（土地の市区選択ページで実測: 既存173件と JIS の食い違い0件）
LIST_PATH = "/tochi/{pref}/{city}/"
#: 1ページ20件（実測 2026-09-11・世田谷区708件/八王子市369件/印西市30件）
PAGE_SIZE = 20
#: 一覧の販売価格欄が価格未定を表す語（新築一戸建てと同じ ``未定``）
_UNDECIDED_MARK = "未定"
#: 仕様表で「記載なし」を表す値
_BLANK_VALUES = frozenset({"", "-"})

#: 詳細の仕様表から JSONB へ残す項目。⚠ 表記揺れが激しく正規化が未確立なので
#: 列にしない（列にすると ``t_listings`` の再作成が要る → 要件定義書 §11.3）。
#: ⚠ ``住所`` は ``[ ■周辺環境 ]`` の導線が同居するので使わない（``所在地`` を使う）
_ATTR_LABELS = (
    "土地の権利形態",
    "私道負担・道路",
    "用途地域",
    "地目",
    "土地状況",
    "引き渡し時期",
    "建築条件",
    "その他制限事項",
)
#: ⚠ **同じページに半角（``･``）と全角（``・``）の2つのラベルがある**（値は同一）。
#: テンプレートによって片方しか無ければ、片方だけを引くと黙って欠ける
#: （戸建ての ``完成時期（築年月）`` / ``完成時期(築年月)`` と同型）。
_COVERAGE_LABELS = ("建ぺい率・容積率", "建ぺい率･容積率")
#: 再建築不可の記載を探す仕様表の欄。⚠ 会社の宣伝欄（``担当者より`` 等）に
#: 「再建築不可物件も買取ります」のような文言が出うるので、物件の欄に限る
_REBUILD_SOURCE_LABELS = ("その他制限事項", "その他概要・特記事項", "私道負担・道路", "土地状況")
_REBUILD_PROHIBITED = "再建築不可"


def _external_id(href: str) -> str | None:
    """詳細URLから物件ID（``nc_21674636``）を取り出す。"""
    for part in href.split("/"):
        if part.startswith("nc_") and part[3:].isdigit():
            return part
    return None


def build_condition_flag(value: str | None) -> bool | None:
    """仕様表の「建築条件」欄を真偽値にする。

    ⚠ **実測したのは ``付`` と ``-`` だけ**なので、それ以外の表記は None にする
    （推測で True/False に倒さない → ADR 0015）。原文は ``建築条件`` のキーに残る。
    """
    if value is None:
        return None
    text = value.strip()
    if text.startswith("付"):
        return True
    if text in {"-", "無", "なし", "無し"}:
        return False
    return None


def mentions_rebuild_prohibited(values: Iterable[str | None]) -> bool:
    """物件の欄に「再建築不可」の記載があるか。

    ⚠ 実データ（2026-09-11 の8本）には1件も無く、どの欄に出るかは**未測定**。
    記載が無ければ False を明示する（JSONB の ``||`` マージで古い値が残らないように）。
    """
    return any(_REBUILD_PROHIBITED in value for value in values if value)


class SuumoTochiScraper:
    """SUUMO 土地の取得と解析。"""

    site_code = SITE_CODE
    property_type = "TOCHI"
    # SEOパスは市区まで含めた形でしか測っていない（robots が
    # ``/jj/bukken/ichiran/`` を禁じているのでこの経路しかない → 課題#4）
    requires_city = True
    # ⚠ 賃貸は JIS5桁だが、売買は SEOパスなのでスラグを引く
    city_value_source = CITY_VALUE_MAPPING
    user_agent = None
    ignore_robots = False
    city_rotation_limit = None
    # ⚠ **サイト側MUST は送らない**（→ ADR 0015）。土地面積の選択肢は 60〜150㎡ の
    # 10刻みで、60㎡未満の MUST は切り下げで「下限なし」になる。価格上限 ``kt`` は効くが
    # 配線していない（効きが1軸だけで、取得量をほとんど減らさない → 課題#61）
    supports_site_filters = False

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """対象エリアから一覧ページ（1ページ目）のURLを組み立てる。"""
        urls: list[str] = []
        for area in areas:
            pref = PREFECTURE_ROMAJI.get(area.prefecture)
            if not pref:
                raise ValueError(f"SUUMO土地: 未知の都道府県です: {area.prefecture}")
            if not area.value:
                # スラグの無い市区は取りに行けない（``resolve_areas`` が落とす）
                continue
            # 新着・更新順で取る。⚠ 土地でも効く（実測: 世田谷区の1ページ目の
            # 「新着」が既定 0/20 → 20/20。対照の ``zzz=1`` は既定と並びが同一）
            urls.append(
                BASE_URL + LIST_PATH.format(pref=pref, city=area.value) + "?" + BUY_LIST_QUERY
            )
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        """一覧URLへページ番号を付ける（``&page=N``・**1始まり**）。"""
        if page <= 1:
            return base_url
        return f"{base_url}{query_separator(base_url)}page={page}"

    def is_last_page(self, count: int) -> bool:
        """1ページに満たない件数しか返らなければ最終ページ。"""
        return count < PAGE_SIZE

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。

        ⚠ **``/tochi/`` を含むリンクだけを採る**（戸建てとちょうど逆）。
        ⚠ **``nc_`` を本文への正規表現で拾わない**（一覧の外の別枠が混ざる）。
        """
        doc = lxml_html.fromstring(html_text)
        listings: list[ScrapedListing] = []
        for unit in doc.cssselect("div.property_unit"):
            links = unit.cssselect("h2.property_unit-title a")
            if not links:
                continue
            href = links[0].get("href") or ""
            external_id = _external_id(href)
            if not external_id or "/tochi/" not in href:
                continue
            fields = list_fields(unit)
            access = fields.get("沿線・駅")
            price_text = fields.get("販売価格")
            price_min, price_max = parse_price_range(price_text)
            # ⚠ フラグを立てないと「価格が取れなかった」と区別できない（→ 要件定義書 §11.4）。
            #   ⚠ JSONB は ``||`` でマージされるので価格があるときは **False を明示**する
            undecided = _UNDECIDED_MARK in (price_text or "") and price_min is None
            listings.append(
                ScrapedListing(
                    site_code=SITE_CODE,
                    external_id=external_id,
                    url=BASE_URL + href if href.startswith("/") else href,
                    type_specific_attrs={"price_undecided": undecided},
                    # ⚠ 物件名が無い掲載が多い（八王子市 0/20）。必須にしない
                    title=fields.get("物件名"),
                    # レンジは下限を ``price`` に入れる（→ 要件定義書 §11.4）
                    price=price_min,
                    price_min=price_min,
                    price_max=price_max,
                    # ⚠ ``parse_area_sqm`` は中黒・レンジとも**先頭（下限）**を採り、
                    #   坪（``180m2（54.44坪）``）は読まない
                    land_area_sqm=parse_area_sqm(fields.get("土地面積")),
                    address=address_without_chiban(fields.get("所在地")),
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

        ⚠ **設備の見出しクラスは見ない**（``secTitleInnerR``/``K`` が混在する → 課題#4）。
        ``features_text`` が見出しの文言（「特徴ピックアップ」）で拾う。
        """
        doc = lxml_html.fromstring(html_text)
        values = read_spec_table(doc)
        features = features_text(doc)

        attrs: dict[str, object] = {
            label: values[label]
            for label in _ATTR_LABELS
            if values.get(label, "").strip() not in _BLANK_VALUES
        }
        coverage = next(
            (values[label] for label in _COVERAGE_LABELS if values.get(label)), None
        )
        if coverage and coverage.strip() not in _BLANK_VALUES:
            attrs["建ぺい率・容積率"] = coverage
        # 取引上の注意事項（→ 論点5・``CAVEAT_LABELS``）。⚠ 詳細は1回しか取らない
        # （``detail_fetched_at``）ので、判定できたら False も明示して書く
        attrs["build_condition"] = build_condition_flag(values.get("建築条件"))
        # ⚠ 判定は売買の「所有権のみ」MUST と共通（``scrape.base.leasehold_flag`` → 課題#65）。
        #    旧版は「借地」の語しか見ておらず、``地上権（旧）、新規20年`` を取りこぼした
        attrs["leasehold"] = leasehold_flag(values.get("土地の権利形態"))
        attrs["rebuild_prohibited"] = mentions_rebuild_prohibited(
            [*(values.get(label) for label in _REBUILD_SOURCE_LABELS), features]
        )

        return ScrapedDetail(
            raw_features_text=features,
            # ⚠ 「住所」欄は ``[ ■周辺環境 ]`` の導線が同居するので「所在地」を使う
            address=address_without_chiban(values.get("所在地")),
            walk_minutes=walk_minutes_of(values.get("交通")),
            type_specific_attrs=attrs,
        )

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """掲載終了は **HTTP 404**（実測 2026-09-11・戸建てと同じ）。"""
        response = fetcher.get(url)
        return response.status_code == 404
