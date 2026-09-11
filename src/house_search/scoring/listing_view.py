"""採点・MUST判定の入力となる物件ビュー。

ORM の行そのものではなくこの不変オブジェクトを介すことで、スコアリングを
「DB保存済みの属性からの純関数」に保てる。``rescore`` がネットワーク不要の
DBバッチで完結するのはこの性質による。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from house_search.scoring.utility import UtilityEstimate, UtilityNotAttachedError

# 「1SLDK」のサービスルーム表記。間取り比較では S を無視する
# （1SLDK は 1LDK に納戸が付いた形で、1LDK を許容するなら除外する理由がない）。
_SERVICE_ROOM = re.compile(r"(?<=\d)s(?=[ldk])", re.IGNORECASE)
# ⚠ **SUUMO 売買は納戸を「2LDK+S（納戸）」と後置する**（賃貸の「2SLDK」とは別表記）。
# 落とさないと MUST の layouts に当たらず **fail** する（unknown ではないので
# 「判定できなかった」ようにも見えない）。実測 2026-09-06 で既存DBに2件あった。
_SERVICE_ROOM_SUFFIX = re.compile(r"\+s(?:（納戸）|\(納戸\))?", re.IGNORECASE)


def normalize_layout(layout: str | None) -> str | None:
    """間取り表記を比較用に正規化する。

    全角・空白・サービスルーム表記の揺れを吸収する。

    ⚠ **複数の間取りをまとめた表記（「1LDK～3LDK」）は潰さない。**
    新築の棟単位の掲載がこの形で、下限へ丸めると実態を過小に表現する。
    判定側（``must._check_layouts``）が unknown に落とす（→ 課題#4）。
    """
    if not layout:
        return None
    value = layout.strip().upper().replace("　", "").replace(" ", "")
    value = value.replace("ＬＤＫ", "LDK").replace("ワンルーム", "1R")
    value = _SERVICE_ROOM_SUFFIX.sub("", value.lower())
    return _SERVICE_ROOM.sub("", value).upper() or None


@dataclass(frozen=True, slots=True)
class StationAccess:
    """掲載が挙げる駅の1件。**表示専用**で採点には使わない。

    ⚠ 通知の「徒歩10分」が**どの駅からなのか分からない**という
    ユーザー報告（2026-09-07）への対応。掲載は複数駅を挙げるのが普通で、
    徒歩は最小の駅、通勤は最短の駅と**別の駅になりうる**。

    ``walk_minutes`` は駅徒歩だけ（バス便は None → 課題#58）。
    """

    name: str
    walk_minutes: int | None = None
    commute_minutes: int | None = None


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
    # 物件種別のファミリ（CHINTAI / MANSION_BUY / KODATE_BUY / TOCHI_BUY）。
    # 通知の金額表示が賃貸と売買で変わるので、採点用ビューから運ぶ。
    property_family: str | None = None

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
    # ⚠ 危険ランク（1〜5）。原典のレベルとは向きが逆で、生成側で反転済み
    #    （→ ADR 0023 決定1）。⚠ None は未解決で、分母から外れる
    liquefaction_rank_avg: float | None = None
    """丁目の面積のうち土砂災害警戒区域（警戒＋特別警戒）が占める割合（0〜1）。"""
    landslide_special_ratio: float | None = None
    """同 特別警戒区域（レッドゾーン）だけの割合（0〜1）。MUST の足切りに使う。"""

    # 相場との比較（→ 課題#49）。⚠ t_listings の列ではなく、
    # city_id × layout で m_market_rates を引いた導出値。
    market_rate_ratio: float | None = None
    """``rent_total ÷ 同じ市区・同じ間取りの相場``。1.0 未満が相場より安い。

    ⚠ **1.0 を基準に best/worst を置いてはいけない。** MUST が安い掲載だけを
    集めているため実測の中央値は 0.54（p10 0.43 / p90 0.74）で、
    母集団の分布に合わせないと配点が死ぬ（→ 課題#31）。
    ⚠ 相場は市区・間取りによって**マンションの相場とアパートの相場が混ざる**
    （マンションに無いセルはアパートで補完している → 課題#49）。
    """

    prefecture: str | None = None
    address: str | None = None
    detail_fetched: bool = False
    feature_codes: frozenset[str] = field(default_factory=frozenset)
    # 掲載（名寄せしたグループ全体）が挙げる駅。**表示専用**で採点には使わない
    # （徒歩と通勤の数値は walk_minutes / commute_minutes が持つ）。
    stations: tuple[StationAccess, ...] = ()
    # 取引上の注意事項（建築条件付き・借地権・再建築不可 → 課題#61 論点5）。
    # **表示専用**で採点にも MUST にも使わない（除外 MUST は件数を実測してから設計する）。
    # ⚠ 掲載そのものの ``type_specific_attrs`` から読む（グループの和集合にはしない。
    #   建築条件は区画ごとに違いうるので、別の掲載の条件を混ぜると誤読の元になる）
    caveats: tuple[str, ...] = ()
    # 借地かどうか（→ 課題#65）。「所有権のみ」の MUST（``freehold_only``）が読む。
    # ⚠ **グループ全体の権利形態の原文から導く**（1件でも借地なら True）。
    #   None ＝ 読めない（詳細未取得・新築マンションの棟・原文が ``‐``）で、MUST は unknown。
    # ⚠ ``caveats`` の「借地権」（土地のアダプタが立てる真偽値・表示専用）とは別物
    leasehold: bool | None = None
    # 推定光熱費（→ 課題#64）。パターンに ``utility`` があるときだけ付く。
    # ⚠ 付いていないビューで ``living_cost`` を求めると例外にする（下の metric_value）。
    utility: UtilityEstimate | None = None

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
        if metric == "living_cost":
            rent = self.metric_value("rent_total")
            if rent is None:
                return None  # 賃料が無ければ欠損（光熱費だけでは月額にならない）
            # ⚠⚠ **光熱費を 0 円として足さない。** 付け忘れた経路で光熱費0円の物件が
            # 最安側へ行く（→ ADR 0021 決定4 と同じ形）。欠損にしても weight 40 が
            # 分母から消えて賃料が採点から抜けるので、どちらも例外にならない。止める。
            if self.utility is None:
                raise UtilityNotAttachedError(
                    "living_cost を求めましたが光熱費の見積もりが付いていません"
                    "（load_listing_views に utility_profile を渡していない経路があります）"
                )
            return rent + self.utility.monthly_yen
        if metric == "monthly_cost":
            cost = self.monthly_cost
            return None if cost is None else float(cost)
        value = getattr(self, metric, None)
        return None if value is None else float(value)
