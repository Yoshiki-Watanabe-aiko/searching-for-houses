"""検索対象エリア（都道府県・市区町村）の解決。

v2 がサイトへ渡すのはエリア・物件種別・価格上限の3つだけなので（→ ADR 0003）、
「どの市区をどの値で指定するか」がサイト別コードの主要な分岐点になる。

市区の検索値には2系統ある。

* **マッピング系**（SUUMO / HOMES など）: ``m_city_site_values`` に登録された
  サイト固有のスラグ。行が無ければその市区は指定できない
* **JIS系**（ABLE / GOO / 賃貸EX など）: 値が JIS5桁コードそのもの。
  ``m_cities.jis_code`` から導出できるため ``m_city_site_values`` が
  未登録でも全市区を指定できる

JIS系を ``m_city_site_values`` に依存させないのは実測上の理由がある。
対象4都県は ``m_cities`` に253市区あるのに ``m_city_site_values`` の行は67件しかなく、
マッピングに頼ると八王子市のような市部が丸ごと検索対象から漏れる
（ABLE で JIS ``13201`` を直接指定すると一覧が返ることを実測で確認した）。

**政令指定都市はマスタが市と行政区の両方を持つ**（横浜市 14100 と横浜市西区 14103）。
サイトによって指定できる粒度が違うので両方を保持するが、取得URLを組み立てるときは
行政区を持つ市の親行を必ず外す。外さないと同じ掲載を市と区で二重に取りに行く。
``search.cities`` に市名だけを書いた場合は、その市の行政区へ展開する（→ ADR 0014）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

# 市区の検索値をどこから得るか。
CITY_VALUE_MAPPING = "mapping"  # m_city_site_values の登録値だけを使う
CITY_VALUE_JIS = "jis"  # JIS5桁コードをそのまま使う（m_cities から導出できる）


@dataclass(frozen=True, slots=True)
class AreaTarget:
    """一覧URLを1本組み立てるための対象エリア。

    ``city_name`` が ``None`` なら都道府県単位の検索を表す。
    """

    prefecture: str
    city_name: str | None = None
    jis_code: str | None = None
    value: str | None = None

    @property
    def is_prefecture(self) -> bool:
        return self.city_name is None


def resolve_areas(
    conn: Connection,
    *,
    site_code: str,
    prefectures: list[str],
    cities: list[str],
    requires_city: bool,
    city_value_source: str,
) -> list[AreaTarget]:
    """検索パターンとサイトの性質から、取りに行くエリアの一覧を組み立てる。

    ``cities`` が空でも ``requires_city`` のサイト（ABLE・SMOCCA）は
    都道府県指定では0件になるため、都道府県内の全市区へ自動展開する（→ 課題#1）。
    それ以外のサイトは都道府県単位で1本にまとめ、取得URL数を抑える。
    """
    if not cities and not requires_city:
        return [AreaTarget(prefecture=pref) for pref in prefectures]

    rows = _city_rows(conn, site_code=site_code, prefectures=prefectures, cities=cities)
    targets: list[AreaTarget] = []
    for pref, name, jis_code, mapped in rows:
        value = jis_code if city_value_source == CITY_VALUE_JIS else mapped
        if not value:
            # マッピング系で未登録の市区は指定しようがないので落とす。
            # 呼び出し側が都道府県へフォールバックできるよう、ここでは黙って除く
            continue
        targets.append(
            AreaTarget(prefecture=pref, city_name=name, jis_code=jis_code, value=value)
        )

    if not targets and not requires_city:
        # 市区を1つも解決できなかったときは都道府県単位へ落とす。
        # requires_city のサイトは都道府県では0件なので、あえて空のまま返す
        return [AreaTarget(prefecture=pref) for pref in prefectures]
    return targets


def _city_rows(
    conn: Connection, *, site_code: str, prefectures: list[str], cities: list[str]
) -> list[tuple[str, str, str | None, str | None]]:
    """対象市区を ``m_cities`` から引き、サイト固有値を左外部結合で添える。"""
    params: dict[str, object] = {"site_code": site_code, "prefectures": prefectures}
    condition = "c.prefecture = ANY(:prefectures)"
    if cities:
        # 政令市名を1つ書いたらその行政区へ展開する。「横浜市」と指定したときに
        # 市そのもの（14100）を送るか区を送るかはサイトごとに違い、確かめようが
        # ないので区に寄せる。区は m_city_site_values にも登録があり確実に引ける。
        condition += " AND (c.canonical_name = ANY(:cities) OR c.parent_city = ANY(:cities))"
        params["cities"] = cities
    rows = conn.execute(
        text(
            f"""
            SELECT c.prefecture, c.canonical_name, c.jis_code, v.value
            FROM m_cities c
            LEFT JOIN m_city_site_values v
                   ON v.city_id = c.id
                  AND v.site_id = (SELECT id FROM m_sites WHERE code = :site_code)
            WHERE {condition}
              -- 行政区を持つ政令市の「親の行」は取得対象から外す。マスタは
              -- 横浜市（14100）と横浜市西区（14103）の両方を持つため、外さないと
              -- 同じ掲載を市と区で二重に取りに行くことになる（→ ADR 0014）。
              AND NOT EXISTS (
                    SELECT 1 FROM m_cities w
                     WHERE w.prefecture = c.prefecture
                       AND w.parent_city = c.canonical_name
              )
            ORDER BY c.jis_code
            """
        ),
        params,
    ).all()
    return [(pref, name, jis, value) for pref, name, jis, value in rows]
