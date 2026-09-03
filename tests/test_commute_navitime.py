"""NAVITIME の乗換案内パーサの回帰テスト（Phase 5D）。

実HTMLフィクスチャで固定する。値は 2026-09-09（水）08:30発・芝公園ゆきの実測。
DBもネットワークも要らない。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.commute.navitime import (
    MIN_INTERVAL_SEC,
    NavitimeError,
    build_search_url,
    parse_calendar_text,
    parse_duration_minutes,
    parse_search,
    resolved_station_matches,
    station_query_name,
)
from house_search.commute.timetable import (
    SegmentObservation,
    harvest_segments,
    merge_observations,
)

FIXTURES = Path(__file__).parent / "fixtures" / "navitime"
SEARCHED_ON = dt.date(2026, 9, 9)


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def akabane() -> str:
    return _load("searchlist_akabane_to_shibakoen.html")


@pytest.fixture
def okubo() -> str:
    return _load("searchlist_okubo_to_shibakoen.html")


# --- 検索URLの組み立て -------------------------------------------------------


def test_月は年スラッシュ月の形式で渡す():
    """⚠ ``202609`` を渡すと NAVITIME は黙って無視し現在時刻の結果を返す。"""
    url = build_search_url(
        origin="赤羽（東京都）",
        destination="芝公園（東京都）",
        depart_on=dt.date(2026, 9, 9),
        depart_at=dt.time(8, 30),
    )
    assert "month=2026%2F09" in url
    assert "day=09" in url
    assert "hour=08" in url
    assert "minute=30" in url


def test_駅名には都道府県を添える():
    """同名異駅が黙って別の駅として処理されるのを防ぐ。"""
    assert station_query_name("大久保", "東京都") == "大久保（東京都）"
    assert station_query_name("大久保", None) == "大久保"


def test_取得間隔はクロールディレイを下回らない():
    """⚠ SiteFetcher は ±30% のジッタを掛ける。下振れで 10 秒を割ってはいけない。"""
    assert MIN_INTERVAL_SEC * 0.7 >= 10.0


# --- 所要時間の書式 ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("43分", 43),
        ("所要時間：39分", 39),
        ("1時間25分", 85),
        ("9時間31分", 571),
    ],
)
def test_所要時間の書式を分に直す(text: str, expected: int):
    assert parse_duration_minutes(text) == expected


def test_読み取れない所要時間は例外にする():
    with pytest.raises(NavitimeError):
        parse_duration_minutes("しばらく")


# --- 経路テキストの解析 ------------------------------------------------------


def test_直通で列車が変わる区間を切り分ける():
    """⚠ 発から着までを1区間として読むと辺の重みが5分ずれる（実測）。

    ``08:35発 赤羽 → （直通）東京 → 08:58着 新橋`` は
    「上野東京ライン18分」「東海道本線2分」の2区間。
    """
    raw = "\n".join(
        [
            "乗換：2回",
            "所要時間：39分",
            "",
            "08:35発　赤羽",
            "↓　ＪＲ上野東京ライン　平塚〔上野経由〕行　３番ホーム",
            "↓　18分　　",
            "（直通）東京",
            "↓　ＪＲ東海道本線　平塚行　１０番ホーム　前・中方車両",
            "↓　2分　　350円 （IC運賃：341円）",
            "↓　自由席 0円　Suica グリーン料金 750円　グリーン席 1,010円　",
            "08:58着　新橋",
        ]
    )
    transfers, total, legs = parse_calendar_text(raw)
    assert (transfers, total) == (2, 39)
    assert [(leg.from_name, leg.to_name, leg.line_name, leg.minutes) for leg in legs] == [
        ("赤羽", "東京", "ＪＲ上野東京ライン", 18),
        ("東京", "新橋", "ＪＲ東海道本線", 2),
    ]
    assert legs[0].through_from_previous is False
    assert legs[1].through_from_previous is True
    # グリーン料金の行を所要時間として拾わない。
    assert legs[1].minutes == 2


def test_徒歩の乗換も区間として拾う():
    raw = "\n".join(
        [
            "乗換：1回",
            "所要時間：43分",
            "",
            "08:38発　板橋",
            "↓　徒歩",
            "↓　10分　　",
            "08:48着　新板橋",
        ]
    )
    _, _, legs = parse_calendar_text(raw)
    assert legs[0].is_walk is True
    assert legs[0].minutes == 10


def test_区間が1つも無い経路は例外にする():
    with pytest.raises(NavitimeError):
        parse_calendar_text("乗換：0回\n所要時間：10分\n")


# --- 検索結果ページの解析 ----------------------------------------------------


def test_赤羽から芝公園の経路を読む(akabane: str):
    search = parse_search(akabane, expected_date=SEARCHED_ON)
    assert (search.origin_label, search.destination_label) == ("赤羽", "芝公園")
    assert (search.origin_code, search.destination_code) == ("00005069", "00003415")
    assert len(search.routes) == 5

    fastest = search.fastest
    assert fastest is not None
    # ⚠ 並び順の1本目（43分）ではなく最短（39分）を採る。
    assert search.routes[0].total_minutes == 43
    assert (fastest.total_minutes, fastest.transfers) == (39, 2)
    assert (fastest.depart_at, fastest.arrive_at) == ("08:35", "09:14")
    assert fastest.distance_km == 18.4
    assert fastest.fare_yen == 530
    assert fastest.raw_text.startswith("乗換：2回")


def test_同名異駅は都道府県つきの表記で返る(okubo: str):
    """⚠ NAVITIME は同名異駅を黙って選ぶ。どちらに解決されたかは表記で分かる。"""
    search = parse_search(okubo, expected_date=SEARCHED_ON)
    assert search.origin_label == "大久保（東京都）"
    fastest = search.fastest
    assert fastest is not None
    assert (fastest.total_minutes, fastest.transfers) == (31, 2)


def test_検索日が要求と違えば例外にする(akabane: str):
    """⚠ 月の書式を誤ると現在時刻の結果が返る。黙って受け入れない。"""
    with pytest.raises(NavitimeError, match="検索日が要求と違います"):
        parse_search(akabane, expected_date=dt.date(2026, 9, 10))


def test_経路が無いページは例外にする():
    with pytest.raises(NavitimeError):
        parse_search("<html><body>経路がありません</body></html>")


# --- 乗車区間の採取 ----------------------------------------------------------


def test_駅名を解決できない区間は捨てて数える(akabane: str):
    search = parse_search(akabane, expected_date=SEARCHED_ON)
    fastest = search.fastest
    assert fastest is not None

    known = {"赤羽": 1, "東京": 2, "新橋": 3, "三田": 4, "芝公園": 5}
    segments, dropped = harvest_segments(fastest, known.get)
    assert dropped == 0
    assert [(s.from_station_g_cd, s.to_station_g_cd, s.minutes) for s in segments] == [
        (1, 2, 18),
        (2, 3, 2),
        (3, 4, 4),
        (4, 5, 1),
    ]

    partial = {"赤羽": 1, "東京": 2}
    segments, dropped = harvest_segments(fastest, partial.get)
    assert len(segments) == 1
    assert dropped == 3


def test_同じ区間の観測は最小の分へ畳む():
    """⚠ ``ON CONFLICT`` は同一コマンド内で同じ行を2度更新できない。"""
    rows = [
        SegmentObservation(1, 2, "ＪＲ山手線", 5, False),
        SegmentObservation(1, 2, "ＪＲ山手線", 4, False),
        SegmentObservation(1, 2, "東京メトロ南北線", 6, False),
    ]
    merged = merge_observations(rows)
    assert len(merged) == 2
    by_line = {row.line_name: row.minutes for row in merged}
    assert by_line == {"ＪＲ山手線": 4, "東京メトロ南北線": 6}


class TestResolvedStationMatches:
    """NAVITIME が解決した駅名の照合（→ 課題#34・29駅が弾かれた件）。"""

    def test_strips_line_note(self) -> None:
        """乗換駅には路線注記が付く。実測で 両国 → 両国〔ＪＲ〕 が返った。"""
        assert resolved_station_matches("両国〔ＪＲ〕", ("両国",))

    def test_strips_prefecture_note(self) -> None:
        """同名異駅の解決結果は 大久保（東京都） の形で返る。"""
        assert resolved_station_matches("大久保（東京都）", ("大久保",))

    def test_matches_any_alias_in_group(self) -> None:
        """駅グループ内の別表記でも同じ駅として通す。

        実測で 町屋 → 町屋〔千代田線〕、武蔵溝ノ口 → 溝の口 が返った。
        代表名だけと照合すると同じ駅を取りこぼす。
        """
        assert resolved_station_matches("町屋〔千代田線〕", ("町屋", "町屋駅前"))
        assert resolved_station_matches("溝の口", ("武蔵溝ノ口", "溝の口"))
        assert resolved_station_matches("京成八幡", ("本八幡", "京成八幡"))

    def test_rejects_different_station(self) -> None:
        """別の駅が返ったら弾く。ここを緩めると取り違えに気づけなくなる。

        ⚠ **同名異駅そのものは、この照合では検知できない。** 注記を落とすため
        ``大久保（兵庫県）`` も ``大久保`` と一致してしまう。同名異駅は
        検索語に都道府県を添えること（``station_query_name``）で防いでいる。
        """
        assert not resolved_station_matches("新宿", ("両国", "両国駅前"))

    def test_treats_subname_as_optional(self) -> None:
        """副名称は付いていなくても同じ駅として通す。

        ``押上〈スカイツリー前〉`` の ``〈〉`` はマスタ側にだけ付く表記で、
        NAVITIME も掲載も付けないことがある。落とすのは ``normalize_key`` の担当で、
        **掲載の駅を同定するときと同じ規則**をそのまま使っている。
        """
        assert resolved_station_matches("押上", ("押上〈スカイツリー前〉",))
        assert resolved_station_matches("獨協大学前", ("獨協大学前〈草加松原〉",))
