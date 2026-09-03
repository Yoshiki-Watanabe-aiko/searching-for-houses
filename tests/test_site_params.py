"""サイト側絞り込みパラメータ（MUST 限定）のテスト。

ここで固定したいのは**丸めの向き**である。サイトへ渡す値は必ず
「ローカルのMUSTと同じか、より緩い」集合を返さなければならない。
逆向きに丸めると掲載を取りこぼすが、**取りこぼしは例外にならず件数が減るだけ**
なので、実データを見ても気づけない（→ 課題#29）。

DBは要らない（YAMLと純関数だけ）。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from house_search.config.settings import load_settings
from house_search.config.site_params import SITE_PARAMS_FILENAME, load_site_params
from house_search.scrape.params import (
    AXIS_BOUND,
    LOWER,
    UPPER,
    ParamAxis,
    ParamError,
    ParamSpec,
    SiteParamTable,
)

WALK = ParamSpec(
    site_code="SUUMO",
    property_type="CHINTAI",
    axis=ParamAxis.WALK_MINUTES_MAX,
    param_name="et",
    value_kind="enum",
    unit="minutes",
    value_spec={"choices": [1, 5, 7, 10, 15, 20], "format": "{:.0f}"},
)
AREA_MIN = ParamSpec(
    site_code="SUUMO",
    property_type="CHINTAI",
    axis=ParamAxis.AREA_MIN,
    param_name="mb",
    value_kind="stepped",
    unit="sqm",
    value_spec={"min": 20, "max": 100, "step": 5, "format": "{:.0f}"},
)
LAYOUTS = ParamSpec(
    site_code="SUUMO",
    property_type="CHINTAI",
    axis=ParamAxis.LAYOUTS,
    param_name="md",
    value_kind="multi",
    unit="minutes",
    value_spec={"mapping": {"1LDK": "04", "2K": "05", "2DK": "06"}},
)


# ---- 丸めの向き ----


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (10, "10"),  # 選択肢そのもの
        (12, "15"),  # ★上限は切り上げる。10 にすると徒歩11〜12分の掲載を取りこぼす
        (1, "1"),
        (21, None),  # 最大の選択肢より緩い＝上限なしなので送らない
    ],
)
def test_上限条件は緩い側へ切り上げる(requested: int, expected: str | None) -> None:
    rendered = WALK.render(requested)
    assert rendered == ({"et": [expected]} if expected else None)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (30.0, "30"),  # 選択肢そのもの
        (32.0, "30"),  # ★下限は切り下げる。35 にすると32〜34㎡の掲載を取りこぼす
        (19.0, None),  # 最小の選択肢より緩い＝下限なしなので送らない
        (120.0, "100"),  # 最大より厳しければ最大で頭打ち（さらに絞れない）
    ],
)
def test_下限条件は緩い側へ切り下げる(requested: float, expected: str | None) -> None:
    rendered = AREA_MIN.render(requested)
    assert rendered == ({"mb": [expected]} if expected else None)


@pytest.mark.parametrize("requested", [1, 4, 6, 8, 11, 14, 16, 20])
def test_どんな入力でも選択肢の中の値しか送らない(requested: int) -> None:
    """選択肢を外すと HTTP 200 のまま0件やエラーページが返る（→ 課題#29）。"""
    rendered = WALK.render(requested)
    assert rendered is not None
    assert rendered["et"][0] in {"1", "5", "7", "10", "15", "20"}


def test_丸めた値は必ず要求より緩い() -> None:
    """上限は要求以上・下限は要求以下。これが崩れると取りこぼす。"""
    for requested in range(1, 21):
        rendered = WALK.render(requested)
        assert rendered is not None
        assert Decimal(rendered["et"][0]) >= requested
    for requested in (20.0, 23.5, 30.0, 47.2, 99.9):
        rendered = AREA_MIN.render(requested)
        assert rendered is not None
        assert Decimal(rendered["mb"][0]) <= Decimal(str(requested))


