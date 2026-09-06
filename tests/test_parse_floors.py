"""階のパーサの回帰テスト（→ 課題#4）。

⚠⚠ **総階数は「地下N階建」の N を拾ってはいけない。** ``地上13階地下1階建`` は
**13階建の建物**であって1階建ではない。⚠ 例外にも件数の減少にもならず
**値だけが静かに狂う**ため、実データを見るまで気づけない
（課題#51 の全角区切り・課題#53 の「1万3020円」とまったく同じ形）。

⚠ 表記は**全16サイトのフィクスチャから実測して集めた**もので、想像で足していない。
地下の位置がサイトによって前後する（賃貸EX は ``地下1地上13階建`` と地下が先）。
"""

from house_search.scrape.base import parse_floor, parse_total_floors


class Test総階数:
    def test_素朴な表記(self) -> None:
        """エイブル・アットホーム・ニフティなど多数のサイトがこの形。"""
        assert parse_total_floors("10階建") == 10
        assert parse_total_floors("2階建て") == 2
        assert parse_total_floors("1階/2階建") == 2

    def test_地上だけの表記(self) -> None:
        """SUUMO 賃貸・スモッカ・賃貸EX・D-room。"""
        assert parse_total_floors("地上10階建") == 10
        assert parse_total_floors("地上2階建") == 2

    def test_地下が先に来る表記は従来どおり地上階を採る(self) -> None:
        """賃貸EX。⚠ **修正前から正しく読めていた**ので回帰として固定する。"""
        assert parse_total_floors("地下1地上13階建") == 13

    def test_地下が後に来る表記でも地上階を採る(self) -> None:
        """⚠⚠ **修正前は 1 を返していた**（「地下1階建」にマッチしていた）。

        goo は稼働中の賃貸サイトで、**7階の住戸が「1階建の建物」**として
        保存されていた（2026-09-06 実測）。
        """
        assert parse_total_floors("地上13階地下1階建") == 13
        assert parse_total_floors("地上14階地下1階建") == 14
        assert parse_total_floors("地上17階地下1階建") == 17

    def test_構造が前置される表記(self) -> None:
        """SUUMO 売買の詳細ページ「構造・階建て」。⚠ **修正前は 1**。"""
        assert parse_total_floors("RC13階地下1階建") == 13
        assert parse_total_floors("1階/RC13階地下1階建") == 13

    def test_括弧を挟む表記(self) -> None:
        """ハウスコム・いい部屋ネット。⚠ 修正前から正しいので回帰として固定する。"""
        assert parse_total_floors("10階部分（地上10階建") == 10
        assert parse_total_floors("3階（3階建") == 3

    def test_全角数字(self) -> None:
        """APAMAN の「鉄骨造３階建」。``\\d`` は全角にマッチする。"""
        assert parse_total_floors("鉄骨造３階建") == 3

    def test_読めない表記はNone(self) -> None:
        """⚠ 「建」が無い表記は従来どおり None（ハウスコムの「地上4階」→ 課題#37）。

        ここを拾うようにすると所在階と区別できなくなるので**広げない**。
        """
        assert parse_total_floors("地上4階") is None
        assert parse_total_floors("") is None
        assert parse_total_floors(None) is None


class Test所在階:
    def test_売買詳細の所在階(self) -> None:
        """SUUMO 売買の詳細ページは「所在階」に ``1階`` を単独で持つ。"""
        assert parse_floor("1階") == 1
        assert parse_floor("13階") == 13

    def test_地下は負値(self) -> None:
        assert parse_floor("地下1階") == -1

    def test_所在階と階建てが同居する表記は所在階を採る(self) -> None:
        """SUUMO 売買の「所在階/構造・階建て」欄。左から順に読むので所在階が先。"""
        assert parse_floor("1階/RC13階地下1階建") == 1
