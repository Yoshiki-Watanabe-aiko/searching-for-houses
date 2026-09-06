"""検索パターンYAML（v2スキーマ）のテスト。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from house_search.config.metrics import must_items_for
from house_search.config.pattern import (
    ChintaiPattern,
    KodateBuyPattern,
    MansionBuyPattern,
    load_pattern_file,
    parse_pattern,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _chintai(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "テスト賃貸",
        "property_type": "CHINTAI",
        "webhook_ref": "CHINTAI_ALONE",
        "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"], "price_max_hint": 90000},
        "must": {"rent_total_max": 70000, "area_min": 30.0},
        "want": {
            "features": [{"code": "SEC_AUTOLOCK", "weight": 8}],
            "numeric": [{"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000}],
        },
    }
    base.update(overrides)
    return base


def test_賃貸パターンが読める() -> None:
    pattern = parse_pattern(_chintai())
    assert isinstance(pattern, ChintaiPattern)
    assert pattern.must.rent_total_max == 70000
    assert pattern.ranking.top_n == 15  # 既定値


def test_物件種別で3ファミリへ分岐する() -> None:
    mansion = parse_pattern(
        _chintai(
            property_type="CHUKO_MANSION",
            must={"price_max": 50_000_000},
            want={"numeric": [{"metric": "price", "weight": 10, "best": 3e7, "worst": 5e7}]},
        )
    )
    assert isinstance(mansion, MansionBuyPattern)

    kodate = parse_pattern(
        _chintai(
            property_type="SHINCHIKU_KODATE",
            must={"price_max": 60_000_000, "land_area_min": 100.0},
            want={"numeric": [{"metric": "land_area_sqm", "weight": 8, "best": 150, "worst": 90}]},
        )
    )
    assert isinstance(kodate, KodateBuyPattern)


def test_種別に適用できないmetricは弾く() -> None:
    """戸建てに専有面積 metric を流用させない（混線と名寄せ事故の防止）。"""
    with pytest.raises(ValidationError, match="area_sqm"):
        parse_pattern(
            _chintai(
                property_type="CHUKO_KODATE",
                must={"price_max": 50_000_000},
                want={"numeric": [{"metric": "area_sqm", "weight": 5, "best": 90, "worst": 60}]},
            )
        )


def test_新築に築年数metricは使えない() -> None:
    with pytest.raises(ValidationError, match="age_years"):
        parse_pattern(
            _chintai(
                property_type="SHINCHIKU_MANSION",
                must={"price_max": 80_000_000},
                want={"numeric": [{"metric": "age_years", "weight": 4, "best": 0, "worst": 30}]},
            )
        )


def test_未知のmetricは弾く() -> None:
    with pytest.raises(ValidationError, match="未知の metric"):
        parse_pattern(
            _chintai(
                want={"numeric": [{"metric": "tsubo_tanka", "weight": 5, "best": 1, "worst": 2}]}
            )
        )


def test_種別に適用できないMUST項目は弾く() -> None:
    """賃貸に土地面積のMUSTを書けないようにする。"""
    with pytest.raises(ValidationError):
        parse_pattern(_chintai(must={"rent_total_max": 70000, "land_area_min": 100.0}))


def test_metricの重複を弾く() -> None:
    with pytest.raises(ValidationError, match="重複"):
        parse_pattern(
            _chintai(
                want={
                    "numeric": [
                        {"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000},
                        {"metric": "rent_total", "weight": 3, "best": 40000, "worst": 60000},
                    ]
                }
            )
        )


def test_bestとworstが同値なら弾く() -> None:
    with pytest.raises(ValidationError, match="0除算"):
        parse_pattern(
            _chintai(
                want={"numeric": [{"metric": "rent_total", "weight": 10, "best": 5, "worst": 5}]}
            )
        )


def test_綴り間違いを黙って無視しない() -> None:
    with pytest.raises(ValidationError):
        parse_pattern(_chintai(rankingg={"top_n": 5}))


def test_weightは正の数のみ() -> None:
    with pytest.raises(ValidationError):
        parse_pattern(_chintai(want={"features": [{"code": "SEC_AUTOLOCK", "weight": 0}]}))


def test_config_hashは検索範囲の変更では変わらない() -> None:
    """エリアを足しただけで全件再スコアが走らないようにする。"""
    a = parse_pattern(_chintai())
    b = parse_pattern(
        _chintai(search={"prefectures": ["東京都", "千葉県"], "price_max_hint": 120000})
    )
    assert a.config_hash() == b.config_hash()


def test_config_hashはWANTの変更で変わる() -> None:
    a = parse_pattern(_chintai())
    b = parse_pattern(
        _chintai(
            want={
                "features": [{"code": "SEC_AUTOLOCK", "weight": 9}],
                "numeric": [{"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000}],
            }
        )
    )
    assert a.config_hash() != b.config_hash()


def test_config_hashはPYTHONHASHSEEDに依存しない() -> None:
    """スコア再計算の判定に使う以上、プロセス間で同じ値でなければならない。

    dict/set の反復順に依存する実装が紛れ込むと、再起動のたびに全件再スコアが走る。
    """
    src_dir = str(REPO_ROOT / "src")
    script = (
        "import json,sys;"
        f"sys.path.insert(0, r'{src_dir}');"
        "from house_search.config.pattern import parse_pattern;"
        "print(parse_pattern(json.loads(sys.argv[1])).config_hash())"
    )
    payload = json.dumps(_chintai())

    hashes = set()
    for seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", script, payload],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        hashes.add(out.stdout.strip())
    assert len(hashes) == 1, f"PYTHONHASHSEED でハッシュが変わった: {hashes}"


def test_同梱の雛形YAMLが読める() -> None:
    pattern = load_pattern_file(REPO_ROOT / "configs" / "examples" / "chintai_v2.yaml")
    assert isinstance(pattern, ChintaiPattern)
    assert pattern.webhook_ref == "CHINTAI_ALONE"
    assert len(pattern.want.features) == 8
    assert len(pattern.want.numeric) == 4


@pytest.mark.parametrize(
    ("filename", "expected_cls", "expected_type"),
    [
        ("mansion_buy_v2.yaml", MansionBuyPattern, "CHUKO_MANSION"),
        # ⚠ 新築は ``age_years`` が適用外。中古からコピーして残すとここで落ちる
        ("shinchiku_mansion_v2.yaml", MansionBuyPattern, "SHINCHIKU_MANSION"),
        ("kodate_buy_v2.yaml", KodateBuyPattern, "CHUKO_KODATE"),
    ],
)
def test_売買の雛形YAMLが読める(
    filename: str, expected_cls: type, expected_type: str
) -> None:
    pattern = load_pattern_file(REPO_ROOT / "configs" / "examples" / filename)
    assert isinstance(pattern, expected_cls)
    assert pattern.property_type == expected_type


def test_雛形YAMLはconfigs直下に置かない() -> None:
    """⚠ 直下の *.yaml は実パターンとして scan が走り、同じ Webhook へ二重通知される。

    実際に起きた事故なので、雛形が増えるたびに人力で気をつけるのではなく固定する
    （glob は非再帰なので examples/ 配下は読まれない）。
    """
    live = {p.name for p in (REPO_ROOT / "configs").glob("*.yaml")}
    assert live == {
        "chintai_23ku.yaml",
        "chintai_suburb60.yaml",
        "chuko_mansion.yaml",
    }, f"configs/ 直下に実運用しないパターンがある: {sorted(live)}"


def test_実運用の売買パターンが読める() -> None:
    """中古マンションの実運用パターンが v2 スキーマを満たすこと（Phase 6 手順5）。

    ⚠ MUST を広めに取ってある（→ 課題#4 のユーザー判断 2026-09-06）。
    MUST 1段目で fail した掲載はDBに残らないので**緩める方向は取り直しになる**
    （→ ADR 0013）。締める方向は `rescore` だけで試せるので、まず分布を見る。
    """
    pattern = load_pattern_file(REPO_ROOT / "configs" / "chuko_mansion.yaml")
    assert pattern.property_type == "CHUKO_MANSION"
    assert pattern.sites == ["SUUMO"], "ホームズは取得枠を賃貸と食い合うので入れない（→ 課題#4）"
    # ⚠ 締める方向へ変えるのは自由だが、緩める方向は取り直しになる
    assert pattern.must.price_max == 100_000_000
    assert pattern.must.layouts == [], "売買の md は部屋数でしか切れないので制約しない"
    # 効きを実測した2軸だけをサイトへ渡す（→ ADR 0015・課題#4 手順4）
    assert pattern.search.site_filters.enabled is True
    assert set(pattern.search.site_filters.axes) == {"area_min", "walk_minutes_max"}
    # ⚠ 売買辞書（buy:）が空なうちに WANT の設備条件を書くと全件 miss になり、
    #   分母にだけ乗ってスコア全体が沈む
    assert pattern.want.features == []


@pytest.mark.parametrize("filename", ["chintai_23ku.yaml", "chintai_suburb60.yaml"])
def test_実運用の検索パターンが読める(filename: str) -> None:
    """エリア帯ごとの実運用パターンが v2 スキーマを満たすこと（課題#9）。"""
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert pattern.property_type == "CHINTAI"
    # RC / SRC は排他なので any_of で1項目にまとめてある
    any_of_items = [f for f in pattern.want.features if f.any_of]
    assert [f.codes for f in any_of_items] == [("STRUCT_RC", "STRUCT_SRC")]


