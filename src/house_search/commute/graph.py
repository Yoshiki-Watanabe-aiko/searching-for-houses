"""鉄道網をグラフとして扱い、駅間の所要時間を見積もる。

⚠ **なぜ自前で計算するのか。** Google Maps Platform は日本の公共交通経路を返さない。
Routes API に TRANSIT を投げると **HTTP 200 のまま本文が空**になり（同じ呼び出しが
米国では経路を返し、日本でも DRIVE なら返る）、レガシー Directions API でも
日本は ZERO_RESULTS になることが報告されている。駅すぱあと API はフリープランに
経路探索が含まれない。よって手元の駅データ.jp の接続情報から自前で求める（→ ADR 0016）。

**求めるのは順位付けに足る精度**であって、分単位の正確さではない。課題#24 の
「スコアに立地の観点が無く、上位が郊外で埋まる」を解くのが目的なので、
都心へ近い順に並べられれば足りる。実ダイヤ（急行・直通・待ち時間）は反映できない。

モデル:

- ノード … 駅（``station_cd``。路線ごとに別ノード）と、乗換用のグループ仮想ノード
- 乗車 … 隣接駅を結ぶ。所要は「直線距離 × 迂回係数 ÷ 表定速度」
- 乗換 … 同じ駅グループの駅どうしを仮想ノード経由で結び、固定のペナルティを課す

パラメータは実測で校正する前提で1か所に集めてある。
"""

from __future__ import annotations

import csv
import heapq
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from house_search.commute.stations import TRAIN_MASTER_DIRNAME, StationMasterError

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class RailGraphParams:
    """所要時間モデルのパラメータ。

    **経路の選び方と、所要時間の見積もり方を分けている。**

    - 前半（``speed_kmh`` / ``detour_factor`` / ``transfer_penalty_min``）は
      ダイクストラが**どの経路を選ぶか**を決めるための重み。相対比較にしか使わない
    - 後半（``minutes_*``）は選ばれた経路の**距離と乗換回数から所要時間を推定**する回帰式

    ⚠ **一律の表定速度だけでは長距離が大きく過大になる**（実測で平均18.2分・最大72分の
    ずれ。近距離は合うのに遠方ほど外れる。優等列車を表現できないため）。距離と乗換回数の
    線形回帰にすると平均5.6分・最大16.6分まで縮む。

    係数は NAVITIME の実測12ペア（芝公園ゆき・水曜08:30発・2026-09-03）で最小二乗した値。
    1kmあたり1.14分は実効52km/h、乗換5.6分、定数8.7分は初乗りの待ち時間にあたる。
    ⚠ **目的地を変えても係数は流用できる**（距離と乗換の関係は路線網の性質なので）が、
    大きく違う地域を対象にするなら測り直すこと。
    """

    speed_kmh: float = 35.0
    detour_factor: float = 1.15
    """直線距離を線路の実距離へ補正する係数。"""
    transfer_penalty_min: float = 5.0
    """乗換1回あたりの分（経路選択の重み）。"""

    minutes_per_km: float = 1.14
    """経路上1kmあたりの分。"""
    minutes_per_transfer: float = 5.6
    """乗換1回あたりの分（所要時間の推定）。"""
    minutes_base: float = 8.7
    """定数項。初乗りの待ち時間にあたる。"""

    def estimate_minutes(self, distance_km: float, transfers: int) -> int:
        """経路の距離と乗換回数から所要時間（分）を見積もる。"""
        if distance_km <= 0 and transfers == 0:
            return 0  # 目的地そのもの
        return math.ceil(
            self.minutes_base
            + self.minutes_per_km * distance_km
            + self.minutes_per_transfer * transfers
        )


@dataclass(frozen=True)
class StationNode:
    """グラフのノードになる駅。"""

    station_cd: int
    station_g_cd: int
    lat: float
    lon: float


@dataclass(frozen=True)
class CommuteEstimate:
    """駅グループ1つに対する見積もり。"""

    station_g_cd: int
    minutes: int
    transfers: int
    distance_km: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の大円距離（km）。"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def load_links(data_dir: Path) -> tuple[tuple[int, int], ...]:
    """駅の隣接関係（``join*.csv``）を読む。

    駅マスタと同じディレクトリにある前提。ファイル名に取得日が入るので glob で拾う。
    """
    directory = data_dir / TRAIN_MASTER_DIRNAME
    matches = sorted(directory.glob("join*.csv"))
    if not matches:
        raise StationMasterError(
            f"駅の接続情報が見つかりません: {directory / 'join*.csv'}\n"
            "data/train_master/README.md の手順で駅データ.jp から取得してください"
        )
    with matches[-1].open(encoding="utf-8", newline="") as fh:
        return tuple(
            (int(row["station_cd1"]), int(row["station_cd2"])) for row in csv.DictReader(fh)
        )


# 乗換用の仮想ノードは駅グループコードの符号を反転させて表す。
# station_cd と station_g_cd は同じ値を取りうるので、符号で名前空間を分ける。
def _group_node(station_g_cd: int) -> int:
    return -station_g_cd


