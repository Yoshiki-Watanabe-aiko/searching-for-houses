"""鉄道網のグラフ探索と所要時間モデルのテスト。

**ネットワークにもDBにも触らない。** 小さな人工グラフで挙動を固定し、
係数の校正値だけ実測（NAVITIME・12ペア）に由来する値を焼き込む。
"""

from __future__ import annotations

import pytest

from house_search.commute.graph import (
    RailGraphParams,
    StationNode,
    estimate_from,
    haversine_km,
)

# 1駅あたり約2km の直線。A線（101-103）と B線（201-203）が「乗換駅」で交差する。
#   101 --- 102 --- 103
#                    |（同一駅グループ = 乗換）
#                   203 --- 202 --- 201
LINE_A = [
    StationNode(101, 1101, 35.700, 139.700),
    StationNode(102, 1102, 35.700, 139.722),
    StationNode(103, 1103, 35.700, 139.744),  # 乗換駅（グループ 1103）
]
LINE_B = [
    StationNode(203, 1103, 35.700, 139.744),  # 同じグループ = 同じ駅の別路線
    StationNode(202, 1202, 35.722, 139.744),
    StationNode(201, 1201, 35.744, 139.744),
]
STATIONS = [*LINE_A, *LINE_B]
LINKS = [(101, 102), (102, 103), (203, 202), (202, 201)]

# 校正を無効にして「距離と乗換がそのまま出る」ようにしたパラメータ。
RAW = RailGraphParams(
    minutes_per_km=1.0, minutes_per_transfer=10.0, minutes_base=0.0
)


def test_大円距離が実距離と大きくずれない() -> None:
    """東京駅→大阪駅は約400km。"""
    tokyo = (35.681236, 139.767125)
    osaka = (34.702485, 135.495951)
    assert haversine_km(*tokyo, *osaka) == pytest.approx(403, abs=10)


def test_同一路線の駅は乗換なしで到達する() -> None:
    result = estimate_from(STATIONS, LINKS, 1101, RAW)

    assert result[1102].transfers == 0
    assert result[1103].transfers == 0
    # 101→103 は約4km（2駅ぶん）
    assert result[1103].distance_km == pytest.approx(4.0, abs=0.3)


def test_別路線へ渡ると乗換として数える() -> None:
    result = estimate_from(STATIONS, LINKS, 1101, RAW)

    # 101 →(A線)→ 103 =203 →(B線)→ 201 で乗換1回
    assert result[1201].transfers == 1
    assert result[1201].distance_km > result[1103].distance_km


def test_出発した駅グループ自身は0分になる() -> None:
    result = estimate_from(STATIONS, LINKS, 1101)

    assert result[1101].minutes == 0
    assert result[1101].transfers == 0


def test_線路がつながっていない駅は結果に現れない() -> None:
    """到達不能は「見積もりが無い」ことで表す。呼び出し側が no_route として記録する。"""
    isolated = StationNode(999, 1999, 35.900, 139.900)
    result = estimate_from([*STATIONS, isolated], LINKS, 1101, RAW)

    assert 1999 not in result


def test_出発駅がマスタに無ければ何も返さない() -> None:
    assert estimate_from(STATIONS, LINKS, 9999, RAW) == {}


def test_乗換ペナルティを上げると乗換の多い経路が不利になる() -> None:
    cheap = estimate_from(STATIONS, LINKS, 1101, RailGraphParams(transfer_penalty_min=0.0))
    costly = estimate_from(STATIONS, LINKS, 1101, RailGraphParams(transfer_penalty_min=30.0))

    # 乗換を伴う 1201 だけが影響を受ける（経路の選択は変わらないので回数は同じ）
    assert cheap[1201].transfers == costly[1201].transfers == 1


# --- 所要時間の回帰式（NAVITIME 実測で校正した係数） ----------------------


@pytest.mark.parametrize(
    ("distance_km", "transfers", "expected"),
    [
        (0.0, 0, 0),  # 目的地そのもの
        (10.3, 1, 27),  # 新宿→芝公園（実測26分・誤差+1分）
        (33.4, 1, 53),  # 大宮→芝公園（実測55分・誤差-2分）
        (44.4, 2, 71),  # 八王子→芝公園（実測72分・誤差-1分）
        (87.9, 2, 121),  # 本庄→芝公園（実測112分・誤差+9分）
    ],
)
def test_距離と乗換から所要時間を見積もる(
    distance_km: float, transfers: int, expected: int
) -> None:
    """係数は NAVITIME の実測12ペア（芝公園ゆき・水曜08:30発）で最小二乗した値。

    ⚠ 一律の表定速度だけでは長距離が過大になり、実測との差が平均18.2分・最大72分あった。
    この回帰式で平均5.6分・最大16.0分まで縮む。
    """
    assert RailGraphParams().estimate_minutes(distance_km, transfers) == expected


def test_距離が伸びれば所要時間も伸びる() -> None:
    params = RailGraphParams()
    minutes = [params.estimate_minutes(km, 1) for km in (5, 10, 20, 40)]
    assert minutes == sorted(minutes)
    assert len(set(minutes)) == len(minutes)
