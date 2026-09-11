"""賃貸の推定光熱費と living_cost（→ 課題#64・ADR 0025）。

⚠⚠ **光熱費の誤りは例外にならず、それらしい額が出るだけ**である
（単位の取り違え・補助の片側適用・付け忘れ）。ここでは次を固定する。
  - 料金の検算値（公表値そのもの）と、使用量の恒等式
  - 計算例の表（1人・2人 × ガス種別 × コンロ）
  - ガス種別の判定と、不明・矛盾の期待値
  - 付け忘れたビューで living_cost を求めると**例外**になること
  - 採点に使う読み込みがすべて ``utility_profile`` を渡していること
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.notify.format import NotifiableListing, price_field, price_summary
from house_search.scoring import utility as u
from house_search.scoring.listing_view import ListingView
from house_search.scoring.score import calculate_score
from house_search.scoring.utility import (
    UtilityEstimate,
    UtilityNotAttachedError,
    UtilityProfile,
    city_gas_bill,
    electricity_bill,
    estimate_utility,
    lpg_bill,
    monthly_bill,
    tariff_fingerprint,
    utility_profile_for,
)

REPO = Path(__file__).resolve().parents[1]


# ============================================================
# 料金の検算（公表値そのもの）
# ============================================================


class Test料金の検算:
    def test_東京ガス30m3は補助前6164円(self) -> None:
        """東京ガス 2026-08-28 発表の10月検針分（補助前）の検算値。"""
        assert city_gas_bill(30) == pytest.approx(6164.4, abs=0.01)

    def test_都市ガスは20m3の境目で連続する(self) -> None:
        """A表と B表は 20m³ で料金が等しい（どちらの表で計算しても同じ）。"""
        assert city_gas_bill(20.0) == pytest.approx(1056.0 + 170.28 * 20.0, abs=0.01)

    def test_LPガス10m3は公表値そのもの(self) -> None:
        """石油情報センター 関東局 2026年8月速報。"""
        assert lpg_bill(10) == pytest.approx(9736.0)
        assert lpg_bill(5) == pytest.approx(5960.0)

    def test_LPガスは5m3未満を5から10m3の傾きで外挿する(self) -> None:
        assert lpg_bill(4) == pytest.approx(5960.0 - (9736.0 - 5960.0) / 5)

    def test_LPガスの上乗せ係数(self) -> None:
        assert lpg_bill(10, multiplier=1.15) == pytest.approx(9736.0 * 1.15)

    def test_電気260kWhは補助後に直すと8276円(self) -> None:
        """⚠ 定数は補助前。公表の請求例（補助3.50円/kWh 込み）と一致することを確かめる。"""
        assert electricity_bill(260) - 3.50 * 260 == pytest.approx(8276.05, abs=0.01)

    def test_使わなければガス代は0円(self) -> None:
        assert city_gas_bill(0) == 0.0
        assert lpg_bill(0) == 0.0


class Test使用量の恒等式:
    def test_1人都市ガスは約12_65m3(self) -> None:
        """東京都の実態調査（集合住宅 1人15m³・暖房込み）より少し低いのが想定どおり。"""
        demand = u.DEMANDS[1]
        mj = u.hot_water_mj(demand) / u.GAS_WATER_HEATER_EFFICIENCY
        mj += demand.cooking_mj / u.GAS_STOVE_EFFICIENCY
        assert mj / u.CITY_GAS_MJ_PER_M3 == pytest.approx(12.65, abs=0.01)

    def test_給湯の有効熱(self) -> None:
        assert u.hot_water_mj(u.DEMANDS[1]) == pytest.approx(375.48, abs=0.01)
        assert u.hot_water_mj(u.DEMANDS[2]) == pytest.approx(693.20, abs=0.01)

    def test_LPガスは同じ熱量を約半分の体積で出す(self) -> None:
        ratio = u.LPG_MJ_PER_M3 / u.CITY_GAS_MJ_PER_M3
        assert ratio == pytest.approx(2.2)


# ============================================================
# 計算例（課題#64 の表）
# ============================================================

EXAMPLES = [
    # (世帯, ガス, コンロ, 月額)
    (1, "city", "gas", 10201),
    (1, "city", "ih", 10326),
    (1, "lpg", "gas", 13626),
    (1, "lpg", "ih", 13400),
    (1, "electric", "ih", 11327),
    (2, "city", "gas", 15555),
    (2, "city", "ih", 15921),
    (2, "lpg", "gas", 20642),
    (2, "lpg", "ih", 20391),
    (2, "electric", "ih", 17525),
]


@pytest.mark.parametrize(("household", "gas", "stove", "expected"), EXAMPLES)
def test_計算例(household: int, gas: str, stove: str, expected: int) -> None:
    assert round(monthly_bill(gas, stove, household).total) == expected


def test_都市ガスとプロパンの差() -> None:
    """課題#64 の決定の根拠（1人 +3,425円前後・2人 +5,087円）。"""
    one = monthly_bill("lpg", "gas", 1).total - monthly_bill("city", "gas", 1).total
    two = monthly_bill("lpg", "gas", 2).total - monthly_bill("city", "gas", 2).total
    assert one == pytest.approx(3426, abs=2)
    assert two == pytest.approx(5087, abs=2)


