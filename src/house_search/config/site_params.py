"""サイト側絞り込みパラメータの読み込みとDB同期。

正典は ``data/site_search_params.yaml``。``sync-site-params`` で
``m_site_search_params`` へ同期し、実行時はDBから読む
（``extract/dictionary.py`` と同じ構成）。

判定ロジックそのものは ``scrape/params.py`` にあり、この層はIOだけを持つ。
アダプタが読む側の ``ParamSpec`` はDBにもYAMLにも依存しない純粋なデータ。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, text

from house_search.scrape.params import (
    AXIS_BOUND,
    KIND_ENUM,
    KIND_MULTI,
    KIND_STEPPED,
    UNIT_DIVISOR,
    ParamError,
    ParamSpec,
    SiteParamTable,
)

SITE_PARAMS_FILENAME = "site_search_params.yaml"
VALID_KINDS = frozenset({KIND_STEPPED, KIND_ENUM, KIND_MULTI})


def load_site_params(path: Path) -> SiteParamTable:
    """正典YAMLを読む。

    未知の軸・単位・種別はここで落とす。綴り間違いを黙って無視すると
    「設定したのに効いていない」に気づけない。
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: list[ParamSpec] = []
    for site_code, by_type in (raw.get("sites") or {}).items():
        for property_type, by_axis in (by_type or {}).items():
            for axis, entry in (by_axis or {}).items():
                specs.append(_build_spec(site_code, property_type, axis, entry))
    # 定義順に依存しないよう並べる（DB由来との突き合わせを楽にする）
    specs.sort(key=lambda s: (s.site_code, s.property_type, s.axis))
    return SiteParamTable(specs=tuple(specs))


def _build_spec(
    site_code: str, property_type: str, axis: str, entry: dict[str, Any]
) -> ParamSpec:
    where = f"{site_code}/{property_type}/{axis}"
    if axis not in AXIS_BOUND:
        raise ParamError(
            f"{where}: サイト側へ渡せない軸です。"
            f"使えるのは {', '.join(sorted(AXIS_BOUND))}"
        )
    kind = entry.get("kind")
    if kind not in VALID_KINDS:
        raise ParamError(
            f"{where}: kind は {sorted(VALID_KINDS)} のいずれかにしてください: {kind!r}"
        )
    unit = entry.get("unit")
    if unit not in UNIT_DIVISOR:
        raise ParamError(f"{where}: 未知の単位です: {unit!r}")
    param_name = entry.get("param")
    if not param_name:
        raise ParamError(f"{where}: param（URLクエリのキー）がありません")
    return ParamSpec(
        site_code=site_code,
        property_type=property_type,
        axis=axis,
        param_name=str(param_name),
        value_kind=str(kind),
        unit=str(unit),
        value_spec=dict(entry.get("spec") or {}),
        is_enabled=bool(entry.get("enabled", True)),
        notes=entry.get("notes"),
    )


_UPSERT = text(
    """
    INSERT INTO m_site_search_params (
        site_id, property_type_id, axis, param_name, value_kind,
        unit, value_spec, is_enabled, notes, created_at, updated_at
    )
    SELECT s.id, p.id, :axis, :param_name, :value_kind,
           :unit, CAST(:value_spec AS jsonb), :is_enabled, :notes, now(), now()
      FROM m_sites s, m_property_types p
     WHERE s.code = :site_code AND p.code = :property_type
    ON CONFLICT (site_id, property_type_id, axis) DO UPDATE SET
        param_name = EXCLUDED.param_name,
        value_kind = EXCLUDED.value_kind,
        unit       = EXCLUDED.unit,
        value_spec = EXCLUDED.value_spec,
        is_enabled = EXCLUDED.is_enabled,
        notes      = EXCLUDED.notes,
        updated_at = now()
    """
)


def sync_site_params(engine: Engine, table: SiteParamTable) -> tuple[int, int]:
    """YAMLの定義をDBへ同期する。``(反映件数, 削除件数)`` を返す。

    YAMLから消えた定義はDBからも消す。残しておくと「YAMLでやめたはずの軸が
    実行時にはまだ送られる」というずれが生まれる。
    """
    import json

    applied = 0
    with engine.begin() as conn:
        for spec in table.specs:
            result = conn.execute(
                _UPSERT,
                {
                    "site_code": spec.site_code,
                    "property_type": spec.property_type,
                    "axis": spec.axis,
                    "param_name": spec.param_name,
                    "value_kind": spec.value_kind,
                    "unit": spec.unit,
                    "value_spec": json.dumps(spec.value_spec, ensure_ascii=False),
                    "is_enabled": spec.is_enabled,
                    "notes": spec.notes,
                },
            )
            if result.rowcount == 0:
                raise ParamError(
                    f"{spec.site_code}/{spec.property_type}/{spec.axis}: "
                    "m_sites または m_property_types に該当が無く同期できません"
                )
            applied += 1

        # psycopg3 は匿名複合型（タプル）の配列を渡せないので、
        # 文字列キーに連結してから比較する
        keys = [
            f"{spec.site_code}/{spec.property_type}/{spec.axis}" for spec in table.specs
        ]
        deleted = conn.execute(
            text(
                """
                DELETE FROM m_site_search_params t
                 USING m_sites s, m_property_types p
                 WHERE s.id = t.site_id AND p.id = t.property_type_id
                   AND s.code || '/' || p.code || '/' || t.axis <> ALL(:keys)
                """
            ),
            {"keys": keys or [""]},
        ).rowcount
    return applied, deleted


def load_from_db(engine: Engine) -> SiteParamTable:
    """DBから定義を読む。実行時はこちらを使う。"""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.code AS site_code, p.code AS property_type, t.axis,
                       t.param_name, t.value_kind, t.unit, t.value_spec,
                       t.is_enabled, t.notes
                  FROM m_site_search_params t
                  JOIN m_sites s ON s.id = t.site_id
                  JOIN m_property_types p ON p.id = t.property_type_id
                 ORDER BY s.code, p.code, t.axis
                """
            )
        ).all()
    return SiteParamTable(
        specs=tuple(
            ParamSpec(
                site_code=row.site_code,
                property_type=row.property_type,
                axis=row.axis,
                param_name=row.param_name,
                value_kind=row.value_kind,
                unit=row.unit,
                value_spec=dict(row.value_spec or {}),
                is_enabled=row.is_enabled,
                notes=row.notes,
            )
            for row in rows
        )
    )
