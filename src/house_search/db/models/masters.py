"""マスタテーブル (``m_*``) のモデル定義。

v2 では「サイト側の検索フォームで絞る」方針を全廃したため、旧 ``site_condition_map``
(サイト×条件の対応表) は存在しない。代わりに設備条件はローカル抽出で判定するので、
抽出辞書テーブル ``m_condition_synonyms`` が新設されている。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from house_search.db.base import Base, TimestampMixin


class PropertyType(TimestampMixin, Base):
    """物件種別マスタ。賃貸＋売買4種別の計5種別。"""

    __tablename__ = "m_property_types"
    __table_args__ = {"comment": "物件種別マスタ（賃貸・新築M・中古M・新築K・中古K）"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="物件種別ID")
    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="物件種別コード（例: CHINTAI）"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="物件種別名")
    family: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "種別ファミリ。metric体系・dedup_key構成要素・YAMLスキーマの分岐単位。"
            "CHINTAI=賃貸 / MANSION_BUY=マンション売買 / KODATE_BUY=戸建て売買"
        ),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="表示順（昇順）"
    )


class Site(TimestampMixin, Base):
    """サイトマスタ。賃貸EX（CHINTAI_EX）を加えた12サイト。"""

    __tablename__ = "m_sites"
    __table_args__ = {"comment": "スクレイピング対象サイトマスタ"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="サイトID")
    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="サイトコード（例: SUUMO）"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="サイト名")
    base_url: Mapped[str | None] = mapped_column(
        String(255), comment="サイトのベースURL（スキームとホストまで）"
    )
    fetch_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="HTTP",
        server_default=text("'HTTP'"),
        comment="取得方式。HTTP=httpx+lxml / PLAYWRIGHT=ブラウザ自動化が必要",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="スクレイピング対象として有効か",
    )
    min_interval_sec: Mapped[float] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=2.5,
        server_default=text("2.5"),
        comment="同一サイトへの最小リクエスト間隔（秒）。実際は±30%のジッタを乗せる",
    )
    max_pages_per_run: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default=text("5"),
        comment="1回の実行で取得する一覧ページ数の上限",
    )
    daily_request_cap: Mapped[int | None] = mapped_column(
        Integer, comment="1日あたりのリクエスト数上限。NULL=無制限"
    )
    representative_priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default=text("100"),
        comment=(
            "名寄せグループの代表選定でのサイト優先順（小さいほど優先）。"
            "価格・抽出情報数で決着しなかった場合のタイブレークに使う"
        ),
    )
    notes: Mapped[str | None] = mapped_column(Text, comment="運用上の備考")


class ConditionCategory(TimestampMixin, Base):
    """条件カテゴリマスタ。"""

    __tablename__ = "m_condition_categories"
    __table_args__ = {"comment": "検索条件のカテゴリマスタ"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="条件カテゴリID")
    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="カテゴリコード（例: SECURITY）"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="カテゴリ名")
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="表示順（昇順）"
    )


class Condition(TimestampMixin, Base):
    """条件マスタ。YAML の ``must.features`` / ``want.features`` が参照するコード体系。"""

    __tablename__ = "m_conditions"
    __table_args__ = {"comment": "検索条件マスタ（設備・特性・価格条件等）"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="条件ID")
    category_id: Mapped[int] = mapped_column(
        ForeignKey("m_condition_categories.id"), nullable=False, comment="条件カテゴリID"
    )
    code: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="条件コード（例: SEC_AUTOLOCK）"
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="条件名")
    description: Mapped[str | None] = mapped_column(Text, comment="条件の補足説明")
    data_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="boolean",
        server_default=text("'boolean'"),
        comment="値の型。boolean=有無 / range=下限上限 / enum=選択肢 / number=数値単体",
    )
    is_extractable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment=(
            "詳細ページ本文からのローカル抽出対象か。"
            "true の条件だけが m_condition_synonyms の辞書を持ち t_listing_features に載る。"
            "エリア・価格上限のような検索軸そのものは false"
        ),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="カテゴリ内の表示順（昇順）",
    )


class ConditionPropertyType(TimestampMixin, Base):
    """条件×物件種別マッピング。どの条件がどの種別に適用されるか。"""

    __tablename__ = "m_condition_property_types"
    __table_args__ = (
        UniqueConstraint("condition_id", "property_type_id"),
        {"comment": "条件×物件種別マッピング（条件の適用可能種別）"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="マッピングID")
    condition_id: Mapped[int] = mapped_column(
        ForeignKey("m_conditions.id"), nullable=False, comment="条件ID"
    )
    property_type_id: Mapped[int] = mapped_column(
        ForeignKey("m_property_types.id"), nullable=False, comment="物件種別ID"
    )


class ConditionSynonym(TimestampMixin, Base):
    """設備抽出辞書。``data/feature_dictionary.yaml`` を正として ``sync-dict`` で同期する。

    Git管理YAMLを正典にするのは、辞書の変更をdiffレビューできるようにするため。
    実行時はこのテーブルを参照して JOIN する。
    """

    __tablename__ = "m_condition_synonyms"
    __table_args__ = (
        # site_id NULL（全サイト共通パターン）同士も重複とみなしたいので NULLS NOT DISTINCT。
        UniqueConstraint(
            "condition_id", "site_id", "pattern", "is_negative", postgresql_nulls_not_distinct=True
        ),
        {"comment": "設備抽出辞書（条件コード → 表記パターン）"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="辞書エントリID")
    condition_id: Mapped[int] = mapped_column(
        ForeignKey("m_conditions.id"), nullable=False, comment="条件ID"
    )
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("m_sites.id"),
        comment="サイト固有パターンの場合のサイトID。NULL=全サイト共通",
    )
    property_family: Mapped[str | None] = mapped_column(
        String(20),
        comment=(
            "適用する種別ファミリ（CHINTAI / MANSION_BUY / KODATE_BUY）。NULL=全ファミリ。"
            "売買の証明書・性能評価系の語彙は賃貸と別体系のため辞書を2部構成にする"
        ),
    )
    pattern: Mapped[str] = mapped_column(
        Text, nullable=False, comment="照合する表記（NFKC正規化・小文字化した後の形で保持）"
    )
    is_negative: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="否定パターンか（例:「オートロックなし」。true にマッチしたら条件は不成立）",
    )


class City(TimestampMixin, Base):
    """市区町村マスタ。YAML の ``search.cities`` に書く値の正典。"""

    __tablename__ = "m_cities"
    __table_args__ = (
        UniqueConstraint("prefecture", "canonical_name"),
        {"comment": "市区町村マスタ（YAML の cities に指定する canonical_name の正典）"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="市区町村ID")
    prefecture: Mapped[str] = mapped_column(String(20), nullable=False, comment="都道府県名")
    parent_city: Mapped[str | None] = mapped_column(
        String(50), comment="政令指定都市名（例: 横浜市）。特別区・一般市町村は NULL"
    )
    city_name: Mapped[str] = mapped_column(String(100), nullable=False, comment="区市町村名")
    canonical_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        comment=(
            "YAML に記述する正規名。同一都道府県内で一意。"
            "政令市の区は市名を prefix する（例: 横浜市西区）"
        ),
    )
    jis_code: Mapped[str | None] = mapped_column(
        String(5), comment="全国地方公共団体コードの上位5桁（JIS X 0402 相当）"
    )


class CitySiteValue(TimestampMixin, Base):
    """市区町村×サイトの検索値（縦持ち）。

    v1 はサイトごとに列を持つワイドテーブル（ADR 0001）だったが、賃貸EX追加で
    「サイトを増やすたびに DDL 変更（かつ監査カラム末尾維持のためテーブル再作成）が要る」
    問題が顕在化したため縦持ちへ転換した（ADR 0009 で ADR 0001 を撤回）。
    以後のサイト追加は行の挿入だけで済む。
    """

    __tablename__ = "m_city_site_values"
    __table_args__ = {"comment": "市区町村×サイトのURL検索値（縦持ち）"}

    city_id: Mapped[int] = mapped_column(
        ForeignKey("m_cities.id", ondelete="CASCADE"), primary_key=True, comment="市区町村ID"
    )
    site_id: Mapped[int] = mapped_column(
        ForeignKey("m_sites.id", ondelete="CASCADE"), primary_key=True, comment="サイトID"
    )
    value: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment=(
            "そのサイトの検索URLに埋める値。"
            "パスセグメント（例: sc_shinjuku）・スラグ（例: tokyo/shinjuku）・"
            "JIS5桁コード（例: 13104）のいずれか。サイトごとの解釈は m_sites 側の実装に従う"
        ),
    )
    # 行が存在しない = そのサイトでは当該市区の検索値が未登録 → 都道府県レベル検索へフォールバック。
