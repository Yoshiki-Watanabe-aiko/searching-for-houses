"""NAVITIME の乗換案内から**実ダイヤ**の所要時間を採る。

⚠ **なぜ NAVITIME なのか（ODPT ではなく）。** 駅データ.jp 無料版には時刻表も駅間所要時間も
無く、Phase 5C の通勤時間は ``8.7 + 1.14 × 距離km + 5.6 × 乗換回数`` の回帰式で
**平均誤差5.6分・最大16.0分**にとどまっていた（→ ADR 0016）。ODPT（公共交通オープン
データセンター）に列車時刻表はあるが**登録とトークン発行に日数がかかる**うえ、
京成・北総・東葉高速などが未参加で穴が残る（→ ``data/odpt/README.md``）。
NAVITIME の乗換案内は**登録不要**で、優等列車・直通運転・乗換待ちを織り込んだ
実ダイヤの結果をそのまま返す。

この層が返すのは**素材**（経路の候補と各乗車区間の実所要時間）だけで、DBには触らない。
保存は ``commute/timetable.py`` の担当。

取得できるもの:

- **O-D の実所要時間** … 回帰式を置き換える本命。目的地までの分・乗換回数・距離
- **乗車区間の実所要時間** … 「乗った駅→降りた駅」を1本の辺として採る。
  急行が通過する駅を飛ばした区間がそのまま辺になるので、種別を表現する列を
  持たなくても優等列車が経路に乗る（前セッションで「スキップ辺方式」と呼んだもの）。
  ⚠ **辺の重みに待ち時間は入っていない**（発→着はひと続きの乗車のため）。
  待ちが要るのは乗車の先頭だけなので、辺を足し合わせても二重計上にならない

⚠ **同名異駅は黙って別の駅で検索される。** ``orvStationName=大久保`` は
「大久保（東京都）」として処理され、HTTP 200 で普通の結果が返る（実測）。
``駅名（都道府県名）`` の形式で渡すと厳密に指定できる（``日本橋（大阪府）`` が
大阪の日本橋として解決されることを実測で確認）ので、**必ず都道府県を添えて投げる**。

⚠ **月指定は ``2026/09`` 形式。** ``202609`` を渡すと黙って無視され現在時刻の結果になる
（深夜に実行すると始発帯の値が返り、朝の通勤時間だと思い込む）。応答側に
検索日が載っている（``beforeyear``/``beforemonth``/``beforeday``）ので、
``parse_search`` が**要求した日付と一致するかを検証して食い違えば例外にする**。
"""

from __future__ import annotations

import datetime as dt
import html as html_lib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import lxml.html

SEARCH_URL = "https://www.navitime.co.jp/transfer/searchlist"

# robots.txt は ClaudeBot に Crawl-delay: 10 を課している（User-agent: * には無い）。
# SiteFetcher は ±30% のジッタを掛けるので、下振れしても 10 秒を割らない値にする。
# 15.0 × 0.7 = 10.5 秒。⚠ ここを 11.0 にすると下振れ 7.7 秒で Crawl-delay を破る。
MIN_INTERVAL_SEC = 15.0

# 出発時刻基準（basis=1）。到着時刻基準の逆探索は別の値になるが本Phaseでは使わない。
BASIS_DEPARTURE = "1"


class NavitimeError(RuntimeError):
    """NAVITIME の応答が期待した形でないことを表す。

    ⚠ **黙って0件や現在時刻の結果を受け入れないための例外。** このプロジェクトは
    「HTTP 200 のまま中身が違う」失敗を繰り返し踏んでいる（SUUMO のエラーページ・
    ATHOME の認証ページ・無効パラメータの0件・Routes API の空ボディ）。
    """


@dataclass(frozen=True)
class RouteLeg:
    """経路の1区間（同じ列車に乗り続けている間、または乗換の徒歩）。

    ⚠ **「発」から「着」までが1区間とは限らない。** NAVITIME は乗り換えずに
    列車が変わる直通運転を ``（直通）東京`` の1行で表し、その前後で路線名と分が
    別々に出る。実測した例では ``08:35発 赤羽 → （直通）東京 → 08:58着 新橋`` が
    「上野東京ライン18分」「東海道本線2分」の2区間だった。ここを1区間として
    読むと**辺の重みが18分になり、実際の23分と5分ずれる**。
    """

    depart_at: str | None
    """出発時刻（``HH:MM``）。直通で引き継いだ区間は時刻が出ないので None。"""
    from_name: str
    to_name: str
    arrive_at: str | None
    line_name: str
    """路線名。種別を含む表記（``都営三田線急行``）のまま持つ。徒歩は ``徒歩``。"""
    minutes: int
    """乗車している分。**待ち時間を含まない**（ひと続きの乗車のため）。"""
    is_walk: bool
    through_from_previous: bool = False
    """直前の区間から降りずに引き継いだか（``（直通）`` の直後）。"""


