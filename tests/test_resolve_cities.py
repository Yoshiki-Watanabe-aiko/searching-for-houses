"""``resolve-cities``（既存掲載の市区引き直し）のDB統合テスト。

⚠ **純関数の ``resolve_city`` が正しくても、配線が値を捨てれば意味が無い。**
初版は ``resolve_city`` が返す都道府県を ``_prefecture`` として捨て、city_id だけを
更新していた。その結果 `東京都立川市…` なのに `prefecture='長野県'` という掲載が
実データに残り、``normalize_base`` がその列を住所へ前置して
`長野県東京都立川市…` という**実在しない住所**を `dedup_key` にしていた
（→ 課題#48）。⚠ 例外にも件数の減少にもならず、名寄せが静かに失敗するだけ。
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import text

_INSERT = text(
    """
    INSERT INTO t_listings (
        site_id, property_type_id, external_id, url, title,
        address, prefecture, city_id, status,
        first_seen_at, last_seen_at, created_at, updated_at
    ) VALUES (
        :site_id, :type_id, :external_id, 'https://example.com/x', 'テスト',
        :address, :prefecture, NULL, 'active',
        now(), now(), now(), now()
    ) RETURNING id
    """
)


def test_市区の引き直しは都道府県も一緒に直す(test_engine) -> None:
    from house_search.pipeline.tasks import resolve_cities

    with test_engine.begin() as conn:
        site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
        type_id = conn.execute(
            text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
        ).scalar_one()
        listing_id = conn.execute(
            _INSERT,
            {
                "site_id": site_id,
                "type_id": type_id,
                "external_id": "resolve-cities-test-1",
                # 住所は正しいのに prefecture 列だけ別の県、という実データと同じ形
                "address": "東京都立川市富士見町4丁目",
                "prefecture": "長野県",
            },
        ).scalar_one()
    try:
        runtime = SimpleNamespace(engine=test_engine)
        patterns = [SimpleNamespace(search=SimpleNamespace(prefectures=["東京都"]))]
        resolve_cities(runtime, patterns)  # type: ignore[arg-type]

        with test_engine.connect() as conn:
            row = conn.execute(
                text("SELECT prefecture, city_id FROM t_listings WHERE id = :id"),
                {"id": listing_id},
            ).one()
        assert row.prefecture == "東京都", "prefecture が住所と食い違ったまま残っている"
        assert row.city_id is not None
    finally:
        with test_engine.begin() as conn:
            conn.execute(text("DELETE FROM t_listings WHERE id = :id"), {"id": listing_id})
