"""サイトアダプタの共通型とパース補助。

v2 のサイト別コードは「取得と解析」だけを担う。設備条件の絞り込みはサイトへ
渡さず（→ ADR 0003）、フォームに載せるのはエリア・物件種別・価格上限だけ。
"""

from __future__ import annotations

import datetime as dt
import re
import string
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from house_search.scrape.area import CITY_VALUE_MAPPING, AreaTarget
from house_search.scrape.fetch import SiteFetcher

# 「3.5万円」「1万3020円」「1億2,800万円」などの金額表記。
# ⚠⚠ **上位の単位だけを拾って下位桁を落とさないこと**（→ 課題#4・課題#53）。
# 初版は億を見ず「1億2,800万円」を 28,000,000 と読み、次の版は万の後ろを見ず
# 「1万3020円」を 10,000 と読んだ。**同じ欠陥を2度踏んでいる。**
# ⚠ NULL になるならまだ気づけるが、「それらしい値」が入ると
# 例外にも件数の減少にもならないまま MUST を通って順位だけが狂う。
# そのため億・万・円をひとつの正規表現の中で連結して読み、
# **「どちらを先に試すか」という順序への依存そのものを無くしてある。**
# ⚠ 下位桁は「万」の直後に数字が続くときだけ拾う（間に空白を許さない）。
# 許すと「15万 5000円」のように並んだ別々の金額を誤って結合する。
# ⚠ ABLE の敷金欄は「23.9万」と円を省くので、円の部分は任意のまま。
_OKU_YEN = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*億\s*(?:([\d,]+(?:\.\d+)?)\s*万)?(?:([\d,]+)\s*円)?"
)
_MAN_YEN = re.compile(r"([\d,]+(?:\.\d+)?)\s*万(?:([\d,]+)\s*円)?")
_YEN = re.compile(r"([\d,]+)\s*円")
# 面積表記。入力を NFKC 正規化してから当てるので「㎡」「m²」「m<sup>2</sup>」が
# すべて "m2" に寄る。正規化しないと ABLE の「42.9㎡」を取りこぼす。
_AREA = re.compile(r"([\d,]+(?:\.\d+)?)\s*m\s*2")
_AGE = re.compile(r"築\s*(\d+)\s*年")
_NEW_BUILDING = re.compile(r"新築")
_WALK = re.compile(r"歩\s*(\d+)\s*分")
_FLOOR = re.compile(r"(地下)?\s*(\d+)\s*階")
# ⚠⚠ **「地下N階」を挟んでから「建」が来る表記がある**（goo の「地上13階地下1階建」、
# SUUMO 売買の「RC13階地下1階建」）。地下の節を任意で飲み込まないと
# **「地下1階建」に当たって総階数が 1 になる**（実測 2026-09-06。7階の住戸が
# 「1階建の建物」として保存されていた）。⚠ 例外にも件数の減少にもならず
# **値だけが静かに狂う**——課題#51・#53 とまったく同じ形。
# ⚠ 地下が先に来る表記（賃貸EX の「地下1地上13階建」）は「1」の直後に「階」が
# 無いのでこの節を通らず、従来どおり地上階に当たる。
_TOTAL_FLOORS = re.compile(r"(\d+)\s*階(?:\s*地下\s*\d+\s*階)?\s*建")
# 「1987年1月」「2024年12月」。日は取れないので1日に固定して格納する。
_BUILT_ON = re.compile(r"(\d{4})\s*年\s*(\d{1,2})?\s*月?")


