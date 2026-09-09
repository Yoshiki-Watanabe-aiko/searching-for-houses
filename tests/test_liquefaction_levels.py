"""液状化（XKT025）の集計の回帰テスト（課題#59・ADR 0023）。

⚠ ここで固定するのは**外すと黙って壊れる**2点である。

1. **レベル6（評価対象外＝湖沼・河道）を分子にも分母にも入れない。**
   含めると水域の多い丁目が「安全」として順位を上げ、例外にならない
   （→ ADR 0021 決定4 の「区域外(0)と未解決(None)を混ぜない」と同型）。
2. **危険ランクへの反転（rank = 6 − level）。** 原典は「小さいほど危険」で
   既存のハザードと向きが逆。反転を落とすと**安全な丘陵が最下位になる**。

メッシュコードからの矩形再構成も固定する（API の geometry はタイル境界で
切り取られ、バッファで隣接タイルへはみ出すので使えない）。

CSVもDBも要らない純関数のテスト。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "tools"))

from build_hazard_levels import (  # noqa: E402
    XKT025_EXCLUDED_LEVEL,
    compute_liquefaction_values,
    mesh_bounds,
)


class Test評価対象外の除外:
    """⚠ レベル6は「液状化しにくい」ではなく「評価していない」。"""

    def test_レベル6は分母に入らない(self) -> None:
        # 丁目の1割が「しにくい」（level 5 → rank 1）、9割が湖沼（level 6）。
        # ⚠ 6を分母に入れると 0.1 になる。正しくは評価対象だけで平均するので 1.0。
        values = compute_liquefaction_values({5: 100.0, 6: 900.0})
        assert values is not None
        assert values["rank_avg"] == pytest.approx(1.0)

    def test_レベル6は分子にも入らない(self) -> None:
        # 半分が「非常にしやすい」（level 1 → rank 5）、半分が河道。
        values = compute_liquefaction_values({1: 50.0, 6: 50.0})
        assert values is not None
        assert values["area_ratio"] == pytest.approx(1.0)
        assert values["rank_avg"] == pytest.approx(5.0)
        assert values["rank_max"] == pytest.approx(5.0)

    def test_全面が評価対象外なら値を作らない(self) -> None:
        # ⚠ ここで 0 や 1 を返すと「安全と確認した」の意味になる。None が正しい
        assert compute_liquefaction_values({6: 1000.0}) is None

    def test_交差が無ければ値を作らない(self) -> None:
        assert compute_liquefaction_values({}) is None

    def test_除外するレベルは6である(self) -> None:
        assert XKT025_EXCLUDED_LEVEL == 6


class Test危険ランクへの反転:
    """⚠ 原典は「小さいほど危険」。既存のハザードと向きを揃える。"""

    def test_最も危険なレベル1は5になる(self) -> None:
        values = compute_liquefaction_values({1: 100.0})
        assert values is not None
        assert values["rank_avg"] == pytest.approx(5.0)
        assert values["rank_max"] == pytest.approx(5.0)

    def test_最も安全なレベル5は1になる(self) -> None:
        values = compute_liquefaction_values({5: 100.0})
        assert values is not None
        assert values["rank_avg"] == pytest.approx(1.0)
        assert values["rank_max"] == pytest.approx(1.0)

    def test_反転を落とすと安全な丘陵が最悪になる(self) -> None:
        # ⚠ この2つの大小が逆転したら反転が壊れている
        safe = compute_liquefaction_values({5: 100.0})
        risky = compute_liquefaction_values({1: 100.0})
        assert safe is not None and risky is not None
        assert safe["rank_avg"] < risky["rank_avg"]


class Test面積加重:
    def test_面積で加重される(self) -> None:
        # level 1（rank 5）が 1割、level 5（rank 1）が 9割 → (5*10 + 1*90)/100 = 1.4
        values = compute_liquefaction_values({1: 10.0, 5: 90.0})
        assert values is not None
        assert values["rank_avg"] == pytest.approx(1.4)

    def test_area_ratioはレベル1と2だけを数える(self) -> None:
        # 「液状化しやすい」以上（level 1〜2 ＝ rank 4〜5）の面積比
        values = compute_liquefaction_values({1: 10.0, 2: 20.0, 3: 30.0, 5: 40.0})
        assert values is not None
        assert values["area_ratio"] == pytest.approx(0.30)

    def test_丸めは小数第4位(self) -> None:
        values = compute_liquefaction_values({1: 1.0, 5: 2.0})
        assert values is not None
        assert values["rank_avg"] == pytest.approx(round((5 * 1 + 1 * 2) / 3, 4))


class Testメッシュコードからの矩形:
    """⚠ API の geometry は切り取られ・はみ出すので、mesh_code を正典にする。"""

    def test_5次メッシュの大きさは経度11_25秒_緯度7_5秒(self) -> None:
        west, south, east, north = mesh_bounds("5339469342")
        assert (east - west) * 3600 == pytest.approx(11.25)
        assert (north - south) * 3600 == pytest.approx(7.5)

    def test_1次メッシュの原点が合う(self) -> None:
        # 5339 → 緯度 53/1.5 = 35.3333…、経度 39+100 = 139
        west, south, _east, _north = mesh_bounds("5339000011")
        assert south == pytest.approx(53 * 2 / 3)
        assert west == pytest.approx(139.0)

    def test_4次_5次の分割は南西_南東_北西_北東(self) -> None:
        base_w, base_s, _e, _n = mesh_bounds("5339000011")
        # 5次の2 = 南東（経度だけ +1/320）
        w2, s2, _, _ = mesh_bounds("5339000012")
        assert w2 - base_w == pytest.approx(1 / 320)
        assert s2 == pytest.approx(base_s)
        # 5次の3 = 北西（緯度だけ +(2/3)/320）
        w3, s3, _, _ = mesh_bounds("5339000013")
        assert w3 == pytest.approx(base_w)
        assert s3 - base_s == pytest.approx((2 / 3) / 320)
        # 5次の4 = 北東（両方）
        w4, s4, _, _ = mesh_bounds("5339000014")
        assert w4 - base_w == pytest.approx(1 / 320)
        assert s4 - base_s == pytest.approx((2 / 3) / 320)

    def test_10桁でなければ例外(self) -> None:
        with pytest.raises(ValueError):
            mesh_bounds("533946934")

    def test_数字でなければ例外(self) -> None:
        with pytest.raises(ValueError):
            mesh_bounds("533946934a")
