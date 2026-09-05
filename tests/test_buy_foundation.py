"""Phase 6（売買）の土台 — 売買データが入る前に塞ぐ欠陥の回帰テスト。

ここで固定しているのは、いずれも **売買掲載が1件でも入った瞬間に黙って発火する**
欠陥である（例外にならず、件数も減らず、値だけが狂う）。賃貸だけで動かしている
限り表面化しないので、**売買アダプタを書く前に**塞いでおく（→ 課題#4）。

1. 既存2パターンの ``config_hash`` が変わらないこと（売買追加で賃貸を壊さない担保）
2. 再抽出が**掲載ごとの種別**で辞書を選ぶこと（固定だと売買が賃貸辞書で抽出される）
3. 売買辞書がマンション・戸建ての**両ファミリ**へ展開されること（戸建てが抽出0件になる）
4. 通知の金額欄が**ファミリで意味を変える**こと（売買で物件価格が賃料として出る）
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from house_search.config.pattern import load_patterns
from house_search.config.settings import load_settings
from house_search.extract.dictionary import load_dictionary
from house_search.notify.format import NotifiableListing, price_field, price_summary
from house_search.pipeline import tasks

# 2026-09-05 に実測した現在値。⚠ **売買の追加でこれが変わってはいけない。**
# 変わったら「賃貸パターンの採点が変わった」ということなので、
# `rescore` が全件走り、通知と順位が動く。
# 意図してスコア設定（want / commute / property_type）を変えたときだけ更新する。
BASELINE_CONFIG_HASH = {
    "東京23区賃貸": "5791d4f05f6e496973d82e9b178f076ff56ee26e4b7fbd8091ee43fbeb1cc916",
    "近郊60分圏賃貸": "9d182e3f30be4445201e8fe4f696d62fef994ecf18f6630e227c42990f80efbe",
}


def test_既存2パターンのconfig_hashが変わらない() -> None:
    """稼働中の賃貸2パターンのスコア設定が変わっていないことを固定する。"""
    patterns = {p.name: p for p in load_patterns(load_settings().configs_dir)}
    for name, expected in BASELINE_CONFIG_HASH.items():
        assert name in patterns, f"検索パターン '{name}' が見つかりません"
        assert patterns[name].config_hash() == expected, (
            f"'{name}' の config_hash が変わっています。"
            "スコア設定を意図して変えたのでなければ、賃貸を壊す変更が入っています"
        )


def test_売買辞書はマンションと戸建ての両ファミリへ展開される(tmp_path: Path) -> None:
    """``buy`` セクションは MANSION_BUY と KODATE_BUY の両方に効かせる。

    ⚠ 片方しか作らないと、そのファミリの掲載は**照合先の辞書が空集合**になり、
    詳細から原文を保存しても**抽出0件のまま正常終了する**。
    """
    path = tmp_path / "dict.yaml"
    path.write_text(
        "chintai:\n"
        "  BATH_SEPARATE:\n"
        "    patterns: ['バス・トイレ別']\n"
        "buy:\n"
        "  CERT_FLAT35:\n"
        "    patterns: ['フラット35適合']\n",
        encoding="utf-8",
    )
    families = {e.family for e in load_dictionary(path).entries if e.code == "CERT_FLAT35"}
    assert families == {"MANSION_BUY", "KODATE_BUY"}
    # 賃貸ブロックは賃貸だけに効く（売買へ漏らさない）。
    chintai = {e.family for e in load_dictionary(path).entries if e.code == "BATH_SEPARATE"}
    assert chintai == {"CHINTAI"}


def test_再抽出は掲載ごとの種別で辞書を選ぶ(test_engine) -> None:
    """⚠ 固定の family で再抽出すると**売買掲載が賃貸辞書で抽出される**。

    設備数もエラーも異常を示さないので、実データを見るまで気づけない。
    ここでは行取得の段で種別が引けていることを固定する
    （`re_extract` はこの行の `property_family` をそのまま辞書の選択に使う）。
    """
    with test_engine.connect() as conn:
        trans = conn.begin()
        try:
            site_id = conn.execute(
                text("SELECT id FROM m_sites WHERE code = 'SUUMO'")
            ).scalar_one()
            types = dict(
                conn.execute(
                    text(
                        "SELECT code, id FROM m_property_types "
                        "WHERE code IN ('CHINTAI', 'CHUKO_MANSION')"
                    )
                ).all()
            )
            ids = []
            for code, external in (("CHINTAI", "buy-test-1"), ("CHUKO_MANSION", "buy-test-2")):
                ids.append(
                    conn.execute(
                        text(
                            """
                            INSERT INTO t_listings (
                                site_id, property_type_id, external_id, url, title,
                                raw_features_text, status,
                                first_seen_at, last_seen_at, created_at, updated_at
                            ) VALUES (
                                :site_id, :type_id, :external_id, :url, '再抽出テスト',
                                'オートロック', 'active', now(), now(), now(), now()
                            ) RETURNING id
                            """
                        ),
                        {
                            "site_id": site_id,
                            "type_id": types[code],
                            "external_id": external,
                            "url": f"https://example.test/{external}",
                        },
                    ).scalar_one()
                )

            rows = {r.id: r for r in tasks.re_extract_rows(conn, limit=None)}
            assert rows[ids[0]].property_family == "CHINTAI"
            assert rows[ids[1]].property_family == "MANSION_BUY"

            # ファミリを指定したときは、そのファミリの掲載だけに絞る。
            only_buy = [r.id for r in tasks.re_extract_rows(conn, limit=None, family="MANSION_BUY")]
            assert ids[1] in only_buy
            assert ids[0] not in only_buy
        finally:
            trans.rollback()


def _listing(family: str) -> NotifiableListing:
    """中古マンション相当の値。⚠ `rent_total` は生成列なので**売買でも値が入る**。"""
    return NotifiableListing(
        listing_id=1,
        site_code="SUUMO",
        url="https://example.test/1",
        title="テストマンション",
        price=35_000_000,
        mgmt_fee_monthly=12_000,
        rent_total=35_012_000,
        layout="3LDK",
        area_sqm=70.0,
        age_years=10,
        walk_minutes=8,
        address="東京都文京区湯島１",
        repair_reserve_monthly=8_000,
        property_family=family,
    )


def test_売買の通知は物件価格を賃料として出さない() -> None:
    """⚠ `rent_total` は生成列 `price + 管理費` なので**売買でも値が入る**。

    賃貸前提のまま表示すると、中古マンションのダイジェストに
    「35,012,000円」が**賃料**として並び、誰も異常と思わない。
    """
    name, value = price_field(_listing("MANSION_BUY"))
    assert name == "価格"
    assert "賃料" not in value
    assert "35,012,000" not in value  # 物件価格＋管理費という無意味な合計を出さない
    assert "3,500万円" in value
    # 月々の負担は管理費＋修繕積立金でまとめて出す（売買の実質的な固定費）。
    assert "20,000円" in value

    summary = price_summary(_listing("MANSION_BUY"))
    assert "3,500万円" in summary
    assert "35,012,000" not in summary


def test_賃貸の通知はこれまでどおり月額を出す() -> None:
    """売買対応で賃貸の表示を変えていないことを固定する。"""
    rent = NotifiableListing(
        listing_id=2,
        site_code="SUUMO",
        url="https://example.test/2",
        title="テストアパート",
        price=78_000,
        mgmt_fee_monthly=3_000,
        rent_total=81_000,
        layout="1LDK",
        area_sqm=40.0,
        age_years=21,
        walk_minutes=17,
        address="東京都足立区１",
        property_family="CHINTAI",
    )
    name, value = price_field(rent)
    assert name == "月額"
    assert "81,000円" in value
    assert "賃料 78,000円" in value
    assert price_summary(rent) == "81,000円"


def test_ファミリ不明の通知は賃貸として扱う() -> None:
    """⚠ 既定は賃貸。稼働中の経路が `property_family` を渡し忘れても表示が壊れない。"""
    prop = NotifiableListing(
        listing_id=3,
        site_code="SUUMO",
        url="https://example.test/3",
        title="種別不明",
        price=78_000,
        mgmt_fee_monthly=3_000,
        rent_total=81_000,
        layout="1LDK",
        area_sqm=40.0,
        age_years=21,
        walk_minutes=17,
        address="東京都足立区１",
    )
    assert price_field(prop)[0] == "月額"


def test_価格未定の売買は価格未定と出す() -> None:
    """新築は価格未定がある。⚠ 0円やハイフンだけだと「安い」と誤読される。"""
    prop = NotifiableListing(
        listing_id=4,
        site_code="SUUMO",
        url="https://example.test/4",
        title="新築マンション",
        price=None,
        mgmt_fee_monthly=None,
        rent_total=None,
        layout="3LDK",
        area_sqm=70.0,
        age_years=None,
        walk_minutes=5,
        address="東京都文京区湯島１",
        property_family="MANSION_BUY",
    )
    assert "価格未定" in price_field(prop)[1]
    assert "価格未定" in price_summary(prop)