@dataclass(frozen=True, slots=True)
class ScrapedListing:
    """一覧ページから取れる1掲載ぶんの情報。

    ここに載る項目だけで MUST の1段目判定を行い、``fail`` なら詳細を取りに行かない。
    """

    site_code: str
    external_id: str
    url: str
    title: str | None = None
    price: int | None = None
    mgmt_fee_monthly: int | None = None
    deposit_amount: int | None = None
    key_money_amount: int | None = None
    area_sqm: float | None = None
    # ⚠ **戸建てに ``area_sqm`` を流用しない**（→ 要件定義書 §5.3）。
    # 専有面積という概念が無く、土地面積・建物面積の2軸になる。
    # 混ぜると metric の意味が濁り、名寄せキーも壊れる
    land_area_sqm: float | None = None
    building_area_sqm: float | None = None
    layout: str | None = None
    floor_num: int | None = None
    total_floors: int | None = None
    age_years: int | None = None
    address: str | None = None
    station_info: str | None = None
    walk_minutes: int | None = None
    image_url: str | None = None
    # 新築（棟／プロジェクト単位）の価格レンジ。``price`` にはレンジ下限を入れる
    # （→ 要件定義書 §11.4）。⚠ **中古・賃貸は None のまま**で、レンジを持つのは
    # 新築だけ。
    price_min: int | None = None
    price_max: int | None = None
    # ⚠⚠ **価格未定は ``price`` を NULL にしたうえで
    # ``type_specific_attrs["price_undecided"]`` を立てる**（→ 要件定義書 §11.4）。
    # 0 やハイフンにすると「安い」と誤読され、順位だけが静かに狂う。
    # ⚠ **「価格が取れなかった」と「価格未定と明記されている」を混ぜない。**
    # ハザードの「区域外(0)」と「未解決(None)」を混ぜてはいけないのと同じ形
    # （→ ADR 0021 決定4・課題#49）。
    # ⚠ 保存は JSONB の ``||`` マージなので、**価格が付いたときは false を明示的に
    # 書く**こと。書かないと未定フラグが残り続ける。
    type_specific_attrs: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScrapedDetail:
    """詳細ページから取れる追加情報。

    ``raw_features_text`` は設備ブロックの原文。辞書を改善したとき
    再スクレイピングせず ``re-extract`` で全件やり直すための保存であり、
    詳細HTML全体は保存しない。
    """

    raw_features_text: str | None = None
    # 未知表記の収集だけに使うテキスト。None なら raw_features_text をそのまま使う。
    # 宣伝の生成文を設備の照合には使いたいが辞書育成の材料にはしたくない
    # サイト（賃貸EX）のための逃げ道（→ 課題#19）。
    unknown_token_text: str | None = None
    built_on: dt.date | None = None
    # 築年数（年）。⚠ **築年月が取れないサイト向け**の逃げ道で、``built_on`` が
    # 取れるサイトでは使わない。UR は API が築年月ではなく築年数を返す（→ 課題#37）
    age_years: int | None = None
    floor_num: int | None = None
    total_floors: int | None = None
    mgmt_fee_monthly: int | None = None
    # 修繕積立金（円/月）。マンション売買の ``monthly_cost`` metric の入力で、
    # 管理費と合算する。⚠ 賃貸では使わない。⚠ 一覧には出ないので詳細専用
    repair_reserve_monthly: int | None = None
    deposit_amount: int | None = None
    key_money_amount: int | None = None
    address: str | None = None
    walk_minutes: int | None = None
    type_specific_attrs: dict = field(default_factory=dict)


class SiteScraper(Protocol):
    """サイトアダプタが満たすべき最小のインタフェース。"""

    site_code: str
    # 市区の指定が必須か（True なら都道府県だけでは0件になる。ABLE・SMOCCA）
    requires_city: bool
    # 市区の検索値の出どころ。``area.CITY_VALUE_MAPPING`` / ``CITY_VALUE_JIS``
    city_value_source: str
    # このサイトにだけ使う User-Agent。None なら .env の USER_AGENT
    user_agent: str | None
    # robots.txt を無視するか。**既定は False。**
    # 宣言してよいのはユーザーが明示的にそう決めたサイトだけ（→ ADR 0011）
    ignore_robots: bool
    # 1回の実行で取りに行く市区の数。``None`` ならローテーションしない（既定）。
    # 宣言するのは**取得数に上限があると実測できたサイトだけ**（HOMES 5・ATHOME 4）。
    # 運用値ではなくサイトの実測特性なのでアダプタが持つ（→ 課題#36）
    city_rotation_limit: int | None

    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """検索パターンと対象エリアから一覧ページ（1ページ目）のURLを組み立てる。"""
        ...

    def page_url(self, base_url: str, page: int) -> str:
        """一覧URLへページ番号を付ける。"""
        ...

    def is_last_page(self, count: int) -> bool:
        """このページの取得件数から最終ページかどうかを判定する。"""
        ...

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧ページHTMLから掲載を取り出す。"""
        ...

    def detail_url(self, listing_url: str) -> str:
        """詳細ページのURL（一覧のリンクをそのまま使えるなら同じ値）。"""
        ...

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細ページHTMLから追加情報を取り出す。"""
        ...

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """成約・掲載終了かどうか。"""
        ...


