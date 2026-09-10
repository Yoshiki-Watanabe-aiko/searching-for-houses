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
    TochiBuyPattern,
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
        # 土地（Phase 9a → 課題#61）。⚠ 建物系の項目を残すとここで落ちる
        ("tochi_buy_v2.yaml", TochiBuyPattern, "TOCHI"),
    ],
)
def test_売買の雛形YAMLが読める(filename: str, expected_cls: type, expected_type: str) -> None:
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
        "shinchiku_mansion.yaml",
        # 戸建て2種別（Phase 6 手順8・2026-09-07）。⚠ 直下へ置く前に scan --seed を流した
        "chuko_kodate.yaml",
        "shinchiku_kodate.yaml",
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
    # 設備は 2026-09-07 に配点した（辞書の buy: が育つまでは空にしてあった）。
    # ⚠ **合計60点** の水準を固定する（4都県全域への拡大時に 40 → 60 へ。購入で検討すべき
    #   材料として設備の比重を上げるユーザー判断 2026-09-07 → 課題#4）。増減はユーザー判断で、
    #   変えると config_hash が変わり全件が自動再スコアされる
    weights = [item.weight for item in pattern.want.features]
    assert sum(weights) == 60, "設備の重み合計（ユーザー判断 2026-09-07・4都県拡大）"
    codes = {c for item in pattern.want.features for c in (item.any_of or [item.code])}
    # ⚠ 出現率が高すぎる条件を入れてはいけない。ほぼ全件が持つと分母 Σw だけ
    #   増えて全掲載が等しく加点され、順位がまったく動かない（→ 課題#15・#31）
    assert not codes & {"EQUIP_ELEVATOR", "LOC_FLOOR_2UP", "KITCHEN_SYSTEM"}, (
        "実測で出現率83%超の条件は判別力を持たない（61掲載・2026-09-07）"
    )


def test_実運用の新築マンションパターンが読める() -> None:
    """新築マンションの実運用パターンが v2 スキーマを満たすこと（Phase 6 手順6-3）。

    ⚠ **中古のパターンからコピーすると落ちる箇所**を固定してある。
    ``age_years`` は新築マンションに適用されない metric なので、`want.numeric` に
    残っているとレジストリが弾く。
    """
    pattern = load_pattern_file(REPO_ROOT / "configs" / "shinchiku_mansion.yaml")
    assert pattern.property_type == "SHINCHIKU_MANSION"
    assert pattern.sites == ["SUUMO"]
    # ⚠ 新築に適用されない metric が紛れていないこと
    metrics = {item.metric for item in pattern.want.numeric}
    assert "age_years" not in metrics, "age_years は新築マンションに適用されない"
    # ⚠ 棟には設備原文が無いので、書くと棟が全件 miss になり分母にだけ乗る。
    #   実測（2026-09-07）で active 50掲載のうち**設備原文があるのは13件（26%）だけ**。
    #   中古マンションには配点したが（設備60点）、新築は据え置く（ユーザー判断 2026-09-07）
    assert pattern.want.features == []
    # ⚠ 価格未定（price が NULL）・間取りレンジ・管理費未取得が構造的に生じるので、
    #   drop へ倒すと棟の掲載がまとめて消える
    assert pattern.must.unknown_policy == "keep"
    # ⚠ 新築のサイト側フィルタは効きが未測定なので送らない（→ ADR 0015）
    assert pattern.search.site_filters.enabled is False


BUY_PATTERN_FILES = (
    "chuko_mansion.yaml",
    "shinchiku_mansion.yaml",
    "chuko_kodate.yaml",
    "shinchiku_kodate.yaml",
)


@pytest.mark.parametrize("filename", BUY_PATTERN_FILES)
def test_売買パターンは4都県全域を対象にする(filename: str) -> None:
    """売買4パターンは「東京・千葉・埼玉・神奈川の全域」で、市区を限定しない
    （ユーザー判断 2026-09-07 → 課題#4「4都県全域への拡大」）。

    ⚠ 賃貸のエリア帯（``cities`` 必須 → ``test_エリア帯は市区を明示列挙し重ならない``）
    とは**逆の運用**。売買は相場より安いことで見るため帯に相当する概念を持たない。
    ⚠ ``cities`` が空なら売買アダプタ（``requires_city=True``）が全市区へ自動展開する。
    SUUMO のスラグが無い市区は ``resolve_areas`` が黙って落とす（実測 173/251 市区）。
    ⚠⚠ 市区を絞り直すのは自由だが、**広げる方向は `scan --seed` を先に流す**
    （配置した瞬間から定期スキャンが新規掲載を全件通知する → ADR 0006）。
    """
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert pattern.property_type != "CHINTAI"
    assert set(pattern.search.prefectures) == {"東京都", "千葉県", "埼玉県", "神奈川県"}, (
        f"{filename}: 4都県すべてを対象にする"
    )
    assert pattern.search.cities == [], f"{filename}: 売買は市区を限定しない（全域）"