def test_単位を換算してから丸める() -> None:
    """MUST は円で持つが、SUUMO の賃料欄は万円で受け取る。"""
    spec = ParamSpec(
        site_code="X",
        property_type="CHINTAI",
        axis=ParamAxis.AREA_MAX,  # 単位換算の確認が目的なので軸は借り物
        param_name="ct",
        value_kind="stepped",
        unit="man_yen",
        value_spec={"min": 3, "max": 100, "step": 0.5, "format": "{:.1f}"},
    )
    assert spec.render(156_000) == {"ct": ["16.0"]}  # 15.6万 -> 16.0万へ切り上げ


# ---- 集合条件（間取り）----


def test_全項目を表現できるときだけ間取りを送る() -> None:
    assert LAYOUTS.render(["1LDK", "2DK"]) == {"md": ["04", "06"]}


def test_対応表に無い間取りが1つでもあれば軸ごと送らない() -> None:
    """部分集合を送ると、対応表に無い間取りの掲載をサイト側で落としてしまう。"""
    assert LAYOUTS.render(["1LDK", "5K以上"]) is None


def test_空の間取りは送らない() -> None:
    assert LAYOUTS.render([]) is None


# ---- 無効化・異常系 ----


def test_無効化された軸は送らない() -> None:
    disabled = replace(WALK, is_enabled=False)
    assert disabled.render(10) is None


def test_数値軸に配列を渡したら落とす() -> None:
    with pytest.raises(ParamError):
        WALK.render(["10"])


def test_軸ごとの丸め方向が定義されている() -> None:
    """向きをサイト定義に持たせると必ず間違えるので、軸から決める。"""
    assert AXIS_BOUND[ParamAxis.AREA_MIN] == LOWER
    assert AXIS_BOUND[ParamAxis.AREA_MAX] == UPPER
    assert AXIS_BOUND[ParamAxis.WALK_MINUTES_MAX] == UPPER
    assert AXIS_BOUND[ParamAxis.AGE_MAX] == UPPER


# ---- 表としての振る舞い ----


class _Must:
    area_min = 30.0
    walk_minutes_max = 12
    layouts = ["1LDK", "2DK"]


def test_クエリの並びが実行ごとに変わらない() -> None:
    """URLが揺れるとログもキャッシュも突き合わせられない。"""
    table = SiteParamTable(specs=(WALK, AREA_MIN, LAYOUTS))
    query = table.build_query(
        site_code="SUUMO",
        property_type="CHINTAI",
        must=_Must(),
        axes=["walk_minutes_max", "layouts", "area_min"],
    )
    assert list(query) == ["mb", "md", "et"]  # 軸名の昇順 area_min/layouts/walk...
    assert query == {"mb": ["30"], "md": ["04", "06"], "et": ["15"]}


def test_定義の無い軸は黙って落ちる() -> None:
    """送らないだけで判定はローカルで行われるため、結果は変わらない。"""
    table = SiteParamTable(specs=(WALK,))
    query = table.build_query(
        site_code="SUUMO", property_type="CHINTAI", must=_Must(), axes=["age_max"]
    )
    assert query == {}


# ---- 正典YAML ----


def test_正典YAMLが読めてSUUMOの5軸がそろっている() -> None:
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    axes = set(table.for_site("SUUMO", "CHINTAI"))
    assert axes == {
        "area_min",
        "area_max",
        "walk_minutes_max",
        "age_max",
        "layouts",
    }


def test_正典YAMLの実測値でSUUMOのURLが組める() -> None:
    """2026-09-03 の実測で確定したキー（mb/mt/et/cn/md）を固定する。"""
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    query = table.build_query(
        site_code="SUUMO",
        property_type="CHINTAI",
        must=_Must(),
        axes=["area_min", "walk_minutes_max", "layouts"],
    )
    assert query == {"mb": ["30"], "et": ["15"], "md": ["04", "06"]}


def test_正典YAMLが読めてHOMESの5軸がそろっている() -> None:
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    axes = set(table.for_site("HOMES", "CHINTAI"))
    assert axes == {"area_min", "area_max", "walk_minutes_max", "age_max", "layouts"}


