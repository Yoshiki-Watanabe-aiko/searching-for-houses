"""相場CSVの読み込み・検証のテスト。

⚠ 相場は「割安さ」の分母になるので、壊れた値が入ると
**例外にならないまま全掲載の順位が狂う**。読み込みの段で弾く。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from house_search.market.rates import MarketRateError, load_rate_rows

HEADER = "city_jis,city_name,segment,rate_value,source,stat_basis,period,acquired_on\n"
GOOD = HEADER + "13121,足立区,1LDK,129000,suumo_soba,rent_listed_mansion,2026-09,2026-09-05\n"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rent_rates.csv"
    path.write_text(body, encoding="utf-8")
    return path


def test_正常なCSVを読める(tmp_path: Path) -> None:
    rows = load_rate_rows(_write(tmp_path, GOOD))
    assert len(rows) == 1
    assert rows[0].city_jis == "13121"
    assert rows[0].segment == "1LDK"
    assert int(rows[0].rate_value) == 129_000


def test_桁を間違えた相場を弾く(tmp_path: Path) -> None:
    """⚠ 万円→円の換算漏れは「12.9」のような値になる。

    そのまま入ると全掲載の ratio が1万倍になるが、例外にならないと気づけない。
    """
    body = HEADER + "13121,足立区,1LDK,12.9,suumo_soba,rent_listed_mansion,2026-09,2026-09-05\n"
    with pytest.raises(MarketRateError, match="想定外"):
        load_rate_rows(_write(tmp_path, body))


def test_正規化されていない間取りを弾く(tmp_path: Path) -> None:
    """⚠ 集計側と採点側で別の表記になると、突き合わせが静かに0件になる。

    「相場が無い」のか「表記がずれている」のかを区別できなくなるので、
    読み込みの段で落とす。
    """
    body = HEADER + (
        "13121,足立区,ワンルーム,77000,suumo_soba,rent_listed_mansion,2026-09,2026-09-05\n"
    )
    with pytest.raises(MarketRateError, match="正規化"):
        load_rate_rows(_write(tmp_path, body))


def test_空のCSVを黙って受け入れない(tmp_path: Path) -> None:
    """⚠ 0件のまま同期すると、相場が無い状態で採点が続く（例外にならない）。"""
    with pytest.raises(MarketRateError, match="1件もありません"):
        load_rate_rows(_write(tmp_path, HEADER))


def test_同梱の相場CSVが読める() -> None:
    """実データ（82市区・1,038行）が検証を通ること。"""
    path = Path(__file__).resolve().parents[1] / "data" / "market_rates" / "rent_rates.csv"
    rows = load_rate_rows(path)
    assert len({r.city_jis for r in rows}) == 82
    assert {r.source for r in rows} == {"suumo_soba"}
    # ⚠ 2DK は DB で最多の間取り（9,387件）だが、相場ページの既定（賃貸マンション）
    # では 56市区にしか載らない。アパート相場（ts=2）で補完して 71市区にした。
    # 件数が急に変わったら気づけるよう固定する（→ 課題#49）
    cities_with_2dk = len({r.city_jis for r in rows if r.segment == "2DK"})
    assert cities_with_2dk == 71, f"2DK の相場がある市区が {cities_with_2dk} に変わった"
    # ⚠ 補完した行がどの建物種別の相場かを残していること。混ざったまま
    # 記録が無いと「どちらと比べた割安さか」を後から検証できない
    assert Counter(r.stat_basis for r in rows) == {
        "rent_listed_mansion": 991,
        "rent_listed_apart": 47,
    }


def test_想定外のstat_basisを弾く(tmp_path: Path) -> None:
    """⚠ 綴りが揺れると「どちらの建物種別の相場と比べたか」を集計できなくなる。"""
    body = HEADER + "13121,足立区,1LDK,129000,suumo_soba,rent_listed,2026-09,2026-09-05\n"
    with pytest.raises(MarketRateError, match="stat_basis"):
        load_rate_rows(_write(tmp_path, body))
