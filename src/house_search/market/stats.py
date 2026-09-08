"""相場比の判別力を測る純関数（→ 課題#49 Step 5）。

⚠ **DBに触らない。** 呼び出し側が `load_listing_views` で読んだ値を渡す
（検証が実装の経路を通るようにするため → 課題#46 で「検証スクリプトが
フォールバックを再現しておらず、正しい実装を誤診しかけた」）。
"""

from __future__ import annotations

from collections.abc import Sequence


def _ranks(values: Sequence[float]) -> list[float]:
    """同順位を平均順位にした順位を返す。

    ⚠ **同順位を単純な並び順で埋めない。** 価格や相場比には同じ値が固まる帯があり
    （価格未定を除いても「2,980万円」のような刻みに集中する）、並び順で番号を振ると
    **入力の順番だけで相関が動く**。順序に依存する指標は再現しないので判断に使えない。
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    """スピアマンの順位相関。値が2件未満、または片方が定数なら ``None``。

    ⚠ **`None` を 0 と混ぜない。** 「相関が無い」と「測れなかった」を同じ値で表すと、
    ゲート（|r| < 0.6）を**測れていないのに通ったこと**にしてしまう。
    """
    if len(pairs) < 2:
        return None
    xs = _ranks([p[0] for p in pairs])
    ys = _ranks([p[1] for p in pairs])
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom = (sum(v * v for v in dx) * sum(v * v for v in dy)) ** 0.5
    if denom == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / denom
