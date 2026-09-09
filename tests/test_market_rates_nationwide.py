"""家賃相場の全国化と「毎月の取り直し」の回帰テスト（課題#49）。

⚠⚠ **旧実装は毎月の取り直しになっていなかった。** 保存先が月を含まない
`raw/{jis}.html` で、`fetch_cities` が `if path.exists(): continue` するため、
**毎月1日のタスクが走っても1本も取得しなかった**。それでいて `period` は生成時の
年月になるので、**9月に取った相場が「2026-10 の相場」としてDBに入る**。
⚠ 例外にもならず、CSV も `period`/`acquired_on` が変わるので「更新されました」と出る。

⚠ 対象を検索パターンの市区（帯82市区）に絞っていた点も同時に直した。絞ると
帯を広げたときにその市区の相場が無く、`market_rate_ratio` が未解決のまま
順位に効かない（→ ADR 0021 決定4 と同じ「無いことに気づけない」形）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tools"))

import build_market_rates as builder  # noqa: E402

ADACHI = {"13121": {"name": "足立区", "slug": "sc_adachi"}}
NATIONWIDE = {
    "13121": {"name": "足立区", "slug": "sc_adachi"},
    # ⚠ 帯（configs の cities）の外。全国化の前はここが取得対象から落ちていた
    "01101": {"name": "札幌市中央区", "slug": "sc_sapporoshichuo"},
}


def _index_html(pref: str, entries: list[tuple[str, str, str]]) -> str:
    """索引ページの JSON 断片を模す（実データと同じ形）。"""
    body = ",".join(
        f'"{code}":{{"name":"{name}","url":"/chintai/{pref}/{slug}/soba/"}}'
        for code, name, slug in entries
    )
    return f"<html><script>var areas={{{body}}};</script></html>"


class Test全国のスラグ:
    def test_47都道府県ぶんそろっている(self) -> None:
        assert len(builder.PREF_SLUG) == 47
        assert set(builder.PREF_SLUG) == {f"{i:02d}" for i in range(1, 48)}

    def test_大半は賃貸一覧のスラグと同じ(self) -> None:
        assert builder.PREF_SLUG["13"] == "tokyo"
        assert builder.PREF_SLUG["47"] == "okinawa"

    def test_相場ページ固有の綴りを上書きする(self) -> None:
        """⚠ 実測 2026-09-09。47都道府県を1本ずつ叩いて確かめた2件。

        ⚠⚠ `hokkaido` は **404 にならない**（HTTP 200 で市区リンク0件の
        案内ページが返る）。上書きを外すと**北海道85市区を落としたまま気づけない**。
        """
        assert builder.PREF_SLUG["01"] == "hokkaido_"
        assert builder.PREF_SLUG["10"] == "gumma"

    def test_スラグは英小文字とアンダースコアだけ(self) -> None:
        assert all(re.fullmatch(r"[a-z_]+", s) for s in builder.PREF_SLUG.values())


class Test索引の解析:
    def test_JISコードをキーにする(self) -> None:
        parsed = builder.parse_index(
            _index_html("tokyo", [("121", "足立区", "sc_adachi")]), "13", "tokyo"
        )

        assert parsed == {"13121": {"name": "足立区", "slug": "sc_adachi"}}

    def test_同名の市区が別々に残る(self) -> None:
        """⚠⚠ 東京都府中市13206 と広島県府中市34208。

        市区名をキーにすると**後に読んだ県で上書きされて片方が黙って消える**
        （実測で全国7組・1,277 → 1,270件になっていた → ADR 0014）。
        """
        tokyo = builder.parse_index(
            _index_html("tokyo", [("206", "府中市", "sc_fuchu")]), "13", "tokyo"
        )
        hiroshima = builder.parse_index(
            _index_html("hiroshima", [("208", "府中市", "sc_fuchu")]), "34", "hiroshima"
        )

        assert set(tokyo | hiroshima) == {"13206", "34208"}

    def test_他県へのリンクは拾わない(self) -> None:
        """⚠ 索引には他県へのリンクも混じる。要求したスラグの行だけ採る。"""
        html = _index_html("tokyo", [("121", "足立区", "sc_adachi")]) + _index_html(
            "kanagawa", [("101", "横浜市鶴見区", "sc_yokohamashitsurumi")]
        )

        assert set(builder.parse_index(html, "13", "tokyo")) == {"13121"}

    def test_市区リンクが無ければ空(self) -> None:
        """⚠ `hokkaido` の案内ページがこれ（HTTP 200 で中身が無い）。"""
        assert builder.parse_index("<html>相場ページ</html>", "01", "hokkaido") == {}


class Test毎月の取り直し:
    def test_月が変われば同じ市区がふたたび取得対象になる(self, tmp_path: Path) -> None:
        """⚠ これが「毎月撮り直し」の実体。旧実装ではここが永久に空になっていた。"""
        sep = tmp_path / "2026-09"
        sep.mkdir()
        (sep / "13121.html").write_text("取得済み", encoding="utf-8")

        assert builder.pending_cities(ADACHI, sep) == []

        octo = tmp_path / "2026-10"
        octo.mkdir()
        assert [t[0] for t in builder.pending_cities(ADACHI, octo)] == ["13121"]

    def test_月ごとに別のディレクトリを使う(self) -> None:
        assert builder.month_dir("2026-09") != builder.month_dir("2026-10")
        assert builder.month_dir("2026-10").name == "2026-10"


class Test取得対象:
    def test_索引にある市区はすべて対象になる(self, tmp_path: Path) -> None:
        """⚠ 検索パターンの市区に絞らない（2026-09-09 ユーザー判断で全国化）。"""
        base = tmp_path / "2026-10"
        base.mkdir()

        assert {t[0] for t in builder.pending_cities(NATIONWIDE, base)} == {
            "13121",
            "01101",
        }

    def test_取得済みの市区は飛ばす(self, tmp_path: Path) -> None:
        base = tmp_path / "2026-10"
        base.mkdir()
        (base / "01101.html").write_text("取得済み", encoding="utf-8")

        assert [t[0] for t in builder.pending_cities(NATIONWIDE, base)] == ["13121"]


class Test古い月の削除:
    def test_当月を含めて指定月数だけ残す(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(builder, "RAW", tmp_path)
        for name in ("2026-07", "2026-08", "2026-09", "2026-10"):
            (tmp_path / name).mkdir()

        removed = builder.prune_old_months("2026-10", keep=2)

        assert sorted(removed) == ["2026-07", "2026-08"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-09", "2026-10"]

    def test_月以外のディレクトリは消さない(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⚠⚠ 売買の応答（`raw/reinfolib`・実測131MB）が同じ `raw/` 配下にある。

        月のパターンに合わないものまで消すと、四半期ぶんの原典が丸ごと消える。
        """
        monkeypatch.setattr(builder, "RAW", tmp_path)
        (tmp_path / "2026-08").mkdir()
        (tmp_path / "2026-10").mkdir()
        (tmp_path / "reinfolib").mkdir()

        builder.prune_old_months("2026-10", keep=1)

        assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10", "reinfolib"]

    def test_当月しか無ければ何も消さない(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(builder, "RAW", tmp_path)
        (tmp_path / "2026-10").mkdir()

        assert builder.prune_old_months("2026-10", keep=2) == []
        assert [p.name for p in tmp_path.iterdir()] == ["2026-10"]