def test_IHとガスコンロの差は400円未満() -> None:
    for household in (1, 2):
        for gas in ("city", "lpg"):
            diff = monthly_bill(gas, "ih", household).total - monthly_bill(
                gas, "gas", household
            ).total
            assert abs(diff) < 400


def test_ガス種別不明はmonthly_billに渡せない() -> None:
    with pytest.raises(ValueError, match="ガス種別"):
        monthly_bill("unknown", "gas", 1)


# ============================================================
# ガス種別とコンロの判定
# ============================================================

_PROFILE = UtilityProfile(household_size=1, unknown_lpg_probability=0.68)


class Testガス種別の判定:
    def test_都市ガス(self) -> None:
        est = estimate_utility({"EQUIP_CITY_GAS"}, _PROFILE)
        assert (est.gas, est.gas_basis) == ("city", "listing")
        assert est.monthly_yen == 10201
        assert est.lpg_probability is None

    def test_プロパン(self) -> None:
        est = estimate_utility({"EQUIP_LPG"}, _PROFILE)
        assert (est.gas, est.gas_basis) == ("lpg", "listing")
        assert est.monthly_yen == 13626
        assert est.city_gas_yen == 10201

    def test_オール電化(self) -> None:
        est = estimate_utility({"KITCHEN_ALL_ELEC"}, _PROFILE)
        assert (est.gas, est.stove) == ("electric", "ih")
        assert est.city_gas_yen is None

    def test_ガス種別の記載があればオール電化より優先する(self) -> None:
        est = estimate_utility({"KITCHEN_ALL_ELEC", "EQUIP_LPG"}, _PROFILE)
        assert est.gas == "lpg"

    def test_不明は帯のプロパン確率で期待値を取る(self) -> None:
        """⚠ 不明を都市ガスとみなさない（近郊では書いていない掲載の79%がプロパン）。"""
        est = estimate_utility(set(), _PROFILE)
        assert (est.gas, est.gas_basis) == ("unknown", "prior")
        assert est.lpg_probability == 0.68
        city = monthly_bill("city", "gas", 1).total
        lpg = monthly_bill("lpg", "gas", 1).total
        assert est.monthly_yen == round(0.68 * lpg + 0.32 * city)
        assert est.is_estimated_gas

    def test_都市ガスとプロパンの両方は矛盾として期待値(self) -> None:
        est = estimate_utility({"EQUIP_CITY_GAS", "EQUIP_LPG"}, _PROFILE)
        assert (est.gas, est.gas_basis) == ("unknown", "conflict")
        assert est.monthly_yen == estimate_utility(set(), _PROFILE).monthly_yen

    def test_確率0なら都市ガス_1ならプロパンと同じ(self) -> None:
        zero = UtilityProfile(household_size=1, unknown_lpg_probability=0.0)
        one = UtilityProfile(household_size=1, unknown_lpg_probability=1.0)
        assert estimate_utility(set(), zero).monthly_yen == 10201
        assert estimate_utility(set(), one).monthly_yen == 13626


