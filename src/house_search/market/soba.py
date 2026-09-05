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

## 建物種別（``ts``）— 実測 2026-09-05

URLの ``ts`` で建物種別を切り替えられる（1=マンション・2=アパート・3=一戸建て等）。
既定は ``ts=1`` なので、素の市区ページは**賃貸マンションの相場**である
（タイトルが「賃貸マンション家賃相場」になる）。
⚠ 効くことは対照 ``zzz=1`` が基準と完全同一になることを先に確かめてから測った（→ 課題#29）。

⚠ **2DK はマンションの掲載が少なく、82市区中26市区で相場が出ない。**
2DK は ``t_listings`` で最多の間取り（9,387件）なので影響が大きい。
アパート相場で埋めると 56 → 約80市区になる（→ ``merge_rates``）。

⚠ **建物種別で相場の水準が違う**（アパート ÷ マンションは実測 0.75〜0.92）。
そのため補完した行は ``stat_basis`` にどちらの相場かを残す。
⚠ **セルごとに基準の建物種別が変わる**が、これは承知のうえの判断である
（ユーザー判断 2026-09-05）。掲載側で測った「賃料 ÷ マンション相場」の中央値は
アパート 0.537 / マンション 0.574 と**7%しか違わない**（うちの MUST が
同じ価格帯で切っているため）ので、順位への影響は小さい。

## 既知の限界

⚠ ``t_listings`` で建物種別が分かるのは 22%（アパート2,664 / マンション1,848 /
不明16,294）しかないため、**掲載ごとに相場を出し分けることはできない**。
⚠ **ページに管理費の扱いも平均/中央値の別も書かれていない**ので、
``stat_basis`` は「掲載賃料」としか言えない。実測で「掲載 ÷ 相場」の中央値は
0.54 で、これは MUST が安い掲載だけを集めているため。
⚠ **``best``/``worst`` を 1.0 中心に置いてはいけない**（実測分布に合わせる → 課題#31）。
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


# ⚠ 相場の母集団がどの建物種別かを行に残す。値が 0.75〜0.92 倍ずれるので、
# 「どちらの相場と比べた割安さか」を後から言えないと検証ができない
STAT_BASIS_MANSION = "rent_listed_mansion"
STAT_BASIS_APART = "rent_listed_apart"


@dataclass(frozen=True, slots=True)
class MergedRate:
    """建物種別をまたいで1セル1値にまとめた相場。"""

    layout: str
    rent_yen: int
    stat_basis: str
    """``STAT_BASIS_MANSION`` か ``STAT_BASIS_APART``。"""


def merge_rates(
    mansion: list[SobaRate], apart: list[SobaRate]
) -> list[MergedRate]:
    """マンション相場を主とし、**無いセルだけ**アパート相場で補完する。

    ⚠ **マンションにあるセルをアパートで上書きしない。** 上書きすると
    同じ間取りでも市区によって基準が入れ替わり、比較の目盛りが揺れる。
    ⚠ 出力は間取り順（決定性。揺れるとCSVの差分が読めなくなる）。
    """
    merged = {
        r.layout: MergedRate(r.layout, r.rent_yen, STAT_BASIS_MANSION) for r in mansion
    }
    for r in apart:
        if r.layout not in merged:
            merged[r.layout] = MergedRate(r.layout, r.rent_yen, STAT_BASIS_APART)
    return [merged[k] for k in sorted(merged)]


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
