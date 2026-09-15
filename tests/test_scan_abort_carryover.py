"""同じ実行の中で打ち切ったサイトを、後続のパターンで叩き直さないことのテスト（→ 課題#67）。

2026-09-15 09:15 の定期スキャンで HOMEMATE が接続タイムアウトを返し続け、
23区帯で「5回連続で失敗」の打ち切りまで約31分、**近郊帯でも同じサイトを叩き直して
さらに31分**を空費した。スキャンは上限 PT1H50M を超え、10:25 の ScanBuy は
ロック競合でスキップされた。

⚠ **打ち切りは ``SiteFetcher`` の中の連続失敗の数で決まり、fetcher はパターンごとに
作り直される**ので、持ち越さないと2パターン目は0から数え直す。

⚠ ``RobotsDisallowed`` は持ち越さない。robots の判定はURLごとで、パターンが違えば
URL も違う（SUUMO の賃貸 ``sort=`` は禁止だが売買の ``po``/``pj`` は許可 → 課題#52）。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from house_search.pipeline import scan as scan_module
from house_search.pipeline.runtime import Runtime
from house_search.scrape.fetch import RateLimit, RobotsDisallowed, SiteAborted


class _FakeEngine:
    @contextmanager
    def begin(self):
        yield SimpleNamespace()

    @contextmanager
    def connect(self):
        yield SimpleNamespace()


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run",
        engine=_FakeEngine(),
        site_ids={"HOMEMATE": 1, "SUUMO": 2, "GOO": 3},
        property_type_ids={"CHINTAI": 1},
        city_index=SimpleNamespace(scoped_to=lambda prefectures: None),
        site_params=None,
        http_client=lambda user_agent=None: SimpleNamespace(close=lambda: None),
        aborted_sites={},
    )


def _pattern(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        family=SimpleNamespace(value="CHINTAI"),
        property_type="CHINTAI",
        sites=["HOMEMATE", "SUUMO", "GOO"],
        search=SimpleNamespace(prefectures=["東京都"]),
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """取得と run の記録を差し替え、どのサイトを何回取りに行ったかを数える。"""
    record: dict[str, list[str]] = {"collect": [], "start_run": [], "finish": []}

    def fake_collect(scraper, fetcher, pattern, **kwargs):
        record["collect"].append(f"{pattern.name}:{scraper.site_code}")
        if scraper.site_code == "HOMEMATE":
            raise SiteAborted("HOMEMATE: 5 回連続で失敗したため打ち切ります（WinError 10060）")
        raise RobotsDisallowed(f"{scraper.site_code}: robots.txt により取得が許可されていません")

    def fake_start_run(conn, *, pattern_name, site_id, **kwargs):
        record["start_run"].append(f"{pattern_name}:{site_id}")
        return SimpleNamespace()

    def fake_finish_run(conn, run_row, *, status, **kwargs):
        record["finish"].append(status)

    monkeypatch.setattr(scan_module, "_inactive_sites", lambda runtime: set())
    monkeypatch.setattr(
        scan_module,
        "get_scraper",
        lambda code, property_type: SimpleNamespace(site_code=code, user_agent=None),
    )
    monkeypatch.setattr(scan_module, "_site_areas", lambda runtime, scraper, pattern: ["area"])
    monkeypatch.setattr(
        scan_module, "_site_rate_limit", lambda runtime, code: RateLimit(min_interval_sec=0)
    )
    monkeypatch.setattr(scan_module, "_collect_listings", fake_collect)
    monkeypatch.setattr(scan_module.persist, "start_run", fake_start_run)
    monkeypatch.setattr(scan_module.persist, "finish_run", fake_finish_run)
    monkeypatch.setattr(scan_module.persist, "log", lambda conn, **kwargs: None)
    monkeypatch.setattr(scan_module.dedup, "sync_groups", lambda conn: [])
    monkeypatch.setattr(scan_module, "_refresh_commute", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_module, "_score_pattern", lambda *args, **kwargs: {})
    return record


def test_打ち切ったサイトは同じ実行の後続パターンで取りに行かない(calls) -> None:
    runtime = _runtime()

    first = scan_module.scan_pattern(runtime, _pattern("東京23区賃貸"), seed_mode=True)
    second = scan_module.scan_pattern(runtime, _pattern("近郊60分圏賃貸"), seed_mode=True)

    # HOMEMATE は1パターン目の1回だけ。2パターン目では run すら作らない
    assert calls["collect"].count("東京23区賃貸:HOMEMATE") == 1
    assert "近郊60分圏賃貸:HOMEMATE" not in calls["collect"]
    assert "近郊60分圏賃貸:1" not in calls["start_run"]
    assert "HOMEMATE（この実行で打ち切り済み）" in second.skipped_sites
    # 1パターン目の打ち切りはエラーとして残る（持ち越しで申告が消えない）
    homemate = next(site for site in first.sites if site.site_code == "HOMEMATE")
    assert any("連続で失敗" in error for error in homemate.errors)
    assert "HOMEMATE" in runtime.aborted_sites


def test_robotsの禁止は持ち越さない(calls) -> None:
    """robots の判定は URL ごとなので、パターンが変われば結果も変わりうる。"""
    runtime = _runtime()

    scan_module.scan_pattern(runtime, _pattern("東京23区賃貸"), seed_mode=True)
    scan_module.scan_pattern(runtime, _pattern("近郊60分圏賃貸"), seed_mode=True)

    assert calls["collect"].count("近郊60分圏賃貸:SUUMO") == 1
    assert calls["collect"].count("近郊60分圏賃貸:GOO") == 1
    assert set(runtime.aborted_sites) == {"HOMEMATE"}


def test_Runtimeは実行ごとに打ち切り済みの集合を持つ() -> None:
    """``default_factory`` で持つ（クラス変数の共有 dict にすると別の実行へ漏れる）。"""
    factory = Runtime.__dataclass_fields__["aborted_sites"].default_factory
    first, second = factory(), factory()
    assert first == {}
    assert first is not second
