"""所有権のみ（借地権を除く）の MUST（``freehold_only``）のテスト（→ 課題#65）。

⚠⚠ **権利形態の表記は「借地」の語を含むとは限らない。** 本番DB（2026-09-12）に
``賃借権（旧）、2025年9月29日～2045年9月28日`` や ``地上権（旧）、新規20年`` がある。
「借地」だけを見ると借地を所有権として通し、**例外にならず順位にそのまま残る**
（実測で中古一戸建ての6位が借地だった）。

判定は保存済みの原文（``type_specific_attrs``）から**採点のたびに導く**。アダプタで
真偽値を立てて保存する方式だと、既存の約31,000件の詳細を取り直さないと反映されない。
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
    LAND_RIGHTS_ATTR_KEYS,
    ScrapedDetail,
    ScrapedListing,
    leasehold_flag,
    leasehold_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

BUY_PATTERN_FILES = (
    "chuko_mansion.yaml",
    "shinchiku_mansion.yaml",
    "chuko_kodate.yaml",
    "shinchiku_kodate.yaml",
)


# --- 権利形態の読み方（純関数） -------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # 所有権（本番DBの表記そのまま）
        ("所有権", False),
        ("所有権の共有", False),
        # 借地（本番DBの表記そのまま）
        ("賃借権（旧）、借地期間新規20年", True),
        # ⚠⚠ 「借地」の語を含まない。「借地」だけを見ると所有権でも借地でもない＝通してしまう
        ("賃借権（旧）、2025年9月29日～2045年9月28日", True),
        ("地上権（旧）、新規20年", True),
        ("一般定期借地権（賃借権）、借地期間新規73年7ヶ月、借地権設定登記不可", True),
        # ⚠ 敷地の一部だけが借地でも「所有権のみ」ではない
        ("一部賃借権（旧）、借地期間残存16年6ヶ月、借地権割合1％", True),
        ("一部地上権（旧）、借地期間残存14年1ヶ月、借地権割合8％", True),
        # ⚠ 所有権の語が一緒に書かれていても借地（並記・注記）
        ("地上権（普）、借地期間残存65年11ヶ月、所有権・地上権両方あり", True),
        ("所有権・借地権", True),
        # 新築マンションの一覧の販売期の注記
        ("（一般定期借地権）", True),
        ("（先着順(定期転借地権)）", True),
        ("（先着順販売（普通借地権））", True),
        # 読めないもの。⚠ 本番DBの「‐」は U+2010（半角の「-」ではない）
        ("‐", None),
        ("-", None),
        ("", None),
        (None, None),
        ("相談", None),
    ],
)
def test_権利形態の読み方(value: str | None, expected: bool | None) -> None:
    assert leasehold_flag(value) is expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["所有権", "賃借権（旧）、借地期間残存7年"], True),  # 1件でも借地なら借地
        ([None, "所有権"], False),
        (["‐", "所有権の共有"], False),
        ([None, "‐"], None),
        ([], None),
    ],
)
def test_複数の原文をまとめる(values: list[str | None], expected: bool | None) -> None:
    """名寄せグループの掲載・複数の欄をまとめる。⚠ 借地が1つでも混ざれば借地。"""
    assert leasehold_of(values) is expected


# --- MUST の判定 -----------------------------------------------------------


def _check(must: object, view: ListingView, **kwargs) -> str:
    result = evaluate_must(view, must, **kwargs)
    return next(c.result for c in result.checks if c.name == "freehold_only")


@pytest.mark.parametrize("cls", [MansionBuyMust, KodateBuyMust])
@pytest.mark.parametrize(
    ("leasehold", "expected"),
    [(True, FAIL), (False, PASS), (None, UNKNOWN)],
)
def test_所有権のみは3値になる(cls: type, leasehold: bool | None, expected: str) -> None:
    """⚠ 不明（None）は落とさず ``unknown_policy`` に委ねる（ユーザー判断 2026-09-12）。"""
    must = cls(freehold_only=True)
    assert _check(must, ListingView(leasehold=leasehold)) == expected


def test_一覧だけの1段目ではunknownになる() -> None:
    """権利形態は詳細ページにしか出ないので ``available_on_list=False``。"""
    must = MansionBuyMust(freehold_only=True)
    assert _check(must, ListingView(leasehold=True), list_stage_only=True) == UNKNOWN


@pytest.mark.parametrize("value", [None, False])
def test_未設定やfalseなら判定に現れない(value: bool | None) -> None:
    """⚠ ``false`` を「借地だけを残す」と読まない。書いていないのと同じにする。"""
    must = KodateBuyMust(freehold_only=value)
    result = evaluate_must(ListingView(leasehold=True), must)
    assert all(c.name != "freehold_only" for c in result.checks)


@pytest.mark.parametrize("cls", [ChintaiMust, TochiBuyMust])
def test_賃貸と土地のMUSTには書けない(cls: type) -> None:
    """ご指定は「マンションと戸建て」。書けても判定されない種別はスキーマの段で弾く。"""
    with pytest.raises(ValidationError):
        cls(freehold_only=True)


@pytest.mark.parametrize("filename", BUY_PATTERN_FILES)
def test_実運用の売買4本は所有権のみ(filename: str) -> None:
    pattern = load_pattern_file(REPO_ROOT / "configs" / filename)
    assert pattern.must.freehold_only is True  # type: ignore[attr-defined]
    # ⚠ 不明は残す（棟の詳細には権利形態の欄が無い）。drop に倒すと新築マンションの
    #    上位15件のうち12件（棟）が消える（→ 課題#65）
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


def _insert(
    conn: Connection,
    *,
    external_id: str,
    property_type: str,
    list_attrs: dict | None = None,
    detail_attrs: dict | None = None,
) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = :code"), {"code": property_type}
    ).scalar_one()
    batch = upsert_listings(
        conn,
        [
            ScrapedListing(
                site_code="SUUMO",
                external_id=external_id,
                url=f"https://suumo.jp/ms/chuko/tokyo/sc_minato/{external_id}/",
                price=50_000_000,
                address="東京都港区芝公園１",
                type_specific_attrs={"price_undecided": False, **(list_attrs or {})},
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


def _leasehold(conn: Connection, listing_id: int) -> bool | None:
    return load_listing_views(conn, listing_ids=[listing_id])[listing_id].leasehold


def test_採点ビューが権利形態の原文から借地を導く(conn: Connection) -> None:
    """⚠⚠ ``load_listing_views`` が導かなければ MUST は常に unknown になる
    （例外にならない「実装済みだが未配線」の形 → 要件定義書 §17）。"""
    mansion = _insert(
        conn,
        external_id="nc_99999921",
        property_type="CHUKO_MANSION",
        detail_attrs={"敷地の権利形態": "地上権（旧）、借地期間残存2年8ヶ月"},
    )
    kodate = _insert(
        conn,
        external_id="nc_99999922",
        property_type="CHUKO_KODATE",
        detail_attrs={"土地の権利形態": "所有権"},
    )
    unknown = _insert(conn, external_id="nc_99999923", property_type="CHUKO_MANSION")

    assert _leasehold(conn, mansion) is True
    assert _leasehold(conn, kodate) is False
    assert _leasehold(conn, unknown) is None


def test_新築の棟は一覧の販売期の注記から借地を導く(conn: Connection) -> None:
    """棟の詳細には権利形態の欄が無い。借地なら一覧の販売期に注記が付く。"""
    tou = _insert(
        conn,
        external_id="nc_99999924",
        property_type="SHINCHIKU_MANSION",
        list_attrs={"販売期の権利形態": "（（一般定期借地権））"},
    )
    assert _leasehold(conn, tou) is True


def test_グループ内に借地の掲載が1件でもあれば借地(conn: Connection) -> None:
    """同じ住戸を複数の会社が載せている。詳細を取っていない掲載が代表になっても、
    別の掲載の権利形態から判定する（設備の和集合・通勤の最短と同じ考え方）。"""
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHUKO_MANSION'")
    ).scalar_one()
    group_id = conn.execute(
        text(
            """
            INSERT INTO t_listing_groups (
                dedup_key, property_type_id, member_count, created_at, updated_at
            ) VALUES ('test-freehold-group', :type_id, 3, now(), now())
            RETURNING id
            """
        ),
        {"type_id": type_id},
    ).scalar_one()
    undetailed = _insert(conn, external_id="nc_99999925", property_type="CHUKO_MANSION")
    freehold = _insert(
        conn,
        external_id="nc_99999926",
        property_type="CHUKO_MANSION",
        detail_attrs={"敷地の権利形態": "所有権"},
    )
    leasehold = _insert(
        conn,
        external_id="nc_99999927",
        property_type="CHUKO_MANSION",
        detail_attrs={"敷地の権利形態": "賃借権（旧）、借地期間残存7年7ヶ月"},
    )
    conn.execute(
        text("UPDATE t_listings SET group_id = :g WHERE id = ANY(:ids)"),
        {"g": group_id, "ids": [undetailed, freehold, leasehold]},
    )

    assert _leasehold(conn, undetailed) is True
    assert _leasehold(conn, freehold) is True


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(*parts: str) -> str:
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


def test_原文を読む欄はアダプタが実際に書く欄() -> None:
    """⚠ 欄の名前を1文字でも違えると、読めずに全件 unknown になる（例外にならない）。
    定数どうしを比べるのではなく、実HTMLを各アダプタに通して書かれる欄を確かめる。"""
    from house_search.scrape.suumo_buy import SuumoBuyMansionScraper
    from house_search.scrape.suumo_kodate import SuumoChukoKodateScraper
    from house_search.scrape.suumo_shinchiku import SuumoNewMansionScraper

    mansion = SuumoBuyMansionScraper().parse_detail(_fixture("suumo_buy", "detail_chuko_m.html"))
    kodate = SuumoChukoKodateScraper().parse_detail(
        _fixture("suumo_kodate", "detail_chuko_k.html")
    )
    tou = SuumoNewMansionScraper().parse_list(
        _fixture("suumo_buy", "list_shinchiku_m_itabashi.html")
    )[0]

    outputs = (mansion.type_specific_attrs, kodate.type_specific_attrs, tou.type_specific_attrs)
    written = {key for attrs in outputs for key in attrs if key in LAND_RIGHTS_ATTR_KEYS}
    assert written == set(LAND_RIGHTS_ATTR_KEYS)
    assert leasehold_flag(mansion.type_specific_attrs["敷地の権利形態"]) is False
    assert leasehold_flag(kodate.type_specific_attrs["土地の権利形態"]) is False