class Testコンロの判定:
    def test_IHの記載があればIH(self) -> None:
        est = estimate_utility({"EQUIP_CITY_GAS", "KITCHEN_IH"}, _PROFILE)
        assert (est.stove, est.stove_basis) == ("ih", "listing")

    def test_IHとガスコンロの両方ならIH(self) -> None:
        est = estimate_utility({"EQUIP_CITY_GAS", "KITCHEN_IH", "KITCHEN_GAS"}, _PROFILE)
        assert est.stove == "ih"

    def test_記載が無ければガスコンロとみなす(self) -> None:
        est = estimate_utility({"EQUIP_CITY_GAS"}, _PROFILE)
        assert (est.stove, est.stove_basis) == ("gas", "assumed")

    def test_ガスコンロの記載(self) -> None:
        est = estimate_utility({"EQUIP_CITY_GAS", "KITCHEN_GAS"}, _PROFILE)
        assert (est.stove, est.stove_basis) == ("gas", "listing")


class Test見積もり条件:
    def test_需要の無い世帯人数は弾く(self) -> None:
        with pytest.raises(ValueError, match="世帯人数"):
            UtilityProfile(household_size=3, unknown_lpg_probability=0.5)

    @pytest.mark.parametrize("p", [-0.1, 1.1])
    def test_確率は0から1(self, p: float) -> None:
        with pytest.raises(ValueError):
            UtilityProfile(household_size=1, unknown_lpg_probability=p)

    def test_内訳の根拠(self) -> None:
        detail = estimate_utility(set(), _PROFILE).to_detail()
        assert detail["estimated"] is True
        assert detail["gas_basis"] == "prior"
        assert detail["lpg_probability"] == 0.68
        assert detail["tariff"] == u.TARIFF_LABEL


# ============================================================
# ListingView と採点
# ============================================================


def _view(**kwargs) -> ListingView:
    defaults = dict(listing_id=1, price=80000, mgmt_fee_monthly=5000, rent_total=85000)
    defaults.update(kwargs)
    return ListingView(**defaults)


class Test光熱費込みの月額:
    def test_賃料と推定光熱費の和(self) -> None:
        view = _view(utility=estimate_utility({"EQUIP_CITY_GAS"}, _PROFILE))
        assert view.metric_value("living_cost") == 85000 + 10201

    def test_付いていないビューでは例外(self) -> None:
        """⚠ 黙って欠損（weight 40 が分母から消える）や 0円にしない。"""
        with pytest.raises(UtilityNotAttachedError):
            _view().metric_value("living_cost")

    def test_賃料が無ければ欠損(self) -> None:
        view = _view(price=None, rent_total=None, utility=estimate_utility(set(), _PROFILE))
        assert view.metric_value("living_cost") is None

    def test_賃料のmetricは光熱費を含まない(self) -> None:
        """相場比・MUST・異常検出は賃料のまま（→ 課題#64 の「変えないもの」）。"""
        view = _view(utility=estimate_utility({"EQUIP_LPG"}, _PROFILE))
        assert view.metric_value("rent_total") == 85000


def _want(numeric):
    from house_search.config.pattern import WantSpec

    return WantSpec.model_validate({"numeric": numeric})


class Test採点の内訳:
    def test_living_costの項目に根拠が残る(self) -> None:
        view = _view(utility=estimate_utility({"EQUIP_LPG"}, _PROFILE))
        want = _want([{"metric": "living_cost", "weight": 40, "best": 80000, "worst": 110000}])
        item = calculate_score(view, want, condition_names={}).breakdown()[0]
        assert item["value"] == 85000 + 13626
        assert item["detail"]["rent_total"] == 85000
        assert item["detail"]["utility"] == 13626
        assert item["detail"]["gas"] == "lpg"

    def test_他の項目のJSONは変えない(self) -> None:
        view = _view(utility=estimate_utility({"EQUIP_LPG"}, _PROFILE))
        want = _want([{"metric": "rent_total", "weight": 40, "best": 70000, "worst": 100000}])
        item = calculate_score(view, want, condition_names={}).breakdown()[0]
        assert "detail" not in item

    def test_プロパンは都市ガスより点が低い(self) -> None:
        want = _want([{"metric": "living_cost", "weight": 40, "best": 80000, "worst": 110000}])
        city = calculate_score(
            _view(utility=estimate_utility({"EQUIP_CITY_GAS"}, _PROFILE)), want, condition_names={}
        )
        lpg = calculate_score(
            _view(utility=estimate_utility({"EQUIP_LPG"}, _PROFILE)), want, condition_names={}
        )
        assert lpg.score < city.score


# ============================================================
# 検索パターン
# ============================================================


