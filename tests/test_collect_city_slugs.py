"""市区スラグ収集ツールの SUUMO 用パーサ（→ 課題#61・Phase 9b）。

⚠ 正規表現が中古マンションの ``/ms/chuko/`` 決め打ちだったので、土地の市区選択ページ
（``/tochi/{pref}/city/``）を読ませると**リンク0件のまま正常終了**した（例外にならない）。
スラグは種別によらず共通（13番のシードの前提）なので、どちらのパスも読めればよい。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tools"))

from collect_city_slugs import SITES, parse_index_suumo  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "suumo_tochi"


def test_土地の市区選択ページから読める() -> None:
    """実物（2026-09-11 取得の東京都）。リンクは52件で、郡（西多摩郡）も含む。

    ⚠ 郡は ``m_cities`` に行が無いので、突き合わせ（``match_cities``）で捨てられる
    （論点10: 郡の扱いは別の課題）。パーサの段では捨てない。
    """
    html = (FIXTURES / "city_tokyo.html").read_text(encoding="utf-8")
    rows = parse_index_suumo(html, pref_slug="tokyo")
    assert len(rows) == 52
    assert ("13112", "sc_setagaya", "世田谷区") in rows
    assert ("13201", "sc_hachioji", "八王子市") in rows
    assert ("13300", "sc_nishitamagun", "西多摩郡") in rows


def test_中古マンションの市区選択ページも従来どおり読める() -> None:
    html = (
        '<input type="checkbox" name="sc" value="13101" id="sa01_sc101" />'
        '<a href="/ms/chuko/tokyo/sc_chiyoda/" id="js-linkSc101">千代田区</a>'
    )
    assert parse_index_suumo(html, pref_slug="tokyo") == [("13101", "sc_chiyoda", "千代田区")]


def test_都道府県が違うリンクは捨てる() -> None:
    html = (
        '<input type="checkbox" name="sc" value="14101" id="sa01_sc101" />'
        '<a href="/tochi/kanagawa/sc_yokohamashitsurumi/" id="js-linkSc101">鶴見区</a>'
    )
    assert parse_index_suumo(html, pref_slug="tokyo") == []


def test_土地の索引URLを選べる() -> None:
    """⚠ 取得する索引を差し替えられないと、土地の市区選択ページを取り直すには
    コードを書き換えることになる（予算の都合で何度も取れない → 課題#36）。"""
    assert SITES["SUUMO"]["index"] == "https://suumo.jp/ms/chuko/{pref}/city/"
    assert SITES["SUUMO"]["index_tochi"] == "https://suumo.jp/tochi/{pref}/city/"