@pytest.mark.parametrize("filename", BUY_PATTERN_FILES)
def test_売買パターンは通勤を下げハザードを上げる(filename: str) -> None:
    """売買の配点は「通勤を下げ、購入で検討すべき材料（ハザード）を上げる」
    （ユーザー判断 2026-09-07 → 課題#4）。4パターン共通の値を固定する。

    ⚠ weight を変えると config_hash が変わり、次回の定期スキャンで全件再スコアされる。
    ⚠ best/worst はここでは固定しない（4都県の母集団で付け直す → 課題#31・#34）。

    ⚠⚠ **価格は 40 → 20 へ半分にし、空いた 20 を相場比へ回した**
    （ユーザー判断 2026-09-08「相場より安いことを重視したい」→ 課題#49 Step 6）。
    起票時（2026-09-07）は「相場が無い間は価格の絶対額で採点する」前提で 40 を
    固定していたが、国交省「不動産情報ライブラリ」の㎡単価相場が入ったので前提が変わった。
    ⚠ **価格軸の合計（20＋20）は据え置き**で、内訳が「絶対額」から
    「絶対額＋相場比」へ半々に変わっただけ。
    """
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    weights = {item.metric: item.weight for item in pattern.want.numeric}
    assert weights["commute_minutes"] == 10, f"{filename}: 通勤は 25 → 10 へ下げた"
    assert weights["walk_minutes"] == 8, f"{filename}: 徒歩は 10 → 8 へ下げた"
    assert weights["flood_rank_avg"] == 25, f"{filename}: 洪水は 15 → 25 へ上げた"
    assert weights["landslide_area_ratio"] == 10, f"{filename}: 土砂は 5 → 10 へ上げた"
    assert weights["price"] == 20, f"{filename}: 価格は 40 → 20（相場比へ半分回した）"
    assert weights["market_rate_ratio"] == 20, f"{filename}: 相場比（→ 課題#49 Step 6）"
    # ⚠ 価格軸の合計は変えていない（内訳が変わっただけ）
    assert weights["price"] + weights["market_rate_ratio"] == 40, f"{filename}: 価格軸の合計"
    # ⚠ 売買は地域を限定しない判断と整合させ、通勤の MUST は置かない（→ 課題#4 手順8）
    assert pattern.must.commute_minutes_max is None, f"{filename}: 通勤の MUST は置かない"

    # ⚠⚠ **液状化は売買4本にだけ配点する**（→ 課題#59・ADR 0023）。
    # 賃貸2帯は帯が低地に集中していて洪水との順位相関が 0.70〜0.77 とゲート不通過
    # （ユーザー判断 2026-09-10）。賃貸に混ざっていないことは別のテストで固定する。
    assert weights["liquefaction_rank_avg"] == 10, f"{filename}: 液状化は土砂と同格の 10"
    # ⚠ **洪水より小さいこと。** 洪水との相関が 0.49〜0.57 で半分ほど重なるので、
    #    同格以上にすると「低地であること」への重みが二重に掛かる
    assert weights["liquefaction_rank_avg"] < weights["flood_rank_avg"], (
        f"{filename}: 液状化は洪水より小さくする（重なりがあるため）"
    )
    liquefaction = next(
        item for item in pattern.want.numeric if item.metric == "liquefaction_rank_avg"
    )
    # ⚠⚠ **best は 0 ではなく 1**（丁目の全面が「液状化しにくい」）。
    #    0 は出ない値で、来たら「評価対象外（原典のレベル6）が漏れている」（→ ADR 0023 決定2）。
    assert liquefaction.best == 1, f"{filename}: 液状化の best は 1（0 は出ない値）"
    # ⚠ **向き**。best < worst でなければ安全な丘陵が最下位になる
    assert liquefaction.best < liquefaction.worst, f"{filename}: 液状化の向き"
    assert liquefaction.worst == 4.5, f"{filename}: 0点張り付き率から 4.5（→ 課題#59）"


@pytest.mark.parametrize("filename", ("chintai_23ku.yaml", "chintai_suburb60.yaml"))
def test_賃貸には液状化を配点しない(filename: str) -> None:
    """⚠ 賃貸2帯は洪水との相関が 0.70〜0.77 でゲート不通過（→ 課題#59）。

    帯が低地（23区・近郊60分圏）に集中しており、液状化は洪水の言い換えに近い。
    足すと分母 Σw だけ増えて順位が動かない（→ 課題#15・#31）。
    """
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    metrics = {item.metric for item in pattern.want.numeric}
    assert "liquefaction_rank_avg" not in metrics, (
        f"{filename}: 賃貸に液状化を配点しない（ゲート不通過 → 課題#59）"
    )
    # ⚠ 洪水・土砂は従来どおり効いていること（液状化の追加で崩していない）
    assert "flood_rank_avg" in metrics
    assert "landslide_area_ratio" in metrics


@pytest.mark.parametrize("filename", ("chuko_kodate.yaml", "shinchiku_kodate.yaml"))
def test_戸建てパターンの設備は50点(filename: str) -> None:
    """戸建て2種別は 4都県拡大時に設備 50 点を新設した（ユーザー判断 2026-09-07 → 課題#4）。

    ⚠ 新築マンションには入れない（棟に設備原文が無く、配点すると棟が構造的に沈む
    → ``test_実運用の新築マンションパターンが読める``）。中古マンションは 60 点
    （→ ``test_実運用の売買パターンが読める``）。
    """
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert sum(item.weight for item in pattern.want.features) == 50, (
        f"{filename}: 設備の重み合計（ユーザー判断 2026-09-07）"
    )


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

_PATTERN_CLASSES = (ChintaiPattern, MansionBuyPattern, KodateBuyPattern, TochiBuyPattern)

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
        spec.name for ptype in _property_types_of(pattern_cls) for spec in must_items_for(ptype)
    }
    extra = _must_fields_of(pattern_cls) - allowed
    assert not extra, (
        f"{pattern_cls.__name__} の must クラスにレジストリ外の項目がある: {sorted(extra)}"
    )