def _chintai(**overrides) -> dict:
    data = {
        "name": "光熱費テスト",
        "property_type": "CHINTAI",
        "webhook_ref": "X",
        "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"]},
        "want": {
            "numeric": [{"metric": "living_cost", "weight": 40, "best": 80000, "worst": 110000}]
        },
        "utility": {"household_size": 1, "unknown_lpg_probability": 0.14},
    }
    data.update(overrides)
    return data


class Test検索パターン:
    def test_living_costにはutilityが要る(self) -> None:
        data = _chintai()
        del data["utility"]
        with pytest.raises(ValueError, match="utility"):
            parse_pattern(data)

    def test_living_costとrent_totalの同時配点は禁止(self) -> None:
        numeric = [
            {"metric": "living_cost", "weight": 40, "best": 80000, "worst": 110000},
            {"metric": "rent_total", "weight": 10, "best": 70000, "worst": 100000},
        ]
        with pytest.raises(ValueError, match="同時"):
            parse_pattern(_chintai(want={"numeric": numeric}))

    def test_utilityは賃貸にしか書けない(self) -> None:
        data = {
            "name": "売買",
            "property_type": "CHUKO_MANSION",
            "webhook_ref": "X",
            "sites": ["SUUMO"],
            "search": {"prefectures": ["東京都"]},
            "utility": {"household_size": 1, "unknown_lpg_probability": 0.14},
        }
        with pytest.raises(ValueError):
            parse_pattern(data)

    def test_世帯人数は1人か2人(self) -> None:
        with pytest.raises(ValueError):
            parse_pattern(_chintai(utility={"household_size": 3, "unknown_lpg_probability": 0.1}))

    def test_utilityの無い賃貸はハッシュ入力にキーを持たない(self) -> None:
        """⚠ 既存パターンのハッシュを変えない（commute と同じ扱い）。"""
        data = _chintai(
            want={"numeric": [{"metric": "rent_total", "weight": 40, "best": 1, "worst": 2}]}
        )
        del data["utility"]
        assert "utility" not in parse_pattern(data).score_config()

    def test_utilityと料金の指紋がハッシュに入る(self) -> None:
        config = parse_pattern(_chintai()).score_config()
        assert config["utility"]["household_size"] == 1
        assert config["utility"]["unknown_lpg_probability"] == 0.14
        assert config["utility"]["tariff"] == tariff_fingerprint()

    def test_確率を変えるとハッシュが変わる(self) -> None:
        a = parse_pattern(_chintai()).config_hash()
        b = parse_pattern(
            _chintai(utility={"household_size": 1, "unknown_lpg_probability": 0.68})
        ).config_hash()
        assert a != b

    def test_utility_profile_for(self) -> None:
        profile = utility_profile_for(parse_pattern(_chintai()))
        assert profile == UtilityProfile(household_size=1, unknown_lpg_probability=0.14)
        data = _chintai(
            want={"numeric": [{"metric": "rent_total", "weight": 40, "best": 1, "worst": 2}]}
        )
        del data["utility"]
        assert utility_profile_for(parse_pattern(data)) is None


@pytest.mark.parametrize(
    ("filename", "probability"),
    [("chintai_23ku.yaml", 0.14), ("chintai_suburb60.yaml", 0.68)],
)
def test_実運用の賃貸2本(filename: str, probability: float) -> None:
    """ユーザー判断（2026-09-11）: 1人・帯ごとの実測確率・都市ガスの加点を外す。"""
    from house_search.config.pattern import load_pattern_file

    pattern = load_pattern_file(REPO / "configs" / filename)
    numeric = {item.metric: item for item in pattern.want.numeric}
    assert numeric["living_cost"].weight == 40
    assert "rent_total" not in numeric  # ⚠ 同時に配点すると賃料に二重の重み
    codes = {code for feat in pattern.want.features for code in feat.codes}
    assert "EQUIP_CITY_GAS" not in codes  # ⚠ 光熱費と二重に効く
    assert pattern.utility is not None
    assert pattern.utility.household_size == 1
    assert pattern.utility.unknown_lpg_probability == probability
    # ⚠ 相場比・MUST は賃料のまま（living_cost に揃えない）
    assert pattern.must.rent_total_max is not None
    assert "market_rate_ratio" in numeric