def query_separator(url: str) -> str:
    """URLにクエリを足すときの区切り文字を選ぶ。

    ⚠ **`?` を固定で書かない。** サイト側フィルタ（→ ADR 0015）が付くと
    一覧URLが既にクエリを持つため、`?` を重ねたURLになる。実測では
    APAMAN が `...?senyu1=30&ekitoho=20?page=2` を **HTTP 200 で受け取り、
    page を黙って無視して1ページ目を返した**（掲載26件が1ページ目と完全に一致）。
    ページ送りが死ぬのに例外にならないため気づけない（→ 課題#29）。
    """
    return "&" if "?" in url else "?"


def _normalize_money_text(text: str) -> str:
    """金額表記の全角を半角へ寄せる（→ 課題#51）。

    ⚠ 正規表現の ``\\d`` は全角数字にマッチするが、``\\.`` と ``[,]`` は
    **半角しか受けない**。正規化しないと全角の区切りを含む金額で
    **値だけが静かに狂う**（例外にならない）。

    ============  ==========  ==========
    入力          正規化なし  正しい値
    ============  ==========  ==========
    １４．３万円   30,000      143,000
    ３，０００円   **0**       3,000
    １．５ヶ月     5ヶ月分     1.5ヶ月分
    ============  ==========  ==========

    ⚠ とくに「３，０００円 → 0」は ``int("０００")`` が 0 になるためで、
    **管理費が黙って0円として ``rent_total`` に足される**。
    面積（``parse_area_sqm``）は元から NFKC を通していたのに、
    金額だけ通っていなかった。
    """
    return unicodedata.normalize("NFKC", text)


def parse_yen(text: str | None) -> int | None:
    """「3.5万円」「1億2,800万円」「25000円」などを円の整数へ。取れなければ None。

    ⚠ **レンジ表記は下限を採る**（``search`` が最初のマッチを返すため）。
    新築の「5,000万円～7,000万円」は 50,000,000 になる（→ 要件定義書 §11.4）。
    ⚠ **「価格未定」は None**。0 にすると「安い」と誤読される。
    """
    if not text:
        return None
    normalized = _normalize_money_text(text)
    if match := _OKU_YEN.search(normalized):
        oku = float(match.group(1).replace(",", ""))
        man = float(match.group(2).replace(",", "")) if match.group(2) else 0.0
        yen = int(match.group(3).replace(",", "")) if match.group(3) else 0
        return int(round(oku * 100_000_000 + man * 10_000)) + yen
    if match := _MAN_YEN.search(normalized):
        man = float(match.group(1).replace(",", ""))
        yen = int(match.group(2).replace(",", "")) if match.group(2) else 0
        return int(round(man * 10_000)) + yen
    if match := _YEN.search(normalized):
        return int(match.group(1).replace(",", ""))
    return None


def parse_area_sqm(text: str | None) -> float | None:
    """「18m2」「42.5m²」「42.9㎡」などを㎡の実数へ。

    面積の単位表記はサイトごとに ㎡（U+33A1）・m²・m<sup>2</sup> とばらつくため、
    NFKC 正規化で "m2" に寄せてから読む。
    """
    if not text:
        return None
    match = _AREA.search(unicodedata.normalize("NFKC", text))
    return float(match.group(1).replace(",", "")) if match else None


def parse_age_years(text: str | None) -> int | None:
    """「築40年」を年数へ。「新築」は0年として扱う。"""
    if not text:
        return None
    if match := _AGE.search(text):
        return int(match.group(1))
    return 0 if _NEW_BUILDING.search(text) else None


def parse_walk_minutes(text: str | None) -> int | None:
    """「歩50分」の最小値を返す。複数路線が併記されるため最短を採る。"""
    if not text:
        return None
    values = [int(m) for m in _WALK.findall(text)]
    return min(values) if values else None


def parse_floor(text: str | None) -> int | None:
    """「3階」「地下1階」を所在階へ（地下は負値）。

    「2-3階」のようなメゾネット表記は下の階を採る。
    """
    if not text:
        return None
    match = _FLOOR.search(text)
    if not match:
        return None
    floor = int(match.group(2))
    return -floor if match.group(1) else floor


