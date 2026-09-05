"""マスタテーブル (``m_*``) のモデル定義。

v2 では「サイト側の検索フォームで**設備条件**を絞る」方針を全廃したため、
旧 ``site_condition_map`` (サイト×設備条件の対応表) は存在しない。設備条件は
ローカル抽出で判定するので、抽出辞書テーブル ``m_condition_synonyms`` が新設されている。

⚠ ``m_site_search_params`` は**その復活ではない**。扱うのは数値系の MUST と間取りだけで、
設備条件は含まない。MUST は「ローカルで fail にする掲載」なので、サイト側で落としても
結果が変わらず取得量だけ減る（→ ADR 0015 が ADR 0003 を補強する）。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class SiteSearchParam(TimestampMixin, Base):
    """サイト側の絞り込みパラメータ定義。

    ``data/site_search_params.yaml`` を正として ``sync-site-params`` で同期する
    （``m_condition_synonyms`` と同じ構成）。Git管理YAMLを正典にするのは、
    実測値の変更をdiffレビューできるようにするため。

    ⚠ **扱うのは数値系の MUST と間取りだけ。** 設備条件は永久に含めない（→ ADR 0015）。
    """

    __tablename__ = "m_site_search_params"
    __table_args__ = (
        UniqueConstraint("site_id", "property_type_id", "axis"),
        {"comment": "サイト側の絞り込みパラメータ定義（MUST限定・サイト×物件種別×軸）"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="パラメータ定義ID")
    site_id: Mapped[int] = mapped_column(
        ForeignKey("m_sites.id"), nullable=False, comment="サイトID"
    )
    property_type_id: Mapped[int] = mapped_column(
        ForeignKey("m_property_types.id"), nullable=False, comment="物件種別ID"
    )
    axis: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "MUSTの軸名（area_min / area_max / walk_minutes_max / age_max / layouts）。"
            "丸めの向きは軸から決まるのでここには持たせない"
        ),
    )
    param_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="URLクエリのキー（SUUMO の mb / et / md など）"
    )
    value_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "値の表し方。stepped=等間隔の選択肢 / enum=不等間隔の選択肢 / "
            "multi=複数値を並べて送る（間取り）"
        ),
    )
    unit: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "サイトが受け取る単位（yen / man_yen / sqm / minutes / years）。"
            "MUST側の値から換算する"
        ),
    )
    value_spec: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment=(
            "値の空間。stepped は min/max/step、enum は choices、multi は mapping。"
            "いずれも format（Python の書式文字列）を伴う"
        ),
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="この軸を実際に送るか。実測で効かないと分かったら false にする",
    )
    notes: Mapped[str | None] = mapped_column(
        Text, comment="実測メモ（件数の変化・0件になる条件など）"
    )


class Station(TimestampMixin, Base):
    """駅マスタ。通勤時間の算出に使う（Phase 5C）。

    正典は駅データ.jp 無料版の CSV（``data/train_master/``）で、``sync-stations`` で同期する。
    ⚠ **CSV は再配布不可のため Git 管理外**にしてある（README だけが追跡対象）。
    設備抽出辞書・サイト検索パラメータは Git 管理 YAML を正典にできたが、
    この表だけはライセンス上そうできない（→ ADR 0016）。

    ``station_g_cd``（駅グループコード）を持つことがこのデータを選んだ理由。
    乗換駅（同一の駅が路線ごとに別レコードになる）と同名異駅の区別が済んでおり、
    通勤時間のキャッシュを**グループ単位**にできる（1都3県で駅2,052に対しグループ1,505）。

    投入するのは営業中（``e_status=0``）の駅だけ。廃止駅を入れても掲載側には現れない。
    """

    __tablename__ = "m_stations"
    __table_args__ = {"comment": "駅マスタ（駅データ.jp 無料版が正典・通勤時間の算出に使う）"}

    station_cd: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False, comment="駅コード（路線ごとに別コード）"
    )
    station_g_cd: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment=(
            "駅グループコード。乗換駅を1つに束ね、同名異駅を区別する単位。"
            "通勤時間キャッシュ（t_station_commutes）のキーになる"
        ),
    )
    station_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="駅名（「駅」を含まない原文表記）"
    )
    station_name_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=(
            "照合用の正規化キー（NFKC・ヶ/ヵ・之/の の統一・小文字化）。"
            "掲載側の駅表記も同じ関数を通してから突き合わせる"
        ),
    )
    line_cd: Mapped[int] = mapped_column(Integer, nullable=False, comment="路線コード")
    line_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=(
            "路線名（例: 都営三田線）。"
            "⚠ 同名の別路線が実在する（「三田線」は神戸電鉄）ため、駅名の照合は都道府県で絞る"
        ),
    )
    company_name: Mapped[str | None] = mapped_column(
        String(100),
        comment=(
            "事業者名（例: 東京都交通局）。"
            "掲載側が路線名に会社名を前置することがある（「東武鉄道東上線」）ため照合に使う"
        ),
    )
    pref_cd: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="都道府県コード（JIS X 0401。m_cities.jis_code の上位2桁と同じ体系）",
    )
    lon: Mapped[float] = mapped_column(
        Numeric(9, 6), nullable=False, comment="経度。Routes API の出発地・目的地に渡す"
    )
    lat: Mapped[float] = mapped_column(
        Numeric(9, 6), nullable=False, comment="緯度。Routes API の出発地・目的地に渡す"
    )


class AddressPoint(TimestampMixin, Base):
    """住所マスタ（大字・町丁目）。**丁目が実在するかの判定に使う**（Phase 5I）。

    正典は国土交通省「位置参照情報」の CSV（``data/address_master/``）で、
    ``sync-addresses`` で同期する。政府標準利用規約（第2.0版）が再配布を認めているため
    **原典を Git 管理下に置ける**（総務省コード表と同じ扱い → ADR 0014。
    再配布不可でGit外へ逃がした駅マスタ → ADR 0016 とは事情が違う）。

    ⚠ **この表が無いと ``normalize_address`` は番地を丁目と誤認する。**
    丁目表記の無い住所は「最初の数字塊を丁目とみなす」規則で処理されるが、
    丁目が存在しない町では番地がそのまま丁目になり
    （``埼玉県深谷市中瀬1480丁目``）、存在しない住所が ``dedup_key`` になる。
    実測（2026-09-05）で active 掲載の **5.4%（1,074件）** がこれだった（→ ADR 0020）。

    ⚠ **``city_jis_code`` に FK を張らない。** ``m_cities.jis_code`` は
    部分ユニーク索引（NULL を許す）なので FK の参照先にできない。
    """

    __tablename__ = "m_address_points"
    __table_args__ = (
        UniqueConstraint("normalized_key", "level"),
        # 丁目行には必ず番号が付き、町名行には付かない。
        # ⚠ 番号の無い丁目行が入ると「その丁目が実在するか」を引けなくなり、
        # ガードが黙って素通りする（誤認が直らないのに例外も出ない）。
        CheckConstraint(
            "(level = 'chome' AND chome_number IS NOT NULL)"
            " OR (level = 'town' AND chome_number IS NULL)",
            name="address_points_level",
        ),
        {"comment": "住所マスタ（位置参照情報が正典。丁目の実在判定とハザードの代表点に使う）"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="住所ポイントID")
    city_jis_code: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        index=True,
        comment="市区町村コード（JIS5桁）。m_cities.jis_code と同じ体系だがFKは張らない",
    )
    town_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
        comment=(
            "町名までの正規化キー（例: 埼玉県深谷市中瀬）。"
            "⚠ 丁目の実在判定の主キーになるので、SQLの文字列操作で導かず物理列で持つ"
        ),
    )
    chome_number: Mapped[int | None] = mapped_column(
        SmallInteger, comment="丁目番号。町名までの行（大字・字）は NULL"
    )
    normalized_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment=(
            "丁目まで（丁目の無い町は町名まで）の正規化キー。"
            "t_listings.address_normalized と同じ normalize_address を通して作る"
        ),
    )
    level: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="粒度。chome=丁目 / town=大字・字（原典の区分コード 3 かどうかで決まる）",
    )
    lon: Mapped[float] = mapped_column(
        Numeric(9, 6), nullable=False, comment="代表点の経度。ハザードのポリゴン照合に使う"
    )
    lat: Mapped[float] = mapped_column(
        Numeric(9, 6), nullable=False, comment="代表点の緯度。ハザードのポリゴン照合に使う"
    )
    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="出典と版（例: mlit_isj_19.0b）。原典が改訂されたことを後から言えるようにする",
    )


class HazardLevel(TimestampMixin, Base):
    """ハザード評価（丁目・町単位）。洪水と土砂災害の危険度を持つ（Phase 5I → 課題#46）。

    正典は国土数値情報の **A31（洪水浸水想定区域・想定最大規模）** と
    **A33（土砂災害警戒区域）** で、丁目の面は e-Stat「町丁・字等境界データ」から採る。
    ポリゴンの交差計算は ``scripts/tools/build_hazard_levels.py``（オフライン）で行い、
    この表には**集計済みの値だけ**を ``sync-hazards`` で入れる。
    ⚠ **``scan`` / ``rescore`` はこの表を JOIN するだけ**にして、
    再採点がネットワーク不要・軽量依存のまま保たれるようにしている。

    ⚠ **``m_address_points.id`` を参照しない。** ``sync-addresses`` は全置換なので
    id が振り直される。突き合わせは ``normalized_key``（同じ ``normalize_base`` を
    通した値）で行う。

    ⚠⚠ **「区域外」と「未解決」を必ず区別する。** 丁目を照合できたら、
    区域に掛からなくても **``value = 0`` の行を必ず書く**（安全だと確認した証拠）。
    行が無い＝そもそも照合できなかった、という意味にする。
    これを混ぜると「危険なのに情報が無いから減点されない」掲載が
    「安全」と同じ扱いになり、**例外にならないまま順位が狂う**。

    ⚠ **縦持ちにしてある**（``hazard_type`` × ``aggregation`` の行）。
    高潮・津波の追加や集計方式の変更が**行の追加だけ**で済む。
    列で持つと、監査カラムを最終列に保つためのテーブル再作成が要る。
    """

    __tablename__ = "m_hazard_levels"
    __table_args__ = (
        UniqueConstraint("normalized_key", "level", "hazard_type", "aggregation"),
        {"comment": "ハザード評価（丁目・町単位。国土数値情報 A31・A33 が正典）"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="ハザード評価ID")
    normalized_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
        comment=(
            "丁目まで（丁目の無い町は町名まで）の正規化キー。"
            "m_address_points.normalized_key・t_listings.address_normalized と同じ規則"
        ),
    )
    level: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment=(
            "粒度。chome=丁目 / town=町"
            "（配下の丁目を集約した値。町名までしか出さないサイト向け）"
        ),
    )
    hazard_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment=(
            "災害の種類。flood=洪水浸水想定（A31 想定最大規模） / "
            "landslide=土砂災害警戒区域（A33 警戒＋特別） / landslide_special=同 特別警戒のみ"
        ),
    )
    aggregation: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "集計方式。area_ratio=丁目に占める区域の面積比（0〜1） / "
            "rank_avg=丁目全面積で加重した平均ランク（区域外を0として含む） / "
            "rank_max=丁目内の最大ランク"
        ),
    )
    value: Mapped[float] = mapped_column(
        Numeric(8, 4),
        nullable=False,
        comment="集計値。⚠ 区域外は 0 を明示的に書く（行が無い＝未解決と区別するため）",
    )
    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="出典と版（例: mlit_a31-22 / mlit_a33-23）。区域の指定替えを追うために持つ",
    )
    acquired_on: Mapped[object] = mapped_column(
        Date, nullable=False, comment="原典の取得日。年次更新の判断に使う"
    )
