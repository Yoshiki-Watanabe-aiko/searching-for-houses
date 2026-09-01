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
"""

from __future__ import annotations

import re
import unicodedata

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
# 丁目表記が無い住所の数字。先頭の数字塊を丁目とみなす
# （都市部の賃貸は「西早稲田3-1-1」のように丁目-番-号で書かれる）。
_FIRST_NUMBER = re.compile(r"(\d+)\D*[\d\-]*.*$")

# 住所粒度の分類（dedup-stats の観測用）。
GRANULARITY_CHOME = "丁目まで"
GRANULARITY_TOWN = "町名まで"
GRANULARITY_NONE = "解決不能"


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


def normalize_address(address: str | None, prefecture: str | None = None) -> str | None:
    """名寄せキーの入力に使う正規化住所を作る。

    ``prefecture`` は住所が都道府県から始まらないとき（賃貸EX が実際にそう書く）に
    前置するためのもの。``resolve_city`` が解決した値をそのまま渡せばよい。

    町名までしか判らない住所はそのまま返す。**判定不能なら None** を返し、
    呼び出し側は ``dedup_key`` を作らない（グループ化せず単独で残す）。
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
    if prefecture and not value.startswith(prefecture):
        value = f"{prefecture}{value}"

    # 「大字」「字」はサイトによって書いたり書かなかったりする。
    value = value.replace("大字", "")
    value = re.sub(r"(?<=[町村])字", "", value)
    # 「霞ヶ関 / 霞ケ関 / 霞が関」の揺れ。ノ/ツ は町名固有表記を壊すので触らない。
    value = value.replace("ヶ", "が").replace("ケ", "が")

    value = _CHOME.sub(_chome_to_arabic, value)
    if mark := _CHOME_MARK.search(value):
        return value[: mark.end()] or None
    # 丁目表記が無いなら、最初の数字塊を丁目とみなして以降を捨てる。
    # 数字が無ければ町名までで確定（SUUMO の「白子町剃金」など）。
    return _FIRST_NUMBER.sub(r"\1丁目", value) or None


def address_granularity(normalized: str | None) -> str:
    """正規化住所の粒度を分類する（``dedup-stats`` の観測用）。"""
    if not normalized:
        return GRANULARITY_NONE
    return GRANULARITY_CHOME if "丁目" in normalized else GRANULARITY_TOWN
