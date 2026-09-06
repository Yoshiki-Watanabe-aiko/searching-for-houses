"""ハザード評価を採点へ配線した部分のテスト（→ 課題#46・Phase 5I 後半 Step 7）。

⚠⚠ **このファイルが本当に守っているのは「区域外」と「未解決」の区別**である。

  - ``0.0`` = 丁目を照合できたうえで区域に掛からないと確認した（安全の証拠）
  - ``None`` = そもそも住所を照合できなかった（情報が無い）

混ぜると「危険なのに情報が無いから減点されない」掲載が「安全」と同じ扱いになり、
**例外にならないまま順位だけが狂う**。生成側（``build_hazard_levels.py`` の恒等式
assert）と読み込み側（``load_hazard_rows`` の検証）は既に固定してあるので、
ここで固定するのは残る一層＝スコア側の扱いになる。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, text

from house_search.config.pattern import ChintaiMust, NumericWant, WantSpec
from house_search.scoring.listing_view import ListingView
from house_search.scoring.must import FAIL, PASS, UNKNOWN, evaluate_must
from house_search.scoring.score import STATUS_HIT, STATUS_UNKNOWN, calculate_score

# --- 純関数（DB不要） -----------------------------------------------------


def _check(must: ChintaiMust, view: ListingView, name: str) -> str:
    result = evaluate_must(view, must)
    return next(c.result for c in result.checks if c.name == name)


def test_区域外の0はNoneに潰れない() -> None:
    """``metric_value`` が 0.0 を返すこと。

    ⚠ ``value or None`` のような書き方をすると 0.0 が falsy なので未解決に化ける。
    """
    view = ListingView(flood_rank_avg=0.0, flood_area_ratio=0.0, landslide_area_ratio=0.0)
    assert view.metric_value("flood_rank_avg") == 0.0
    assert view.metric_value("flood_area_ratio") == 0.0
    assert view.metric_value("landslide_area_ratio") == 0.0


def test_未解決はNoneのまま() -> None:
    assert ListingView().metric_value("flood_rank_avg") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, PASS),  # 区域外だと確認した → 通す
        (3.0, PASS),  # 上限ちょうど
        (5.0, FAIL),
        (None, UNKNOWN),  # 情報が無い → 落とさず unknown_policy に委ねる
    ],
)
def test_洪水ランクのMUSTは3値になる(value: float | None, expected: str) -> None:
    must = ChintaiMust(flood_rank_max=3)
    assert _check(must, ListingView(flood_rank_max=value), "flood_rank_max") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, PASS), (0.1, PASS), (0.5, FAIL), (None, UNKNOWN)],
)
def test_土砂特別警戒のMUSTは3値になる(value: float | None, expected: str) -> None:
    must = ChintaiMust(landslide_special_ratio_max=0.1)
    field = "landslide_special_ratio_max"
    assert _check(must, ListingView(landslide_special_ratio=value), field) == expected


def test_ハザードのMUSTは一覧段では判定しない() -> None:
    """住所は詳細ページで初めて埋まるサイトがある（→ 課題#44）。

    1段目で fail にすると、そのサイトの掲載を詳細取得の前に捨ててしまう。
    """
    must = ChintaiMust(flood_rank_max=1, landslide_special_ratio_max=0.0)
    view = ListingView(flood_rank_max=6.0, landslide_special_ratio=1.0)
    result = evaluate_must(view, must, list_stage_only=True)
    assert {c.name: c.result for c in result.checks} == {
        "flood_rank_max": UNKNOWN,
        "landslide_special_ratio_max": UNKNOWN,
    }
    # 2段目（詳細取得後）では同じ値がきちんと fail になる。
    assert evaluate_must(view, must).result == FAIL


def test_未解決の掲載はunknown_policyで通る() -> None:
    """既定 ``keep`` を ``drop`` に倒すと、町名までしか出さないサイトが全滅する。"""
    must = ChintaiMust(flood_rank_max=3)
    result = evaluate_must(ListingView(), must)
    assert result.result == UNKNOWN
    assert result.passes("keep") is True
    assert result.passes("drop") is False


def test_区域外は満点で加点され未解決は分母から外れる() -> None:
    """WANT 側での 0.0 と None の違い。

    ⚠ 未解決を0点として分母に残すと、住所が町名までしか無いサイトの掲載が
    「危険」と同じ点数まで沈む。欠損は再正規化で外すのが既定の扱い（→ ADR 0004 相当）。
    """
    want = WantSpec(
        numeric=[
            NumericWant(metric="flood_rank_avg", weight=10, best=0, worst=3),
            NumericWant(metric="rent_total", weight=10, best=50000, worst=70000),
        ]
    )

    safe = calculate_score(
        ListingView(rent_total=50000, flood_rank_avg=0.0), want, condition_names={}
    )
    flood_item = next(i for i in safe.items if i.code == "flood_rank_avg")
    assert flood_item.status == STATUS_HIT
    assert flood_item.missing is False
    assert safe.score == pytest.approx(100.0)

    unknown = calculate_score(ListingView(rent_total=50000), want, condition_names={})
    flood_item = next(i for i in unknown.items if i.code == "flood_rank_avg")
    assert flood_item.status == STATUS_UNKNOWN
    assert flood_item.missing is True
    # 分母から外れるので、賃料だけで満点になる（0点扱いなら50点に沈む）。
    assert unknown.score == pytest.approx(100.0)

    risky = calculate_score(
        ListingView(rent_total=50000, flood_rank_avg=3.0), want, condition_names={}
    )
    assert risky.score == pytest.approx(50.0)


# --- DB統合（``DATABASE_TEST_URL`` 未設定ならスキップ） --------------------

_CHOME = "東京都足立区東和5丁目"
_TOWN = "東京都足立区東和"
# 住所マスタには載っているが、丁目境界が無くハザード評価を作れなかった丁目。
# この掲載は住所マスタの town_key 経由で町の評価へ落ちる（実測で1.8%がこの形）。
_CHOME_WITHOUT_HAZARD = "東京都足立区東和6丁目"
_UNMAPPED = "群馬県神流町万場"


@pytest.fixture
def conn(test_engine: Engine) -> Iterator[Connection]:
    """ロールバックされるトランザクション。テストDBを汚さない。"""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _seed_hazards(conn: Connection) -> None:
    """丁目と町の両方に評価を入れる。⚠ 値をわざと変えて、どちらを引いたか判る形にする。"""
    conn.execute(text("DELETE FROM m_hazard_levels"))
    conn.execute(
        text("DELETE FROM m_address_points WHERE normalized_key = ANY(:keys)"),
        {"keys": [_CHOME, _CHOME_WITHOUT_HAZARD]},
    )
    conn.execute(
        text(
            """
            INSERT INTO m_address_points (
                city_jis_code, town_key, chome_number, normalized_key, level,
                lat, lon, source, created_at, updated_at
            ) VALUES (
                '13121', :town, :chome_number, :chome, 'chome', 35.0, 139.0, 'test', now(), now()
            )
            """
        ),
        [
            {"town": _TOWN, "chome_number": 5, "chome": _CHOME},
            {"town": _TOWN, "chome_number": 6, "chome": _CHOME_WITHOUT_HAZARD},
        ],
    )
    rows = [
        # (キー, 粒度, 種別, 方式, 値)
        (_CHOME, "chome", "flood", "rank_avg", 2.5),
        (_CHOME, "chome", "flood", "rank_max", 4.0),
        (_CHOME, "chome", "flood", "area_ratio", 0.8),
        (_CHOME, "chome", "landslide", "area_ratio", 0.0),
        (_CHOME, "chome", "landslide_special", "area_ratio", 0.0),
        (_TOWN, "town", "flood", "rank_avg", 1.25),
        (_TOWN, "town", "flood", "rank_max", 4.0),
        (_TOWN, "town", "flood", "area_ratio", 0.4),
        (_TOWN, "town", "landslide", "area_ratio", 0.0),
        (_TOWN, "town", "landslide_special", "area_ratio", 0.0),
    ]
    conn.execute(
        text(
            """
            INSERT INTO m_hazard_levels (
                normalized_key, level, hazard_type, aggregation, value,
                source, acquired_on, created_at, updated_at
            ) VALUES (
                :key, :level, :hazard_type, :aggregation, :value,
                'test', DATE '2026-09-05', now(), now()
            )
            """
        ),
        [
            {
                "key": key,
                "level": level,
                "hazard_type": hazard_type,
                "aggregation": aggregation,
                "value": value,
            }
            for key, level, hazard_type, aggregation, value in rows
        ],
    )


def _insert_listing(
    conn: Connection,
    *,
    external_id: str,
    address_normalized: str | None,
    group_id: int | None = None,
) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    return conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title,
                price, area_sqm, layout, address, address_normalized, prefecture,
                group_id, status, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :property_type_id, :external_id, :url, 'ハザードテスト',
                90000, 30.0, '1LDK', :address, :address_normalized, '東京都',
                :group_id, 'active', now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "property_type_id": property_type_id,
            "external_id": external_id,
            "url": f"https://example.test/hazard/{external_id}",
            "address": address_normalized or "住所不明",
            "address_normalized": address_normalized,
            "group_id": group_id,
        },
    ).scalar_one()


def _load(conn: Connection, listing_id: int) -> ListingView:
    from house_search.pipeline import persist

    return persist.load_listing_views(conn, listing_ids=[listing_id])[listing_id]


def test_丁目の住所は丁目の評価を引く(conn: Connection) -> None:
    _seed_hazards(conn)
    listing_id = _insert_listing(conn, external_id="hz-chome", address_normalized=_CHOME)

    view = _load(conn, listing_id)
    assert view.flood_rank_avg == pytest.approx(2.5)
    assert view.flood_rank_max == pytest.approx(4.0)
    assert view.flood_area_ratio == pytest.approx(0.8)


def test_町名までの住所は町の評価を引く(conn: Connection) -> None:
    """SUUMO・ハウスコムは住所が町名までしか無い（→ 課題#46 の着手前実測）。

    ここが効いていないと、そのサイトの掲載が丸ごと未解決になる。
    """
    _seed_hazards(conn)
    listing_id = _insert_listing(conn, external_id="hz-town", address_normalized=_TOWN)

    view = _load(conn, listing_id)
    assert view.flood_rank_avg == pytest.approx(1.25)
    assert view.flood_area_ratio == pytest.approx(0.4)


def test_丁目の評価が無ければ住所マスタ経由で町へ落ちる(conn: Connection) -> None:
    """⚠ 町へ落とすのに SQL の文字列操作で「N丁目」を剥がさない。

    住所マスタの物理列 ``town_key`` を引く。正規表現で削る実装は、丁目の無い町で
    番地を削るなどして黙って別の町を指す（→ ADR 0020 と同じ失敗の形）。
    """
    _seed_hazards(conn)
    listing_id = _insert_listing(
        conn, external_id="hz-fallback", address_normalized=_CHOME_WITHOUT_HAZARD
    )

    view = _load(conn, listing_id)
    assert view.flood_rank_avg == pytest.approx(1.25)
    assert view.flood_area_ratio == pytest.approx(0.4)


def test_区域外は0として引かれ未解決と区別される(conn: Connection) -> None:
    """⚠⚠ 本ファイルの主題。0.0 と None は別物として届かなければならない。"""
    _seed_hazards(conn)
    resolved = _insert_listing(conn, external_id="hz-zero", address_normalized=_CHOME)
    unresolved = _insert_listing(conn, external_id="hz-none", address_normalized=_UNMAPPED)

    safe = _load(conn, resolved)
    assert safe.landslide_area_ratio == 0.0
    assert safe.landslide_special_ratio == 0.0

    missing = _load(conn, unresolved)
    assert missing.landslide_area_ratio is None
    assert missing.landslide_special_ratio is None
    assert missing.flood_rank_avg is None


def test_住所が無い掲載は未解決になる(conn: Connection) -> None:
    _seed_hazards(conn)
    listing_id = _insert_listing(conn, external_id="hz-null", address_normalized=None)
    assert _load(conn, listing_id).flood_rank_avg is None


def test_グループ内で最も細かい住所の評価を採る(conn: Connection) -> None:
    """設備の和集合・通勤時間の最短と同じ考え方（グループ全体で情報を最大化する）。

    町名までしか出さないサイトの掲載が代表になっても、同じ住戸を丁目まで載せている
    掲載があるならそちらの評価を使う。
    """
    _seed_hazards(conn)
    property_type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    group_id = conn.execute(
        text(
            """
            INSERT INTO t_listing_groups (
                dedup_key, property_type_id, member_count, created_at, updated_at
            ) VALUES ('test-hazard-group', :property_type_id, 2, now(), now())
            RETURNING id
            """
        ),
        {"property_type_id": property_type_id},
    ).scalar_one()

    coarse = _insert_listing(
        conn, external_id="hz-g-town", address_normalized=_TOWN, group_id=group_id
    )
    _insert_listing(conn, external_id="hz-g-chome", address_normalized=_CHOME, group_id=group_id)

    # 町名までの掲載を引いても、グループ内の丁目の値（2.5）が返る。
    assert _load(conn, coarse).flood_rank_avg == pytest.approx(2.5)
