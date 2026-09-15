"""建築条件付きの土地を除く MUST（``exclude_build_condition``）のテスト（→ 課題#61）。

土地の詳細（2026-09-15・詳細済み9,326件）の17%＝1,593件が建築条件付きで、上位100件に16件いた。
建築条件付きは「土地売買の契約後、指定の業者と数か月以内に建築請負契約を結ぶ」条件で、
ハウスメーカーを選べないので、土地だけを探す目的では候補にならない（ユーザー判断 2026-09-15）。

判定は ``freehold_only``（→ 課題#65）と同じ形にしてある:

- 付 → fail／なし → pass／詳細未取得・読めない表記 → unknown（``unknown_policy`` に従う）
- **グループ内の全掲載**から集め、1件でも付なら付
- 保存済みの原文（``建築条件``）があれば採点のたびにそこから導く
  （規則を直したら ``rescore`` だけで直る）
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from house_search.config.pattern import (
    ChintaiMust,
    KodateBuyMust,
    MansionBuyMust,
    TochiBuyMust,
    load_pattern_file,
)
from house_search.pipeline.persist import (
    load_city_index,
    load_listing_views,
    save_detail,
    upsert_listings,
)
from house_search.scoring.listing_view import ListingView
from house_search.scoring.must import FAIL, PASS, UNKNOWN, evaluate_must
from house_search.scrape.base import (
    ScrapedDetail,
    ScrapedListing,
    build_condition_flag,
    build_condition_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- 建築条件の読み方（純関数） -------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("付", True),
        ("-", False),
        ("無", False),
        ("なし", False),
        (None, None),
        ("相談", None),
        # ⚠ 本番DB（2026-09-15）に23件。区画分譲で「一部の区画だけ」条件付きで、
        #    この掲載の区画かは読めない。推測で True/False に倒さない（→ ADR 0015）
        ("一部建築条件付", None),
        ("一部建築条件付2区画No.2", None),
    ],
)
def test_建築条件の読み方(value: str | None, expected: bool | None) -> None:
    assert build_condition_flag(value) is expected


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        # 原文があれば原文から導く（保存済みの真偽値より優先する）
        ([("付", None)], True),
        ([("一部建築条件付", None)], None),
        # アダプタは「-」を原文として残さないので、保存済みの真偽値へ落とす
        ([(None, False)], False),
        ([(None, True)], True),
        ([(None, None)], None),
        # ⚠ 1件でも付なら付（同じ区画を別の会社が「-」で載せていても除外側に倒す）
        ([(None, False), ("付", True)], True),
        ([(None, None), (None, False)], False),
        ([], None),
    ],
)
def test_グループの掲載をまとめる(
    members: list[tuple[str | None, bool | None]], expected: bool | None
) -> None:
    assert build_condition_of(members) is expected


# --- MUST の判定 -----------------------------------------------------------


def _check(must: object, view: ListingView, **kwargs) -> str:
    result = evaluate_must(view, must, **kwargs)
    return next(c.result for c in result.checks if c.name == "exclude_build_condition")


@pytest.mark.parametrize(
    ("build_condition", "expected"),
    [(True, FAIL), (False, PASS), (None, UNKNOWN)],
)
def test_建築条件付きを除くは3値になる(build_condition: bool | None, expected: str) -> None:
    """⚠ 不明（詳細未取得）は落とさず ``unknown_policy`` に委ねる。"""
    must = TochiBuyMust(exclude_build_condition=True)
    assert _check(must, ListingView(build_condition=build_condition)) == expected


def test_一覧だけの1段目ではunknownになる() -> None:
    """建築条件は詳細ページの仕様表にしか出ないので ``available_on_list=False``。

    ⚠ 一覧で fail にすると詳細を取りに行かず、fail した掲載は DB にも残らない（→ ADR 0013）。
    """
    must = TochiBuyMust(exclude_build_condition=True)
    assert _check(must, ListingView(build_condition=True), list_stage_only=True) == UNKNOWN


@pytest.mark.parametrize("value", [None, False])
def test_未設定やfalseなら判定に現れない(value: bool | None) -> None:
    """⚠ ``false`` を「建築条件付きだけを残す」と読まない。書いていないのと同じにする。"""
    must = TochiBuyMust(exclude_build_condition=value)
    result = evaluate_must(ListingView(build_condition=True), must)
    assert all(c.name != "exclude_build_condition" for c in result.checks)


@pytest.mark.parametrize("cls", [ChintaiMust, MansionBuyMust, KodateBuyMust])
def test_土地以外のMUSTには書けない(cls: type) -> None:
    """建物を伴う売買には建築条件の欄が無い。書けても判定されない種別はスキーマの段で弾く。"""
    with pytest.raises(ValidationError):
        cls(exclude_build_condition=True)


def test_実運用の土地は建築条件付きを除く() -> None:
    pattern = load_pattern_file(REPO_ROOT / "configs" / "tochi.yaml")
    assert pattern.must.exclude_build_condition is True  # type: ignore[attr-defined]
    # ⚠ 不明は残す（詳細未取得の掲載が約1,500件ある）。drop に倒すと未取得が全件消える
    assert pattern.must.unknown_policy == "keep"  # type: ignore[attr-defined]


# --- 採点ビューへの配線（DB統合） ----------------------------------------------


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _insert(conn: Connection, *, external_id: str, detail_attrs: dict | None = None) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'TOCHI'")
    ).scalar_one()
    batch = upsert_listings(
        conn,
        [
            ScrapedListing(
                site_code="SUUMO",
                external_id=external_id,
                url=f"https://suumo.jp/tochi/tokyo/sc_hachioji/{external_id}/",
                price=30_000_000,
                address="東京都八王子市元本郷町１",
            )
        ],
        site_id=site_id,
        property_type_id=type_id,
        city_index=load_city_index(conn),
    )
    listing_id = batch.outcomes[0].listing_id
    if detail_attrs is not None:
        save_detail(conn, listing_id, ScrapedDetail(type_specific_attrs=detail_attrs))
    return listing_id


def _build_condition(conn: Connection, listing_id: int) -> bool | None:
    return load_listing_views(conn, listing_ids=[listing_id])[listing_id].build_condition


def test_採点ビューが建築条件を導く(conn: Connection) -> None:
    """⚠⚠ ``load_listing_views`` が導かなければ MUST は常に unknown になる
    （例外にならない「実装済みだが未配線」の形 → 要件定義書 §17）。
    アダプタが書く形（``build_condition`` の真偽値＋「付」系だけ ``建築条件`` の原文）で入れる。"""
    tsuki = _insert(
        conn,
        external_id="nc_99999931",
        detail_attrs={"建築条件": "付", "build_condition": True},
    )
    nashi = _insert(conn, external_id="nc_99999932", detail_attrs={"build_condition": False})
    ichibu = _insert(
        conn,
        external_id="nc_99999933",
        detail_attrs={"建築条件": "一部建築条件付", "build_condition": None},
    )
    undetailed = _insert(conn, external_id="nc_99999934")

    assert _build_condition(conn, tsuki) is True
    assert _build_condition(conn, nashi) is False
    assert _build_condition(conn, ichibu) is None
    assert _build_condition(conn, undetailed) is None


def test_グループ内に建築条件付きが1件でもあれば付(conn: Connection) -> None:
    """同じ区画を複数の会社が載せている。代表は最安の掲載なので、条件付き（安い方）が
    代表でも条件なしの掲載が代表でも、グループとして同じ判定になるようにする。"""
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'TOCHI'")
    ).scalar_one()
    group_id = conn.execute(
        text(
            """
            INSERT INTO t_listing_groups (
                dedup_key, property_type_id, member_count, created_at, updated_at
            ) VALUES ('test-build-condition-group', :type_id, 3, now(), now())
            RETURNING id
            """
        ),
        {"type_id": type_id},
    ).scalar_one()
    undetailed = _insert(conn, external_id="nc_99999935")
    nashi = _insert(conn, external_id="nc_99999936", detail_attrs={"build_condition": False})
    tsuki = _insert(
        conn,
        external_id="nc_99999937",
        detail_attrs={"建築条件": "付", "build_condition": True},
    )
    conn.execute(
        text("UPDATE t_listings SET group_id = :g WHERE id = ANY(:ids)"),
        {"g": group_id, "ids": [undetailed, nashi, tsuki]},
    )

    assert _build_condition(conn, undetailed) is True
    assert _build_condition(conn, nashi) is True


FIXTURES = Path(__file__).parent / "fixtures" / "suumo_tochi"


def test_原文を読む欄はアダプタが実際に書く欄() -> None:
    """⚠ 欄の名前を1文字でも違えると、読めずに全件 unknown になる（例外にならない）。
    定数どうしを比べるのではなく、実HTMLをアダプタに通して書かれる欄を確かめる。"""
    from house_search.scrape.suumo_tochi import SuumoTochiScraper

    scraper = SuumoTochiScraper()
    tsuki = scraper.parse_detail(
        (FIXTURES / "detail_jouken_tsuki.html").read_text(encoding="utf-8")
    ).type_specific_attrs
    plain = scraper.parse_detail(
        (FIXTURES / "detail_plain.html").read_text(encoding="utf-8")
    ).type_specific_attrs

    assert build_condition_of([(tsuki.get("建築条件"), tsuki.get("build_condition"))]) is True
    assert build_condition_of([(plain.get("建築条件"), plain.get("build_condition"))]) is False
