"""通知メッセージの整形とDiscord送信のテスト。"""

from __future__ import annotations

import httpx
import pytest

from house_search.config.pattern import parse_pattern
from house_search.notify.discord import MAX_EMBEDS_PER_MESSAGE, DiscordSender
from house_search.notify.format import (
    COLORS,
    MAX_DESCRIPTION_CHARS,
    MAX_STATIONS_IN_NOTIFY,
    DigestEntry,
    NotifiableListing,
    access_lines,
    build_digest_message,
    build_error_message,
    build_listing_message,
)
from house_search.scoring.listing_view import ListingView, StationAccess
from house_search.scoring.score import calculate_score

PATTERN = parse_pattern(
    {
        "name": "東京賃貸",
        "property_type": "CHINTAI",
        "webhook_ref": "T",
        "sites": ["SUUMO"],
        "search": {"prefectures": ["東京都"]},
        "want": {
            "features": [
                {"code": "SEC_AUTOLOCK", "weight": 8},
                {"code": "INT_LAUNDRY", "weight": 10},
            ],
            "numeric": [{"metric": "rent_total", "weight": 10, "best": 50000, "worst": 70000}],
        },
        "ranking": {"top_n": 15},
    }
)


def make_prop(listing_id: int = 1, **overrides) -> NotifiableListing:
    defaults = {
        "listing_id": listing_id,
        "site_code": "SUUMO",
        "url": f"https://suumo.jp/chintai/jnc_{listing_id}/",
        "title": "テストマンション",
        "price": 58000,
        "mgmt_fee_monthly": 2000,
        "rent_total": 60000,
        "layout": "2DK",
        "area_sqm": 38.0,
        "age_years": 12,
        "walk_minutes": 8,
        "address": "東京都新宿区西新宿",
    }
    return NotifiableListing(**{**defaults, **overrides})


def make_score(detail_fetched: bool = True, codes: frozenset[str] = frozenset({"SEC_AUTOLOCK"})):
    view = ListingView(
        price=58000, mgmt_fee_monthly=2000, detail_fetched=detail_fetched, feature_codes=codes
    )
    return calculate_score(view, PATTERN.want, condition_names={})


# --- 個別通知 ------------------------------------------------------------


def test_新着通知の色とタイトル() -> None:
    embed = build_listing_message(
        make_prop(), make_score(), notification_type="new", pattern_name="東京賃貸"
    )["embeds"][0]
    assert embed["color"] == COLORS["new"]
    assert embed["title"] == "テストマンション"
    assert embed["url"].startswith("https://suumo.jp/")


def test_値下がり通知に差分が入る() -> None:
    prop = make_prop(price=55000, price_prev=60000)
    embed = build_listing_message(
        prop, make_score(), notification_type="price_down", pattern_name="東京賃貸"
    )["embeds"][0]
    values = " ".join(field["value"] for field in embed["fields"])
    assert "60,000円 → 55,000円" in values
    assert "-5,000円" in values
    assert embed["color"] == COLORS["price_down"]


def test_順位とスコアが入る() -> None:
    embed = build_listing_message(
        make_prop(),
        make_score(),
        notification_type="new",
        pattern_name="東京賃貸",
        rank_in_pattern=3,
    )["embeds"][0]
    score_field = embed["fields"][0]["value"]
    assert "パターン内 3位" in score_field
    assert "/ 100" in score_field


def test_未確認項目数がフッタに出る() -> None:
    embed = build_listing_message(
        make_prop(),
        make_score(detail_fetched=False),
        notification_type="new",
        pattern_name="東京賃貸",
    )["embeds"][0]
    # 設備2件が未確認 → 判断材料として件数を出す
    assert "未確認 2項目" in embed["footer"]["text"]


def test_全て確認済みなら未確認の表記を出さない() -> None:
    score = make_score(codes=frozenset({"SEC_AUTOLOCK", "INT_LAUNDRY"}))
    embed = build_listing_message(
        make_prop(), score, notification_type="new", pattern_name="東京賃貸"
    )["embeds"][0]
    assert "未確認" not in embed["footer"]["text"]


def test_サムネイルは画像がある時だけ付く() -> None:
    with_image = build_listing_message(
        make_prop(image_url="https://img/x.jpg"),
        make_score(),
        notification_type="new",
        pattern_name="P",
    )["embeds"][0]
    assert with_image["thumbnail"]["url"] == "https://img/x.jpg"
    without = build_listing_message(
        make_prop(), make_score(), notification_type="new", pattern_name="P"
    )["embeds"][0]
    assert "thumbnail" not in without


def test_物件名が無くても落ちない() -> None:
    embed = build_listing_message(
        make_prop(title=None), make_score(), notification_type="new", pattern_name="P"
    )["embeds"][0]
    assert embed["title"] == "（物件名なし）"


# --- ダイジェスト --------------------------------------------------------


def _entries(count: int) -> list[DigestEntry]:
    return [
        DigestEntry(rank=i, prop=make_prop(i), score=make_score()) for i in range(1, count + 1)
    ]


def test_ダイジェストは1embedにまとまる() -> None:
    message = build_digest_message(_entries(15), pattern_name="東京賃貸")
    # 上位15件は embed 10個/メッセージの上限を超えるためテキスト表にする
    assert len(message["embeds"]) == 1
    description = message["embeds"][0]["description"]
    assert description.count("https://suumo.jp/") == 15


