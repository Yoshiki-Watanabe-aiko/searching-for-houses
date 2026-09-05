"""相場に対して極端に安い掲載を「サイト側のデータ異常の疑い」として拾う（→ 課題#50）。

実測（2026-09-05）で、**広告元が賃料の単位を取り違えて登録している**掲載が
ランキング上位を占めていた。

===========================  =====================  ===================
掲載                         サイトの記載            正しいと思われる値
===========================  =====================  ===================
リザーブ北綾瀬（GOO/NIFTY）  ``賃料 1.40万円``      14.0万円
目黒区（HOMES）              JSON-LD ``price:19500``  19.5万円
===========================  =====================  ===================

⚠ **こちらのパーサは正常**で、サイトが提供した値を正しく読んでいる。直せるのは
サイト側だけなので、**こちらにできるのは気づけるようにすることだけ**である。

⚠⚠ **スコアの数字を見ても異常と分からない。** ``rent_total`` の best/worst の
内側にある限り、異常な安値は満点になるだけで何も警告されない（課題#24 で
「スコアは高いのに使えないランキング」だったのと同じ形）。相場という
**外部の基準**があって初めて外れ値として浮かぶ。

⚠ **配点では覆わない**（→ ADR 0021 と同じ思想で ADR 0022 が決めた）。
``best`` を切り上げると**実在する激安物件まで一緒に抑える**ので、
順位はそのままにして検出だけ行う。

⚠ **エラー通知（Discord）へは送らない。** 既知の偽陽性で通知が埋まると
「読まれない通知は本物のエラーを見逃すという形で実害になる」
（→ 課題#45・要件定義書 §14.1）。実行サマリとログに出して見て判断できるようにする。
"""

from __future__ import annotations

from collections.abc import Iterable

from house_search.scoring.listing_view import ListingView

#: 相場の何割を下回ったら「疑い」とするか。
#:
#: ⚠ **実測してから決めた**（2026-09-05・本番DBの MUST 通過 3,710 / 7,576件）。
#:
#: ======  ==========  ==============
#: 閾値    東京23区    近郊60分圏
#: ======  ==========  ==============
#: 0.30    8件         **10件**
#: 0.25    5件         2件
#: **0.20**  **5件**   **0件**
#: ======  ==========  ==============
#:
#: ⚠ **0.30 だと近郊帯に偽陽性が10件出る**（横浜市神奈川区55.8㎡3LDK 68,000円など、
#: 郊外の古いアパート・戸建てとして実在しうる値）。0.20 なら23区の5件だけが残り、
#: **これは実サイトで誤りを確認済みの掲載**（リザーブ北綾瀬×2・パークアクシス御茶ノ水・
#: 牛込柳町・目黒区）と一致する。
#: ⚠ MUST の上限で切った母集団の中央値は 23区 0.618 / 近郊 0.541 なので、
#: 0.20 は「相場の5分の1未満」という極端な外れ値だけを拾う。
#: ⚠ **見逃す方向に倒してある。** 順位は変えないので、見逃しても実害は
#: 「気づけない」だけだが、偽陽性が多いと**サマリが読まれなくなる**
#: （読まれない通知は本物を見逃す実害になる → 課題#45）。
MARKET_RATE_ANOMALY_THRESHOLD = 0.20


def is_price_anomaly(
    view: ListingView, *, threshold: float = MARKET_RATE_ANOMALY_THRESHOLD
) -> bool:
    """相場に対して極端に安いか。

    ⚠ **相場が引けない掲載は判定できないので False を返す**（実測で
    23区帯 6.3% / 近郊帯 12.1% が未解決）。「異常が無い」のではなく
    「判定していない」ことに注意する——相場が無いセルの異常は検出できない。
    """
    ratio = view.market_rate_ratio
    return ratio is not None and ratio < threshold


def describe_price_anomaly(view: ListingView) -> str:
    """実行サマリ1行ぶんの説明。"""
    ratio = view.market_rate_ratio
    parts = [f"{view.site_code or '?'} id={view.listing_id}"]
    if view.rent_total is not None:
        parts.append(f"月額{view.rent_total:,}円")
    if view.area_sqm is not None:
        parts.append(f"{view.area_sqm}㎡")
    if view.layout:
        parts.append(view.layout)
    if ratio is not None:
        parts.append(f"相場比{ratio:.3f}")
    if view.title:
        parts.append(f"「{view.title[:24]}」")
    return " / ".join(parts)


def collect_price_anomalies(
    views: Iterable[ListingView],
    *,
    threshold: float = MARKET_RATE_ANOMALY_THRESHOLD,
    limit: int = 10,
) -> list[str]:
    """疑いのある掲載の説明を、相場比の低い順に最大 ``limit`` 件返す。

    ⚠ **呼び出し側は MUST を通った掲載だけを渡す**こと。``fail`` の掲載は
    ランキングにも通知にも出ないので、警告しても行動につながらない。
    """
    hits = [v for v in views if is_price_anomaly(v, threshold=threshold)]
    hits.sort(key=lambda v: (v.market_rate_ratio, v.listing_id or 0))
    return [describe_price_anomaly(v) for v in hits[:limit]]