def test_正典YAMLの実測値でHOMESのURLが組める() -> None:
    """2026-09-03 の実測で確定したキーを固定する（足立区・基準 52,515件）。

    間取りは ``cond[madori][15]=15`` のように**値ごとにキーが変わる**。
    チェックボックスの name 属性がこの形で、実測でも件数が動いた（27,029件）。
    """
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    query = table.build_query(
        site_code="HOMES",
        property_type="CHINTAI",
        must=_Must(),
        axes=["area_min", "walk_minutes_max", "layouts"],
    )
    assert query == {
        "cond[housearea]": ["30"],
        "cond[walkminutesh]": ["15"],  # ★12分の要求は緩い側（15分）へ切り上げる
        "cond[madori][15]": ["15"],
        "cond[madori][23]": ["23"],
    }


def test_HOMESの築年数の選択肢に7年は無い() -> None:
    """⚠ SUUMO には 7 があるが HOME'S には無い。サイト間で流用しないための固定。"""
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    homes = table.for_site("HOMES", "CHINTAI")["age_max"]
    suumo = table.for_site("SUUMO", "CHINTAI")["age_max"]
    assert 7 not in homes.value_spec["choices"]
    assert 7 in suumo.value_spec["choices"]
    # 築7年の要求は、HOME'S では緩い側の10年へ切り上がる
    assert homes.render(7) == {"cond[houseageh]": ["10"]}
    assert suumo.render(7) == {"cn": ["7"]}


# ---- 実際にURLへ載せるかどうかのゲート ----


class _Filters:
    def __init__(self, enabled=True, axes=("area_min",), exclude_sites=()):
        self.enabled = enabled
        self.axes = list(axes)
        self.exclude_sites = list(exclude_sites)


class _Search:
    def __init__(self, filters):
        self.site_filters = filters


class _Pattern:
    property_type = "CHINTAI"
    must = _Must()

    def __init__(self, filters):
        self.search = _Search(filters)


class _Scraper:
    site_code = "SUUMO"
    supports_site_filters = True


def _query(filters, scraper=None):
    from house_search.pipeline.scan import site_filter_query

    table = SiteParamTable(specs=(WALK, AREA_MIN, LAYOUTS))
    return site_filter_query(scraper or _Scraper(), _Pattern(filters), table)


def test_有効化されていなければ何も送らない() -> None:
    """既定は無効。無効値の事故が起きたら enabled: false で即座に戻せる。"""
    assert _query(_Filters(enabled=False)) == {}


def test_対応を宣言していないアダプタには送らない() -> None:
    """クエリ文字列を持たないURL体系のサイトへ機械的に付けても効かない。"""

    class _NoSupport:
        site_code = "SMOCCA"
        supports_site_filters = False

    assert _query(_Filters(), scraper=_NoSupport()) == {}


def test_除外指定したサイトには送らない() -> None:
    assert _query(_Filters(exclude_sites=["SUUMO"])) == {}


def test_有効なら指定した軸だけを送る() -> None:
    assert _query(_Filters(axes=["area_min"])) == {"mb": ["30"]}


def test_実運用の検索パターンで実際にURLへ載る() -> None:
    """「実装済みだが未配線」を防ぐ。

    正典YAML・検索パターン・アダプタの宣言・scan の配線が全部そろって
    初めてクエリが出る。どれか1つでも欠けたらここが空になる。
    """
    from house_search.config.pattern import load_patterns
    from house_search.pipeline.scan import site_filter_query

    settings = load_settings()
    table = load_site_params(settings.data_dir / SITE_PARAMS_FILENAME)
    patterns = load_patterns(settings.configs_dir)
    assert patterns, "検索パターンが1つも読めていない"

    for pattern in patterns:
        if not pattern.search.site_filters.enabled:
            continue
        query = site_filter_query(_Scraper(), pattern, table)
        assert query, f"{pattern.name}: SUUMO へ渡すクエリが空になっている"
        # 実測（2026-09-03）で確定したキー
        assert set(query) <= {"mb", "mt", "et", "cn", "md"}


def test_正典YAMLが読めてGOOの3軸がそろっている() -> None:
    """⚠ 面積上限 fu と築年数 by は**未測定なので入れていない**。

    by=5 で掲載が基準99件より多い121件になり説明が付かなかった。
    測っていないものを推測で書かないための固定。
    """
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    axes = set(table.for_site("GOO", "CHINTAI"))
    assert axes == {"area_min", "walk_minutes_max", "layouts"}


