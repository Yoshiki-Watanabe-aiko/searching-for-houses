"""UR賃貸住宅（都市再生機構）の取得と解析。

⚠ **このアダプタだけ既存の枠に収まらない。** 他の10サイトは
「GETで一覧HTML → GETで詳細HTML」だが、UR は次の3点が違う（実測 → 詳細設計書 §9.3）。

1. すべて **JSON API への POST**
2. **団地（建物）と住戸が別の階層**で、一覧を作るのに2回のAPIが要る
3. 設備・築年は3つ目のAPI（``detail_room``）にしかない

そこで ``SiteScraper`` の ``list_urls`` → ``parse_list`` の経路は使わず、
**任意フック** ``collect_listings`` / ``fetch_detail`` を宣言して
``pipeline.scan`` から委譲を受ける（``supports_site_filters`` と同じ宣言ベースの拡張で、
**既存10アダプタには一切触らない**）。

対応づけは次のとおりで、**③が詳細キューに乗る**ので ``--detail-limit`` がそのまま効く。

===== ===================================== ============ ==================
段     エンドポイント                          単位          役割
===== ===================================== ============ ==================
①     ``search/list_bukken``                団地          エリア内の全団地
②     ``detail/detail_bukken_room``         住戸          空室の住戸一覧
③     ``detail/detail_room``                住戸          設備・築年・敷金
===== ===================================== ============ ==================

⚠ **①と②でページ送りの有無が逆**（①は ``pageIndex`` を黙って無視して全件返し、
②は1ページ5件で末尾を超えると **``null``** を返す）。片方の挙動をもう片方へ
流用すると、同じ団地を何度も取るか5件目以降を永久に取り逃すかのどちらかになる。

⚠ **市区で検索する手段が無い。** エリアは UR 独自の ``area=01..``（JISでもスラグでもない）
なので、その都県の全 area を列挙して応答の ``skcs``（市区名）で**ローカルに絞る**。
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Sequence
from typing import Any

from house_search.scrape.area import CITY_VALUE_JIS, AreaTarget
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    parse_area_sqm,
    parse_fee,
    parse_floor,
    parse_months_fee,
    parse_total_floors,
)
from house_search.scrape.fetch import SiteFetcher
from house_search.scrape.prefectures import PREFECTURE_JIS

SITE_CODE = "UR"

API_ROOT = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken"
SEARCH_API = f"{API_ROOT}/search/list_bukken/"
ROOM_LIST_API = f"{API_ROOT}/detail/detail_bukken_room/"
ROOM_DETAIL_API = f"{API_ROOT}/detail/detail_room/"
SITE_ROOT = "https://www.ur-net.go.jp"

# ② の1ページあたりの住戸数（応答の ``rowMax``）。実測値。
ROOMS_PER_PAGE = 5
# area を 01 から順に叩き、空応答が返ったら止める。実測でとびとびの欠番は無いが、
# 万一に備えて空が2回続くまで見る。上限は暴走よけの安全弁（実測の最大は千葉の7）。
MAX_AREA_INDEX = 12
EMPTY_AREAS_TO_STOP = 2

# 交通欄。「JR中央線｢高尾｣駅バス7分 徒歩1～11分」「JR八高線｢北八王子｣駅 徒歩9～12分」
_ACCESS_LI = re.compile(r"<li>(.*?)</li>", re.DOTALL)
_STATION = re.compile(r"[｢「]([^｣」]+)[｣」]\s*駅")
_BUS_MINUTES = re.compile(r"バス\s*(\d+)\s*分")
# 徒歩は「1～11分」のようにレンジで出る（団地は棟が複数あるため）。
_WALK_RANGE = re.compile(r"徒歩\s*(\d+)\s*(?:[～〜~]\s*(\d+)\s*)?分")
# 1行に「徒歩◯分 または バス◯分 徒歩◯分」と選択肢が並ぶことがある。
_ACCESS_ALTERNATIVE = re.compile(r"または|又は")

# UR は礼金・仲介手数料・更新料が制度上ゼロで、保証人も不要。
# ⚠ ``rent_total``（賃料＋共益費）だけで他サイトと比べると、この初期費用の
# 有利さが**スコアに1点も現れない**。EHEYA のフラグ合成と同じやり方で、
# 辞書に手を入れずに既存の条件（PRICE_NO_REIKIN / PRICE_NO_GUARANTOR）へ載せる。
# ⚠ **敷金は UR も2か月取られる**ので「敷金なし」は足してはいけない。
INSTITUTIONAL_TOKENS = ("礼金なし", "仲介手数料なし", "更新料なし")
NO_GUARANTOR_TOKEN = "保証人不要"
# ③ の ``requirement``（保証人の要否）。「ナシ」なら保証人不要。
_NO_GUARANTOR_VALUES = frozenset({"ナシ", "なし", "無"})


def _text(value: Any) -> str | None:
    """JSONの値を文字列にする。``None`` と空文字は ``None`` に寄せる。"""
    if value is None:
        return None
    text = html.unescape(str(value)).strip()
    return text or None


def parse_access(access: str | None) -> tuple[str | None, int | None]:
    """団地の交通欄から「駅名の並び」と「駅徒歩（分）」を取り出す。

    ⚠⚠ **バス経由の行を徒歩分として採ってはいけない。**
    「JR中央線｢高尾｣駅バス7分 徒歩1～11分」の徒歩11分は**バス停からの徒歩**で、
    駅からの徒歩ではない。他サイトの ``walk_minutes`` は駅徒歩なので、
    そのまま入れると MUST の ``walk_minutes_max`` を不当に通過する。
    実測では交通欄107本の**約半数（50本）がバス経由**なので影響が大きい。

    ⚠ **徒歩はレンジで出る**（団地に棟が複数あるため）。棟によって差があるが、
    他サイトが最寄りの値を出すのに合わせて**下限**を採る。

    ⚠ **1行に徒歩とバスの両方が並ぶことがある**
    （``JR武蔵野線「東浦和」駅 徒歩16～19分 または バス3分 徒歩2～5分``）。
    行ごとに弾くと、この徒歩16分という**本物の駅徒歩を捨ててしまう**ので、
    「または」で選択肢に割ってから1つずつ見る。

    駅名はバス経由の行からも拾う（通勤時間の算出には使えるため）。
    """
    if not access:
        return None, None
    stations: list[str] = []
    walk_candidates: list[int] = []
    for line in _ACCESS_LI.findall(access) or [access]:
        plain = html.unescape(re.sub(r"<[^>]+>", " ", line)).strip()
        if not plain:
            continue
        stations.extend(_STATION.findall(plain))
        for choice in _ACCESS_ALTERNATIVE.split(plain):
            if _BUS_MINUTES.search(choice):
                continue
            walk = _WALK_RANGE.search(choice)
            if walk:
                walk_candidates.append(int(walk.group(1)))
    station_info = " / ".join(dict.fromkeys(stations)) or None
    return station_info, (min(walk_candidates) if walk_candidates else None)


def split_danchi_id(danchi_id: str) -> dict[str, str]:
    """``"20_2600"`` を ``shisya=20`` / ``danchi=260`` / ``shikibetu=0`` に分解する。"""
    shisya, rest = danchi_id.split("_", 1)
    return {"shisya": shisya, "danchi": rest[:-1], "shikibetu": rest[-1]}


def build_external_id(danchi_id: str, room_id: str) -> str:
    """``20_2600_001120513``。

    ⚠ **住戸IDは団地の中でしか一意でない。** 「棟＋部屋番号」を符号化した値
    （``001120513`` ＝ 1-12号棟513号室）なので、別の団地にも同じ番号がありうる。
    団地IDを前に置いて衝突を防ぐ。
    """
    return f"{danchi_id}_{room_id}"


def room_page_url(bukken_url: str, room_id: str) -> str:
    """住戸のページURL（通知でユーザーが開くリンク）。

    ``/chintai/kanto/tokyo/20_2600.html`` → ``.../20_2600_room.html?JKSS=001120513``。
    ⚠ このページ自体はJSで描画されるので**解析には使えない**（③のAPIを使う）が、
    ホストは ``www.ur-net.go.jp`` で robots.txt に許可されている。
    """
    return f"{SITE_ROOT}{bukken_url.removesuffix('.html')}_room.html?JKSS={room_id}"


def parse_room_page_url(url: str) -> dict[str, str] | None:
    """住戸ページURLから③のAPIパラメータを復元する。

    詳細取得とe成約確認はキューに積んだURLしか手掛かりが無いので、
    アダプタ側に状態を持たずにURLから戻せるようにしておく。
    """
    match = re.search(r"/(\d+_\d+)_room\.html\?JKSS=(\w+)", url)
    if match is None:
        return None
    params = split_danchi_id(match.group(1))
    params["id"] = match.group(2)
    return params


def room_rent(room: dict[str, Any]) -> int | None:
    """住戸の賃料（円）。

    ⚠ **賃料は ``rent`` に入るとは限らない。** 割引が適用される住戸は
    ``rent`` が**空文字**になり、割引前の家賃が ``rent_normal`` に入る
    （サイトの表示も「割引適用 前家賃 103,600円／家賃についてはお問い合わせください」）。

    ⚠⚠ **これを見落とすと ``price`` が NULL のまま黙って通る。**
    ``rent_total`` が NULL になり MUST 判定が ``unknown`` へ落ちるので、
    ``unknown_policy: keep`` の下では**賃料不明の掲載がランキングに並ぶ**。
    例外にならないので気づけない（実測で さいたま市南区の3件がこれだった）。

    割引後の額は公開されないので、**割引前の額を採る**（実際の支払いはこれ以下）。
    """
    return parse_fee(_text(room.get("rent"))) or parse_fee(_text(room.get("rent_normal")))


def _rows(payload: str) -> list[dict[str, Any]]:
    """APIの本文をレコードの配列にする。

    ⚠ **②は末尾のページを超えると ``null`` を返す**（空配列ではない）。
    そのまま反復すると ``TypeError`` になるので、ここで空扱いに寄せる。
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _as_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


