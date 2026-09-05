"""検索パターンYAML（v2スキーマ）の型定義と読み込み。

物件種別を discriminator にして3ファミリ粒度の discriminated union へ分岐する。
5種別を5クラスに割らないのは、新築/中古の差が age_years・価格未定・リノベ関連の
数項目だけで、クラスを分けるほどの構造差がないため。

売買ファミリ（``MansionBuyPattern`` / ``KodateBuyPattern``）は Phase 0 時点では
骨格のみ。Phase 6 で売買metricの実装と併せて肉付けする。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from house_search.config.metrics import (
    FAMILY_OF,
    METRICS_BY_NAME,
    MUST_ITEMS_BY_NAME,
    Family,
)
from house_search.scrape.params import AXIS_BOUND


class Strict(BaseModel):
    """YAMLの綴り間違いを黙って無視しないための共通設定。"""

    model_config = ConfigDict(extra="forbid")


class SiteFilterSpec(Strict):
    """MUST をサイト側のフォームにも渡すかどうかの指定（→ ADR 0015）。

    **WANT は決して渡さない。** 渡すと対応サイトでは加点対象の掲載が除外されて
    順位に現れず、非対応サイトでは素通りするため、ランキングと両立しない。
    MUST は「ローカルで fail にする掲載」なので、サイト側で落としても結果が
    変わらず取得量だけ減る。

    ⚠ **既定は無効。** 無効値を渡すと HTTP 200 のまま0件になる事故があり
    （→ 課題#29）、``enabled: false`` に戻せば全サイトが即座に従来の動作へ戻る。
    """

    enabled: bool = Field(
        default=False,
        description="MUSTをサイト側へも渡すか。false なら従来どおりエリアと価格上限だけ",
    )
    axes: list[str] = Field(
        default_factory=list,
        description=(
            "サイト側へ渡す MUST の軸。"
            "area_min / area_max / walk_minutes_max / age_max / layouts"
        ),
    )
    exclude_sites: list[str] = Field(
        default_factory=list,
        description="この指定から外すサイトコード（実測で不調なサイトを個別に止める）",
    )

    @model_validator(mode="after")
    def _check_axes(self) -> SiteFilterSpec:
        """設備条件などサイト側へ渡してはいけない軸を弾く。"""
        for axis in self.axes:
            if axis not in AXIS_BOUND:
                known = ", ".join(sorted(AXIS_BOUND))
                raise ValueError(
                    f"サイト側へ渡せない軸です: '{axis}'（使えるのは: {known}）。"
                    "設備条件は仕様として渡せません"
                )
        if self.enabled and not self.axes:
            raise ValueError("site_filters.enabled が true なのに axes が空です")
        return self


class SearchSpec(Strict):
    """サイト側へ渡す条件。

    基本はエリア・物件種別・価格上限（バッファ付き）の3つだけ。
    ``site_filters`` を有効にすると、これに **MUST の数値条件と間取り**が加わる
    （→ ADR 0015 が ADR 0003 を補強する）。設備条件は永久に渡さない。
    """

    prefectures: list[str] = Field(min_length=1, description="対象都道府県")
    cities: list[str] = Field(
        default_factory=list,
        description=(
            "対象市区町村（m_cities.canonical_name）。"
            "空なら ABLE / SMOCCA は都道府県内の全市区へ自動展開する"
        ),
    )
    price_max_hint: int | None = Field(
        default=None,
        description=(
            "サイト側フォームに渡す価格上限。MUST上限の2〜3割増しにする"
            "（管理費が別計上のサイトで取りこぼさないためのバッファ）"
        ),
    )
    site_filters: SiteFilterSpec = Field(
        default_factory=SiteFilterSpec,
        description="MUSTをサイト側のフォームにも渡すかどうか（既定は無効）",
    )


class CommuteSpec(Strict):
    """通勤時間の基準（→ Phase 5C）。

    所要時間は Google Maps Routes API（公共交通）で**駅ペアごとに一度だけ**取得し
    ``t_station_commutes`` へキャッシュする。採点・再採点はキャッシュを読むだけなので
    ネットワークに触らない。

    ⚠ **測るのは駅から駅まで**で、駅までの徒歩は含めない。徒歩は
    ``walk_minutes_max``（MUST）と ``walk_minutes``（WANT）で独立に効いており、
    足すと二重に不利になる（2026-09-03 ユーザー確定）。
    """

    destination_station: str = Field(
        min_length=1, description="勤務先の最寄り駅名（m_stations.station_name）"
    )
    destination_prefecture: str | None = Field(
        default=None,
        description=(
            "目的地の都道府県名。同名異駅（日本橋＝東京/大阪、府中＝東京/広島）を"
            "避けるため指定を推奨する"
        ),
    )


class MustBase(Strict):
    """MUST条件の共通部。全ファミリで意味が通る項目だけを置く。

    ⚠ **間取り（layouts）はここに置かない。** 土地（Phase 9）には間取りの概念が
    無いのに、共通部にあると土地パターンにも書けてしまう。書けたところで判定は
    されず全件 unknown になるだけで**例外にならない**ので、ファミリごとの Must
    クラスへ降ろしてある（→ 課題#4）。レジストリとの対応は
    ``tests/test_pattern.py`` が双方向で固定している。
    """

    commute_minutes_max: int | None = Field(
        default=None,
        description=(
            "通勤時間の上限（分・駅から駅まで）。commute セクションが必要。"
            "判定は駅の同定とキャッシュに依存するので unknown になりうる"
        ),
    )

    flood_rank_max: float | None = Field(
        default=None,
        ge=0,
        le=6,
        description=(
            "洪水浸水深ランクの上限（丁目内の最大・0〜6）。"
            "1=0.5m未満 2=0.5〜3m 3=3〜5m 4=5〜10m 5=10〜20m 6=20m以上。"
            "住所の照合に依存するので unknown になりうる"
        ),
    )
    landslide_special_ratio_max: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "土砂災害特別警戒区域（レッドゾーン）が丁目に占める面積比の上限（0〜1）。"
            "住所の照合に依存するので unknown になりうる"
        ),
    )

    walk_minutes_max: int | None = Field(default=None, description="駅徒歩の上限（分）")
    features: list[str] = Field(
        default_factory=list, description="必須の条件コード（m_conditions.code）"
    )
    unknown_policy: Literal["keep", "drop"] = Field(
        default="keep",
        description=(
            "判定不能（unknown）なMUSTの扱い。keep=通す / drop=除外。"
            "MUST判定は pass / fail / unknown の3値で、詳細取得をスキップするのは fail のみ"
        ),
    )


class ChintaiMust(MustBase):
    """賃貸のMUST条件。"""

    layouts: list[str] = Field(default_factory=list, description="許容する間取り")
    rent_total_max: int | None = Field(default=None, description="賃料＋管理費の上限（円/月）")
    area_min: float | None = Field(default=None, description="専有面積の下限（㎡）")
    area_max: float | None = Field(default=None, description="専有面積の上限（㎡）")
    age_max: int | None = Field(default=None, description="築年数の上限（年）")
    floor_min: int | None = Field(default=None, description="所在階の下限")


class MansionBuyMust(MustBase):
    """マンション売買のMUST条件。"""

    layouts: list[str] = Field(default_factory=list, description="許容する間取り")
    price_max: int | None = Field(default=None, description="物件価格の上限（円）")
    monthly_cost_max: int | None = Field(
        default=None, description="管理費＋修繕積立金の上限（円/月）"
    )
    area_min: float | None = Field(default=None, description="専有面積の下限（㎡）")
    area_max: float | None = Field(default=None, description="専有面積の上限（㎡）")
    age_max: int | None = Field(default=None, description="築年数の上限（年・中古のみ）")
    floor_min: int | None = Field(default=None, description="所在階の下限")


class KodateBuyMust(MustBase):
    """戸建て売買のMUST条件。"""

    layouts: list[str] = Field(default_factory=list, description="許容する間取り")
    price_max: int | None = Field(default=None, description="物件価格の上限（円）")
    land_area_min: float | None = Field(default=None, description="土地面積の下限（㎡）")
    building_area_min: float | None = Field(default=None, description="建物面積の下限（㎡）")
    age_max: int | None = Field(default=None, description="築年数の上限（年・中古のみ）")


class FeatureWant(Strict):
    """WANT の設備条件。該当すれば weight 満点を加点する。

    ``code`` で単一条件を、``any_of`` で「どれか1つ満たせば満点」の排他グループを表す。

    ``any_of`` が要る理由: ``STRUCT_RC`` と ``STRUCT_SRC`` のように同時に満たしえない
    条件へ別々に weight を振ると、片方は必ず miss になるのに分母には両方が乗る。
    結果として全物件のスコア上限が構造的に下がり、weight 予算が無駄になる
    （順位は壊れないが 0〜100 点という数字の意味が濁る）。
    """

    code: str | None = Field(default=None, description="条件コード（m_conditions.code）")
    any_of: list[str] = Field(
        default_factory=list,
        description="排他グループ。いずれか1つでも該当すれば weight 満点を加点する",
    )
    weight: float = Field(gt=0, description="重み。大きいほど優先度が高い")

    @model_validator(mode="after")
    def _exactly_one_form(self) -> FeatureWant:
        if bool(self.code) == bool(self.any_of):
            raise ValueError("WANT の設備条件は code か any_of のどちらか一方を指定してください")
        if self.any_of and len(set(self.any_of)) < 2:
            raise ValueError("any_of には異なる条件コードを2つ以上指定してください")
        return self

    @property
    def codes(self) -> tuple[str, ...]:
        """この項目が参照する条件コード（昇順＝決定的）。"""
        return (self.code,) if self.code else tuple(sorted(self.any_of))

    @property
    def key(self) -> str:
        """内訳・ソートで使う安定した識別子。"""
        return self.code if self.code else "|".join(self.codes)


class NumericWant(Strict):
    """WANT の数値条件。best〜worst で線形正規化して加点する。"""

    metric: str = Field(description="metric名（MetricRegistry に登録されたもの）")
    weight: float = Field(gt=0, description="重み")
    best: float = Field(description="満点(1.0)になる値")
    worst: float = Field(description="0点になる値")

    @model_validator(mode="after")
    def _check_range(self) -> NumericWant:
        if self.best == self.worst:
            raise ValueError(
                f"metric '{self.metric}' の best と worst が同じ値です（0除算になります）"
            )
        return self


class WantSpec(Strict):
    """加点条件。単位の異なる条件は正規化値 s で無次元化されるため、
    weight だけで相対優先度を表現できる。"""

    features: list[FeatureWant] = Field(default_factory=list)
    numeric: list[NumericWant] = Field(default_factory=list)


class RankingSpec(Strict):
    """ランキング・ダイジェストの設定。"""

    top_n: int = Field(default=15, gt=0, description="ダイジェストに載せる件数")
    digest_group: str | None = Field(
        default=None,
        description=(
            "ダイジェストの見出しにグループ名のラベルを付ける。"
            "⚠ 同一グループを1メッセージへ並記はしない（パターンごとに1通ずつ届く → 課題#28）。"
            "スコアは種別間で混ぜない（正規化基準が異なり数字が意味を失うため）"
        ),
    )
    notify_max_rank: int | None = Field(
        default=None,
        gt=0,
        description=(
            "個別通知（新着・価格変動・他サイト安値）を上位N位までに絞る。"
            "None は無制限（従来動作）。⚠ 収集・採点・ダイジェストには一切効かない"
        ),
    )


class PatternBase(Strict):
    """全ファミリ共通の検索パターン定義。"""

    name: str = Field(min_length=1, description="パターン名（DB・通知ログで使う識別子）")
    webhook_ref: str = Field(
        min_length=1,
        description="通知先の論理名。.env の DISCORD_WEBHOOK_{大文字} を参照する",
    )
    digest_webhook_ref: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "日次ランキングダイジェストの通知先。省略時は webhook_ref と同じ。"
            "上位N件だけを別チャンネルへ流したいときに指定する"
        ),
    )
    sites: list[str] = Field(min_length=1, description="スクレイプ対象サイトコード")
    search: SearchSpec
    commute: CommuteSpec | None = Field(
        default=None, description="通勤時間の基準。MUST・WANT で通勤時間を使うなら必須"
    )
    want: WantSpec = Field(default_factory=WantSpec)
    ranking: RankingSpec = Field(default_factory=RankingSpec)

    @property
    def family(self) -> Family:
        return FAMILY_OF[self.property_type]  # type: ignore[attr-defined]

    @property
    def effective_digest_webhook_ref(self) -> str:
        """ダイジェストの実際の通知先。未指定なら個別通知と同じチャンネルへ送る。"""
        return self.digest_webhook_ref or self.webhook_ref

    @model_validator(mode="after")
    def _validate_against_registry(self) -> PatternBase:
        """metric・MUST項目・条件コードが物件種別に適合するかをレジストリで検証する。"""
        ptype: str = self.property_type  # type: ignore[attr-defined]

        seen_metrics: set[str] = set()
        for item in self.want.numeric:
            spec = METRICS_BY_NAME.get(item.metric)
            if spec is None:
                known = ", ".join(sorted(METRICS_BY_NAME))
                raise ValueError(f"未知の metric '{item.metric}'（使えるのは: {known}）")
            if not spec.applies_to(ptype):
                raise ValueError(f"metric '{item.metric}' は物件種別 {ptype} には適用できません")
            if item.metric in seen_metrics:
                raise ValueError(f"metric '{item.metric}' が重複しています")
            seen_metrics.add(item.metric)

        seen_features: set[str] = set()
        for feat in self.want.features:
            for code in feat.codes:
                if code in seen_features:
                    raise ValueError(f"WANT の条件コード '{code}' が重複しています")
                seen_features.add(code)

        must_fields = type(self.must).model_fields  # type: ignore[attr-defined]
        for field_name in must_fields:
            if getattr(self.must, field_name, None) in (None, [], ""):  # type: ignore[attr-defined]
                continue
            spec_must = MUST_ITEMS_BY_NAME.get(field_name)
            if spec_must is None:
                continue  # unknown_policy のような制御項目
            if ptype not in spec_must.property_types:
                raise ValueError(f"MUST項目 '{field_name}' は物件種別 {ptype} には適用できません")

        # 通勤時間を使うなら目的地が要る。無いと採点時に黙って unknown になり、
        # 「設定したのに効いていない」に気づけない。
        uses_commute = self.must.commute_minutes_max is not None or any(  # type: ignore[attr-defined]
            item.metric == "commute_minutes" for item in self.want.numeric
        )
        if uses_commute and self.commute is None:
            raise ValueError(
                "通勤時間を条件に使うには commute セクション（destination_station）が要ります"
            )

        # サイト側へ渡す軸が、この種別の MUST に存在することを確かめる。
        # 存在しない軸を書いても実行時は黙って送られないだけなので、
        # 「設定したのに効いていない」に気づけなくなる
        for axis in self.search.site_filters.axes:
            if axis not in must_fields:
                raise ValueError(
                    f"site_filters.axes の '{axis}' は物件種別 {ptype} の MUST にありません"
                )
        return self

    def score_config(self) -> dict[str, Any]:
        """スコアに影響する部分だけを取り出す（config_hash の入力）。

        検索範囲や通知先を変えただけで全件再スコアが走らないよう、
        WANT と物件種別だけをハッシュ対象にする。
        """
        config: dict[str, Any] = {
            "property_type": self.property_type,  # type: ignore[attr-defined]
            "want": self.want.model_dump(mode="json"),
        }
        if self.commute is not None:
            # 目的地が変われば通勤時間の意味も変わるので再スコアの対象にする。
            # 未設定のパターンではキーごと省き、既存のハッシュを変えない
            # （意図しない全件再スコアを起こさないため）。
            config["commute"] = self.commute.model_dump(mode="json")
        return config

    def config_hash(self) -> str:
        """スコア関連設定のSHA256。DB上の値と不一致なら自動再スコアの対象になる。"""
        payload = json.dumps(
            self.score_config(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChintaiPattern(PatternBase):
    """賃貸の検索パターン。"""

    property_type: Literal["CHINTAI"]
    must: ChintaiMust = Field(default_factory=ChintaiMust)


class MansionBuyPattern(PatternBase):
    """マンション売買（新築・中古）の検索パターン。"""

    property_type: Literal["SHINCHIKU_MANSION", "CHUKO_MANSION"]
    must: MansionBuyMust = Field(default_factory=MansionBuyMust)


class KodateBuyPattern(PatternBase):
    """戸建て売買（新築・中古）の検索パターン。"""

    property_type: Literal["SHINCHIKU_KODATE", "CHUKO_KODATE"]
    must: KodateBuyMust = Field(default_factory=KodateBuyMust)


SearchPattern = Annotated[
    ChintaiPattern | MansionBuyPattern | KodateBuyPattern,
    Field(discriminator="property_type"),
]

PATTERN_ADAPTER: TypeAdapter[SearchPattern] = TypeAdapter(SearchPattern)


def parse_pattern(data: dict[str, Any]) -> SearchPattern:
    """dict から検索パターンを構築する。"""
    return PATTERN_ADAPTER.validate_python(data)


def load_pattern_file(path: Path) -> SearchPattern:
    """YAMLファイル1件を読み込む。"""
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} の内容がマッピングではありません")
    return parse_pattern(data)


def load_patterns(configs_dir: Path) -> list[SearchPattern]:
    """ディレクトリ直下の ``*.yaml`` を名前順に読み込む。"""
    return [load_pattern_file(p) for p in sorted(configs_dir.glob("*.yaml"))]
