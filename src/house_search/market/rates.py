"""相場CSVの読み込みと ``m_market_rates`` への同期。

⚠ **検証を省かない。** 相場は「割安さ」の分母になるので、値が壊れると
**例外にならないまま全掲載の順位が狂う**。読み込みの段で値域と市区の解決を
確かめ、通らない行は数えて報告する（ハザードの ``sync-hazards`` と同じ形）。
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from house_search.market.soba import STAT_BASIS_APART, STAT_BASIS_MANSION
from house_search.scoring.listing_view import normalize_layout

# ⚠ 相場としてありえない値を弾く。桁を1つ間違えた（万円→円の換算漏れ）ときに
# 気づけるのはここだけ。実測では 4.4万〜30万円の範囲に収まっていた
MIN_RATE_YEN = 10_000
MAX_RATE_YEN = 2_000_000

# ⚠ 相場の母集団（どの建物種別か）を自由文字列にしない。綴りが揺れると
# 「どちらの相場と比べたのか」を後から集計できず、検証そのものができなくなる
ALLOWED_STAT_BASIS = frozenset({STAT_BASIS_MANSION, STAT_BASIS_APART})


@dataclass(frozen=True, slots=True)
class MarketRateRow:
    """相場CSVの1行。"""

    city_jis: str
    city_name: str
    segment: str
    rate_value: Decimal
    source: str
    stat_basis: str
    period: str
    acquired_on: dt.date
    family: str | None = None
    """種別ファミリ。⚠ 賃貸CSVには列が無いので None（sync の引数が使われる）。"""
    sample_count: int | None = None
    """集計に使った件数。⚠ 賃貸の相場ページは母数を出さないので NULL になる。"""


@dataclass(frozen=True, slots=True)
class SyncResult:
    """同期の結果。"""

    inserted: int
    updated: int
    unresolved_cities: list[str]
    """``m_cities`` に無い市区。⚠ 黙って捨てると相場が歯抜けのまま気づけない。"""


class MarketRateError(ValueError):
    """相場CSVが妥当でない。"""


def load_rate_rows(path: Path) -> list[MarketRateRow]:
    """相場CSVを読む。値域と間取りの正規化をここで確かめる。"""
    rows: list[MarketRateRow] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for lineno, raw in enumerate(csv.DictReader(fh), start=2):
            value = Decimal(raw["rate_value"])
            if not (MIN_RATE_YEN <= value <= MAX_RATE_YEN):
                raise MarketRateError(
                    f"{path.name}:{lineno} 相場が想定外の値です: {value}"
                    f"（{MIN_RATE_YEN:,}〜{MAX_RATE_YEN:,} 円の範囲を想定）"
                )
            stat_basis = raw["stat_basis"]
            if stat_basis not in ALLOWED_STAT_BASIS:
                raise MarketRateError(
                    f"{path.name}:{lineno} 想定外の stat_basis です: {stat_basis!r}"
                    f"（{sorted(ALLOWED_STAT_BASIS)} のいずれかを想定）"
                )
            segment = raw["segment"]
            # ⚠ 集計側と採点側で同じ正規化を通す。ここで正規化済みでない値を
            # 受け入れると、突き合わせが0件になったとき原因を切り分けられない
            if normalize_layout(segment) != segment:
                raise MarketRateError(
                    f"{path.name}:{lineno} 間取りが正規化されていません: {segment!r}"
                    f"（normalize_layout の結果は {normalize_layout(segment)!r}）"
                )
            rows.append(
                MarketRateRow(
                    city_jis=raw["city_jis"],
                    city_name=raw["city_name"],
                    segment=segment,
                    rate_value=value,
                    source=raw["source"],
                    stat_basis=stat_basis,
                    period=raw["period"],
                    acquired_on=dt.date.fromisoformat(raw["acquired_on"]),
                )
            )
    if not rows:
        raise MarketRateError(f"{path} に相場が1件もありません")
    return rows


# === 売買の相場（→ 課題#49）===================================================
# ⚠⚠ **賃貸と同じ関数で読まない。** segment は間取りではなく単価の区分、
# rate_value は月額ではなく㎡単価で、値域は桁が違う。ひとつの検証で通すと
# **片方の値域をもう片方に当てる**ことになり、例外にならないまま通ってしまう。

BUY_FAMILIES = frozenset({"MANSION_BUY", "KODATE_BUY"})

# 単価の区分。AREA_SQM=マンションの専有㎡単価 / LAND_SQM・FLOOR_SQM=戸建ての
# 土地・延床㎡単価。⚠ 掲載側のどの面積で割るかと必ず対にする
BUY_SEGMENTS = frozenset({"AREA_SQM", "LAND_SQM", "FLOOR_SQM"})

# 01=不動産取引価格情報 / 02=成約価格情報。⚠ **どちらで測ったかを行が持つ。**
# 同一市区で比べても水準が系統的に違い、しかも種別で向きが逆
# （成約÷取引の中央は マンション 0.914 / 戸建て 1.082。2026-09-08 実測）。
# 自由文字列にすると「どちらと比べた割安さか」を後から集計できない（→ ADR 0022 決定2）
BUY_STAT_BASIS = frozenset({"trade_unit_price_01", "trade_unit_price_02"})

# ⚠ ㎡単価としてありえない値を弾く。実測の市区中央値は
# 7,447〜5,692,308 円/㎡（戸建て土地）に収まっていた
MIN_BUY_RATE_YEN = 5_000
MAX_BUY_RATE_YEN = 20_000_000

# ⚠ 薄いセルは相場と呼べない。生成側（build_buy_market_rates.py）で落としてあるが、
# 読み込みでも確かめる。0 で埋めた行が紛れ込むと「相場ちょうど」と区別がつかない
MIN_BUY_SAMPLES = 10

# ⚠ 賃貸のCSVを渡されたときに KeyError ではなく「列が違う」と言えるようにする。
#    取り違えは実際に起こりうるうえ、KeyError だけでは原因が読み取れない
BUY_COLUMNS = (
    "family",
    "city_jis",
    "city_name",
    "segment",
    "rate_value",
    "sample_count",
    "source",
    "stat_basis",
    "period",
    "acquired_on",
)


def _bad(path: Path, lineno: int, message: str) -> MarketRateError:
    """CSVの行番号を添えたエラーを作る。⚠ どの行が壊れているかを必ず言う。"""
    return MarketRateError(f"{path.name}:{lineno} {message}")


def load_buy_rate_rows(path: Path) -> list[MarketRateRow]:
    """売買の相場CSV（市区 × 単価区分の㎡単価）を読む。

    ⚠ 賃貸の ``load_rate_rows`` とは検証がすべて違うので分けてある。
    """
    rows: list[MarketRateRow] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in BUY_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise MarketRateError(
                f"{path.name}: 売買の相場CSVに必要な列がありません: {missing}"
                "（賃貸のCSVを渡していないか確認してください）"
            )
        for lineno, raw in enumerate(reader, start=2):
            family = raw["family"]
            if family not in BUY_FAMILIES:
                raise _bad(
                    path,
                    lineno,
                    f"想定外の family です: {family!r}（{sorted(BUY_FAMILIES)} を想定）",
                )
            segment = raw["segment"]
            if segment not in BUY_SEGMENTS:
                raise _bad(
                    path,
                    lineno,
                    f"想定外の segment です: {segment!r}（{sorted(BUY_SEGMENTS)} を想定）",
                )
            stat_basis = raw["stat_basis"]
            if stat_basis not in BUY_STAT_BASIS:
                raise _bad(
                    path,
                    lineno,
                    f"想定外の stat_basis です: {stat_basis!r}（{sorted(BUY_STAT_BASIS)} を想定）",
                )
            value = Decimal(raw["rate_value"])
            if not (MIN_BUY_RATE_YEN <= value <= MAX_BUY_RATE_YEN):
                raise _bad(
                    path,
                    lineno,
                    f"㎡単価が想定外の値です: {value}"
                    f"（{MIN_BUY_RATE_YEN:,}〜{MAX_BUY_RATE_YEN:,} 円/㎡ を想定）",
                )
            samples = int(raw["sample_count"])
            if samples < MIN_BUY_SAMPLES:
                raise _bad(
                    path,
                    lineno,
                    f"サンプルが少なすぎます: {samples}件（{MIN_BUY_SAMPLES}件以上を想定）",
                )
            rows.append(
                MarketRateRow(
                    city_jis=raw["city_jis"],
                    city_name=raw["city_name"],
                    segment=segment,
                    rate_value=value,
                    source=raw["source"],
                    stat_basis=stat_basis,
                    period=raw["period"],
                    acquired_on=dt.date.fromisoformat(raw["acquired_on"]),
                    family=family,
                    sample_count=samples,
                )
            )
    if not rows:
        raise MarketRateError(f"{path} に相場が1件もありません")
    return rows


_UPSERT = text(
    """
    INSERT INTO m_market_rates (
        family, source, level, city_id, segment, stat_basis,
        rate_value, sample_count, period, acquired_on, created_at, updated_at
    ) VALUES (
        :family, :source, 'city', :city_id, :segment, :stat_basis,
        :rate_value, :sample_count, :period, :acquired_on, now(), now()
    )
    ON CONFLICT (family, source, level, city_id, segment, period) DO UPDATE SET
        stat_basis = EXCLUDED.stat_basis,
        rate_value = EXCLUDED.rate_value,
        sample_count = EXCLUDED.sample_count,
        acquired_on = EXCLUDED.acquired_on,
        updated_at = now()
    RETURNING (xmax = 0) AS inserted
    """
)


def sync_market_rates(
    conn: Connection, rows: list[MarketRateRow], *, family: str = "CHINTAI"
) -> SyncResult:
    """相場を ``m_market_rates`` へ upsert する。

    ⚠ **履歴を消さない**（``period`` が違えば別の行として残る）。
    「いつの相場で採点したか」を後から言えるようにするため。
    """
    city_ids = {
        jis: cid
        for jis, cid in conn.execute(
            text("SELECT jis_code, id FROM m_cities WHERE jis_code IS NOT NULL")
        ).all()
    }
    inserted = updated = 0
    unresolved: set[str] = set()
    for row in rows:
        city_id = city_ids.get(row.city_jis)
        if city_id is None:
            unresolved.add(f"{row.city_name}({row.city_jis})")
            continue
        result = conn.execute(
            _UPSERT,
            {
                # ⚠ 行が family を持つならそちらを使う。同じタプルから来る値の
                # 片方だけを書くと、列と中身が食い違ったまま固定される（→ 課題#48）
                "family": row.family or family,
                "source": row.source,
                "city_id": city_id,
                "segment": row.segment,
                "stat_basis": row.stat_basis,
                "rate_value": row.rate_value,
                "sample_count": row.sample_count,
                "period": row.period,
                "acquired_on": row.acquired_on,
            },
        ).scalar_one()
        if result:
            inserted += 1
        else:
            updated += 1
    return SyncResult(inserted=inserted, updated=updated, unresolved_cities=sorted(unresolved))
