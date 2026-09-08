"""売買の相場CSV（㎡単価）の読み込み・検証と、DBへの同期のテスト（→ 課題#49）。

⚠⚠ **賃貸と売買を同じ関数で読まないことを固定するのが本題。** segment は
間取りではなく単価の区分、rate_value は月額ではなく㎡単価で、値域も桁が違う。
ひとつの検証で通すと**片方の値域をもう片方に当てる**ことになり、
例外にならないまま通ってしまう（相場は割安さの分母なので、順位だけが静かに狂う）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from house_search.market.rates import MarketRateError, load_buy_rate_rows, load_rate_rows

BUY_HEADER = (
    "family,city_jis,city_name,segment,rate_value,sample_count,"
    "source,stat_basis,period,acquired_on\n"
)
BUY_GOOD = BUY_HEADER + (
    "MANSION_BUY,13101,千代田区,AREA_SQM,1800000,347,mlit_library,"
    "trade_unit_price_02,2025Q2-2026Q1,2026-09-08\n"
)
RENT_HEADER = "city_jis,city_name,segment,rate_value,source,stat_basis,period,acquired_on\n"
RENT_GOOD = (
    RENT_HEADER + "13121,足立区,1LDK,129000,suumo_soba,rent_listed_mansion,2026-09,2026-09-05\n"
)


def _write(tmp_path: Path, body: str, name: str = "buy_rates.csv") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_正常な売買CSVを読める(tmp_path: Path) -> None:
    rows = load_buy_rate_rows(_write(tmp_path, BUY_GOOD))
    assert len(rows) == 1
    row = rows[0]
    assert row.family == "MANSION_BUY"
    assert row.segment == "AREA_SQM"
    assert int(row.rate_value) == 1_800_000
    assert row.sample_count == 347
    assert row.stat_basis == "trade_unit_price_02"


def test_想定外のファミリを弾く(tmp_path: Path) -> None:
    """⚠ CHINTAI を書くと賃貸の行を㎡単価として上書きしかねない。"""
    body = BUY_GOOD.replace("MANSION_BUY", "CHINTAI")
    with pytest.raises(MarketRateError, match="family"):
        load_buy_rate_rows(_write(tmp_path, body))


def test_間取りをsegmentに書いたら弾く(tmp_path: Path) -> None:
    """⚠ 売買の segment は単価の区分。間取りが混ざると採点側の突き合わせが0件になる。"""
    body = BUY_GOOD.replace("AREA_SQM", "1LDK")
    with pytest.raises(MarketRateError, match="segment"):
        load_buy_rate_rows(_write(tmp_path, body))


def test_賃貸のstat_basisを弾く(tmp_path: Path) -> None:
    """⚠ どちらの母集団で測ったかが分からなくなると、水準差の検証ができない。"""
    body = BUY_GOOD.replace("trade_unit_price_02", "rent_listed_mansion")
    with pytest.raises(MarketRateError, match="stat_basis"):
        load_buy_rate_rows(_write(tmp_path, body))


def test_桁を間違えた単価を弾く(tmp_path: Path) -> None:
    """⚠ 万円のまま書くと ratio が1万倍になるが、例外が無いと気づけない。"""
    body = BUY_GOOD.replace(",1800000,", ",180,")
    with pytest.raises(MarketRateError, match="㎡単価"):
        load_buy_rate_rows(_write(tmp_path, body))


def test_薄いセルを弾く(tmp_path: Path) -> None:
    """⚠ サンプルが少ないセルは相場と呼べない。生成側で落とすが読み込みでも確かめる。"""
    body = BUY_GOOD.replace(",347,", ",9,")
    with pytest.raises(MarketRateError, match="サンプル"):
        load_buy_rate_rows(_write(tmp_path, body))


def test_賃貸の読み込みで売買CSVを読むと落ちる(tmp_path: Path) -> None:
    """⚠⚠ 取り違えを黙って通さない。

    売買の㎡単価（180万円）は賃貸の値域（1万〜200万円）に**収まってしまう**ので、
    値域だけでは弾けない。segment（AREA_SQM）と stat_basis が防波堤になる。
    """
    with pytest.raises(MarketRateError):
        load_rate_rows(_write(tmp_path, BUY_GOOD))


def test_売買の読み込みで賃貸CSVを読むと落ちる(tmp_path: Path) -> None:
    """⚠⚠ 逆方向も固定する。片方向だけ守っても、もう片方で同じ事故が起きる。"""
    with pytest.raises(MarketRateError, match="列"):
        load_buy_rate_rows(_write(tmp_path, RENT_GOOD, name="rent_rates.csv"))


def test_売買の相場がDBへ入る(test_engine: Engine, tmp_path: Path) -> None:
    """⚠ family と sample_count が実際に保存されることを確かめる。

    型に足しただけでは保存されない（→ 課題#4 手順4後半で `repair_reserve_monthly` が
    UPDATE 文から漏れていた）。
    """
    from house_search.market.rates import sync_market_rates

    rows = load_buy_rate_rows(_write(tmp_path, BUY_GOOD))
    with test_engine.begin() as conn:
        city_id = conn.execute(
            text("SELECT id FROM m_cities WHERE jis_code = '13101'")
        ).scalar_one()
        conn.execute(
            text("DELETE FROM m_market_rates WHERE city_id = :cid AND source = 'mlit_library'"),
            {"cid": city_id},
        )
        result = sync_market_rates(conn, rows)
        assert result.inserted == 1
        assert result.unresolved_cities == []
        stored = conn.execute(
            text(
                "SELECT family, segment, rate_value, sample_count, stat_basis "
                "FROM m_market_rates WHERE city_id = :cid AND source = 'mlit_library'"
            ),
            {"cid": city_id},
        ).one()
        # ⚠ family は引数の既定（CHINTAI）ではなく**行の値**が使われること
        assert stored.family == "MANSION_BUY"
        assert stored.segment == "AREA_SQM"
        assert int(stored.rate_value) == 1_800_000
        assert stored.sample_count == 347
        assert stored.stat_basis == "trade_unit_price_02"
        conn.execute(
            text("DELETE FROM m_market_rates WHERE city_id = :cid AND source = 'mlit_library'"),
            {"cid": city_id},
        )


def test_賃貸の同期はsample_countを埋めない(test_engine: Engine, tmp_path: Path) -> None:
    """⚠ 賃貸の相場ページは母数を出さない。0 で埋めると「薄いセル」と区別がつかない。"""
    from house_search.market.rates import sync_market_rates

    rows = load_rate_rows(_write(tmp_path, RENT_GOOD, name="rent_rates.csv"))
    assert rows[0].family is None
    assert rows[0].sample_count is None
    with test_engine.begin() as conn:
        city_id = conn.execute(
            text("SELECT id FROM m_cities WHERE jis_code = '13121'")
        ).scalar_one()
        conn.execute(
            text(
                "DELETE FROM m_market_rates "
                "WHERE city_id = :cid AND source = 'suumo_soba' AND period = '2026-09'"
            ),
            {"cid": city_id},
        )
        sync_market_rates(conn, rows)
        stored = conn.execute(
            text(
                "SELECT family, sample_count FROM m_market_rates "
                "WHERE city_id = :cid AND source = 'suumo_soba' AND period = '2026-09'"
            ),
            {"cid": city_id},
        ).one()
        assert stored.family == "CHINTAI"
        assert stored.sample_count is None
        conn.execute(
            text(
                "DELETE FROM m_market_rates "
                "WHERE city_id = :cid AND source = 'suumo_soba' AND period = '2026-09'"
            ),
            {"cid": city_id},
        )
