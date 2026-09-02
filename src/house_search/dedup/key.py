"""名寄せキー（``dedup_key``）の合成。

DBに触らない純関数。``sha256`` の hex 64桁を返し、``t_listings.dedup_key`` と
``t_listing_groups.dedup_key`` の双方に同じ値が入る。

**構成要素が1つでも欠けたら None を返す**（キーを作らない）。名寄せの誤爆は
ランキングから物件を1件消すことを意味し偽陽性のコストが高いため、
欠損はセンチネルで埋めずグループ化の対象外にして単独で残す。

実測（2026-09-02・301掲載）で確かめた設計上の判断は ADR 0012 を参照:

- **建物名は含めない。** 匿名掲載（「ＪＲ相模線 上溝駅 2階建 築41年」）が実在し、
  含めるとクロスサイト一致5件が全て分断される
- **築年月・総階数も含めない。** 含めると真の一致2件が分断され 5→4 に減る
- **面積は丸めない。** 丸めても一致は1件も増えず、同一建物の隣接住戸を余分に潰すだけ
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

from house_search.scoring.listing_view import normalize_layout

# キーの世代。正規化ルールを変えたらこれを上げ、``regroup`` で全再計算する
# （新旧キーが混在して半端に一致する事故を防ぐ）。
DEDUP_KEY_VERSION = "v1"

# ファミリごとのキー構成要素。再設計計画 §6 の定義そのまま。
FAMILY_CHINTAI = "CHINTAI"
FAMILY_MANSION_BUY = "MANSION_BUY"
FAMILY_KODATE_BUY = "KODATE_BUY"


def _area(value: object) -> str | None:
    """面積を小数第2位の固定表記にする（丸めない）。

    float でも Decimal でも同じ文字列になるようにして、キーの決定性を保つ。
    """
    if value is None:
        return None
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return None


def dedup_components(
    *,
    family: str,
    address_normalized: str | None,
    layout: str | None = None,
    area_sqm: object = None,
    floor_num: int | None = None,
    land_area_sqm: object = None,
    building_area_sqm: object = None,
) -> list[str] | None:
    """キーの構成要素を並べる。1つでも欠けたら None。

    ハッシュ化前の値を返すので、名寄せがなぜ一致した／しなかったかを
    テストやデバッグで目視できる。
    """
    if not address_normalized:
        return None

    if family == FAMILY_KODATE_BUY:
        # 戸建ては専有面積が存在せず土地・建物の2軸になる。
        # area_sqm を流用すると名寄せ事故になるので別の要素で組む。
        parts = [_area(land_area_sqm), _area(building_area_sqm), normalize_layout(layout)]
    else:
        parts = [normalize_layout(layout), _area(area_sqm), _int(floor_num)]

    if any(part is None for part in parts):
        return None
    return [DEDUP_KEY_VERSION, family, address_normalized, *parts]  # type: ignore[list-item]


def _int(value: int | None) -> str | None:
    return None if value is None else str(int(value))


def compute_dedup_key(
    *,
    family: str,
    address_normalized: str | None,
    layout: str | None = None,
    area_sqm: object = None,
    floor_num: int | None = None,
    land_area_sqm: object = None,
    building_area_sqm: object = None,
) -> str | None:
    """名寄せキーを作る。構成要素が欠けていれば None。"""
    components = dedup_components(
        family=family,
        address_normalized=address_normalized,
        layout=layout,
        area_sqm=area_sqm,
        floor_num=floor_num,
        land_area_sqm=land_area_sqm,
        building_area_sqm=building_area_sqm,
    )
    if components is None:
        return None
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()
