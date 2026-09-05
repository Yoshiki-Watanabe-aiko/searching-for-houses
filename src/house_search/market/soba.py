"""SUUMO の家賃相場ページを解析して「市区 × 間取り」の相場を取り出す。

## なぜ外部の相場が要るのか（実測 2026-09-05）

⚠ **自DBの掲載から相場を作ることはできない。** ``t_listings`` には MUST 1段目を
通った掲載しか残らず、賃料上限で右側が切断されている。実測では **3分の2のセルで
中央値が MUST 上限の90%以上**に張り付いており、自DB中央値は「実勢相場」ではなく
「上限の93%」をなぞっているだけだった。

さらに悪いことに、**切断の度合いはセルごとにバラつく**（足立区で実勢比 1LDK 75% /
2DK 60%）。一様なら順位への影響は小さいが、バラつくと市区・間取りによって
ものさしの目盛りが変わる。**例外にならず順位だけが静かに狂う**ので、外部の相場が要る。

## 取得の形

- 市区ページ（``/chintai/soba/{pref}/sc_{slug}/``）1本に**全間取りの相場が1表**で載る
- 都県索引（``/chintai/soba/{pref}/``）にも相場の JSON が埋まっているが、
  ⚠ **間取りが `1LDK/2K/2DK` のようにまとめられていて粒度が粗い**。
  実測では 2K 10.3万・1LDK 12.9万が同じ 11.7万に丸められており、
  **2K が割高・1LDK が割安と出る歪み**が入る。市区ページを使う
- 都県索引は**市区スラグと JIS コードの対応**を採るのに使う（4リクエストで足りる）

## 既知の限界

⚠ **このページは「賃貸マンション」の相場**で、アパートは含まれない可能性が高い。
一方 ``t_listings`` で建物種別が分かるのは 22%（4,646/20,940）しかないため、
**マンション相場を全掲載に当てるしかない**。アパートは相場より安く出るので、
系統的に「割安」と判定される。⚠ 順位に効く既知の偏りとして記録しておく。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import html

from house_search.scoring.listing_view import normalize_layout

# 「7.7万円」「12.9万円」。⚠ 単位を確かめずに数値だけ拾わない
_RATE = re.compile(r"^(\d+(?:\.\d+)?)万円$")

# 表の見出し。これを含む表だけを相場表とみなす（table[0] 決め打ちは脆い）
_HEADER_WORDS = ("間取り", "家賃相場")


@dataclass(frozen=True, slots=True)
class SobaRate:
    """市区 × 間取りの家賃相場。"""

    layout: str
    """正規化済みの間取り（``normalize_layout`` を通した値）。"""

    rent_yen: int
    """相場の月額（円）。ページは万円表記なので 10,000 倍して丸める。"""


class SobaParseError(ValueError):
    """相場ページを解析できなかった。

    ⚠ **0件を黙って返さない。** 相場が取れないまま正常終了すると、
    そのセルだけ採点軸が1本消えた状態で順位が出る（例外にならない）。
    """


def parse_soba(page: str) -> list[SobaRate]:
    """相場ページのHTMLから「間取り → 相場」を取り出す。

    ⚠ 同じ間取りが2回出たら例外にする。マンション用とアパート用の表が
    同居していた場合に、**どちらを採ったのか分からないまま**値が入るのを防ぐ。
    """
    doc = html.fromstring(page)
    for table in doc.cssselect("table"):
        body = re.sub(r"\s+", " ", table.text_content())
        if not all(word in body for word in _HEADER_WORDS):
            continue
        rates = _parse_table(table)
        if rates:
            return rates
    raise SobaParseError("相場表が見つかりません（ページ構造が変わった可能性）")


def _parse_table(table: html.HtmlElement) -> list[SobaRate]:
    found: dict[str, int] = {}
    for row in table.cssselect("tr"):
        cells = [re.sub(r"\s+", "", cell.text_content()) for cell in row.cssselect("th, td")]
        layout_raw = next((c for c in cells if normalize_layout(c)), None)
        if layout_raw is None:
            continue
        amount = next((m for c in cells if (m := _RATE.match(c))), None)
        if amount is None:
            # 掲載が少ない間取りは「-」になる。相場が無いだけなので飛ばす
            continue
        layout = normalize_layout(layout_raw)
        assert layout is not None
        yen = int(round(float(amount.group(1)) * 10_000))
        if layout in found and found[layout] != yen:
            raise SobaParseError(
                f"間取り {layout} の相場が2通りあります（{found[layout]}円 と {yen}円）。"
                "マンション用とアパート用の表が同居している可能性があります"
            )
        found[layout] = yen
    return [SobaRate(layout=k, rent_yen=v) for k, v in sorted(found.items())]
