"""個別通知を順位の良い順に送る（ユーザー要望 2026-09-07）。

⚠ 取得順（サイト順・ページ順）のまま送っていたため、パターン内196位の掲載が
上位の掲載より先に届いていた。ダイジェストは順位順（``ORDER BY rank_in_pattern``）
なので、**個別通知だけが順不同**だった。

⚠ 並べ替えても**例外にならず件数も変わらない**ので、配線を外しても気づけない。
そのため純関数の検査に加えて、``_notify`` が実際に通していることも固定する
（``test_notify_rank.py`` が絞り込みについて取っているのと同じ備え）。
"""

from __future__ import annotations

import inspect

from house_search.dedup import NO_GROUP, GroupMembership
from house_search.pipeline import persist
from house_search.pipeline import scan as scan_module
from house_search.pipeline.scan import _sort_by_notify_rank


def _outcome(listing_id: int) -> persist.UpsertOutcome:
    return persist.UpsertOutcome(
        listing_id=listing_id,
        external_id=f"ext-{listing_id}",
        is_new=True,
        is_reinstated=False,
        price_event=None,
        price_prev=None,
    )


def _ids(outcomes: list[persist.UpsertOutcome]) -> list[int]:
    return [o.listing_id for o in outcomes]


def test_順位の良い順に並ぶ() -> None:
    outcomes = [_outcome(10), _outcome(20), _outcome(30)]
    ranks = {10: 196, 20: 3, 30: 47}

    assert _ids(_sort_by_notify_rank(outcomes, ranks, {})) == [20, 30, 10]


def test_順位が付いていない掲載は末尾に置く() -> None:
    """⚠ 先頭へ置いてはいけない。

    順位が引けない掲載は「順位未確定」として通知される仕様（→
    ``_within_notify_rank``）なので、順位付けが壊れたときに
    **未確定の掲載でチャンネルが埋まる**。
    """
    outcomes = [_outcome(10), _outcome(20), _outcome(30)]
    ranks = {20: 5}

    assert _ids(_sort_by_notify_rank(outcomes, ranks, {})) == [20, 10, 30]


def test_非代表メンバーは代表の順位で並ぶ() -> None:
    """順位はグループ代表にしか振られない（→ ``_group_rank``）。

    ⚠ 代表の順位を見ないと、非代表の掲載が全部「順位なし」として
    末尾へ落ちる（通知は届くが順番が壊れる）。
    """
    outcomes = [_outcome(10), _outcome(20)]
    ranks = {99: 2, 10: 150}
    memberships = {
        20: GroupMembership(
            group_id=7, member_count=2, representative_listing_id=99, other_site_codes=()
        ),
    }

    assert _ids(_sort_by_notify_rank(outcomes, ranks, memberships)) == [20, 10]


def test_同順位は掲載ID昇順で決定的に並ぶ() -> None:
    """同じグループ代表の順位を共有する掲載は同順位になりうる。

    ⚠ 並びが実行ごとに揺れると、通知の届く順番が再現しない。
    """
    outcomes = [_outcome(30), _outcome(10), _outcome(20)]
    ranks = {10: 5, 20: 5, 30: 5}

    assert _ids(_sort_by_notify_rank(outcomes, ranks, {})) == [10, 20, 30]


def test_順位なしどうしも掲載ID昇順で並ぶ() -> None:
    outcomes = [_outcome(30), _outcome(10), _outcome(20)]

    assert _ids(_sort_by_notify_rank(outcomes, ranks={}, memberships={})) == [10, 20, 30]


def test_件数と中身は変わらない() -> None:
    """⚠ 並べ替えるのは**届く順番だけ**。通知対象を減らしも増やしもしない。"""
    outcomes = [_outcome(i) for i in (5, 1, 9, 3)]
    ranks = {5: 10, 9: 1}

    sorted_outcomes = _sort_by_notify_rank(outcomes, ranks, {})

    assert sorted(_ids(sorted_outcomes)) == [1, 3, 5, 9]
    assert set(map(id, sorted_outcomes)) == set(map(id, outcomes))


def test_所属が無い掲載はNO_GROUPとして扱う() -> None:
    """``memberships`` に行が無くても KeyError にしない。"""
    outcomes = [_outcome(10)]

    assert _sort_by_notify_rank(outcomes, {10: 1}, {})[0].listing_id == 10
    assert _sort_by_notify_rank(outcomes, {10: 1}, {10: NO_GROUP})[0].listing_id == 10


def test_通知経路が並べ替えを通している() -> None:
    """⚠ 外しても例外にならず、取得順に戻るだけなので気づけない。"""
    source = inspect.getsource(scan_module._notify)

    assert "_sort_by_notify_rank" in source, "_notify が順位順の並べ替えを通していない"
    assert "for outcome in outcomes:" not in source, (
        "_notify が outcomes を取得順のまま回している"
    )