@dataclass(frozen=True)
class Route:
    """経路の候補1本。"""

    rank: int
    """NAVITIME が並べた順（1始まり）。"""
    total_minutes: int
    transfers: int
    depart_at: str
    arrive_at: str
    distance_km: float | None
    fare_yen: int | None
    legs: tuple[RouteLeg, ...]
    raw_text: str = ""
    """経路の原文。DBへそのまま残し、パーサを直したときの再解析に使う。"""

    @property
    def ride_legs(self) -> tuple[RouteLeg, ...]:
        """徒歩を除いた乗車区間。"""
        return tuple(leg for leg in self.legs if not leg.is_walk)


@dataclass(frozen=True)
class RouteSearch:
    """1回の検索の結果。"""

    origin_label: str
    """NAVITIME が解決した出発駅の表記。同名異駅では ``大久保（東京都）`` になる。"""
    destination_label: str
    origin_code: str | None
    """NAVITIME の駅ノードコード。次回以降の厳密指定に使えるので残す。"""
    destination_code: str | None
    routes: tuple[Route, ...]

    @property
    def fastest(self) -> Route | None:
        """所要時間が最短の候補。

        「どれくらいで着くか」の答えとして最短を採る。同着なら乗換の少ない方。
        ⚠ **並び順（rank）で選ばない。** NAVITIME の既定の並びは
        「楽」「安」などが混ざり、必ずしも最短ではない（実測で rank 1 が43分・
        別候補が39分だった）。
        """
        if not self.routes:
            return None
        return min(self.routes, key=lambda r: (r.total_minutes, r.transfers, r.rank))


def build_search_url(
    *,
    origin: str,
    destination: str,
    depart_on: dt.date,
    depart_at: dt.time,
) -> str:
    """検索URLを組み立てる。

    ⚠ ``month`` は ``YYYY/MM``。``YYYYMM`` は黙って無視され現在時刻になる。
    """
    query = urlencode(
        {
            "orvStationName": origin,
            "dnvStationName": destination,
            "month": f"{depart_on.year:04d}/{depart_on.month:02d}",
            "day": f"{depart_on.day:02d}",
            "hour": f"{depart_at.hour:02d}",
            "minute": f"{depart_at.minute:02d}",
            "basis": BASIS_DEPARTURE,
        },
        encoding="utf-8",
    )
    return f"{SEARCH_URL}?{query}"


_STATION_NOTE_RE = re.compile(r"[（(〔\[][^）)〕\]]*[）)〕\]]\s*$")


def strip_station_note(label: str) -> str:
    """駅名の末尾に付く注記を落とす。

    NAVITIME は2種類の注記を付けて返す。

    - ``大久保（東京都）`` — こちらが**どの駅として解決したか**を示す都道府県
    - ``両国〔ＪＲ〕`` / ``町屋〔千代田線〕`` — 乗換駅で**どの路線のホームか**を示す路線名

    ⚠ **後者を落とし忘れると、同じ駅なのに「意図と違う駅」として弾いてしまう**
    （実測で29駅が保存されず回帰式の値のまま残った）。

    副名称（``押上〈スカイツリー前〉`` の ``〈〉``）はここでは触らない。
    落とすのは ``normalize_key`` の担当で、**掲載の駅を同定するときと同じ規則**を
    そのまま使うため。ここで二重に処理すると規則が2箇所に散る。
    """
    return _STATION_NOTE_RE.sub("", label).strip()


def resolved_station_matches(origin_label: str, candidates: Sequence[str]) -> bool:
    """NAVITIME が解決した駅名が、意図した駅の表記のいずれかと一致するか。

    ⚠ **候補は駅グループ内の全表記にする。** 駅データ.jp のグループは同一駅の
    別表記を束ねており（``町屋`` / ``町屋駅前``、``本八幡`` / ``京成八幡``、
    ``武蔵溝ノ口`` / ``溝の口``）、代表1つとしか照合しないと NAVITIME が
    別表記を返した時点で同じ駅を取りこぼす。
    """
    from house_search.commute.normalize import normalize_key

    resolved = normalize_key(strip_station_note(origin_label))
    return any(resolved == normalize_key(strip_station_note(name)) for name in candidates)


def station_query_name(station_name: str, prefecture: str | None) -> str:
    """``駅名（都道府県名）`` の検索語を作る。

    都道府県を添えないと同名異駅が黙って別の駅で処理される（実測）。
    """
    if not prefecture:
        return station_name
    return f"{station_name}（{prefecture}）"


