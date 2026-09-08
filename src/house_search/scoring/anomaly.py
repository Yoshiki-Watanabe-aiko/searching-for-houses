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

⚠⚠ **閾値は種別ファミリごとに違う**（2026-09-09 → ``BUY_MARKET_RATE_ANOMALY_THRESHOLD``）。
相場比の母集団が賃貸と売買で別の位置にあるため、**同じ数字が同じ意味を持たない**。
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

#: 売買の種別ファミリ。⚠ 金額と面積の意味も、下の閾値もここで変わる
_BUY_FAMILIES = frozenset({"MANSION_BUY", "KODATE_BUY"})

#: 売買の閾値。
#:
#: ⚠⚠ **賃貸の 0.20 をそのまま当ててはいけない**（2026-09-09 実測）。
#: 相場比の母集団の中央がファミリで大きく違うため、**同じ数字でも意味が変わる**。
#: うちの ``price`` は売出価格で相場は取引価格なので、売買の比は 1.0 をまたぐ。
#:
#: ==================  ========  =========================
#: パターン            中央      0.20 が相当する位置
#: ==================  ========  =========================
#: 東京23区賃貸        0.618     中央の約 1/3
#: 近郊60分圏賃貸      0.541     中央の約 1/2.7
#: 中古一戸建て        0.978     中央の約 1/5
#: 中古マンション      1.171     中央の約 **1/6**
#: 新築マンション      1.962     中央の約 **1/10**
#: ==================  ========  =========================
#:
#: ⚠ 実測（MUST 通過 29,781件）で 0.20 未満は **20件**あり、**全件が築44〜82年**で
#: 実在しうる安値だった（再建築不可・借地・築古）。賃貸の課題#50 のような
#: 「サイトが単位を取り違えた」掲載は1件も含まれていない。
#:
#: ======  ==============  ==============
#: 閾値    中古一戸建て    中古マンション
#: ======  ==============  ==============
#: 0.20    11件            9件
#: 0.15    3件             3件
#: 0.12    1件             3件
#: **0.10**  **0件**       **1件**
#: ======  ==============  ==============
#:
#: 0.10 で残る1件（中古M id=190852・築48年・**50万円**・36.61㎡）は、
#: 他の19件（0.104〜0.19）から**1桁外れている**唯一の掲載である。
#: ⚠ **新築は 0.20 未満が元から0件**（最小 0.598 / 0.694）なので、この変更で
#: 新築の検出力は変わらない。
BUY_MARKET_RATE_ANOMALY_THRESHOLD = 0.10


def threshold_for(view: ListingView) -> float:
    """その掲載に当てる閾値。

    ⚠ **ファミリ未設定は賃貸に倒す。** 既定を売買（厳しい側）にすると、
    ``property_family`` を渡し忘れた経路で**検出が黙って止まる**
    （``describe_price_anomaly`` が既定を賃貸に倒しているのと同じ理由）。
    """
    if view.property_family in _BUY_FAMILIES:
        return BUY_MARKET_RATE_ANOMALY_THRESHOLD
    return MARKET_RATE_ANOMALY_THRESHOLD


def is_price_anomaly(view: ListingView, *, threshold: float | None = None) -> bool:
    """相場に対して極端に安いか。

    ``threshold`` を渡さなければ**ファミリごとの閾値**を使う（→ ``threshold_for``）。
    明示的に渡した値はファミリより優先する（実測や切り戻しのため）。

    ⚠ **相場が引けない掲載は判定できないので False を返す**（実測で
    23区帯 6.3% / 近郊帯 12.1%、売買は 0.0〜13.1% が未解決）。「異常が無い」のではなく
    「判定していない」ことに注意する——相場が無いセルの異常は検出できない。
    """
    ratio = view.market_rate_ratio
    if ratio is None:
        return False
    return ratio < (threshold_for(view) if threshold is None else threshold)


def describe_price_anomaly(view: ListingView) -> str:
    """実行サマリ1行ぶんの説明。

    ⚠⚠ **金額の意味はファミリで変わる。** ``rent_total`` は生成列
    （``price`` ＋ 管理費）なので**売買でも値が入る**。賃貸前提のまま出すと
    中古マンションの警告に「月額6,005,510円」という無意味な数字が並ぶ
    （→ 課題#4 が ``notify/format.py`` で塞いだのとまったく同じ形）。
    ⚠ **面積も相場比の分母に合わせる**（マンションは専有・戸建ては延床）。
    別の面積を出すと「なぜこの比になるのか」が読み取れない。
    ⚠ **既定は賃貸に倒す**（``property_family`` を渡し忘れた経路が
    黙って売買表示にならないように）。
    """
    ratio = view.market_rate_ratio
    parts = [f"{view.site_code or '?'} id={view.listing_id}"]
    is_buy = view.property_family in _BUY_FAMILIES
    if is_buy:
        if view.price is not None:
            parts.append(f"価格{view.price:,}円")
    elif view.rent_total is not None:
        parts.append(f"月額{view.rent_total:,}円")
    if view.property_family == "KODATE_BUY":
        if view.building_area_sqm is not None:
            parts.append(f"延床{view.building_area_sqm}㎡")
    elif view.area_sqm is not None:
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
    threshold: float | None = None,
    limit: int = 10,
) -> list[str]:
    """疑いのある掲載の説明を、相場比の低い順に最大 ``limit`` 件返す。

    ⚠ **呼び出し側は MUST を通った掲載だけを渡す**こと。``fail`` の掲載は
    ランキングにも通知にも出ないので、警告しても行動につながらない。

    ⚠ 閾値は**掲載ごとに**ファミリから決まる（→ ``threshold_for``）。
    賃貸と売買が混ざった一覧を渡しても、それぞれの閾値で判定される。
    """
    hits = [v for v in views if is_price_anomaly(v, threshold=threshold)]
    hits.sort(key=lambda v: (v.market_rate_ratio, v.listing_id or 0))
    return [describe_price_anomaly(v) for v in hits[:limit]]
