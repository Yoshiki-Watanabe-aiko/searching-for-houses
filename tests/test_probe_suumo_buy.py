"""調査ツール ``probe_suumo_buy.py`` の安全装置（→ 計画書 Phase9 §6.6・課題#52）。

⚠ 調査ツールは本体の ``SiteFetcher`` を通らないので、取得ロックを自前で取らないと
定期スキャン・掃き出しと並走し、**SUUMO への実効間隔が半分になる**（レート制御は
プロセス内にしかない → ADR 0013 決定8）。人がプロセスを確かめる運用だけに頼らない。

⚠ robots の判定は本体と同じ ``RobotsRules`` で行う。標準の ``RobotFileParser`` は
``*`` を展開せず、``/*?*sort=`` のような規則を**黙って「許可」と判定する**（→ 課題#52）。
調査ツールが「OK」と言ったパスを本体が禁止と判定すると、実測が担保にならない。
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tools"))

import probe_suumo_buy as probe  # noqa: E402

ROBOTS_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "robots" / "suumo.txt"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """.env に依存しないよう Settings を差し替える。"""
    monkeypatch.setattr(
        probe,
        "Settings",
        lambda: SimpleNamespace(request_timeout_sec=5, user_agent="house-search/2.0"),
    )


def _fake_lock(acquired: bool, calls: list[str]):
    @contextmanager
    def lock():
        calls.append("lock")
        yield acquired

    return lock


class _ExplodingClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("ロックが取れていないのに HTTP クライアントを作った")


class _FakeResponse:
    status_code = 200
    text = "<html></html>"
    content = b"<html></html>"
    headers = {"content-type": "text/html"}


class _FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.headers = kwargs.get("headers", {})
        self.urls: list[str] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def get(self, url: str) -> _FakeResponse:
        self.urls.append(url)
        return _FakeResponse()


def _fetch_argv(cache_dir: Path) -> list[str]:
    return [
        "probe_suumo_buy.py",
        "--stage",
        "fetch",
        "--url",
        "https://suumo.jp/tochi/tokyo/sc_setagaya/",
        "--label",
        "list_setagaya",
        "--cache-dir",
        str(cache_dir),
    ]


def test_ロックが取れなければ1本も取得せず非0で終わる(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(probe, "scraping_lock", _fake_lock(False, calls))
    monkeypatch.setattr(probe.httpx, "Client", _ExplodingClient)
    monkeypatch.setattr(sys, "argv", _fetch_argv(tmp_path))

    assert probe.main() == 1
    assert calls == ["lock"]
    # ⚠ 何も保存していない＝1本も取得していない
    assert list(tmp_path.iterdir()) == []
    assert "中止" in capsys.readouterr().err


def test_ロックが取れたときだけ取得へ進み応答を保存する(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(probe, "scraping_lock", _fake_lock(True, calls))
    monkeypatch.setattr(probe.httpx, "Client", _FakeClient)
    monkeypatch.setattr(sys, "argv", _fetch_argv(tmp_path))

    assert probe.main() == 0
    assert calls == ["lock"]
    assert (tmp_path / "list_setagaya.html").exists()
    assert (tmp_path / "list_setagaya.meta.json").exists()


def test_保存済みHTMLの解析はロックを取らない(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """解析だけの段は DB に繋がずに回せる（取得予算を使わずに解析をやり直すため）。"""

    def _must_not_lock():
        raise AssertionError("解析だけの段で取得ロックを取った")

    monkeypatch.setattr(probe, "scraping_lock", _must_not_lock)
    (tmp_path / "city.html").write_text(
        '<html><a href="/tochi/tokyo/sc_setagaya/">世田谷区</a></html>', encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe_suumo_buy.py",
            "--stage",
            "links",
            "--label",
            "city",
            "--pattern",
            "/tochi/",
            "--cache-dir",
            str(tmp_path),
        ],
    )

    assert probe.main() == 0
    assert "sc_setagaya" in capsys.readouterr().out


def test_robotsの判定はワイルドカードを展開する(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """実物の robots.txt（フィクスチャ）で、本体と同じ判定になることを固定する。"""
    (tmp_path / "robots.html").write_text(
        ROBOTS_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )

    probe.stage_robots(None, tmp_path, reuse=True)
    out = capsys.readouterr().out

    # ⚠ 標準の RobotFileParser だとここが OK になる（`/*?*sort=` が当たらない → 課題#52）
    assert "NG  /jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13&sort=2" in out
    # 土地（Phase 9b）で叩く形は許可されている
    assert "OK  /tochi/tokyo/city/" in out
    assert "OK  /tochi/tokyo/sc_setagaya/?po=1&pj=2&page=2" in out
