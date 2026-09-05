"""名寄せ用の住所正規化。

設備テキスト用の ``extract/normalize.py`` とは目的が違うので分けてある
（住所固有の規則を設備の照合に混ぜない）。

**丁目までで打ち切るのが要。** サイトによって住所の粒度が
「番地まで（LIFULL HOME'S）」「丁目まで（多数）」「町名まで（SUUMO の一部）」と
ばらつくため、番地を残すとクロスサイトの名寄せが原理的に成立しない
（実測 → ADR 0012）。

実データで観測した揺れ（2026-09-02・301掲載）::

    ABLE        東京都足立区梅田７丁目周辺地図      末尾に「周辺地図」
    APAMAN      東京都 足立区 南花畑 ４丁目 35-15   全角スペース区切り＋番地
    EHEYA       東京都足立区伊興四丁目              漢数字の丁目
    GOO         東京都足立区古千谷本町１            丁目を省いて数字だけ
    HOMES       東京都足立区谷中1丁目28-1           番地まで
    SUUMO       神奈川県相模原市緑区中野            丁目すら無い

⚠ **丁目表記が無い住所の「最初の数字塊」は丁目とは限らない**（→ ADR 0020・課題#48）。
丁目が存在しない町では番地がそのまま丁目になり、``埼玉県深谷市中瀬1480丁目`` という
**実在しない住所が ``dedup_key`` になる**。実測（2026-09-05）で active 掲載の
5.4%（1,074件）がこれだった。⚠ **例外にならず件数も減らない**（名寄せが静かに失敗して
ユニーク率が高く見えるだけ）ので、住所マスタ（``m_address_points``）と
突き合わせるまで検出できなかった。

そのため ``normalize_address`` は ``index``（``AddressIndex``）を受け取り、
**その町に丁目が実在するときだけ**数字塊を丁目として採る。
⚠ **``index`` を渡さなければ挙動は従来どおり**にしてある（マスタ未同期の環境で
結果がぶれないため）。DBに触らない純関数のまま保つ設計で、索引の組み立ては
``dedup/address_master.py`` が受け持つ（``commute/matcher.py`` の ``StationIndex`` と同じ形）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from house_search.scrape.prefectures import PREFECTURE_ROMAJI

# 住所が都道府県から始まっているかの判定に使う（定数だけのモジュールなので循環しない）。
_PREFECTURE_NAMES: tuple[str, ...] = tuple(PREFECTURE_ROMAJI)

# 一覧・詳細から住所欄を拾うときに紛れ込むサイト固有の付随文字列。
# ABLE の「周辺地図」は実データで確認済み。
_TRAILING_NOISE = re.compile(r"(周辺地図|地図を見る|地図|以下未定|その他)+$")
_WHITESPACE = re.compile(r"[\s　]+")
# 住所に現れうるハイフン類。NFKC で吸収しきれない罫線・長音を含める。
_HYPHENS = re.compile(r"[-‐‑‒–—―ー−ｰ]")

_KANJI_DIGITS = {
    "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CHOME = re.compile(r"([〇一二三四五六七八九十]+)丁目")
# 「丁目」以降を切り落とすための位置。最初の1つで打ち切る
# （「1丁目28」を丁目化してしまった場合でも先頭の丁目が残る）。
_CHOME_MARK = re.compile(r"丁目")
# 丁目表記が無い住所の数字。先頭の数字塊を丁目**候補**とみなす
# （都市部の賃貸は「西早稲田3-1-1」のように丁目-番-号で書かれる）。
# ⚠ 候補どまりであって確定ではない。丁目の実在は AddressIndex で確かめる。
_FIRST_NUMBER = re.compile(r"(\d+)\D*[\d\-]*.*$")

# 住所粒度の分類（dedup-stats の観測用）。
GRANULARITY_CHOME = "丁目まで"
GRANULARITY_TOWN = "町名まで"
GRANULARITY_NONE = "解決不能"


@dataclass(frozen=True, slots=True)
class AddressIndex:
    """住所マスタの索引。「その町に丁目が実在するか」だけを答える。

    ``m_address_points`` から作る（``dedup/address_master.py``）。
    ⚠ **キーは正規化済みの町名**（``埼玉県深谷市中瀬``）で、掲載側も原典側も
    同じ ``_normalize_base`` を通してから突き合わせる。別の正規化を作ると
    2系統がドリフトして、どちらが正しいか言えなくなる。
    """

    towns: frozenset[str]
    chomes: frozenset[tuple[str, int]]

    @classmethod
    def build(cls, rows: Iterable[tuple[str, int | None]]) -> AddressIndex:
        """``(town_key, chome_number)`` の並びから索引を作る。"""
        towns: set[str] = set()
        chomes: set[tuple[str, int]] = set()
        for town_key, chome_number in rows:
            if not town_key:
                continue
            towns.add(town_key)
            if chome_number is not None:
                chomes.add((town_key, int(chome_number)))
        return cls(towns=frozenset(towns), chomes=frozenset(chomes))

    @property
    def is_empty(self) -> bool:
        """マスタ未同期の検出用。空の索引は「全件フォールバック」と同義になる。"""
        return not self.towns

    def has_town(self, town_key: str) -> bool:
        return town_key in self.towns

    def has_chome(self, town_key: str, number: int) -> bool:
        return (town_key, number) in self.chomes


def _kanji_to_int(word: str) -> int | None:
    """漢数字を整数にする。住所の丁目で現れる 1〜99 までを扱う。"""
    if word in _KANJI_DIGITS:
        return _KANJI_DIGITS[word]
    if word == "十":
        return 10
    if match := re.fullmatch(r"十([一二三四五六七八九])", word):
        return 10 + _KANJI_DIGITS[match.group(1)]
    if match := re.fullmatch(r"([一二三四五六七八九])十([一二三四五六七八九])?", word):
        tens = _KANJI_DIGITS[match.group(1)] * 10
        return tens + (_KANJI_DIGITS[match.group(2)] if match.group(2) else 0)
    return None


def _chome_to_arabic(match: re.Match[str]) -> str:
    value = _kanji_to_int(match.group(1))
    return f"{value}丁目" if value is not None else match.group(0)


def normalize_base(address: str | None, prefecture: str | None = None) -> str | None:
    """丁目の切り詰めを**しない**共通の正規化。

    掲載側（``normalize_address``）と住所マスタ側（``address_master``）の双方が通す。
    ⚠ **原典にこの関数を通さず別の規則で正規化してはいけない。** 突き合わせが
    静かに0件になり、「マスタと一致しない」のか「正規化がずれている」のかを
    区別できなくなる。
    """
    if not address:
        return None
    value = unicodedata.normalize("NFKC", address)
    value = _WHITESPACE.sub("", value)
    value = _TRAILING_NOISE.sub("", value)
    value = _HYPHENS.sub("-", value)
    if not value:
        return None

    # 都道府県が無い掲載（賃貸EX）は解決済みの都道府県を前置して粒度を揃える。
    # ⚠ **すでにどこかの都道府県で始まっているなら前置しない。**
    # 判定を「渡された prefecture で始まるか」だけにすると、`t_listings.prefecture`
    # が誤っているとき `長野県東京都立川市富士見町4丁目` という**実在しない住所**が
    # できあがり、名寄せが黙って失敗する（実測3件 → 課題#48）。
    # 住所側に都道府県があるなら、そちらのほうが引数より信用できる。
    if prefecture and not value.startswith(_PREFECTURE_NAMES):
        value = f"{prefecture}{value}"

    # 「大字」「字」はサイトによって書いたり書かなかったりする。
    value = value.replace("大字", "")
    value = re.sub(r"(?<=[町村])字", "", value)
    # 「霞ヶ関 / 霞ケ関 / 霞が関」の揺れ。ノ/ツ は町名固有表記を壊すので触らない。
    value = value.replace("ヶ", "が").replace("ケ", "が")

    return _CHOME.sub(_chome_to_arabic, value) or None


def normalize_address(
    address: str | None,
    prefecture: str | None = None,
    index: AddressIndex | None = None,
) -> str | None:
    """名寄せキーの入力に使う正規化住所を作る。

    ``prefecture`` は住所が都道府県から始まらないとき（賃貸EX が実際にそう書く）に
    前置するためのもの。``resolve_city`` が解決した値をそのまま渡せばよい。

    ``index`` は住所マスタの索引（``sync-addresses`` で作る）。丁目表記の無い住所で
    数字塊を丁目とみなしてよいかの判定に使う。**渡さなければ従来どおり**
    「最初の数字塊は丁目」として扱う（→ ADR 0020）。

    町名までしか判らない住所はそのまま返す。**判定不能なら None** を返し、
    呼び出し側は ``dedup_key`` を作らない（グループ化せず単独で残す）。
    """
    value = normalize_base(address, prefecture)
    if not value:
        return None

    # 明示的な「丁目」表記は信じる。ここは推測ではないのでマスタに問い合わせない。
    if mark := _CHOME_MARK.search(value):
        return value[: mark.end()] or None

    match = _FIRST_NUMBER.search(value)
    if match is None:
        # 数字が無ければ町名までで確定（SUUMO の「白子町剃金」など）。
        return value
    raw_town = value[: match.start()]
    number = int(match.group(1))
    fallback = f"{raw_town}{number}丁目"
    if index is None:
        return fallback

    for candidate in _town_candidates(raw_town):
        if not index.has_town(candidate):
            continue
        if index.has_chome(candidate, number):
            # その丁目が実在する。従来どおり丁目として採る。
            return fallback
        # 町はマスタにあるのに、その丁目が実在しない = 数字は番地。
        return candidate
    # ⚠ 町がマスタに無ければ判定材料が無いので、従来どおり丁目とみなす（安全側）。
    return fallback


def _town_candidates(raw_town: str) -> tuple[str, ...]:
    """マスタに問い合わせる町名の候補を、確からしい順に返す。

    ⚠ **小字（``早野字下夕田``）はマスタに無い。** 位置参照情報が持つのは大字・町丁目まで
    なので、小字つきの住所（APAMAN・goo が実際に返す）は「字」の手前で引き直す。
    そうしないと「町がマスタに無い」経路へ落ちて番地が丁目のまま残る
    （実測 2026-09-05 で ``千葉県茂原市早野字下夕田1079丁目`` など14件）。

    ⚠ **「字」を ``normalize_base`` で無条件に落としてはいけない。**
    町名の一部に「字」を含む地名が実在する（小田原市 ``十字四丁目``）。
    ここでマスタに当たった候補だけを採れば、実在する町名を壊さずに済む。
    """
    town = raw_town.rstrip("-")
    if not town:
        return ()
    position = town.find("字")
    if position > 0:
        return (town, town[:position])
    return (town,)


def address_granularity(normalized: str | None) -> str:
    """正規化住所の粒度を分類する（``dedup-stats`` の観測用）。"""
    if not normalized:
        return GRANULARITY_NONE
    return GRANULARITY_CHOME if "丁目" in normalized else GRANULARITY_TOWN
