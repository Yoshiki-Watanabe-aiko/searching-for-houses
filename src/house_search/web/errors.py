"""画面に理由を出してよい入力エラー。"""

from __future__ import annotations

from werkzeug.exceptions import BadRequest


class UserInputError(BadRequest):
    """利用者の入力の誤り（絞り込み条件・メモの上限）。

    ⚠ 画面に出すのは**ここで自前で書いた文言だけ**。一般の例外の文言は
    内部の値（SQL・接続先）を含みうるので出さない。
    """

    def __init__(self, message: str) -> None:
        super().__init__(description=message)