@pytest.mark.parametrize(
    ("filename", "webhook_ref"),
    [
        ("chintai_23ku.yaml", "CHINTAI_23KU"),
        ("chintai_suburb60.yaml", "CHINTAI_SUBURB60"),
    ],
)
def test_個別通知は帯ごとに別チャンネルへ送る(filename: str, webhook_ref: str) -> None:
    """帯ごとに ``webhook_ref`` を分けてあること（2026-09-05 ユーザー判断）。

    同じ ref を共有していると「設定ファイルごとにチャンネルを分ける」が
    崩れるが、送信自体は成功するので Discord を見るまで気づけない。
    """
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert pattern.webhook_ref == webhook_ref


@pytest.mark.parametrize("filename", ["chintai_23ku.yaml", "chintai_suburb60.yaml"])
def test_ダイジェストは専用チャンネルへ集約する(filename: str) -> None:
    """上位N件は帯をまたいで1つの「厳選」チャンネルへ送る。"""
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert pattern.digest_webhook_ref == "CHINTAI_DIGEST"
    assert pattern.effective_digest_webhook_ref == "CHINTAI_DIGEST"
    # ⚠ 個別通知と同じチャンネルに戻っていないこと
    assert pattern.effective_digest_webhook_ref != pattern.webhook_ref


