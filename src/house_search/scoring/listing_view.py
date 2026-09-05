"""採点・MUST判定の入力となる物件ビュー。

ORM の行そのものではなくこの不変オブジェクトを介すことで、スコアリングを
「DB保存済みの属性からの純関数」に保てる。``rescore`` がネットワーク不要の
DBバッチで完結するのはこの性質による。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 「1SLDK」のサービスルーム表記。間取り比較では S を無視する
# （1SLDK は 1LDK に納戸が付いた形で、1LDK を許容するなら除外する理由がない）。
_SERVICE_ROOM = re.compile(r"(?<=\d)s(?=[ldk])", re.IGNORECASE)


def normalize_layout(layout: str | None) -> str | None:
    """間取り表記を比較用に正規化する。

    全角・空白・サービスルーム表記の揺れを吸収する。
    """
    if not layout:
        return None
    value = layout.strip().upper().replace("　", "").replace(" ", "")
    value = value.replace("ＬＤＫ", "LDK").replace("ワンルーム", "1R")
    return _SERVICE_ROOM.sub("", value.lower()).upper() or None


@dataclass(frozen=True, slots=True)
class ListingView:
    """1物件の採点に必要な属性一式。

    ``detail_fetched`` が False のときは設備が「未確認」であることを意味し、
    WANT の設備項目は miss ではなく unknown として扱う（0点なのは同じだが、
    通知に「未確認N項目」と出して判断材料にする）。
    """

    listing_id: int | None = None
    site_code: str | None = None
    url: str | None = None
    title: str | None = None

    price: int | None = None
    mgmt_fee_monthly: int | None = None
    rent_total: int | None = None
    repair_reserve_monthly: int | None = None

    area_sqm: float | None = None
    land_area_sqm: float | None = None
    building_area_sqm: float | None = None
    layout: str | None = None
    floor_num: int | None = None
    total_floors: int | None = None
    age_years: int | None = None
    walk_minutes: int | None = None
    commute_minutes: int | None = None
    """勤務先の最寄り駅までの所要時間（分）。t_listing_stations × t_station_commutes
    からの導出値で、``t_listings`` の列ではない。駅を同定できないか目的地が
    未設定なら None（= MUST は unknown）。"""

    # --- ハザード評価（→ 課題#46） -------------------------------------
    # ⚠⚠ **None と 0.0 の意味がまったく違う。**
    #   None = 住所を照合できなかった（情報が無い）→ WANT は欠損で分母から外し、
    #          MUST は unknown になる
    #   0.0  = 照合できたうえで区域外だと確認した（安全の証拠）→ WANT は満点
    # 混ぜると「危険なのに情報が無いから減点されない」掲載が「安全」と同じ扱いになり、
    # 例外にならないまま順位が狂う。
    # ⚠ t_listings の列ではなく、address_normalized から m_hazard_levels を
    # 引いた導出値（丁目で引けなければ町の値）。
    flood_rank_avg: float | None = None
    """洪水の浸水深ランク（0〜6）を丁目の全面積で加重平均した値。"""
    flood_rank_max: float | None = None
    """丁目内の最大浸水深ランク（0〜6）。MUST の足切りに使う。
    ⚠ 外れ値に引っ張られるので WANT の主力にはしない（→ 課題#46 の実測）。"""
    flood_area_ratio: float | None = None
    """丁目の面積のうち浸水域が占める割合（0〜1）。"""
    landslide_area_ratio: float | None = None
    """丁目の面積のうち土砂災害警戒区域（警戒＋特別警戒）が占める割合（0〜1）。"""
    landslide_special_ratio: float | None = None
    """同 特別警戒区域（レッドゾーン）だけの割合（0〜1）。MUST の足切りに使う。"""

    prefecture: str | None = None
    address: str | None = None
    detail_fetched: bool = False
    feature_codes: frozenset[str] = field(default_factory=frozenset)

    @property
    def normalized_layout(self) -> str | None:
        return normalize_layout(self.layout)

    @property
    def monthly_cost(self) -> int | None:
        """管理費＋修繕積立金。双方 None のときだけ None を返す。"""
        if self.mgmt_fee_monthly is None and self.repair_reserve_monthly is None:
            return None
        return (self.mgmt_fee_monthly or 0) + (self.repair_reserve_monthly or 0)

    def metric_value(self, metric: str) -> float | None:
        """metric名から値を取り出す。未定義・欠損は None。"""
        if metric == "rent_total":
            # 生成列が無いビュー（テスト・スクレイプ直後）でも計算できるようにする。
            if self.rent_total is not None:
                return float(self.rent_total)
            if self.price is None:
                return None
            return float(self.price + (self.mgmt_fee_monthly or 0))
        if metric == "monthly_cost":
            cost = self.monthly_cost
            return None if cost is None else float(cost)
        value = getattr(self, metric, None)
        return None if value is None else float(value)
