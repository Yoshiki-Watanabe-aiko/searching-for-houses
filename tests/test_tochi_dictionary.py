"""土地（TOCHI_BUY）の設備抽出辞書（→ 課題#61 9c）。

⚠⚠ **辞書のセクションを間違えても例外にならず、抽出が減るだけ**である。
土地の掲載は詳細から設備原文を保存しているのに、照合先の辞書が空集合だと
**抽出0件のまま正常終了する**（課題#4 が `buy` で予見した形）。

ユーザー判断（2026-09-11）:

1. 建築条件付きは**表示専用**のまま辞書に入れない（判定は詳細の仕様表の「建築条件」欄
   → ``type_specific_attrs.build_condition``）。⚠ 建築条件付きの実例2件の原文に
   「建築条件」の語は無く、逆に条件なしの掲載に「建築条件なし」のタグが付く
2. 新設は整形地・前道6m以上・更地渡しの3条件。平坦地は入れない
3. 語彙は保存済みの HTML（市区選択ページの絞り込みフォーム・一覧・詳細）から採る
4. 土地の紐づけは辞書で使う条件だけ（→ ``tests/test_condition_seed.py``）
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from house_search.config.pattern import load_pattern_file, parse_pattern
from house_search.extract.dictionary import FAMILY_SECTIONS, FeatureDictionary, load_dictionary
from house_search.extract.extractor import extract_from_text
from house_search.extract.pattern_check import unknown_condition_codes
from house_search.scrape.suumo_tochi import SuumoTochiScraper

REPO = Path(__file__).resolve().parents[1]
DICTIONARY_PATH = REPO / "data" / "feature_dictionary.yaml"
TEMPLATE = REPO / "configs" / "examples" / "tochi_buy_v2.yaml"
FIXTURES = Path(__file__).parent / "fixtures" / "suumo_tochi"


@pytest.fixture(scope="module")
def dictionary() -> FeatureDictionary:
    return load_dictionary(DICTIONARY_PATH)


def _codes(text: str, dictionary: FeatureDictionary) -> set[str]:
    return set(extract_from_text(text, dictionary, family="TOCHI_BUY", site_code="SUUMO").codes)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "dict.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- セクションの展開 ---------------------------------------------------------


class Testセクションの展開:
    def test_セクションとファミリの対応(self) -> None:
        """⚠ 足すときは展開先を意識して書き換える（黙って増えないようリテラルで固定）。"""
        assert FAMILY_SECTIONS == {
            "chintai": ("CHINTAI",),
            "common": ("CHINTAI", "MANSION_BUY", "KODATE_BUY"),
            "buy": ("MANSION_BUY", "KODATE_BUY"),
            "tochi": ("TOCHI_BUY",),
        }

    def test_土地ファミリへ展開するのは土地セクションだけ(self) -> None:
        """⚠ `common` を土地へ展開すると、エレベーター・オートロックのような
        建物の設備を土地で抽出しうる（土地には建物が無い → ADR 0024）。
        """
        sections = [name for name, fams in FAMILY_SECTIONS.items() if "TOCHI_BUY" in fams]
        assert sections == ["tochi"]

    def test_土地セクションは土地ファミリだけへ展開される(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "tochi:\n  LOC_CORNER_LOT:\n    patterns: ['角地']\n"
            "buy:\n  CERT_FLAT35:\n    patterns: ['フラット35適合']\n",
        )
        entries = load_dictionary(path).entries
        assert {e.family for e in entries if e.code == "LOC_CORNER_LOT"} == {"TOCHI_BUY"}
        # 売買のセクションは土地へ漏れない
        assert {e.family for e in entries if e.code == "CERT_FLAT35"} == {
            "MANSION_BUY",
            "KODATE_BUY",
        }

    def test_知らないセクションは例外にする(self, tmp_path: Path) -> None:
        """⚠ 綴り違い（`tochii:`）は、これまで**黙って読み飛ばされていた**。

        そのセクションの条件はどのファミリにも展開されず、抽出0件のまま正常終了する。
        """
        path = _write(tmp_path, "tochii:\n  LOC_CORNER_LOT:\n    patterns: ['角地']\n")
        with pytest.raises(ValueError, match="知らないセクション"):
            load_dictionary(path)

    def test_versionはセクションとして扱わない(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "version: 1\ntochi:\n  LOC_CORNER_LOT:\n    patterns: ['角地']\n")
        assert {e.code for e in load_dictionary(path).entries} == {"LOC_CORNER_LOT"}

    def test_同じ条件が同じファミリへ2度展開されたら例外にする(self, tmp_path: Path) -> None:
        """⚠ `chintai` と `common` の両方に書くと CHINTAI に2エントリでき、
        どちらの表記が効くのか分からなくなる（`sync-dict` は両方を入れる）。
        """
        path = _write(
            tmp_path,
            "chintai:\n  EQUIP_ELEVATOR:\n    patterns: ['エレベーター']\n"
            "common:\n  EQUIP_ELEVATOR:\n    patterns: ['EV']\n",
        )
        with pytest.raises(ValueError, match="EQUIP_ELEVATOR"):
            load_dictionary(path)

    def test_ファミリが違えば同じ条件を別のセクションに書ける(self, tmp_path: Path) -> None:
        """土地の「即引渡し可」と売買の「即引渡可」は同じ条件の別表記（→ 表記は下記）。"""
        path = _write(
            tmp_path,
            "buy:\n  FEAT_VACANT:\n    patterns: ['即引渡可']\n"
            "tochi:\n  FEAT_VACANT:\n    patterns: ['即引渡し可']\n",
        )
        entries = load_dictionary(path).entries
        assert {e.family for e in entries if e.code == "FEAT_VACANT"} == {
            "MANSION_BUY",
            "KODATE_BUY",
            "TOCHI_BUY",
        }


def _family_digest(dictionary: FeatureDictionary, family: str) -> str:
    """ファミリの辞書の中身（条件・表記・否定・サイト固有）の指紋。表記の並び順には依らない。"""
    rows = [
        [
            entry.code,
            sorted(entry.patterns),
            sorted(entry.negative_patterns),
            sorted(list(pair) for pair in entry.site_patterns),
        ]
        for entry in dictionary.for_family(family)
    ]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


# 9c の着手時点（HEAD efde3c4）で計算した値。
# ⚠ 既存3ファミリの辞書を変えたときだけ書き換える（土地を足しても変わってはいけない）。
_BASELINE_DIGESTS = {
    "CHINTAI": "9111f3a81c1d1d947238e3420beb85d43f155d9184f055273a0eeac925dccd72",
    "MANSION_BUY": "8cb5be2385bbd9c610768670a7aa3902a8552453a83b6e810ea9d02d3dcb72a6",
    "KODATE_BUY": "8cb5be2385bbd9c610768670a7aa3902a8552453a83b6e810ea9d02d3dcb72a6",
}


@pytest.mark.parametrize("family", sorted(_BASELINE_DIGESTS))
def test_既存3ファミリの辞書は土地の追加で変わらない(
    family: str, dictionary: FeatureDictionary
) -> None:
    """⚠ `sync-dict` は全置換なので、ここがずれると稼働中の賃貸・売買の抽出が変わる。"""
    assert _family_digest(dictionary, family) == _BASELINE_DIGESTS[family]


# --- 土地の表記 -------------------------------------------------------------


class Test土地の表記:
    @pytest.mark.parametrize(
        ("text", "code"),
        [
            ("角地", "LOC_CORNER_LOT"),
            ("南側道路面す", "LOC_SOUTH_ROAD"),  # 特徴ピックアップのタグ
            ("南道路", "LOC_SOUTH_ROAD"),
            ("即引渡し可", "FEAT_VACANT"),  # ⚠ 土地のタグ。売買の「即引渡可」では拾えない
            ("即引き渡し可", "FEAT_VACANT"),  # 市区選択ページの絞り込みの表記
            ("即引渡可", "FEAT_VACANT"),
            ("都市ガス", "EQUIP_CITY_GAS"),
            ("整形地", "LAND_REGULAR_SHAPE"),
            ("前道６ｍ以上", "LAND_ROAD_6M"),  # ⚠ 全角。NFKC 正規化が効かないと抽出0件
            ("前道6m以上", "LAND_ROAD_6M"),
            ("更地渡し", "LAND_CLEARED_DELIVERY"),
        ],
    )
    def test_土地のタグを拾える(self, text: str, code: str, dictionary: FeatureDictionary) -> None:
        assert _codes(text, dictionary) == {code}

    def test_不整形地は整形地として取らない(self, dictionary: FeatureDictionary) -> None:
        """⚠ 部分一致なので「不整形地」は「整形地」を含む。否定パターンで打ち消す。"""
        assert _codes("不整形地 / 角地", dictionary) == {"LOC_CORNER_LOT"}

    @pytest.mark.parametrize("text", ["建築条件なし", "建築条件付", "建築条件付き土地"])
    def test_建築条件は辞書では取らない(self, text: str, dictionary: FeatureDictionary) -> None:
        """⚠ 建築条件付きは表示専用（ユーザー判断 2026-09-11）。

        辞書に「建築条件」を入れると、条件なしの掲載のタグ「建築条件なし」を拾う。
        """
        assert _codes(text, dictionary) == set()

    def test_平坦地は取らない(self, dictionary: FeatureDictionary) -> None:
        """⚠ ユーザー判断で入れない（「駅まで平坦」も同じく取らない）。"""
        assert _codes("平坦地 / 駅まで平坦", dictionary) == set()

    @pytest.mark.parametrize(
        "text",
        ["エレベーター", "オートロック", "システムキッチン", "食洗機", "ウォークインクローゼット"],
    )
    def test_建物の設備は土地では取らない(self, text: str, dictionary: FeatureDictionary) -> None:
        """⚠ `common` / `buy` を土地へ展開していないことの確認（土地には建物が無い）。"""
        assert _codes(text, dictionary) == set()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # 本番DBの土地26件（2026-09-11 検証走行）の設備原文から抜いた実物
            (
                "２沿線以上利用可 / 更地渡し / 市街地が近い / 陽当り良好 / 駅まで平坦 / "
                "南側道路面す / 閑静な住宅地 / 前道６ｍ以上 / 角地 / 整形地 / 緑豊かな住宅地 / "
                "都市近郊 / 都市ガス / 高台に立地 / 平坦地 / 建物プラン例有り",
                {
                    "LAND_CLEARED_DELIVERY",
                    "LOC_SOUTH_ROAD",
                    "LAND_ROAD_6M",
                    "LOC_CORNER_LOT",
                    "LAND_REGULAR_SHAPE",
                    "EQUIP_CITY_GAS",
                },
            ),
            (
                "２沿線以上利用可 / 土地50坪以上 / 即引渡し可 / 前道６ｍ以上 / 始発駅 / "
                "整形地 / 建築条件なし / 都市ガス / 平坦地 / 区画整理地内",
                {"FEAT_VACANT", "LAND_ROAD_6M", "LAND_REGULAR_SHAPE", "EQUIP_CITY_GAS"},
            ),
            # ⚠ 建築条件付きの実例（詳細の仕様表の欄は「付」）。原文に「建築条件」の語が無い
            (
                "南側道路面す / 閑静な住宅地 / 都市ガス / 小学校 徒歩10分以内",
                {"LOC_SOUTH_ROAD", "EQUIP_CITY_GAS"},
            ),
            ("陽当り良好 / 前道６ｍ以上 / 大型タウン内 / 平坦地", {"LAND_ROAD_6M"}),
        ],
    )
    def test_実データの原文(
        self, text: str, expected: set[str], dictionary: FeatureDictionary
    ) -> None:
        assert _codes(text, dictionary) == expected


# --- 詳細フィクスチャ（アダプタの出力をそのまま通す）--------------------------


class Test詳細フィクスチャ:
    """⚠ 純関数（抽出）だけでなく、アダプタが実際に作る設備原文を通して確かめる。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("detail_plain", {"LAND_CLEARED_DELIVERY", "LAND_REGULAR_SHAPE", "EQUIP_CITY_GAS"}),
            ("detail_jouken_tsuki", {"LAND_CLEARED_DELIVERY", "FEAT_VACANT", "EQUIP_CITY_GAS"}),
            (
                "detail_multi",
                {"LAND_CLEARED_DELIVERY", "LOC_CORNER_LOT", "LAND_REGULAR_SHAPE", "EQUIP_CITY_GAS"},
            ),
            ("detail_bus_noname", {"LAND_REGULAR_SHAPE"}),
            (
                "detail_undecided_range",
                {
                    "LAND_CLEARED_DELIVERY",
                    "LOC_SOUTH_ROAD",
                    "LAND_ROAD_6M",
                    "LOC_CORNER_LOT",
                    "LAND_REGULAR_SHAPE",
                    "EQUIP_CITY_GAS",
                },
            ),
        ],
    )
    def test_詳細の設備原文から抽出できる(
        self, name: str, expected: set[str], dictionary: FeatureDictionary
    ) -> None:
        html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
        detail = SuumoTochiScraper().parse_detail(html)
        assert detail.raw_features_text
        assert _codes(detail.raw_features_text, dictionary) == expected

    def test_建築条件付きでも辞書は建築条件を取らない(self, dictionary: FeatureDictionary) -> None:
        """判定は仕様表の欄（build_condition）で行い、辞書には持たせない。"""
        html = (FIXTURES / "detail_jouken_tsuki.html").read_text(encoding="utf-8")
        detail = SuumoTochiScraper().parse_detail(html)
        assert detail.type_specific_attrs["build_condition"] is True
        assert "LAND_BUILD_CONDITION" not in _codes(detail.raw_features_text or "", dictionary)