class UrScraper:
    """UR賃貸住宅の取得と解析。"""

    site_code = SITE_CODE
    # 市区で検索する手段が無いので都道府県（tdfk）単位で取り、skcs でローカルに絞る。
    requires_city = False
    city_value_source = CITY_VALUE_JIS
    user_agent = None
    # ⚠ **APIホストの robots.txt は HTTP 403 を返す**（不在ではない）。
    # ``ignore_robots`` は立てない。403 は ``SiteFetcher`` 側で
    # 「記録されたうえでの全許可」として扱う（→ ADR 0019）
    ignore_robots = False
    city_rotation_limit = None
    # 賃料上限は ``collect_listings`` の中で ``rent_high`` として送る。
    # 一覧URLを持たないので既存の ``site_filters`` 機構（クエリ文字列の付与）は使えない
    supports_site_filters = False

    # ------------------------------------------------------------------
    # 任意フック（``pipeline.scan`` が getattr で見つけて委譲する）
    # ------------------------------------------------------------------
    def collect_listings(
        self,
        fetcher: SiteFetcher,
        pattern: object,
        areas: Sequence[AreaTarget],
        *,
        max_pages: int,
        outcome: Any,
    ) -> list[ScrapedListing]:
        """①団地の列挙 → ②空室の住戸、で掲載を集める。"""
        rent_high = _rent_high(pattern)
        listings: list[ScrapedListing] = []
        for tdfk, wanted_cities in _search_targets(areas).items():
            for danchi in self._iter_danchi(fetcher, tdfk, rent_high, outcome):
                city = _text(danchi.get("skcs"))
                # ⚠ エリア区分は帯の市区と一致しないので、ここで落とすのが唯一の絞り。
                # 例: 東京都 area=06（多摩117団地）は23区帯に1件も寄与しない
                if wanted_cities and city not in wanted_cities:
                    continue
                if _as_int(danchi.get("roomCount")) < 1:
                    continue
                listings.extend(self._rooms_of(fetcher, danchi, tdfk, outcome))
        return listings

    def fetch_detail(self, fetcher: SiteFetcher, url: str) -> ScrapedDetail:
        """③住戸詳細API。設備原文・築年・敷金・保証人要件を取る。

        ``_fetch_details`` は既定で ``fetcher.get(detail_url)`` するが、UR は
        POST でしか取れないのでこのフックで置き換える。キューと ``--detail-limit``
        の扱いは既存のまま共有する。
        """
        params = parse_room_page_url(url)
        if params is None:
            raise ValueError(f"UR: 住戸ページURLとして解釈できません: {url}")
        response = fetcher.post(ROOM_DETAIL_API, {**params, "sp": ""})
        rows = _rows(response.text)
        if not rows:
            raise ValueError(f"UR: 住戸詳細が空でした: {url}")
        return self.parse_room_detail(rows[0])

    # ------------------------------------------------------------------
    # 解析（純関数。ネットワークに触らないのでフィクスチャで回帰テストできる）
    # ------------------------------------------------------------------
    def parse_danchi_rooms(
        self, danchi: dict[str, Any], rooms: Iterable[dict[str, Any]], *, prefecture: str
    ) -> list[ScrapedListing]:
        """①の団地と②の住戸から掲載を組み立てる。"""
        danchi_id = _text(danchi.get("id"))
        bukken_url = _text(danchi.get("bukkenUrl"))
        if not danchi_id or not bukken_url:
            return []
        station_info, walk_minutes = parse_access(_text(danchi.get("access")))
        city = _text(danchi.get("skcs"))
        # ⚠ 住所は市区までしか取れない（①に住所欄が無く、③にも無い）。
        # 番地まで欲しければ団地ページHTMLを別に取ることになるが、
        # 団地1つにつき1リクエスト増える。市区が分かれば帯の絞り込みと
        # 採点は成立し、UR は他サイトと掲載が重ならないので名寄せキーが
        # 作れなくても実害が無い（→ 詳細設計書 §9.3）
        address = f"{prefecture}{city}" if city else prefecture
        danchi_name = _text(danchi.get("name"))

        listings: list[ScrapedListing] = []
        for room in rooms:
            room_id = _text(room.get("id"))
            if not room_id:
                continue
            room_name = _text(room.get("name"))
            rent = room_rent(room)
            listings.append(
                ScrapedListing(
                    site_code=SITE_CODE,
                    external_id=build_external_id(danchi_id, room_id),
                    url=room_page_url(bukken_url, room_id),
                    title=" ".join(part for part in (danchi_name, room_name) if part) or None,
                    price=rent,
                    mgmt_fee_monthly=parse_fee(_text(room.get("commonfee"))),
                    # 礼金は制度上ゼロ。敷金は「2か月」表記なので賃料から円へ直す
                    key_money_amount=0,
                    deposit_amount=parse_months_fee(_text(room.get("shikikin")), rent),
                    area_sqm=parse_area_sqm(_text(room.get("floorspace"))),
                    layout=_text(room.get("type")),
                    floor_num=parse_floor(_text(room.get("floor"))),
                    total_floors=parse_total_floors(_text(room.get("floorAll")))
                    or parse_floor(_text(room.get("floorAll"))),
                    address=address,
                    station_info=station_info,
                    walk_minutes=walk_minutes,
                    image_url=_text(danchi.get("image")),
                )
            )
        return listings

    def parse_room_detail(self, room: dict[str, Any]) -> ScrapedDetail:
        """③の住戸詳細から設備原文と補足項目を取り出す。

        ⚠ **②と③は同じキー名でも中身が違う。** ③では ``type`` / ``floorspace`` /
        ``commonfee`` が ``None`` で、代わりに ``madori`` / ``madoriYuka`` /
        ``commonfee_sp`` に入る。②のパーサを流用してはいけない。
        """
        parts: list[str] = []
        # ``facility`` が設備原文そのもの（「バス・トイレ別、洗面所独立、追い焚き…」）
        for key in ("facility", "feature"):
            value = _text(room.get(key))
            if value:
                parts.append(value)
        parts.extend(_named_values(room.get("feature_pickup")))
        parts.extend(_named_values(room.get("design"), key="デザイン名"))
        parts.extend(_named_values(room.get("system"), key="制度名"))
        parts.extend(INSTITUTIONAL_TOKENS)
        if _text(room.get("requirement")) in _NO_GUARANTOR_VALUES:
            parts.append(NO_GUARANTOR_TOKEN)

        # ⚠ ``year`` は築年月ではなく**築年数（年）**。日付列へは入れられないので
        # ``type_specific_attrs`` に残し、採点は age_years の導出に任せる
        age_years = _as_int(room.get("year")) or None
        attrs: dict[str, Any] = {"ur_age_years": age_years} if age_years else {}
        available = _text(room.get("availableDate"))
        if available:
            attrs["available_date"] = available

        floor_text = _text(room.get("floor"))
        return ScrapedDetail(
            raw_features_text="、".join(dict.fromkeys(parts)) or None,
            floor_num=parse_floor(floor_text),
            total_floors=parse_total_floors(floor_text) or _last_floor(floor_text),
            mgmt_fee_monthly=parse_fee(_text(room.get("commonfee_sp"))),
            key_money_amount=0,
            type_specific_attrs=attrs,
        )

    # ------------------------------------------------------------------
    # 取得（フックの下請け）
    # ------------------------------------------------------------------
    def _iter_danchi(
        self, fetcher: SiteFetcher, tdfk: str, rent_high: int | None, outcome: Any
    ) -> Iterable[dict[str, Any]]:
        """①検索API。area を 01 から順に叩き、空が続いたら止める。

        ⚠ **``pageIndex`` は黙って無視され、そのエリアの全団地が1応答で返る**
        （実測: 東京 area=06 は117件）。ページ送りを回すと同じ団地を何度も取る。
        """
        empty_streak = 0
        for index in range(1, MAX_AREA_INDEX + 1):
            payload = {
                "rent_low": "",
                "rent_high": str(rent_high) if rent_high else "",
                "floorspace_low": "",
                "floorspace_high": "",
                "tdfk": tdfk,
                "area": f"{index:02d}",
                "block": "",
                "danchi": "",
                "shisya": "",
                "pageIndex": "0",
                "orderByField": "0",
                "orderBySort": "0",
            }
            try:
                response = fetcher.post(SEARCH_API, payload)
            except Exception as exc:  # noqa: BLE001 - 1エリアの失敗で全体を止めない
                if _is_fatal(exc):
                    raise
                outcome.errors.append(f"団地一覧の取得に失敗: tdfk={tdfk} area={index:02d} ({exc})")
                return
            rows = _rows(response.text)
            if not rows:
                empty_streak += 1
                if empty_streak >= EMPTY_AREAS_TO_STOP:
                    return
                continue
            empty_streak = 0
            yield from rows

    def _rooms_of(
        self, fetcher: SiteFetcher, danchi: dict[str, Any], tdfk: str, outcome: Any
    ) -> list[ScrapedListing]:
        """②住戸一覧API。1ページ5件で、末尾を超えると ``null`` が返る。"""
        danchi_id = _text(danchi.get("id"))
        if not danchi_id:
            return []
        prefecture = _prefecture_of(tdfk)
        base = split_danchi_id(danchi_id)
        rooms: list[dict[str, Any]] = []
        expected = _as_int(danchi.get("roomCount"))
        for page in range(_page_budget(expected)):
            payload = {**base, "orderByField": "0", "orderBySort": "0", "pageIndex": str(page)}
            try:
                response = fetcher.post(ROOM_LIST_API, payload)
            except Exception as exc:  # noqa: BLE001 - 1団地の失敗で全体を止めない
                if _is_fatal(exc):
                    raise
                outcome.errors.append(f"住戸一覧の取得に失敗: {danchi_id} ({exc})")
                break
            page_rows = _rows(response.text)
            if not page_rows:
                break
            rooms.extend(page_rows)
            # ⚠ 総数は①の roomCount ではなく②の allCount を信じる。
            # rent_high を送ると①は絞り込み後の数になり、②は絞り込みを受けないためずれる
            all_count = _as_int(page_rows[0].get("allCount")) or expected
            if len(rooms) >= all_count:
                break
        return self.parse_danchi_rooms(danchi, rooms, prefecture=prefecture)

    # ------------------------------------------------------------------
    # ``SiteScraper`` の必須メソッド
    # ------------------------------------------------------------------
    def list_urls(self, pattern: object, areas: Sequence[AreaTarget]) -> list[str]:
        """UR は一覧URLを持たない（``collect_listings`` が代わりに動く）。"""
        return []

    def page_url(self, base_url: str, page: int) -> str:
        return base_url

    def is_last_page(self, count: int) -> bool:
        return True

    def parse_list(self, html_text: str) -> list[ScrapedListing]:
        """一覧HTMLは存在しない。``collect_listings`` を宣言しているので呼ばれない。"""
        raise NotImplementedError("UR は collect_listings フックで一覧を集める")

    def detail_url(self, listing_url: str) -> str:
        return listing_url

    def parse_detail(self, html_text: str) -> ScrapedDetail:
        """詳細HTMLは存在しない。``fetch_detail`` を宣言しているので呼ばれない。"""
        raise NotImplementedError("UR は fetch_detail フックで詳細を取る")

    def is_sold(self, fetcher: SiteFetcher, url: str) -> bool:
        """③のAPIが空を返したら掲載終了とみなす。

        ⚠ **住戸ページ（HTML）の有無では判定できない。** JSで描画されるため、
        空室が無くなっても HTTP 200 で「枠だけのページ」が返る。
        """
        params = parse_room_page_url(url)
        if params is None:
            return False
        try:
            response = fetcher.post(ROOM_DETAIL_API, {**params, "sp": ""})
        except Exception:  # noqa: BLE001 - 判定できないときは状態を変えない
            return False
        return not _rows(response.text)