def test_料金定数を変えると指紋が変わる(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠ 指紋に入れ忘れた定数があると、改定しても再採点されない。"""
    before = tariff_fingerprint()
    monkeypatch.setattr(u, "RENEWABLE_SURCHARGE_PER_KWH", 4.50)
    assert tariff_fingerprint() != before
    monkeypatch.undo()
    monkeypatch.setattr(u, "LPG_PRICE_POINTS", ((5.0, 6000.0), (10.0, 9736.0)))
    assert tariff_fingerprint() != before


# ============================================================
# 採点に使う読み込みの配線
# ============================================================


def _calls_in(func: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == name)
            or (isinstance(node.func, ast.Name) and node.func.id == name)
        )
    ]


def test_採点に使う読み込みはすべてutility_profileを渡す() -> None:
    """⚠⚠ **付け忘れた経路は、その経路でだけ living_cost が例外になる**（スキャンが止まる）。

    ``load_listing_views`` と ``calculate_score`` を同じ関数で呼んでいる箇所
    （＝読み込んだビューで採点する経路）は、必ず ``utility_profile`` を渡す。
    採点しない統計コマンドは対象外。経路を足しても自動で検査対象に入る。
    """
    offenders: list[str] = []
    checked = 0
    for path in sorted((REPO / "src" / "house_search").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            loads = _calls_in(func, "load_listing_views")
            if not loads or not _calls_in(func, "calculate_score"):
                continue
            for call in loads:
                checked += 1
                if not any(kw.arg == "utility_profile" for kw in call.keywords):
                    offenders.append(f"{path.relative_to(REPO)}:{call.lineno} {func.name}")
    # scan の採点・rescore・digest の3経路（減ったら検査が空振りしている）
    assert checked >= 3
    assert not offenders, f"utility_profile を渡していない採点経路: {offenders}"


# ============================================================
# 表示
# ============================================================


def _notifiable(utility: UtilityEstimate | None, **kwargs) -> NotifiableListing:
    defaults = dict(
        listing_id=1,
        site_code="SUUMO",
        url="https://example.invalid/1",
        title="テスト",
        price=80000,
        mgmt_fee_monthly=5000,
        rent_total=85000,
        layout="1LDK",
        area_sqm=40.0,
        age_years=10,
        walk_minutes=5,
        address="東京都足立区",
        property_family="CHINTAI",
        utility=utility,
    )
    defaults.update(kwargs)
    return NotifiableListing(**defaults)


class Test表示:
    def test_光熱費が無ければ従来どおり(self) -> None:
        name, body = price_field(_notifiable(None))
        assert name == "月額"
        assert "光熱費" not in body
        assert price_summary(_notifiable(None)) == "85,000円"

    def test_都市ガス(self) -> None:
        est = estimate_utility({"EQUIP_CITY_GAS", "KITCHEN_IH"}, _PROFILE)
        name, body = price_field(_notifiable(est))
        assert name == "月額（光熱費込み）"
        assert f"{85000 + est.monthly_yen:,}円" in body
        assert f"光熱費 推定{est.monthly_yen:,}円" in body
        assert "※都市ガス・IH／1人暮らし想定" in body

    def test_プロパンは都市ガスとの差を出す(self) -> None:
        est = estimate_utility({"EQUIP_LPG"}, _PROFILE)
        _, body = price_field(_notifiable(est))
        assert "プロパンガス・ガスコンロ想定" in body
        assert f"都市ガスなら −{est.monthly_yen - est.city_gas_yen:,}円" in body

    def test_不明は確率を出し断定しない(self) -> None:
        _, body = price_field(_notifiable(estimate_utility(set(), _PROFILE)))
        assert "ガス種別不明（プロパンの確率68%で期待値）" in body

    def test_ダイジェストの1行(self) -> None:
        city = estimate_utility({"EQUIP_CITY_GAS"}, _PROFILE)
        assert price_summary(_notifiable(city)) == (
            f"{85000 + city.monthly_yen:,}円〔賃料等85,000＋光熱{city.monthly_yen:,}・都市ガス〕"
        )
        unknown = estimate_utility(set(), _PROFILE)
        assert "ガス不明" in price_summary(_notifiable(unknown))
        assert "光熱約" in price_summary(_notifiable(unknown))

    def test_売買の表示は変えない(self) -> None:
        prop = _notifiable(None, property_family="MANSION_BUY", price=30_000_000)
        assert price_summary(prop) == "3,000万円"
