"""相場に対して極端に安い掲載の検出（→ 課題#50）。

⚠ **実測でサイト側（広告元）が賃料の単位を取り違えて登録していた**
（「14.0万円」を `1.40万円`、「19.5万円」を `19500` として登録）。
こちらのパーサは正常なので直せるのはサイト側だけで、**できるのは気づけるようにすること**。

⚠⚠ **スコアの数字を見ても異常と分からない。** `rent_total` の best/worst の
内側にある限り異常な安値は満点になるだけで、何も警告されない。
"""

from __future__ import annotations

from pathlib import Path

from house_search.scoring.anomaly import (
    MARKET_RATE_ANOMALY_THRESHOLD,
    collect_price_anomalies,
    is_price_anomaly,
)
from house_search.scoring.listing_view import ListingView


def _view(listing_id: int, ratio: float | None, **kwargs) -> ListingView:
    return ListingView(
        listing_id=listing_id,
        site_code=kwargs.pop("site_code", "GOO"),
        market_rate_ratio=ratio,
        **kwargs,
    )


class Test判定:
    def test_相場の5分の1未満は疑いとする(self) -> None:
        # 実測値: リザーブ北綾瀬（サイトが「1.40万円」と誤記）
        assert is_price_anomaly(_view(1, 0.119)) is True

    def test_郊外の激安物件は疑いにしない(self) -> None:
        """⚠ **偽陽性を避けるほうに倒してある**（→ 閾値の根拠）。

        0.244 は横浜市神奈川区の 55.8㎡ 3LDK 68,000円で、
        郊外の古い物件として実在しうる。閾値 0.30 だとこれを拾ってしまう。
        """
        assert is_price_anomaly(_view(1, 0.244)) is False

    def test_閾値ちょうどは疑いにしない(self) -> None:
        assert is_price_anomaly(_view(1, MARKET_RATE_ANOMALY_THRESHOLD)) is False

    def test_通常の掲載は疑いにしない(self) -> None:
        # 母集団の中央は 23区 0.618 / 近郊 0.541（2026-09-05 実測）
        assert is_price_anomaly(_view(1, 0.618)) is False

    def test_相場が引けない掲載は判定しない(self) -> None:
        """⚠ **「異常が無い」のではなく「判定していない」。**

        相場が引けない掲載は実測で 23区帯 6.3% / 近郊帯 12.1% あり、
        そこにある異常はこの仕組みでは検出できない。
        """
        assert is_price_anomaly(_view(1, None)) is False


class Test一覧の組み立て:
    def test_相場比の低い順に返る(self) -> None:
        views = [_view(1, 0.19), _view(2, 0.119), _view(3, 0.15)]

        result = collect_price_anomalies(views)

        assert [r.split("id=")[1].split(" ")[0] for r in result] == ["2", "3", "1"]

    def test_件数を絞れる(self) -> None:
        views = [_view(i, 0.1 + i * 0.01) for i in range(20)]

        assert len(collect_price_anomalies(views, limit=3)) == 3

    def test_疑いが無ければ空(self) -> None:
        assert collect_price_anomalies([_view(1, 0.618), _view(2, None)]) == []

    def test_説明に判断材料が入る(self) -> None:
        view = _view(
            42, 0.119, rent_total=19000, area_sqm=42.17, layout="2LDK", title="リザーブ北綾瀬"
        )

        (message,) = collect_price_anomalies([view])

        assert "id=42" in message
        assert "19,000円" in message
        assert "42.17" in message
        assert "0.119" in message
        assert "リザーブ北綾瀬" in message


def test_採点の経路から実際に呼ばれている() -> None:
    """⚠ **「実装済みだが未配線」を防ぐ。**

    純関数が緑でも、``scan`` / ``rescore`` が呼んでいなければ
    **警告は永久に出ない**（`notify_error` が定義だけで呼び出し元が
    1箇所も無かった → 課題#45 と同じ形）。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "house_search" / "pipeline"
    callers = {
        path.name
        for path in src.glob("*.py")
        if "collect_price_anomalies(" in path.read_text(encoding="utf-8")
    }
    assert "scan.py" in callers, "scan が相場比の異常検出を呼んでいない"
    assert "tasks.py" in callers, "rescore が相場比の異常検出を呼んでいない"