def _named_values(value: Any, *, key: str = "") -> list[str]:
    """``["ペット可", ...]`` / ``[{"制度名": "近居割"}, ...]`` を文字列の並びにする。"""
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        text = (_text(item.get(key)) if key else None) if isinstance(item, dict) else _text(item)
        if text:
            names.append(text)
    return names


def _last_floor(text: str | None) -> int | None:
    """``"5階 /5階"``（所在階/総階数）の後ろ側を総階数として読む。"""
    if not text or "/" not in text:
        return None
    return parse_floor(text.split("/")[-1])


def _page_budget(expected_rooms: int) -> int:
    """②で回すページ数の上限。実際の打ち切りは ``allCount`` で行う。"""
    if expected_rooms < 1:
        return 1
    return -(-expected_rooms // ROOMS_PER_PAGE) + 1


def _rent_high(pattern: object) -> int | None:
    """サイト側へ渡す賃料上限。

    ⚠ **これは団地を絞らず ``roomCount`` を絞る。** 実測で東京 area=06 の
    「空室のある団地」が 38 → 18 に減り、②③のリクエストがそのぶん減った。
    ⚠ 端数を渡しても0件にならない（SUUMO の ``ct`` のような事故は起きない）。
    """
    search = getattr(pattern, "search", None)
    hint = getattr(search, "price_max_hint", None)
    return int(hint) if hint else None


def _search_targets(areas: Sequence[AreaTarget]) -> dict[str, set[str]]:
    """対象エリアを ``tdfk`` → 許可する市区名 に畳む。

    市区の指定が無い（都道府県だけの）都県は空集合になり、その場合は絞らない。
    """
    targets: dict[str, set[str]] = {}
    for area in areas:
        tdfk = _tdfk_of(area)
        if tdfk is None:
            continue
        cities = targets.setdefault(tdfk, set())
        if area.city_name:
            cities.add(area.city_name)
    return targets


def _tdfk_of(area: AreaTarget) -> str | None:
    if area.jis_code:
        return area.jis_code[:2]
    return PREFECTURE_JIS.get(area.prefecture)


def _prefecture_of(tdfk: str) -> str:
    for name, code in PREFECTURE_JIS.items():
        if code == tdfk:
            return name
    return ""


def _is_fatal(exc: Exception) -> bool:
    """サイト打ち切り・robots拒否は握りつぶさず上へ投げる。"""
    from house_search.scrape.fetch import RobotsDisallowed, SiteAborted

    return isinstance(exc, SiteAborted | RobotsDisallowed)
