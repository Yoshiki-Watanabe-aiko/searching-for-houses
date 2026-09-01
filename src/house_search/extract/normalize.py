"""設備テキストの正規化とトークン化。

照合とパターン定義の双方を同じ関数に通すことで、辞書YAMLを書く人が
全角/半角・大文字小文字の揺れを気にしなくて済むようにしている。
"""

from __future__ import annotations

import re
import unicodedata

# トークンの区切り文字。
# 「・」を区切りに含めないのは意図的で、「バス・トイレ別」「敷金・礼金不要」のように
# 語そのものに含まれるため。区切りとして使うサイトもあるが、照合は本文全体への
# 部分一致で行うため取りこぼさない（トークン化は未知表記の抽出だけに使う）。
_SEPARATORS = re.compile(r"[、,，/／｜|\n\r\t;；]+")
_WHITESPACE = re.compile(r"[\s　]+")
# 数量・記号だけのトークンは辞書育成の材料にならないため未知表記から除く。
_NOISE_ONLY = re.compile(r"^[\d\W_]*$")

# 未知表記として記録するトークンの長さ範囲。
MIN_TOKEN_LEN = 2
MAX_TOKEN_LEN = 40


def normalize_text(value: str | None) -> str:
    """NFKC正規化 → 小文字化 → 空白の圧縮。

    NFKC で全角英数・半角カナ・``㎡`` などが正規形へ寄る。
    """
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).lower()
    return _WHITESPACE.sub(" ", normalized).strip()


def tokenize(value: str | None) -> list[str]:
    """正規化済みテキストを区切り文字で分割する（未知表記の抽出用）。

    重複は取り除き、出現順を保つ。
    """
    normalized = normalize_text(value)
    if not normalized:
        return []
    seen: dict[str, None] = {}
    for raw in _SEPARATORS.split(normalized):
        token = raw.strip(" 　()（）[]【】「」")
        if not token:
            continue
        seen.setdefault(token, None)
    return list(seen)


def is_recordable_token(token: str) -> bool:
    """未知表記として記録する価値のあるトークンか。"""
    if not (MIN_TOKEN_LEN <= len(token) <= MAX_TOKEN_LEN):
        return False
    return not _NOISE_ONLY.match(token)