@dataclass(frozen=True)
class _Edge:
    """隣のノードへの辺。"""

    to_node: int
    minutes: float
    distance_km: float
    is_transfer: bool


def build_graph(
    stations: Sequence[StationNode],
    links: Iterable[tuple[int, int]],
    params: RailGraphParams,
) -> dict[int, list[_Edge]]:
    """隣接リストを組み立てる。

    駅マスタに無い駅（廃止済みなど）を含むリンクは捨てる。
    """
    by_cd = {station.station_cd: station for station in stations}
    graph: dict[int, list[_Edge]] = {}

    def connect(a: int, b: int, edge_a: _Edge, edge_b: _Edge) -> None:
        graph.setdefault(a, []).append(edge_a)
        graph.setdefault(b, []).append(edge_b)

    for cd1, cd2 in links:
        left, right = by_cd.get(cd1), by_cd.get(cd2)
        if left is None or right is None:
            continue
        distance = haversine_km(left.lat, left.lon, right.lat, right.lon)
        minutes = distance * params.detour_factor / params.speed_kmh * 60
        connect(
            cd1,
            cd2,
            _Edge(cd2, minutes, distance, False),
            _Edge(cd1, minutes, distance, False),
        )

    # 乗換。駅 ↔ グループ仮想ノードを結び、グループから駅へ出るときだけ
    # 乗換1回として数える（駅A→G→駅B でちょうど1回になる）。
    half = params.transfer_penalty_min / 2
    for station in stations:
        node = _group_node(station.station_g_cd)
        connect(
            station.station_cd,
            node,
            _Edge(node, half, 0.0, False),
            _Edge(station.station_cd, half, 0.0, True),
        )
    return graph


def estimate_from(
    stations: Sequence[StationNode],
    links: Iterable[tuple[int, int]],
    origin_g_cd: int,
    params: RailGraphParams | None = None,
) -> dict[int, CommuteEstimate]:
    """1つの駅グループから全駅グループへの所要時間を一度に求める。

    ダイクストラの単一始点最短路なので、目的地を1つ決めれば全駅ぶんが1回で出る。
    駅ペアごとにAPIを叩く必要がそもそも無い。
    """
    params = params or RailGraphParams()
    graph = build_graph(stations, links, params)

    origins = [s.station_cd for s in stations if s.station_g_cd == origin_g_cd]
    if not origins:
        return {}

    # (分, 乗換回数, 距離, ノード)。分で比較し、同着なら乗換の少ない方を採る
    best: dict[int, tuple[float, int, float]] = {}
    queue: list[tuple[float, int, float, int]] = []
    for node in origins:
        best[node] = (0.0, 0, 0.0)
        heapq.heappush(queue, (0.0, 0, 0.0, node))

    while queue:
        minutes, transfers, distance, node = heapq.heappop(queue)
        if best.get(node, (math.inf, 0, 0.0))[0] < minutes:
            continue
        for edge in graph.get(node, ()):
            next_minutes = minutes + edge.minutes
            next_transfers = transfers + (1 if edge.is_transfer else 0)
            next_distance = distance + edge.distance_km
            current = best.get(edge.to_node)
            if current is None or (next_minutes, next_transfers) < (current[0], current[1]):
                best[edge.to_node] = (next_minutes, next_transfers, next_distance)
                heapq.heappush(
                    queue, (next_minutes, next_transfers, next_distance, edge.to_node)
                )

    # 駅ノードだけをグループ単位に畳む（仮想ノードは乗換ペナルティの半分を含むため使わない）。
    # ⚠ 畳むときは分を丸める前の値で比べる。丸めてから比べると同着が増え、
    # どの駅が選ばれるかが実行ごとに揺れる。
    folded: dict[int, tuple[float, int, float]] = {}
    for station in stations:
        found = best.get(station.station_cd)
        if found is None:
            continue
        current = folded.get(station.station_g_cd)
        if current is None or found[:2] < current[:2]:
            folded[station.station_g_cd] = found
    return {
        group_code: CommuteEstimate(
            station_g_cd=group_code,
            minutes=params.estimate_minutes(distance, transfers),
            transfers=transfers,
            distance_km=round(distance, 2),
        )
        for group_code, (_, transfers, distance) in folded.items()
    }


def station_nodes(rows: Iterable[tuple[int, int, float, float]]) -> tuple[StationNode, ...]:
    """``(station_cd, station_g_cd, lat, lon)`` の並びからノードを作る。"""
    return tuple(
        StationNode(
            station_cd=int(cd), station_g_cd=int(g_cd), lat=float(lat), lon=float(lon)
        )
        for cd, g_cd, lat, lon in rows
    )


def as_mapping(estimates: Iterable[CommuteEstimate]) -> Mapping[int, CommuteEstimate]:
    """駅グループコードで引ける形にする。"""
    return {estimate.station_g_cd: estimate for estimate in estimates}