# --- 雛形と validate-config ---------------------------------------------------


def _tochi(**overrides) -> dict:
    base = {
        "name": "土地テスト",
        "property_type": "TOCHI",
        "webhook_ref": "TOCHI",
        "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"]},
        "must": {"price_max": 80_000_000},
        "want": {"numeric": [{"metric": "price", "weight": 25, "best": 1, "worst": 2}]},
    }
    base.update(overrides)
    return base


class Test雛形:
    def test_雛形のWANTは土地の辞書の条件だけで組まれている(
        self, dictionary: FeatureDictionary
    ) -> None:
        pattern = load_pattern_file(TEMPLATE)
        assert pattern.want.features, "9c で辞書ができたので、雛形に設備の WANT を置く"
        assert unknown_condition_codes(pattern, dictionary) == ()

    def test_雛形のMUSTには設備条件を書かない(self) -> None:
        """⚠ MUST の設備条件は、詳細取得済みで抽出されない掲載を**全件 fail**にする。"""
        assert load_pattern_file(TEMPLATE).must.features == []

    @pytest.mark.parametrize("code", ["EQUIP_ELEVATOR", "LAND_BUILD_CONDITION", "LAND_SETBACK"])
    def test_土地の辞書に無い条件はNG(self, code: str, dictionary: FeatureDictionary) -> None:
        want = {
            "features": [{"code": code, "weight": 3}],
            "numeric": [{"metric": "price", "weight": 25, "best": 1, "worst": 2}],
        }
        pattern = parse_pattern(_tochi(want=want))
        assert unknown_condition_codes(pattern, dictionary) == (code,)

    def test_土地の辞書にある条件はOK(self, dictionary: FeatureDictionary) -> None:
        want = {
            "features": [
                {"code": "LAND_REGULAR_SHAPE", "weight": 3},
                {"any_of": ["LOC_CORNER_LOT", "LOC_SOUTH_ROAD"], "weight": 3},
            ],
            "numeric": [{"metric": "price", "weight": 25, "best": 1, "worst": 2}],
        }
        pattern = parse_pattern(_tochi(want=want))
        assert unknown_condition_codes(pattern, dictionary) == ()

    def test_validate_configが雛形を通す(self, tmp_path: Path, monkeypatch) -> None:
        from house_search import cli

        def _refuse(names: list[str]) -> int:
            raise AssertionError("別ディレクトリの検証で孤児スコアの確認が走った")

        monkeypatch.setattr(cli, "_warn_orphan_scores", _refuse)
        data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
        (tmp_path / "tochi.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
        )
        assert cli.main(["validate-config", "--configs-dir", str(tmp_path), "--skip-webhook"]) == 0