def test_正典YAMLの実測値でGOOのURLが組める() -> None:
    """2026-09-03 の実測で確定したキー（fl/wm/lo[]）を固定する。

    GOO は総件数を出さないので、**返る掲載の中身**で効きと向きを確かめた
    （fl=50 → 面積50.1㎡以上だけ / wm=5 → 徒歩5分以内だけ）。
    """
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    query = table.build_query(
        site_code="GOO",
        property_type="CHINTAI",
        must=_Must(),
        axes=["area_min", "walk_minutes_max", "layouts"],
    )
    assert query == {
        "fl": ["30"],
        "wm": ["15"],  # ★12分の要求は緩い側（15分）へ切り上げる
        "lo[]": ["lo0105", "lo0203"],
    }


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (30.0, "30"),  # 選択肢そのもの
        (32.0, "30"),  # 下限は切り下げる
        (22.0, "20"),  # ★10〜20は10刻み。5刻みだと思って25を選ぶと22〜24㎡を取りこぼす
        (55.0, "50"),  # ★50以降は10刻み
        (9.0, None),  # 最小の選択肢より緩い＝送らない
    ],
)
def test_GOOの面積下限は不等間隔の選択肢へ切り下げる(
    requested: float, expected: str | None
) -> None:
    """⚠ GOO の fl は **5刻みではない**（10/20/25…50/60…150）。

    SUUMO の stepped（20〜100の5刻み）を流用すると、選択肢に無い値を送って
    しまう。SUUMO はエラーページを返すが、GOO の挙動は未測定なので
    そもそも作らせないのが安全。
    """
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    spec = table.for_site("GOO", "CHINTAI")["area_min"]
    assert spec.render(requested) == ({"fl": [expected]} if expected else None)


def test_クエリ無しのURLにもフィルタを足せる() -> None:
    """⚠ 区切り文字を & で固定しない。

    「list_urls は必ずクエリ付きのURLを返す」という前提だったが、GOO は
    price_max_hint が無いとクエリ無しのURLを返す。& 固定だと
    ``....html&fl=30`` という壊れたURLになり、**0件になるだけで例外にならない**。
    """
    from house_search.pipeline.scan import _with_site_filters

    class _Scraper:
        site_code = "GOO"
        supports_site_filters = True

        def list_urls(self, pattern: object, areas: object) -> list[str]:
            return ["https://example.test/a.html", "https://example.test/b.html?ru=10"]

    class _Filters:
        enabled = True
        axes = ["area_min"]
        exclude_sites: list[str] = []

    class _Search:
        site_filters = _Filters()

    class _Pattern:
        property_type = "CHINTAI"
        search = _Search()
        must = _Must()

    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    urls = _with_site_filters(_Scraper(), _Pattern(), [], table)
    assert urls == [
        "https://example.test/a.html?fl=30",
        "https://example.test/b.html?ru=10&fl=30",
    ]


def test_正典YAMLが読めてAPAMANの2軸がそろっている() -> None:
    """⚠ 間取り（madori）は**送っても効かなかった**ので配線していない。"""
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    axes = set(table.for_site("APAMAN", "CHINTAI"))
    assert axes == {"area_min", "walk_minutes_max"}


def test_正典YAMLの実測値でAPAMANのURLが組める() -> None:
    """2026-09-03 の実測（足立区 tokyo/121/・基準 掲載28件）で確定したキーを固定する。

    ⚠ APAMAN は総件数を出さないので**返る掲載の中身**で確かめた。
    senyu1=30 で面積30.6㎡以上・ekitoho=10 で徒歩10分以内だけが返った。

    ⚠ axes に layouts を渡しても**定義が無いので黙って落ちる**。
    キーと値は実HTMLから採れた（madori14=1LDK 等）が、5種すべてを送っても
    掲載28件・間取りの内訳とも基準と完全に同一で効かなかったため。
    """
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    query = table.build_query(
        site_code="APAMAN",
        property_type="CHINTAI",
        must=_Must(),
        axes=["area_min", "walk_minutes_max", "layouts"],
    )
    # ★12分の要求は選択肢に無いので緩い側（15分）へ切り上がる
    assert query == {"senyu1": ["30"], "ekitoho": ["15"]}


