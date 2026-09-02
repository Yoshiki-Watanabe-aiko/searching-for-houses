"""LIFULL HOME'S アダプタのテスト（実HTMLフィクスチャ方式）。

``tests/fixtures/homes/`` に保存した実ページを使い、ネットワーク無しで
一覧・詳細のパースを回帰テストする。DOM構造が変わったらここが落ちる。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from house_search.config.pattern import parse_pattern
from house_search.scrape.area import AreaTarget
from house_search.scrape.base import prefecture_targets
from house_search.scrape.homes import PAGE_SIZE, HomesScraper

FIXTURES = Path(__file__).parent / "fixtures" / "homes"


@pytest.fixture(scope="module")
def scraper() -> HomesScraper:
    return HomesScraper()


@pytest.fixture(scope="module")
def listings(scraper: HomesScraper):
    return scraper.parse_list((FIXTURES / "list_page1.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail(scraper: HomesScraper):
    return scraper.parse_detail((FIXTURES / "detail_sample.html").read_text(encoding="utf-8"))


def _pattern(**search_overrides):
    return parse_pattern(
        {
            "name": "t",
            "property_type": "CHINTAI",
            "webhook_ref": "T",
            "sites": ["HOMES"],
            "search": {"prefectures": ["東京都", "千葉県"], **search_overrides},
        }
    )


# --- URL構築 -------------------------------------------------------------


def test_都道府県単位のURLはローマ字スラグを使う(scraper: HomesScraper) -> None:
    pattern = _pattern()
    urls = scraper.list_urls(pattern, prefecture_targets(pattern.search.prefectures))
    assert urls[0].startswith("https://www.homes.co.jp/chintai/tokyo/list/")
    assert urls[1].startswith("https://www.homes.co.jp/chintai/chiba/list/")


def test_市区指定はマッピング値をそのままパスに使う(scraper: HomesScraper) -> None:
    # HOME'S の市区値は都道府県スラグを含む（tokyo/chiyoda-city）
    areas = [AreaTarget(prefecture="東京都", city_name="千代田区", value="tokyo/chiyoda-city")]
    url = scraper.list_urls(_pattern(), areas)[0]
    assert url.startswith("https://www.homes.co.jp/chintai/tokyo/chiyoda-city/list/")


def test_価格上限は万円で渡り新着順で並ぶ(scraper: HomesScraper) -> None:
    pattern = _pattern(price_max_hint=90000)
    url = scraper.list_urls(pattern, prefecture_targets(pattern.search.prefectures))[0]
    assert "monthmoneyroomh" in url
    assert "9.0" in url
    assert "newdate" in url


def test_設備条件のパラメータを含めない(scraper: HomesScraper) -> None:
    # v2 でサイトへ渡すのはエリア・種別・価格上限だけ（→ ADR 0003）
    pattern = _pattern(price_max_hint=90000)
    url = scraper.list_urls(pattern, prefecture_targets(pattern.search.prefectures))[0]
    assert "mcf" not in url
    assert "madori" not in url
    assert "houseage" not in url


def test_未知の都道府県はエラーになる(scraper: HomesScraper) -> None:
    with pytest.raises(ValueError, match="未知の都道府県"):
        scraper.list_urls(_pattern(), [AreaTarget(prefecture="架空県")])


def test_ページ番号の付与と最終ページ判定(scraper: HomesScraper) -> None:
    assert scraper.page_url("https://x/list/?a=1", 3).endswith("&page=3")
    assert scraper.is_last_page(PAGE_SIZE - 1) is True
    assert scraper.is_last_page(PAGE_SIZE) is False


# --- 一覧パース ----------------------------------------------------------


def test_建物ごとに複数住戸を展開する(listings) -> None:
    assert len(listings) > 30


def test_仲介業者の行を掲載として拾わない(listings) -> None:
    # tr.prg-room には業者名だけの memberDataRow も混ざる。
    # data-kykey を持たないので掲載としては数えない
    assert all(x.external_id for x in listings)
    assert all(x.title for x in listings)


def test_物件IDは掲載ごとに一意(listings) -> None:
    ids = [x.external_id for x in listings]
    assert len(ids) == len(set(ids))


def test_一覧から必須項目が取れる(listings) -> None:
    for field in ("price", "area_sqm", "layout", "age_years", "walk_minutes", "address"):
        missing = [x for x in listings if getattr(x, field) is None]
        assert not missing, f"{field} が取れない掲載が {len(missing)} 件あります"


def test_管理費と敷金礼金が取れる(listings) -> None:
    for field in ("mgmt_fee_monthly", "deposit_amount", "key_money_amount"):
        assert all(getattr(x, field) is not None for x in listings), field


def test_敷金礼金の月数表記を円へ換算する(listings) -> None:
    # HOME'S は「1ヶ月」表記。賃料と同額になる掲載が必ずある
    assert any(x.deposit_amount == x.price for x in listings if x.price)


def test_築年数は築の字が無くても読める(listings) -> None:
    # HOME'S は「8年 / 8階建」と書き「築」を付けない
    assert all(x.age_years is not None for x in listings)
    assert any(x.age_years and x.age_years > 0 for x in listings)


def test_詳細URLは絶対URLになる(listings) -> None:
    assert all(x.url.startswith("https://www.homes.co.jp/") for x in listings)


def test_サムネイルにデータURIやプレースホルダを拾わない(listings) -> None:
    for listing in listings:
        assert not (listing.image_url or "").startswith("data:")
        assert "loading_24x24" not in (listing.image_url or "")


def test_サイトコードが入る(listings) -> None:
    assert {x.site_code for x in listings} == {"HOMES"}


# --- 詳細パース ----------------------------------------------------------


def test_詳細から設備原文が取れる(detail) -> None:
    assert detail.raw_features_text
    assert "オートロック" in detail.raw_features_text


def test_非該当の条件を原文に載せない(detail) -> None:
    # 人気条件アイコンは非該当も同じマークアップで並ぶ。
    # sr-only の「(該当)」で選別しないと辞書が非該当まで拾う
    assert "非該当" not in detail.raw_features_text
    assert "新築" not in detail.raw_features_text


def test_口コミを詳細項目として拾わない(detail) -> None:
    # 口コミも dt/dd を使うため、ラベルの白名簿で絞らないと住所が汚れる
    assert detail.address == "東京都千代田区飯田橋１丁目12-12"


def test_住所から地図リンクの文言を落とす(detail) -> None:
    assert "地図" not in (detail.address or "")


def test_詳細から築年月と階数が取れる(detail) -> None:
    assert detail.built_on == dt.date(2019, 7, 1)
    assert detail.floor_num == 8
    assert detail.total_floors == 8


def test_詳細から管理費と敷金礼金が取れる(detail) -> None:
    assert detail.mgmt_fee_monthly == 20000
    assert detail.deposit_amount == 270000
    assert detail.key_money_amount == 270000


@pytest.mark.parametrize(
    ("hint", "expected"),
    [(90000, "=9.0"), (156000, "=16.0"), (93000, "=9.5"), (91000, "=9.5")],
)
def test_賃料上限は05刻みへ切り上げる(scraper: HomesScraper, hint, expected) -> None:
    """セレクトの選択肢が 0.5 刻みなので、端数のまま渡すと掲載0件になる。

    SUUMO の ``ct`` で同じ事故を実測している（→ 課題#29）。
    従来のコメントは「丸める」と書いていたが実際は丸めておらず、
    運用中の 90,000 円が偶然 9.0 になっていたため表面化していなかった。
    """
    p = _pattern(price_max_hint=hint)
    url = scraper.list_urls(p, prefecture_targets(p.search.prefectures))[0]
    assert f"monthmoneyroomh%5D{expected}" in url


# ---- スロットリング／ボットチャレンジ（→ 課題#17） ----


def test_空ボディを例外にする(scraper: HomesScraper) -> None:
    """⚠ HOME'S は絞られると HTTP 202 ＋ 空ボディを返す（実測 2026-09-03）。

    ``SiteFetcher`` は 400 未満を成功として返すので、ここで判別しないと
    「取得できたつもりで0件」になり、エラーにもならない。
    """
    with pytest.raises(ValueError, match="応答が空です"):
        scraper.parse_list("")


def test_AWS_WAFのチャレンジを例外にする(scraper: HomesScraper) -> None:
    with pytest.raises(ValueError, match="AWS WAF"):
        scraper.parse_list("<html><script>var gokuProps = {};</script></html>")


def test_サイト側フィルタへの対応を宣言している(scraper: HomesScraper) -> None:
    """宣言しないと scan がクエリを足さない（→ ADR 0015）。"""
    assert scraper.supports_site_filters is True
