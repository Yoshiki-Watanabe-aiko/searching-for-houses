"""用語統一: 物件(property) -> 掲載(listing)

Revision ID: c2d5f8a13e47
Revises: a1c4e7f92b30
Create Date: 2026-09-02

「物件」が掲載（1サイトの1件の募集）と住戸（現実の1部屋）のどちらを指すか
曖昧なまま混用されていた。名寄せの導入で「301掲載 -> 253グループ」のような
場面が日常になったため、掲載を指すものを listing へ揃える（-> CONTEXT.md・課題#30）。

「物件種別」（m_property_types / property_type_id / property_family）は
業界用語で曖昧さが無いため対象外。

ALTER TABLE ... RENAME は物理列順を変えないので、監査カラムは末尾のまま保たれる
（DB規約のテーブル再作成手順は列の「追加」に対するもの）。

シーケンス・インデックス・制約の名前は RENAME TO では追随しないため、
末尾で動的に付け替える。
"""

from __future__ import annotations

from alembic import op

revision = "c2d5f8a13e47"
down_revision = "a1c4e7f92b30"
branch_labels = None
depends_on = None

TABLES = [
    ("t_properties", "t_listings"),
    ("t_property_features", "t_listing_features"),
    ("t_property_groups", "t_listing_groups"),
    ("t_property_scores", "t_listing_scores"),
]

COLUMNS = [
    ("t_notifications", "property_id", "listing_id"),
    ("t_listing_features", "property_id", "listing_id"),
    ("t_listing_groups", "representative_property_id", "representative_listing_id"),
    ("t_listing_scores", "property_id", "listing_id"),
    ("t_ranking_digests", "property_ids", "listing_ids"),
]

# 名前の付け替え規則。長い方から順に当てる（t_property_ を先に処理すると
# t_properties が t_propertiess のように壊れるため、順序が意味を持つ）
NAME_RULES = [
    ("t_properties", "t_listings"),
    ("t_property_", "t_listing_"),
    # CHECK 制約は ck_{テーブル}_{名前} で、名前の側にも古いテーブル名が入る
    # （ck_t_properties_properties_status）。テーブル接頭辞の無い形も要る
    ("property_features", "listing_features"),
    ("property_groups", "listing_groups"),
    ("property_scores", "listing_scores"),
    ("properties", "listings"),
    ("representative_property_id", "representative_listing_id"),
    ("property_ids", "listing_ids"),
    # ⚠ property_type_id は「物件種別」なので置換対象にしない。
    # property_id は property_type_id の部分文字列ではないため、
    # このルールが property_type_id を壊すことはない
    ("property_id", "listing_id"),
]

COMMENTS = {
    "t_listings": "掲載（1行=1サイトの1件の募集）",
    "t_listing_features": "掲載から抽出した設備・特性",
    "t_listing_scores": "検索パターン別のスコアと内訳",
    "t_listing_groups": "同一住戸と判定した掲載のグループ（クロスサイト名寄せ）",
}


# 名前を付け替える対象のテーブル（新名称）。m_property_types などを
# 巻き込まないよう、パターン一致ではなく**テーブルを明示して**拾う
TOUCHED_TABLES = (
    "t_listings",
    "t_listing_features",
    "t_listing_groups",
    "t_listing_scores",
    "t_notifications",
    "t_ranking_digests",
)

SEQUENCES = [
    ("t_properties_id_seq", "t_listings_id_seq"),
    ("t_property_features_id_seq", "t_listing_features_id_seq"),
    ("t_property_groups_id_seq", "t_listing_groups_id_seq"),
    ("t_property_scores_id_seq", "t_listing_scores_id_seq"),
]


def _rename_objects(reverse: bool = False) -> None:
    """制約・インデックス・シーケンスの名前を規則に沿って付け替える。

    ``ALTER TABLE ... RENAME TO`` は制約名にもインデックス名にもシーケンス名にも
    追随しないため、放っておくと ``t_listings`` に ``pk_t_properties`` が
    付いたままになる。

    ``m_property_types`` 系を巻き込まないよう、対象は **``TOUCHED_TABLES`` に
    属するものだけ**に限る（名前のパターン一致だけで拾うと
    ``uq_m_property_types_code`` まで書き換えてしまう）。
    """
    rules = [(b, a) for a, b in NAME_RULES] if reverse else list(NAME_RULES)
    tables = ", ".join(f"'{t}'" for t in TOUCHED_TABLES)

    def expr(column: str) -> str:
        out = column
        for src, dst in rules:
            out = f"replace({out}, '{src}', '{dst}')"
        return out

    # 制約が先。制約に紐づくインデックスは制約側のリネームに追随する
    op.execute(
        f"""
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN
                SELECT c.conname AS old_name, {expr('c.conname')} AS new_name,
                       t.relname AS table_name
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'public' AND t.relname IN ({tables})
                  AND c.conname LIKE '%propert%'
            LOOP
                IF r.old_name <> r.new_name THEN
                    EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                                   r.table_name, r.old_name, r.new_name);
                END IF;
            END LOOP;
        END $$;
        """
    )

    # 制約に紐づかない裸のインデックス
    op.execute(
        f"""
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN
                SELECT c.relname AS old_name, {expr('c.relname')} AS new_name
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indexrelid
                JOIN pg_class t ON t.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'public' AND t.relname IN ({tables})
                  AND c.relname LIKE '%propert%'
            LOOP
                IF r.old_name <> r.new_name THEN
                    EXECUTE format('ALTER INDEX %I RENAME TO %I', r.old_name, r.new_name);
                END IF;
            END LOOP;
        END $$;
        """
    )

    for old_name, new_name in SEQUENCES:
        src, dst = (new_name, old_name) if reverse else (old_name, new_name)
        op.execute(f"ALTER SEQUENCE IF EXISTS {src} RENAME TO {dst}")


def upgrade() -> None:
    for old, new in TABLES:
        op.rename_table(old, new)
    for table, old, new in COLUMNS:
        op.alter_column(table, old, new_column_name=new)
    _rename_objects()
    for table, comment in COMMENTS.items():
        op.execute(f"COMMENT ON TABLE {table} IS '{comment}'")
    op.execute("COMMENT ON COLUMN t_notifications.listing_id IS '通知した掲載のID'")
    op.execute("COMMENT ON COLUMN t_listing_features.listing_id IS '掲載ID'")
    op.execute("COMMENT ON COLUMN t_listing_scores.listing_id IS '掲載ID'")
    op.execute(
        "COMMENT ON COLUMN t_listing_groups.representative_listing_id IS "
        "'代表に選んだ掲載のID（順位と通知はこの掲載で出す）'"
    )
    op.execute("COMMENT ON COLUMN t_ranking_digests.listing_ids IS '送信した掲載IDの配列'")


def downgrade() -> None:
    for table, old, new in COLUMNS:
        op.alter_column(table, new, new_column_name=old)
    for old, new in TABLES:
        op.rename_table(new, old)
    _rename_objects(reverse=True)