def test_APAMANの面積の選択肢は不等間隔() -> None:
    """⚠ 50㎡以降は10刻み。SUUMO の stepped（5刻み）を流用してはいけない。"""
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    choices = table.for_site("APAMAN", "CHINTAI")["area_min"].value_spec["choices"]
    assert 55 not in choices
    assert [c for c in choices if c >= 50] == [50, 60, 70, 80, 90, 100]


# ---- アットホーム（実測 2026-09-03 → 課題#39）----


def test_正典YAMLが読めてATHOMEの2軸がそろっている() -> None:
    """⚠ 間取り（MADORI[]）は**未測定なので入れていない**。

    キーと選択肢は実HTMLから採れているが、APAMAN の madori のように
    「送っても黙って無視される」ことがあるため、効きを実測するまで書かない。
    """
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    axes = set(table.for_site("ATHOME", "CHINTAI"))
    assert axes == {"area_min", "walk_minutes_max"}


def test_正典YAMLの実測値でATHOMEのURLが組める() -> None:
    """2026-09-03 の実測で確定したキー（MENSEKI/EKITOHO）を固定する。

    ATHOME は総件数を出さないので**返る掲載の中身**で効きと向きを確かめた
    （MENSEKI=kt004 → 30㎡未満0件 / EKITOHO=ke006 → 20分超0件）。
    """
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    query = table.build_query(
        site_code="ATHOME",
        property_type="CHINTAI",
        must=_Must(),
        axes=["area_min", "walk_minutes_max", "layouts"],
    )
    # ★12分の要求は選択肢に無いので緩い側（15分＝ke005）へ切り上がる
    assert query == {"MENSEKI": ["kt004"], "EKITOHO": ["ke005"]}


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (30.0, "kt004"),  # 選択肢そのもの
        (32.0, "kt004"),  # 下限は切り下げる
        (100.0, "kt018"),
        (19.0, None),  # 最小の選択肢より緩い＝送らない
    ],
)
def test_ATHOMEの面積下限はコードへ写される(requested: float, expected: str | None) -> None:
    """⚠ 値から算術で導けないので ``codes`` の対応表で写す。"""
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    spec = table.for_site("ATHOME", "CHINTAI")["area_min"]
    assert spec.render(requested) == ({"MENSEKI": [expected]} if expected else None)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (20, "ke006"),
        (12, "ke005"),  # 上限は切り上げる
        (7, "ke101"),  # ★連番でない（10=ke004 より後に差し込まれている）
        (1, "ke102"),  # ★同上
        (25, None),  # 最大の選択肢より緩い＝送らない
    ],
)
def test_ATHOMEの駅徒歩は連番でないコードへ写される(
    requested: int, expected: str | None
) -> None:
    """⚠ 1→ke102 / 7→ke101 と後から差し込まれており、算術では導けない。"""
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    spec = table.for_site("ATHOME", "CHINTAI")["walk_minutes_max"]
    assert spec.render(requested) == ({"EKITOHO": [expected]} if expected else None)


def test_選択肢とコードの対応が欠けていたら落とす() -> None:
    """⚠ ``choices`` だけ増やして ``codes`` を足し忘れると黙って壊れる。

    送らない（＝母集団が広がるだけ）で済ませず例外にする。0件事故と違い、
    これは設定そのものの誤りだと確定しているため。
    """
    spec = ParamSpec(
        site_code="ATHOME",
        property_type="CHINTAI",
        axis="area_min",
        param_name="MENSEKI",
        value_kind="enum",
        unit="sqm",
        value_spec={"choices": [20, 30], "format": "{:.0f}", "codes": {20: "kt002"}},
    )
    with pytest.raises(ParamError, match="codes"):
        spec.render(30.0)


def test_コード対応表を持つ軸は全選択肢ぶんそろっている() -> None:
    """正典YAML全体の回帰。片方だけ増やす事故を実行前に落とす。"""
    table = load_site_params(load_settings().data_dir / SITE_PARAMS_FILENAME)
    for spec in table.specs:
        codes = spec.value_spec.get("codes")
        if not codes:
            continue
        template = str(spec.value_spec.get("format", "{}"))
        keys = {str(key) for key in codes}
        missing = {
            template.format(Decimal(str(choice)))
            for choice in spec.value_spec["choices"]
        } - keys
        assert not missing, f"{spec.site_code}/{spec.axis}: codes が欠けています: {missing}"
