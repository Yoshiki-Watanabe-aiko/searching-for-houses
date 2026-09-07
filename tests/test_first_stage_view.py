"""1段目（一覧だけ）の MUST 判定に、判定できるはずの列がすべて渡っているか。

⚠ ``scan._listing_view`` は ``ScrapedListing`` → ``ListingView`` の写しを**手で列挙**している。
列を増やしたときにここへ足し忘れると、その列の MUST は1段目で **unknown → keep で通り**、
2段目（詳細取得後）で fail する掲載に詳細リクエストを使う。⚠ 例外にならず件数も減らない。
実測（2026-09-07・中古一戸建て）で ``land_area_sqm`` / ``building_area_sqm`` が抜けており、
土地34.89㎡の掲載が1段目を通って詳細を取ったあと2段目で落ちていた。

``tests/test_persist.py`` が ``ScrapedListing`` と ``_UPSERT`` を突き合わせるのと同じ考え方で、
「一覧で判定できる（``available_on_list=True``）MUST 項目の入力列」を
``MUST_ITEMS`` から機械的に引き、``_listing_view`` が写していることを固定する。
"""

from __future__ import annotations

import dataclasses

from house_search.config.metrics import MUST_ITEMS
from house_search.pipeline.scan import _listing_view
from house_search.scrape.base import ScrapedListing

_SCRAPED_FIELDS = {f.name for f in dataclasses.fields(ScrapedListing)}


def _list_stage_columns() -> set[str]:
    """一覧だけで判定できる MUST 項目の入力列（``ScrapedListing`` が持つものに限る）。

    ``rent_total`` のような生成列は ``ScrapedListing`` に無いので対象外
    （``ListingView`` 側で ``price`` と ``mgmt_fee_monthly`` から導く）。
    """
    columns = {
        column
        for spec in MUST_ITEMS
        if spec.available_on_list
        for column in spec.source_columns
    }
    return columns & _SCRAPED_FIELDS


def test_一覧で判定できるMUSTの入力列は1段目のビューへ写される() -> None:
    columns = _list_stage_columns()
    assert {"land_area_sqm", "building_area_sqm", "price", "walk_minutes"} <= columns

    # 列ごとに違う値を入れ、写し漏れ（None のまま）と取り違え（別の列の値）の両方を検出する
    values: dict[str, object] = {}
    for index, column in enumerate(sorted(columns), start=1):
        field_type = next(f.type for f in dataclasses.fields(ScrapedListing) if f.name == column)
        values[column] = float(index) if "float" in str(field_type) else index
    scraped = ScrapedListing(site_code="SUUMO", external_id="x", url="https://example.test/x")
    scraped = dataclasses.replace(scraped, **values)

    view = _listing_view(scraped)
    missing = {
        column: (getattr(scraped, column), getattr(view, column))
        for column in columns
        if getattr(view, column) != getattr(scraped, column)
    }
    assert not missing, f"_listing_view が写していない MUST の入力列: {missing}"
