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

from house_search.scoring.listing_view import normalize_layout

# ⚠ 相場としてありえない値を弾く。桁を1つ間違えた（万円→円の換算漏れ）ときに
# 気づけるのはここだけ。実測では 4.4万〜30万円の範囲に収まっていた
MIN_RATE_YEN = 10_000
MAX_RATE_YEN = 2_000_000


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
                    stat_basis=raw["stat_basis"],
                    period=raw["period"],
                    acquired_on=dt.date.fromisoformat(raw["acquired_on"]),
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
        :rate_value, NULL, :period, :acquired_on, now(), now()
    )
    ON CONFLICT (family, source, level, city_id, segment, period) DO UPDATE SET
        stat_basis = EXCLUDED.stat_basis,
        rate_value = EXCLUDED.rate_value,
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
                "family": family,
                "source": row.source,
                "city_id": city_id,
                "segment": row.segment,
                "stat_basis": row.stat_basis,
                "rate_value": row.rate_value,
                "period": row.period,
                "acquired_on": row.acquired_on,
            },
        ).scalar_one()
        if result:
            inserted += 1
        else:
            updated += 1
    return SyncResult(
        inserted=inserted, updated=updated, unresolved_cities=sorted(unresolved)
    )
