"""市区ローテーション（→ 課題#36・Phase 5E）。

HOMES・ATHOME は**1回の実行で取れるリクエスト数に上限がある**（実測 2026-09-03）。

=========  =========  ===========================================
サイト     上限        超えたときの応答
=========  =========  ===========================================
HOMES      5           HTTP 202 ＋ 空ボディ
ATHOME     4           HTTP 200 ＋ パズル認証ページ（8KB）
=========  =========  ===========================================

⚠ **どちらも1リクエスト目は正常に返る。** 単発の疎通確認では再現できないので、
「1回叩いて 200 が返った」を取得可能の根拠にしてはいけない。

⚠ **間隔を広げても上限は動かない。** HOMES は4秒でも10秒でも6件目で頭打ちになる
（→ 課題#17）。絞りは**リクエスト数**で掛かっているので、``min_interval_sec`` を
上げる対策は所要時間を伸ばすだけで取得量は増えない。

そこで1回の実行では上限ぶんの市区だけ取り、**次回は続きの市区から**始める。
帯82市区なら HOMES が約1.4日・ATHOME が約1.8日で一巡する（2時間ごとの実行）。

⚠ **カーソルは位置番号でなく JIS5桁で持つ。** 市区リストは YAML 編集で増減する
（課題#32 で実際に4市区を外した）ため、番号だと編集のたびにずれて別の市区へ飛ぶ。
``resolve_areas`` は ``jis_code`` 順に決定的へ並ぶので、
「カーソルより大きい最初の JIS から n 件、末尾に達したら先頭へ戻る」で周回できる。
"""

from __future__ import annotations

from collections.abc import Sequence

from house_search.scrape.area import AreaTarget


def rotate_areas(
    areas: Sequence[AreaTarget], *, last_city_jis: str | None, size: int
) -> list[AreaTarget]:
    """カーソルの続きから ``size`` 件の市区を切り出す（末尾に達したら先頭へ戻る）。

    ``areas`` は ``resolve_areas`` が返す JIS5桁の昇順を前提にする。

    ⚠ **JIS を持たないエリア（都道府県単位）が混じるときはローテーションしない。**
    ``search.cities`` が空だと市区必須でないサイトは都道府県1本のURLになり、
    そもそも本数が上限に収まる。ここで無理に絞ると逆に取得できなくなる。
    """
    if size < 1:
        raise ValueError("ローテーションの件数は1以上を指定してください")
    if not areas or any(area.jis_code is None for area in areas):
        return list(areas)
    if len(areas) <= size:
        return list(areas)

    start = 0
    if last_city_jis is not None:
        # カーソルより大きい最初の市区。カーソルの市区自体が YAML から
        # 外れていても「その次」へ進めるので、位置番号のようにずれない
        start = next(
            (i for i, area in enumerate(areas) if (area.jis_code or "") > last_city_jis),
            0,
        )
    return [areas[(start + offset) % len(areas)] for offset in range(size)]


def next_cursor(rotated: Sequence[AreaTarget]) -> str | None:
    """切り出した区間の末尾のJIS5桁（次回の開始位置になる）。"""
    return rotated[-1].jis_code if rotated else None