def test_ダイジェストがDiscordの文字数上限を超えない() -> None:
    message = build_digest_message(_entries(200), pattern_name="東京賃貸")
    description = message["embeds"][0]["description"]
    assert len(description) <= MAX_DESCRIPTION_CHARS
    assert description.endswith("…（以降は文字数上限のため省略）")


def test_該当なしでも送れる形になる() -> None:
    description = build_digest_message([], pattern_name="東京賃貸")["embeds"][0]["description"]
    assert "条件に合う物件がありませんでした" in description


def test_digest_groupは見出しに出る() -> None:
    title = build_digest_message(
        _entries(2), pattern_name="中古M", digest_group="住み替え"
    )["embeds"][0]["title"]
    assert "[住み替え]" in title


def test_エラー通知は赤で組み立てる() -> None:
    embed = build_error_message(title="SUUMO 取得失敗", detail="HTTP 503")["embeds"][0]
    assert embed["color"] == COLORS["sold"]
    assert "SUUMO 取得失敗" in embed["title"]


# --- 送信 ----------------------------------------------------------------


def _sender(handler) -> DiscordSender:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return DiscordSender(client=client, interval_sec=0.0, sleep=lambda _: None)


def test_送信成功でTrueを返す() -> None:
    sender = _sender(lambda request: httpx.Response(204))
    assert sender.send("https://discord/webhook", {"content": "x"}) is True


def test_送信失敗は例外にせずFalseを返す() -> None:
    # 1件の通知失敗で実行全体を止めないため、呼び出し側が failed として記録できるようにする
    sender = _sender(lambda request: httpx.Response(400))
    assert sender.send("https://discord/webhook", {"content": "x"}) is False


def test_429はretry_afterに従って再送する() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"retry_after": 0.01})
        return httpx.Response(204)

    assert _sender(handler).send("https://discord/webhook", {"content": "x"}) is True
    assert len(calls) == 2


def test_通信エラーもFalseで返る() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    assert _sender(handler).send("https://discord/webhook", {"content": "x"}) is False


def test_embed数の上限を超えたら送る前に落とす() -> None:
    sender = _sender(lambda request: httpx.Response(204))
    payload = {"embeds": [{"title": str(i)} for i in range(MAX_EMBEDS_PER_MESSAGE + 1)]}
    with pytest.raises(ValueError, match="embed"):
        sender.send("https://discord/webhook", payload)


# --- 交通欄（駅ごとの徒歩・通勤） ---------------------------------------
# ⚠ 「徒歩10分」だけではどの駅からか分からない、というユーザー報告（2026-09-07）。
# ⚠ **徒歩が最小の駅と通勤が最短の駅は別になりうる**（採点はそれぞれの最小を採る）。


def _prop_with_stations(stations: tuple[StationAccess, ...], **kwargs: object):
    from house_search.notify.format import NotifiableListing

    base = dict(
        listing_id=1,
        site_code="SUUMO",
        url="https://example.invalid/1",
        title="テスト物件",
        price=70000,
        mgmt_fee_monthly=3000,
        rent_total=73000,
        layout="2DK",
        area_sqm=40.0,
        age_years=10,
        walk_minutes=4,
        address="東京都大田区北千束１",
        stations=stations,
    )
    base.update(kwargs)
    return NotifiableListing(**base)  # type: ignore[arg-type]


def test_交通欄は駅ごとに徒歩と通勤を並べる() -> None:
    prop = _prop_with_stations(
        (
            StationAccess("大岡山", walk_minutes=4, commute_minutes=18),
            StationAccess("北千束", walk_minutes=7, commute_minutes=22),
        ),
        commute_destination="芝公園",
    )
    assert access_lines(prop) == (
        "・大岡山 徒歩4分 → 芝公園 18分\n・北千束 徒歩7分 → 芝公園 22分"
    )


def test_バス便の駅は徒歩不明と明示する() -> None:
    """⚠ 黙って省くと駅そのものが無いように見える（→ 課題#58）。"""
    prop = _prop_with_stations(
        (StationAccess("流山セントラルパーク", walk_minutes=None, commute_minutes=55),),
        commute_destination="芝公園",
    )
    assert access_lines(prop) == "・流山セントラルパーク 徒歩不明 → 芝公園 55分"


def test_通勤の目的地が無ければ駅と徒歩だけ出す() -> None:
    prop = _prop_with_stations((StationAccess("大岡山", walk_minutes=4),))
    assert access_lines(prop) == "・大岡山 徒歩4分"


def test_駅が多いときは件数を明示して打ち切る() -> None:
    stations = tuple(
        StationAccess(f"駅{i}", walk_minutes=i) for i in range(1, MAX_STATIONS_IN_NOTIFY + 3)
    )
    lines = access_lines(_prop_with_stations(stations))
    assert lines is not None
    assert lines.count("・") == MAX_STATIONS_IN_NOTIFY
    assert lines.endswith("（ほか2駅）")


def test_駅が無ければ交通欄を出さない() -> None:
    assert access_lines(_prop_with_stations(())) is None
