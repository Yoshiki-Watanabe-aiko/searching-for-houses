"""閲覧画面をテストDBに対して通しで動かす（→ 課題#68・ADR 0026）。

⚠ 検索パターンは実運用の ``configs/chintai_23ku.yaml`` を**名前だけ変えて**一時ディレクトリへ
写して使う（実運用のパターン名でテストDBに行を作らない）。
⚠ アプリは自分で接続を開くので、テストが作った行は ``finally`` で消す。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from house_search.marks import marks_of
from house_search.web import EXTENSION_KEY, create_app
from house_search.web.context import build_context

pytestmark = pytest.mark.db

REPO = Path(__file__).resolve().parents[1]
PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"
PATTERN_NAME = "閲覧画面テスト"
SLUG = "web_test"
XSS_TITLE = "<script>alert('xss')</script>物件"


@pytest.fixture
def configs_dir(tmp_path: Path) -> Path:
    source = (REPO / "configs" / "chintai_23ku.yaml").read_text(encoding="utf-8")
    renamed, count = re.subn(r'^name: ".*"$', f'name: "{PATTERN_NAME}"', source, flags=re.M)
    assert count == 1
    (tmp_path / f"{SLUG}.yaml").write_text(renamed, encoding="utf-8")
    return tmp_path


def _insert(conn, *, rank: int, title: str, url: str, group_id: int | None = None) -> int:
    site_id = conn.execute(text("SELECT id FROM m_sites WHERE code = 'SUUMO'")).scalar_one()
    type_id = conn.execute(
        text("SELECT id FROM m_property_types WHERE code = 'CHINTAI'")
    ).scalar_one()
    listing_id = conn.execute(
        text(
            """
            INSERT INTO t_listings (
                site_id, property_type_id, external_id, url, title, price, mgmt_fee_monthly,
                area_sqm, layout, address, status, group_id, raw_features_text,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (
                :site_id, :type_id, :external_id, :url, :title, 90000, 5000,
                45.0, '2LDK', '東京都足立区千住1丁目', 'active', :group_id,
                '<img src=x onerror=alert(1)>エアコン',
                now(), now(), now(), now()
            ) RETURNING id
            """
        ),
        {
            "site_id": site_id,
            "type_id": type_id,
            "external_id": f"web-test-{uuid.uuid4().hex}",
            "url": url,
            "title": title,
            "group_id": group_id,
        },
    ).scalar_one()
    conn.execute(
        text(
            """
            INSERT INTO t_listing_scores (
                listing_id, pattern_name, score, score_breakdown, must_result,
                rank_in_pattern, config_hash, created_at, updated_at
            ) VALUES (
                :listing_id, :pattern_name, :score, CAST(:breakdown AS jsonb), 'pass', :rank,
                'test-hash', now(), now()
            )
            """
        ),
        {
            "listing_id": listing_id,
            "pattern_name": PATTERN_NAME,
            "score": 80 - rank,
            "rank": rank,
            "breakdown": (
                '[{"code": "walk_minutes", "name": "駅徒歩", "kind": "numeric", "weight": 10,'
                f' "s": 0.5, "points": 5, "status": "hit", "value": {rank * 5}}}]'
            ),
        },
    )
    return listing_id


@pytest.fixture
def seeded(test_engine: Engine) -> Iterator[list[int]]:
    with test_engine.begin() as conn:
        ids = [
            _insert(conn, rank=1, title=XSS_TITLE, url="javascript:alert(1)"),
            _insert(conn, rank=2, title="普通の物件", url="https://example.invalid/2"),
        ]
    try:
        yield ids
    finally:
        with test_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM t_listing_scores WHERE pattern_name = :name"),
                {"name": PATTERN_NAME},
            )
            conn.execute(text("DELETE FROM t_listings WHERE id = ANY(:ids)"), {"ids": ids})


@pytest.fixture
def client(test_engine: Engine, configs_dir: Path):
    context = build_context(test_engine, configs_dir=configs_dir, port=PORT, csrf_token="tkn")
    app = create_app(context)
    with app.test_client() as test_client:
        yield test_client


def _flag(client, listing_id: int, flag: str, value: str):
    return client.post(
        f"/listings/{listing_id}/flags",
        base_url=BASE_URL,
        data={"flag": flag, "value": value, "csrf_token": "tkn", "next": f"/patterns/{SLUG}"},
        headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
    )


def test_トップページにパターンが並ぶ(client, seeded: list[int]) -> None:
    response = client.get("/", base_url=BASE_URL)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert PATTERN_NAME in body


def test_ランキングは物件名をエスケープしjavascriptをリンクにしない(
    client, seeded: list[int]
) -> None:
    response = client.get(f"/patterns/{SLUG}", base_url=BASE_URL)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<script>alert" not in body
    assert "&lt;script&gt;alert" in body
    assert 'href="javascript:' not in body
    assert "普通の物件" in body


def test_絞り込みは採点に使った徒歩の値で効く(client, seeded: list[int]) -> None:
    response = client.get(f"/patterns/{SLUG}?walk_max=5", base_url=BASE_URL)
    body = response.get_data(as_text=True)
    assert "普通の物件" not in body  # 徒歩10分（rank 2 × 5）
    assert "&lt;script&gt;" in body  # 徒歩5分


def test_不正な絞り込みは400で理由を出す(client, seeded: list[int]) -> None:
    response = client.get(f"/patterns/{SLUG}?sort=bad", base_url=BASE_URL)
    assert response.status_code == 400
    assert "sort" in response.get_data(as_text=True)


def test_詳細は設備原文もエスケープする(client, seeded: list[int]) -> None:
    response = client.get(f"/patterns/{SLUG}/listings/{seeded[0]}", base_url=BASE_URL)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "<img src=x" not in body
    assert "&lt;img src=x" in body


def test_種別の違うパターンからは詳細を開けない(client, seeded: list[int]) -> None:
    other = client.get(f"/patterns/unknown/listings/{seeded[0]}", base_url=BASE_URL)
    missing = client.get(f"/patterns/{SLUG}/listings/999999999", base_url=BASE_URL)
    assert other.status_code == 404
    assert missing.status_code == 404


def test_除外すると既定の一覧から隠れて切替で戻る(client, seeded: list[int]) -> None:
    response = _flag(client, seeded[1], "excluded", "1")
    assert response.status_code == 303
    assert response.headers["Location"].endswith(f"/patterns/{SLUG}")

    hidden = client.get(f"/patterns/{SLUG}", base_url=BASE_URL).get_data(as_text=True)
    shown = client.get(
        f"/patterns/{SLUG}?include_excluded=1", base_url=BASE_URL
    ).get_data(as_text=True)
    assert "普通の物件" not in hidden
    assert "普通の物件" in shown


def test_お気に入りだけに絞れる(client, seeded: list[int]) -> None:
    _flag(client, seeded[1], "favorite", "1")
    body = client.get(f"/patterns/{SLUG}?favorites=1", base_url=BASE_URL).get_data(as_text=True)
    assert "普通の物件" in body
    assert "&lt;script&gt;" not in body


def test_メモを保存して詳細に出す(client, seeded: list[int]) -> None:
    response = client.post(
        f"/listings/{seeded[1]}/mark",
        base_url=BASE_URL,
        data={
            "favorite": "1",
            "memo": "<b>内見したい</b>",
            "csrf_token": "tkn",
            "next": f"/patterns/{SLUG}/listings/{seeded[1]}",
        },
    )
    assert response.status_code == 303

    body = client.get(
        f"/patterns/{SLUG}/listings/{seeded[1]}", base_url=BASE_URL
    ).get_data(as_text=True)
    assert "&lt;b&gt;内見したい&lt;/b&gt;" in body


def test_メモの無い印を開き直して保存してもメモにNoneが入らない(client, seeded: list[int]) -> None:
    """⚠ 印の行があってメモが NULL のとき、メモ欄に「None」と出て、次の保存で書き込まれていた
    （→ 課題#68・2026-09-15 本番で実測）。ブラウザと同じく、画面のメモ欄の中身をそのまま送り返す。
    """
    detail = f"/patterns/{SLUG}/listings/{seeded[1]}"

    def save(excluded: bool) -> None:
        body = client.get(detail, base_url=BASE_URL).get_data(as_text=True)
        memo = re.search(r'<textarea name="memo"[^>]*>(.*?)</textarea>', body, re.S)
        assert memo is not None
        assert memo.group(1) == ""
        data = {"memo": memo.group(1), "csrf_token": "tkn", "next": detail}
        if excluded:
            data["excluded"] = "1"
        assert client.post(
            f"/listings/{seeded[1]}/mark", base_url=BASE_URL, data=data
        ).status_code == 303

    save(excluded=True)
    save(excluded=True)
    save(excluded=False)
    # 印をすべて外したら行ごと消える（メモに「None」が残ると消えずにメモ件数へ出る）
    engine = client.application.extensions[EXTENSION_KEY].engine
    with engine.connect() as conn:
        assert marks_of(conn, [seeded[1]]) == {}


def test_メモが上限を超えたら保存せず400(client, seeded: list[int]) -> None:
    response = client.post(
        f"/listings/{seeded[1]}/mark",
        base_url=BASE_URL,
        data={"memo": "あ" * 2001, "csrf_token": "tkn"},
    )
    assert response.status_code == 400
    assert "2000字" in response.get_data(as_text=True)


def test_存在しない掲載には印を付けない(client, seeded: list[int]) -> None:
    assert _flag(client, 999999999, "favorite", "1").status_code == 404


def test_閲覧は読み取り専用の接続で行う(test_engine: Engine) -> None:
    """⚠ GET の経路で誤って書いても DB 側で止まること（読み取り専用トランザクション）。"""
    with test_engine.connect() as conn:
        conn = conn.execution_options(postgresql_readonly=True)
        assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on"