@pytest.mark.parametrize("filename", ["chintai_23ku.yaml", "chintai_suburb60.yaml"])
def test_個別通知は上位200位までに絞る(filename: str) -> None:
    """``notify_max_rank`` が実運用パターンに配線されていること。"""
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert pattern.ranking.notify_max_rank == 200


def test_digest_webhook_refは未指定ならwebhook_refへ落ちる() -> None:
    """既存パターンの挙動を変えない（省略時は従来どおり同じチャンネル）。"""
    pattern = parse_pattern(_chintai())
    assert pattern.digest_webhook_ref is None
    assert pattern.effective_digest_webhook_ref == "CHINTAI_ALONE"


def test_notify_max_rankの既定は無制限() -> None:
    """既定を200にすると、新しいパターンが黙って通知を絞ることになる。"""
    pattern = parse_pattern(_chintai())
    assert pattern.ranking.notify_max_rank is None


def test_notify_max_rankは0以下を受け付けない() -> None:
    with pytest.raises(ValidationError):
        parse_pattern(_chintai(ranking={"notify_max_rank": 0}))


def test_通知先と順位上限はconfig_hashを変えない() -> None:
    """通知の設定を変えただけで全件再スコアが走らないこと。

    ``config_hash`` は property_type / want / commute だけを見る。
    ここに通知の設定が混ざると、チャンネルを分けた瞬間に
    数千件の再採点が走る（実害は時間だけだが意図しない挙動）。
    """
    base = parse_pattern(_chintai()).config_hash()
    changed = parse_pattern(
        _chintai(
            webhook_ref="CHINTAI_23KU",
            digest_webhook_ref="DIGEST",
            ranking={"top_n": 15, "notify_max_rank": 200},
        )
    ).config_hash()
    assert base == changed


def test_エリア帯は市区を明示列挙し重ならない() -> None:
    """帯は行政区画ではなく通勤圏で切るため、市区の明示リストで定義する。

    帯が重なると同じ掲載が2つのランキングに出て通知も二重になる。
    ``cities`` が空だと都道府県内の全市区へ自動展開され、
    群馬県境や外房まで同じ帯に入ってしまう（実測でランキングが埋まった）。
    """
    bands = {
        name: set(load_pattern_file(REPO_ROOT / "configs" / name).search.cities)
        for name in ("chintai_23ku.yaml", "chintai_suburb60.yaml")
    }
    for name, cities in bands.items():
        assert cities, f"{name}: cities が空だと都道府県内の全市区へ広がる"
    overlap = bands["chintai_23ku.yaml"] & bands["chintai_suburb60.yaml"]
    assert not overlap, f"エリア帯が重なっている: {sorted(overlap)}"