_DURATION_RE = re.compile(r"(?:(\d+)\s*時間)?\s*(\d+)?\s*分")


def parse_duration_minutes(text: str) -> int:
    """``43分`` / ``1時間25分`` / ``9時間31分`` を分に直す。"""
    match = _DURATION_RE.search(text.replace(",", ""))
    if match is None or (match.group(1) is None and match.group(2) is None):
        raise NavitimeError(f"所要時間を読み取れません: {text!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return hours * 60 + minutes


_LEG_DEPART_RE = re.compile(r"^(\d{1,2}:\d{2})発\s*(.+)$")
_LEG_ARRIVE_RE = re.compile(r"^(\d{1,2}:\d{2})着\s*(.+)$")
_LEG_MINUTES_RE = re.compile(r"^\d+\s*(?:時間\s*\d*)?\s*分|^\d+\s*時間")
# 直通運転で列車が変わる地点。降りていないので乗換には数えない。
_LEG_THROUGH_RE = re.compile(r"^[（(]直通[）)]\s*(.+)$")


def parse_calendar_text(text: str) -> tuple[int, int, tuple[RouteLeg, ...]]:
    """``data-calendar-text`` の本文から乗換回数・所要時間・区間を読む。

    NAVITIME が「カレンダー登録」用に持たせている整形済みテキストで、
    HTMLの構造変更に対して**経路の明細より頑健**なのでここを正典にする。形式::

        乗換：1回
        所要時間：43分
        ...
        08:32発　赤羽
        ↓　ＪＲ埼京線　新宿行　７番ホーム　後方車両
        ↓　6分　　200円 （IC運賃：199円）
        08:38着　板橋
    """
    lines = [line.strip() for line in text.replace("　", " ").splitlines()]
    transfers: int | None = None
    total_minutes: int | None = None
    for line in lines:
        if transfers is None and line.startswith("乗換："):
            found = re.search(r"(\d+)", line)
            transfers = int(found.group(1)) if found else 0
        elif total_minutes is None and line.startswith("所要時間："):
            total_minutes = parse_duration_minutes(line)
    if transfers is None or total_minutes is None:
        raise NavitimeError("経路テキストに乗換回数か所要時間がありません")

    legs: list[RouteLeg] = []
    from_name: str | None = None
    depart_at: str | None = None
    through: bool = False
    line_name: str | None = None
    leg_minutes: int | None = None

    def close(to_name: str, arrive_at: str | None) -> None:
        """いま組み立て中の区間を確定する。"""
        nonlocal from_name, depart_at, through, line_name, leg_minutes
        if from_name is not None and line_name is not None and leg_minutes is not None:
            legs.append(
                RouteLeg(
                    depart_at=depart_at,
                    from_name=from_name,
                    to_name=to_name,
                    arrive_at=arrive_at,
                    line_name=line_name,
                    minutes=leg_minutes,
                    is_walk=line_name.startswith("徒歩"),
                    through_from_previous=through,
                )
            )
        # 読めない区間は捨てる。黙って0分にすると辺の重みが壊れるため。
        from_name = depart_at = line_name = None
        leg_minutes = None
        through = False

    for line in lines:
        depart = _LEG_DEPART_RE.match(line)
        if depart is not None:
            depart_at, from_name = depart.group(1), depart.group(2).strip()
            through, line_name = False, None
            leg_minutes = None
            continue
        if line.startswith("↓"):
            body = line.lstrip("↓").strip()
            if not body:
                continue
            # 1行目が路線名、2行目が「6分　200円」。分で始まる行を所要とみなす。
            # グリーン料金など後続の行は分で始まらないので拾わない。
            if line_name is None:
                line_name = body.split(" ")[0]
            elif leg_minutes is None and _LEG_MINUTES_RE.match(body):
                leg_minutes = parse_duration_minutes(body.split(" ")[0])
            continue
        via = _LEG_THROUGH_RE.match(line)
        if via is not None:
            # 直通運転で列車が変わる。降りていないので乗換にはならないが、
            # 路線も分も別に出るので区間としては切る。
            station = via.group(1).strip()
            close(station, None)
            from_name, depart_at, through = station, None, True
            continue
        arrive = _LEG_ARRIVE_RE.match(line)
        if arrive is not None:
            close(arrive.group(2).strip(), arrive.group(1))
    if not legs:
        raise NavitimeError("経路テキストから区間を1つも読み取れませんでした")
    return transfers, total_minutes, tuple(legs)


_DISTANCE_RE = re.compile(r"([\d.]+)\s*km")


def _summary_of(node: lxml.html.HtmlElement) -> tuple[float | None, int | None]:
    """候補ブロックから距離と運賃を読む（無ければ None）。"""
    distance: float | None = None
    for block in node.cssselect("div.summary_info_frame dl"):
        label = block.cssselect("dt")
        if label and label[0].text_content().strip() == "距離":
            found = _DISTANCE_RE.search(block.text_content())
            if found:
                distance = float(found.group(1))
    fare: int | None = None
    for field in node.cssselect("input[id^='total-fare']"):
        value = (field.get("value") or "").replace(",", "")
        if value.isdigit():
            fare = int(value)
            break
    return distance, fare


def _before_after_params(root: lxml.html.HtmlElement) -> list[dict[str, list[str]]]:
    """前後の便へのリンクのクエリを配列で返す。"""
    return [
        parse_qs(urlsplit(link.get("href") or "").query)
        for link in root.cssselect("a.before_after")
    ]


def _resolved_stations(root: lxml.html.HtmlElement) -> tuple[str, str, str | None, str | None]:
    """前後の便へのリンクから、NAVITIME が解決した駅名とノードコードを取る。

    ⚠ ``<title>`` から取らない。駅名に「から」を含む駅があると切り出しを誤るため、
    機械可読なクエリパラメータのほうを正典にする。
    """
    for params in _before_after_params(root):

        def one(key: str, params: dict[str, list[str]] = params) -> str | None:
            values = params.get(key)
            return values[0] if values else None

        origin, destination = one("orvStationName"), one("dnvStationName")
        if origin and destination:
            return origin, destination, one("orvStationCode"), one("dnvStationCode")
    raise NavitimeError("応答に出発駅・到着駅のパラメータが見つかりません")


def _searched_date(root: lxml.html.HtmlElement) -> dt.date | None:
    """応答が実際に使った検索日（結果の出発日）を読む。"""
    for params in _before_after_params(root):
        try:
            return dt.date(
                int(params["beforeyear"][0]),
                int(params["beforemonth"][0]),
                int(params["beforeday"][0]),
            )
        except (KeyError, IndexError, ValueError):
            continue
    return None


def parse_search(html: str, *, expected_date: dt.date | None = None) -> RouteSearch:
    """検索結果ページを解析する。

    ``expected_date`` を渡すと、応答が本当にその日で検索されたかを検証する。
    ⚠ **この検証を外さないこと。** 月の書式を誤ると NAVITIME は例外を出さず
    現在時刻の結果を返すため、深夜に走らせた値を朝の通勤時間だと思い込む。

    ⚠ **ただし「翌日」だけは正常として通す**（→ 課題#42）。朝の出発で当日中に
    着かない経路では、応答の前後便リンクが**到着日**を指して翌日になる。
    要求日ちょうどしか許さないと、正常な応答まで弾いて遠方の駅を取りこぼす
    （実測: 北海道411駅のうち38駅＝根室本線・釧網本線の末端が該当）。
    ⚠ **要求日より前は引き続き弾く。** 書式を誤って現在時刻へ落ちた応答は
    未来日の要求より前になるので、本来の検出力はそのまま保たれる。
    """
    root = lxml.html.fromstring(html)
    origin, destination, origin_code, destination_code = _resolved_stations(root)

    if expected_date is not None:
        actual = _searched_date(root)
        latest = expected_date + dt.timedelta(days=1)
        if actual is not None and not expected_date <= actual <= latest:
            raise NavitimeError(
                f"検索日が要求と違います: 要求 {expected_date}"
                f"（日をまたぐ経路のため {latest} まで許容）/ 応答 {actual}。"
                "month は YYYY/MM 形式で渡すこと（YYYYMM は黙って無視される）"
            )

    routes: list[Route] = []
    for index, node in enumerate(root.cssselect("li.route_detail"), start=1):
        holders = node.cssselect("[data-calendar-text]")
        if not holders:
            continue
        raw_text = html_lib.unescape(
            (holders[0].get("data-calendar-text") or "").replace("<br>", "\n")
        )
        transfers, total_minutes, legs = parse_calendar_text(raw_text)
        distance_km, fare_yen = _summary_of(node)
        if legs[0].depart_at is None or legs[-1].arrive_at is None:
            raise NavitimeError("経路の出発時刻または到着時刻を読み取れません")
        routes.append(
            Route(
                rank=index,
                total_minutes=total_minutes,
                transfers=transfers,
                depart_at=legs[0].depart_at,
                arrive_at=legs[-1].arrive_at,
                distance_km=distance_km,
                fare_yen=fare_yen,
                legs=legs,
                raw_text=raw_text,
            )
        )
    return RouteSearch(
        origin_label=origin,
        destination_label=destination,
        origin_code=origin_code,
        destination_code=destination_code,
        routes=tuple(routes),
    )
