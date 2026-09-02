"""サイト側の絞り込みパラメータ（MUST 限定）。

v2 の原則は「サイトは粗く取り、判定と採点はローカル」（→ ADR 0003）。この方針は
**WANT** をサイト側へ渡せないことを言っている。WANT をフォームに渡すと、対応サイトでは
加点対象の掲載が除外されて順位に現れず、非対応サイトでは素通りするからである。

**MUST は事情が違う。** ローカルで `fail` にする掲載は、そもそもDBにも入らず採点も
されない。サイト側で落としても結果は変わらないので、母集団を削って取得量を減らせる
（→ ADR 0015 が ADR 0003 を補強する）。

⚠ **設備条件（`must.features`）だけは永久にサイト側へ送らない。** 辞書抽出と
サイトのタグ付けは判定が食い違い、こちらが `unknown` として残したい掲載を
サイトが落としてしまう。数値系・間取りに限る。

## 丸めの向きは軸の意味から機械的に決まる

サイト側へ渡す値は必ず「ローカルのMUSTと同じか、より緩い」集合を返さなければならない。
判定の正はローカルにあり、サイト側フィルタは母集団削減の最適化にすぎない。

* 上限（`*_max`）は**切り上げ**る。徒歩12分が欲しくて選択肢が 10/15 分なら 15 分を送る
* 下限（`*_min`）は**切り下げ**る。30㎡が欲しくて選択肢が 25/35 なら 25 を送る
* 集合（`layouts`）は**全項目を表現できるときだけ**送る。1つでも対応表に無ければ
  その軸ごと送らない（部分集合を送ると取りこぼす）

この向きをサイトごとに書かせると必ず間違えるので、``AXIS_BOUND`` で軸ごとに1回だけ決める。

## 選択肢を外すと HTTP 200 のまま0件になる

SUUMO の賃料上限 `ct` は選択肢が決まっており、端数を渡すと**掲載0件で正常終了する**
（実測: `ct=15.6` で0件・`ct=16.0` で100件 → 課題#29）。エラーにならないので
「取れているつもり」で気づけない。丸めを型で強制するのはこの事故を防ぐため。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

# 値の丸め方向。
UPPER = "upper"  # 上限条件。要求値以上で最小の選択肢へ切り上げる
LOWER = "lower"  # 下限条件。要求値以下で最大の選択肢へ切り下げる
SET = "set"  # 集合条件。全項目を表現できるときだけ送る


class ParamAxis(StrEnum):
    """サイト側へ渡してよい MUST の軸。

    ``rent_total_max`` は**あえて含めない**。SUUMO などの賃料上限は既に
    ``search.price_max_hint`` の経路が担っており（課題#29 を修正済みの実績がある）、
    軸を足すと同じURLパラメータに供給源が2つできる。バッファ幅の判断を
    ユーザーの手に残す意味もある。
    """

    AREA_MIN = "area_min"
    AREA_MAX = "area_max"
    WALK_MINUTES_MAX = "walk_minutes_max"
    AGE_MAX = "age_max"
    LAYOUTS = "layouts"


# 軸ごとの丸め方向。サイト別に持たせない（間違えようがないようにする）。
AXIS_BOUND: Mapping[str, str] = {
    ParamAxis.AREA_MIN: LOWER,
    ParamAxis.AREA_MAX: UPPER,
    ParamAxis.WALK_MINUTES_MAX: UPPER,
    ParamAxis.AGE_MAX: UPPER,
    ParamAxis.LAYOUTS: SET,
}

# MUST の値の単位から、サイトが受け取る単位への換算。
# MUST 側は 円 / ㎡ / 分 / 年 で持っている。
UNIT_DIVISOR: Mapping[str, Decimal] = {
    "yen": Decimal(1),
    "man_yen": Decimal(10_000),
    "sqm": Decimal(1),
    "minutes": Decimal(1),
    "years": Decimal(1),
}

# value_kind の値。
KIND_STEPPED = "stepped"  # 等間隔の選択肢（min/max/step で表す）
KIND_ENUM = "enum"  # 不等間隔の選択肢（choices で列挙する）
KIND_MULTI = "multi"  # 複数値を並べて送る（間取りなど。mapping で対応づける）


class ParamError(ValueError):
    """パラメータ定義そのものの誤り。設定の読み込み時に落とす。"""


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """1サイト・1物件種別・1軸ぶんのURLパラメータ定義。

    正典は ``data/site_search_params.yaml``。``sync-site-params`` で
    ``m_site_search_params`` へ同期し、実行時はDBから読む
    （設備抽出辞書と同じ構成）。
    """

    site_code: str
    property_type: str
    axis: str
    param_name: str
    value_kind: str
    unit: str
    value_spec: Mapping[str, Any]
    is_enabled: bool = True
    notes: str | None = None

    @property
    def bound(self) -> str:
        """丸めの向き。軸から決まるのでサイト定義には持たせない。"""
        return AXIS_BOUND[self.axis]

    def render(self, value: object) -> dict[str, list[str]] | None:
        """MUST の値をURLパラメータへ変換する。送れないときは ``None``。

        ``None`` を返すのは「その軸を送らない」という意味で、エラーではない。
        母集団が広いまま取得されるだけで、判定はローカルで行われるので結果は変わらない。
        """
        if not self.is_enabled or value is None:
            return None
        if self.value_kind == KIND_MULTI:
            return self._render_multi(value)
        return self._render_numeric(value)

    def _render_multi(self, value: object) -> dict[str, list[str]] | None:
        """集合条件。**全項目を表現できるときだけ**送る。"""
        if not isinstance(value, Sequence) or isinstance(value, str):
            raise ParamError(f"{self.axis} には配列を渡してください: {value!r}")
        if not value:
            return None
        mapping = self.value_spec.get("mapping") or {}
        codes: list[str] = []
        for item in value:
            code = mapping.get(item)
            if code is None:
                # 1つでも対応表に無ければ軸ごと送らない。部分集合を送ると
                # 対応表に無い間取りの掲載をサイト側で落としてしまう
                return None
            if code not in codes:
                codes.append(code)
        return {self.param_name: codes}

    def _render_numeric(self, value: object) -> dict[str, list[str]] | None:
        """数値条件。単位を換算し、丸めてから書式を当てる。"""
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ParamError(f"{self.axis} には数値を渡してください: {value!r}")
        divisor = UNIT_DIVISOR.get(self.unit)
        if divisor is None:
            raise ParamError(f"未知の単位です: {self.unit}")
        converted = Decimal(str(value)) / divisor
        snapped = self._snap(converted)
        if snapped is None:
            return None
        template = str(self.value_spec.get("format", "{}"))
        return {self.param_name: [template.format(snapped)]}

    def _snap(self, value: Decimal) -> Decimal | None:
        """選択肢へ丸める。範囲外で送りようがないときは ``None``。"""
        if self.value_kind == KIND_ENUM:
            return _snap_choices(value, self._choices(), self.bound)
        if self.value_kind == KIND_STEPPED:
            return _snap_stepped(
                value,
                minimum=self._decimal("min"),
                maximum=self._decimal("max"),
                step=self._decimal("step"),
                bound=self.bound,
            )
        raise ParamError(f"未知の value_kind です: {self.value_kind}")

    def _choices(self) -> list[Decimal]:
        raw = self.value_spec.get("choices")
        if not raw:
            raise ParamError(f"{self.site_code}/{self.axis}: choices がありません")
        return sorted(Decimal(str(item)) for item in raw)

    def _decimal(self, key: str) -> Decimal:
        if key not in self.value_spec:
            raise ParamError(f"{self.site_code}/{self.axis}: {key} がありません")
        return Decimal(str(self.value_spec[key]))


def _snap_choices(value: Decimal, choices: list[Decimal], bound: str) -> Decimal | None:
    """列挙された選択肢へ丸める。"""
    if bound == UPPER:
        # 要求値以上で最小のもの。全部より大きいなら「上限なし」なので送らない
        candidates = [c for c in choices if c >= value]
        return min(candidates) if candidates else None
    # 要求値以下で最大のもの。全部より小さいなら「下限なし」なので送らない
    candidates = [c for c in choices if c <= value]
    return max(candidates) if candidates else None


def _snap_stepped(
    value: Decimal, *, minimum: Decimal, maximum: Decimal, step: Decimal, bound: str
) -> Decimal | None:
    """等間隔の選択肢へ丸める。

    選択肢を全部並べずに算術で解く。SUUMO の賃料は 3.0〜100.0 万円の 0.5 刻みで
    195 個あり、列挙する意味がない。
    """
    if step <= 0:
        raise ParamError(f"step は正の数にしてください: {step}")
    if bound == UPPER:
        if value > maximum:
            return None  # 上限なしに等しい
        if value <= minimum:
            return minimum
        offsets = (value - minimum) / step
        return minimum + step * _ceil(offsets)
    if value < minimum:
        return None  # 下限なしに等しい
    if value >= maximum:
        return maximum
    offsets = (value - minimum) / step
    return minimum + step * _floor(offsets)


def _ceil(value: Decimal) -> Decimal:
    """Decimal の切り上げ。``math.ceil`` を通すと float に戻ってしまう。"""
    integral = int(value)
    return Decimal(integral if Decimal(integral) == value else integral + 1)


def _floor(value: Decimal) -> Decimal:
    integral = int(value)
    return Decimal(integral if Decimal(integral) == value or value > 0 else integral - 1)


@dataclass(frozen=True, slots=True)
class SiteParamTable:
    """サイト×物件種別×軸のパラメータ定義表。

    実行時は ``Runtime`` が1つ持ち、アダプタが ``for_site`` で引く。
    """

    specs: tuple[ParamSpec, ...] = ()

    def for_site(self, site_code: str, property_type: str) -> dict[str, ParamSpec]:
        """指定サイト・種別の軸ごとの定義を返す。"""
        return {
            spec.axis: spec
            for spec in self.specs
            if spec.site_code == site_code and spec.property_type == property_type
        }

    def build_query(
        self,
        *,
        site_code: str,
        property_type: str,
        must: object,
        axes: Sequence[str],
    ) -> dict[str, list[str]]:
        """MUST から、そのサイトへ渡せる分だけのクエリを組み立てる。

        ``axes`` は検索パターンの ``search.site_filters.axes``。
        定義が無い軸・無効化された軸・丸めきれない軸は黙って落ちる
        （送らないだけで判定はローカルで行われるため、結果は変わらない）。
        """
        table = self.for_site(site_code, property_type)
        query: dict[str, list[str]] = {}
        # 軸の順序を固定する。URLが実行ごとに変わるとキャッシュもログも比較できない
        for axis in sorted(axes):
            spec = table.get(axis)
            if spec is None:
                continue
            rendered = spec.render(getattr(must, axis, None))
            if rendered:
                query.update(rendered)
        return query