def test_codeとany_ofの同時指定はエラーになる() -> None:
    with pytest.raises(ValidationError, match="code か any_of"):
        parse_pattern(
            _chintai(want={"features": [{"code": "A", "any_of": ["B", "C"], "weight": 1}]})
        )


def test_codeもany_ofも無いとエラーになる() -> None:
    with pytest.raises(ValidationError, match="code か any_of"):
        parse_pattern(_chintai(want={"features": [{"weight": 1}]}))


def test_any_ofが1件だけならエラーになる() -> None:
    with pytest.raises(ValidationError, match="2つ以上"):
        parse_pattern(_chintai(want={"features": [{"any_of": ["STRUCT_RC"], "weight": 1}]}))


def test_any_of内の条件コードも重複検査の対象になる() -> None:
    with pytest.raises(ValidationError, match="重複"):
        parse_pattern(
            _chintai(
                want={
                    "features": [
                        {"code": "STRUCT_RC", "weight": 1},
                        {"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 1},
                    ]
                }
            )
        )


def test_any_ofのキーは条件コードの昇順で安定する() -> None:
    pattern = parse_pattern(
        _chintai(want={"features": [{"any_of": ["STRUCT_SRC", "STRUCT_RC"], "weight": 1}]})
    )
    assert pattern.want.features[0].key == "STRUCT_RC|STRUCT_SRC"


def test_config_hashはany_ofの記法を区別する() -> None:
    merged = parse_pattern(
        _chintai(want={"features": [{"any_of": ["STRUCT_RC", "STRUCT_SRC"], "weight": 6}]})
    )
    split = parse_pattern(
        _chintai(
            want={
                "features": [
                    {"code": "STRUCT_RC", "weight": 6},
                    {"code": "STRUCT_SRC", "weight": 6},
                ]
            }
        )
    )
    # スコアの出方が変わる以上、自動再スコアが走るようハッシュも変わるべき
    assert merged.config_hash() != split.config_hash()


# --- MUST項目のレジストリと Must クラスの突き合わせ ---------------------------
#
# ⚠ 片方だけ増やしても既存テストは緑のまま通る。レジストリが「使える」と言う項目が
# Must クラスに無ければ YAML に書いた時点で extra="forbid" に弾かれ、逆に Must クラス
# にしか無い項目は _validate_against_registry が制御項目（unknown_policy 等）とみなして
# 素通りさせる。前者は明示エラーだが、後者は**設定したのに効かない**まま通る。
# 種別を足すときに黙って古くならないよう機械的に固定する（→ 課題#4）。

_PATTERN_CLASSES = (ChintaiPattern, MansionBuyPattern, KodateBuyPattern)

# レジストリに対応する MustSpec を持たない制御項目。
_CONTROL_FIELDS = frozenset({"unknown_policy"})


def _property_types_of(pattern_cls: type) -> tuple[str, ...]:
    """パターンクラスが受け持つ物件種別（Literal の値）。"""
    return get_args(pattern_cls.model_fields["property_type"].annotation)


def _must_fields_of(pattern_cls: type) -> set[str]:
    """そのパターンの must クラスが持つ条件フィールド。"""
    must_cls = pattern_cls.model_fields["must"].annotation
    return set(must_cls.model_fields) - _CONTROL_FIELDS


@pytest.mark.parametrize("pattern_cls", _PATTERN_CLASSES)
def test_レジストリが使えると言うMUST項目はYAMLに書ける(pattern_cls: type) -> None:
    fields = _must_fields_of(pattern_cls)
    for ptype in _property_types_of(pattern_cls):
        missing = {spec.name for spec in must_items_for(ptype)} - fields
        assert not missing, (
            f"{ptype} で使えるはずの MUST が {pattern_cls.__name__} の "
            f"must クラスに無い: {sorted(missing)}"
        )


@pytest.mark.parametrize("pattern_cls", _PATTERN_CLASSES)
def test_MustクラスのフィールドはそのファミリのMUSTに限られる(pattern_cls: type) -> None:
    """⚠ 土地（Phase 9）を足したとき、間取りが土地に書けてしまうのを防ぐ側の担保。

    レジストリが許していない項目をクラスに置くと、YAML には書けるのに判定されない
    （実行時に全件 unknown になるだけで例外にならない）。
    """
    allowed = {
        spec.name
        for ptype in _property_types_of(pattern_cls)
        for spec in must_items_for(ptype)
    }
    extra = _must_fields_of(pattern_cls) - allowed
    assert not extra, (
        f"{pattern_cls.__name__} の must クラスにレジストリ外の項目がある: {sorted(extra)}"
    )
