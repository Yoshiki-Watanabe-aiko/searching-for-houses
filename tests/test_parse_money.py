"""金額パーサが全角表記を読めることのテスト（→ 課題#51）。

⚠ **面積は NFKC 正規化しているのに、金額はしていなかった。**
``parse_area_sqm`` は「㎡」「m²」の揺れを吸収するため NFKC を通しているが、
``parse_yen`` 系は生の文字列に正規表現を当てていた。``\\d`` は全角数字にマッチする
一方 ``\\.`` と ``[,]`` は半角しか受けないため、**全角の区切りを含む金額で
値だけが静かに狂う**（例外にならない）。

⚠ 現時点で全角区切りの金額を出すサイトは確認できていないが、
「取得が成功してしまうので気づけない」類なので、実害が出る前に塞ぐ
（robots のグループ統合 → 課題#43 と同じ判断）。
"""

from __future__ import annotations

import pytest

from house_search.scrape.base import parse_fee, parse_months_fee, parse_yen


class Test全角の金額を読める:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # ⚠ 全角の小数点。半角の \. にマッチせず、小数部だけを読んで
            # 10倍していた（「１４．３万円」→ 30000）
            ("１４．３万円", 143_000),
            ("９．５万円", 95_000),
            # ⚠ 全角のカンマ。int('０００') が 0 になるため、
            # **管理費が黙って0円になる**のが最も危険な現れ方
            ("３，０００円", 3_000),
            ("１４３，０００円", 143_000),
            # 区切りを含まない全角は元から読めていた（回帰の固定）
            ("１万円", 10_000),
            ("２２０００円", 22_000),
        ],
    )
    def test_全角表記(self, text: str, expected: int) -> None:
        assert parse_yen(text) == expected


class Test半角の挙動は変わらない:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("3.5万円", 35_000),
            ("14.3万円", 143_000),
            ("15.90万円", 159_000),
            ("25000円", 25_000),
            ("143,000円", 143_000),
            ("23.9万", 239_000),  # ABLE の敷金欄は「円」を省く
            ("7万円", 70_000),
        ],
    )
    def test_半角表記(self, text: str, expected: int) -> None:
        assert parse_yen(text) == expected

    def test_読めない表記はNone(self) -> None:
        assert parse_yen("お問い合わせ") is None
        assert parse_yen(None) is None


class Test管理費と敷金:
    def test_全角カンマの管理費が0にならない(self) -> None:
        """⚠ **これが最も危険**。0 だと「管理費は0円」として ``rent_total`` に
        足され、MUST も通ってしまう（判定不能なら unknown になるのに）。"""
        assert parse_fee("３，０００円") == 3_000

    def test_空欄は従来どおり0(self) -> None:
        assert parse_fee("-") == 0
        assert parse_fee("なし") == 0
        assert parse_fee(None) is None

    def test_全角の月数が正しく換算される(self) -> None:
        """⚠ 「１．５ヶ月」は小数点で切れて「５ヶ月」と読まれ、
        敷金が **5ヶ月分** に化けていた。"""
        assert parse_months_fee("１．５ヶ月", 100_000) == 150_000

    def test_半角の月数は変わらない(self) -> None:
        assert parse_months_fee("1.5ヶ月", 100_000) == 150_000
        assert parse_months_fee("2ヶ月", 80_000) == 160_000
        assert parse_months_fee("なし", 80_000) == 0

class Test売買の億表記:
    """⚠ **売買データが入る前に塞いだ**（→ 課題#4 手順3の下ごしらえ）。

    ⚠⚠ **NULL になるのではなく「それらしい値」が入るのが最も危険。**
    ``1億2,800万円`` が 2,800万円として記録されると、MUST の価格上限を
    通ってランキング上位に来る（課題#50 と同じ「値だけが静かに狂う」形）。
    """

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1億円", 100_000_000),
            ("1億2,800万円", 128_000_000),
            ("1億2800万円", 128_000_000),
            ("2億5,000万円", 250_000_000),
            ("10億円", 1_000_000_000),
            # レンジは下限を採る（新築の価格帯表示 → 要件定義書 §11.4）
            ("1億2,800万円 ～ 1億5,000万円", 128_000_000),
        ],
    )
    def test_億を含む価格(self, text: str, expected: int) -> None:
        assert parse_yen(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2,980万円", 29_800_000),
            ("4980万円", 49_800_000),
            ("9,800万円", 98_000_000),
            ("3,480万円(税込)", 34_800_000),
            # 新築のレンジ表示。下限を採る
            ("5,000万円～7,000万円", 50_000_000),
        ],
    )
    def test_億を含まない価格は変わらない(self, text: str, expected: int) -> None:
        assert parse_yen(text) == expected

    @pytest.mark.parametrize("text", ["価格未定", "未定", "応相談"])
    def test_価格未定はNone(self, text: str) -> None:
        """⚠ 0 にしてはいけない（「安い」と誤読される → 要件定義書 §9）。"""
        assert parse_yen(text) is None

    def test_賃貸の表記に影響しない(self) -> None:
        """⚠ 賃貸に「億」は出ないが、正規表現の順序を変えるので回帰を固定する。"""
        assert parse_yen("8.4万円") == 84_000
        assert parse_yen("13.4万円") == 134_000
        assert parse_yen("3,000円") == 3_000