def parse_total_floors(text: str | None) -> int | None:
    """「2階建」「1階/2階建」から建物の階数を返す。"""
    if not text:
        return None
    match = _TOTAL_FLOORS.search(text)
    return int(match.group(1)) if match else None


def parse_built_on(text: str | None) -> dt.date | None:
    """「1987年1月」を築年月へ。日は取れないので1日に固定する。"""
    if not text:
        return None
    match = _BUILT_ON.search(text)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else 1
    if not (1800 <= year <= 2200 and 1 <= month <= 12):
        return None
    return dt.date(year, month, 1)


# 金額欄で「無し」を意味する表記。サイトを問わず共通に現れる。
EMPTY_MARKERS = ("-", "－", "—", "‐", "なし", "無", "無し", "無料", "0円")
# ABLE の「--」のようにダッシュだけが並ぶ欄も「無し」を意味する。
# 記号だけで構成されているかで判定する（「-」との完全一致では取りこぼす）
_EMPTY_CHARS = frozenset("-－—‐/／ 　") | frozenset(string.whitespace)

_MONTHS = re.compile(r"([\d.]+)\s*(?:ヶ月|ケ月|カ月|か月|ヵ月|月分)")


def is_empty_fee(value: str) -> bool:
    """金額欄が「無し」を意味しているか。"""
    return not value or value in EMPTY_MARKERS or set(value) <= _EMPTY_CHARS


def parse_fee(value: str | None) -> int | None:
    """管理費・敷金・礼金の欄を円へ。「-」「なし」は 0 として扱う。

    ``None`` のままにすると ``rent_total``（賃料＋管理費）が「管理費不明」となり、
    実際には0円の物件が MUST 判定で ``unknown`` に落ちてしまう。
    欄そのものが存在しない場合だけ ``None``（判定不能）を返す。
    """
    if value is None:
        return None
    stripped = value.strip()
    if is_empty_fee(stripped):
        return 0
    return parse_yen(stripped)


def parse_months_fee(value: str | None, rent: int | None) -> int | None:
    """「1ヶ月」形式の敷金・礼金を円へ換算する。

    HOMES・ABLE は敷金/礼金を賃料の月数で表す。円表記が混在するため、
    まず月数として読み、当たらなければ通常の金額表記として読む。
    賃料が不明なら月数を円に直せないので ``None``（判定不能）を返す。
    """
    if value is None:
        return None
    stripped = value.strip()
    if is_empty_fee(stripped):
        return 0
    if match := _MONTHS.search(_normalize_money_text(stripped)):
        if rent is None:
            return None
        return int(round(float(match.group(1)) * rent))
    return parse_yen(stripped)


def default_city_value_source() -> str:
    """アダプタが指定しなかった場合の既定（サイト固有マッピング）。"""
    return CITY_VALUE_MAPPING


def prefecture_targets(prefectures: Sequence[str]) -> list[AreaTarget]:
    """都道府県だけのエリア指定を作る（テストとフォールバック用）。"""
    return [AreaTarget(prefecture=pref) for pref in prefectures]


def age_years_from_built(text: str | None, *, today: dt.date | None = None) -> int | None:
    """「1971年11月」のような築年月から築年数を計算する。

    賃貸EX のように「築N年」を出さず築年月しか載せないサイト用。
    月まで見て切り下げる（築年数は満年で数えるため）。
    """
    built = parse_built_on(text)
    if built is None:
        return None
    reference = today or dt.date.today()
    years = reference.year - built.year - ((reference.month, 1) < (built.month, 1))
    return max(years, 0)


# 住所欄に紛れ込む導線リンクの文言。text_content で拾うと住所の末尾に付く。
_ADDRESS_NOISE = (
    "地図を見る",
    "地図で見る",
    "周辺地図",
    "詳細地図",
    "の行政データ",
    "の家賃相場",
    "周辺の",
)


def clean_address(value: str | None) -> str | None:
    """住所欄から導線リンクの文言を落とす。

    HOME'S は「…12-12地図を見る」、goo は「…23-2周辺地図 千代田区の行政データ」の
    ように、住所の td/dd に案内リンクが同居する。最初に現れた文言以降を切る。
    """
    if not value:
        return None
    cleaned = value
    for marker in _ADDRESS_NOISE:
        index = cleaned.find(marker)
        if index > 0:
            cleaned = cleaned[:index]
    cleaned = cleaned.strip()
    return cleaned or None
