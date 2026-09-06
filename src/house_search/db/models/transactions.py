"""トランザクションテーブル (``t_*``) のモデル定義。

Phase 0 の時点で売買4種別に必要な列も含めて確定させている。DB規約により
列追加は監査カラムを最終列に保つためテーブル再作成を伴うため、後付けを避ける狙い。
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from house_search.db.base import Base, CreatedAtMixin, TimestampMixin

# 物件ステータス。sold/removed の物件が再取得されたら active に戻して new 通知する。
PROPERTY_STATUSES = ("active", "sold", "removed")
# MUST判定の3値。詳細ページ取得をスキップするのは fail のみ。
MUST_RESULTS = ("pass", "fail", "unknown")
NOTIFICATION_TYPES = ("new", "sold", "price_up", "price_down", "cheaper_listing")
LOG_LEVELS = ("INFO", "WARN", "ERROR")
RUN_STATUSES = ("running", "completed", "failed", "aborted")
# 掲載の駅表記と駅マスタの同定結果。ambiguous / unmatched は採点上 unknown として扱う。
MATCH_STATUSES = ("matched", "ambiguous", "unmatched")
# 通勤時間キャッシュの状態。no_route は「線路がつながっていない」を明示的に記録する値。
COMMUTE_STATUSES = ("ok", "no_route")


class ListingGroup(TimestampMixin, Base):
    """クロスサイト名寄せグループ。

    ランキング上位が同一物件のサイト違いで埋まるのを防ぐため v2 では本体要件。
    完全一致（dedup_key 一致）のみ自動グループ化し、曖昧一致は候補フラグ止まりにする
    （名寄せの誤爆はランキングから物件を1件消すことを意味し偽陽性のコストが高い）。
    """

    __tablename__ = "t_listing_groups"
    __table_args__ = {"comment": "クロスサイト名寄せグループ"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="グループID")
    dedup_key: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        comment=(
            "名寄せキー（SHA256 hex）。ファミリ識別子＋正規化住所＋種別ごとの構成要素から生成。"
            "賃貸/マンション=間取り＋専有面積＋階数、戸建て=土地面積＋建物面積＋間取り。"
            "建物名は賃貸で非公開・伏字が多く偽陰性の主因になるため含めない"
        ),
    )
    property_type_id: Mapped[int] = mapped_column(
        ForeignKey("m_property_types.id"), nullable=False, comment="物件種別ID"
    )
    representative_listing_id: Mapped[int | None] = mapped_column(
        # t_listings との相互参照になるため、ALTER TABLE で後付けする。
        ForeignKey("t_listings.id", ondelete="SET NULL", use_alter=True),
        comment="代表物件ID。月額/価格が最安 → 設備抽出数が最多 → サイト優先順 で選定",
    )
    member_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="グループに属する掲載件数",
    )


class Listing(TimestampMixin, Base):
    """物件。1行=1掲載（サイト×external_id）。名寄せ後の実体はグループ側で表す。

    新築マンション・新築分譲戸建ては「1物件=1棟/1プロジェクト」粒度で、価格がレンジ表示
    または未定になる。その場合 ``price`` にレンジ下限、``price_min``/``price_max`` にレンジ、
    価格未定は ``price`` を NULL にして ``type_specific_attrs.price_undecided`` を立てる。
    """

    __tablename__ = "t_listings"
    __table_args__ = (
        UniqueConstraint("site_id", "external_id"),
        CheckConstraint("status IN ('active', 'sold', 'removed')", name="properties_status"),
        Index("ix_t_listings_status", "status"),
        Index("ix_t_listings_dedup_key", "dedup_key", postgresql_where="dedup_key IS NOT NULL"),
        Index("ix_t_listings_group_id", "group_id", postgresql_where="group_id IS NOT NULL"),
        Index(
            "ix_t_listings_detail_pending",
            "site_id",
            # 詳細取得キューはこの部分インデックスで引く。中断・再開はSQLの自然な帰結になる。
            postgresql_where="detail_fetched_at IS NULL AND status = 'active'",
        ),
        {"comment": "物件（1行=1サイト掲載）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="物件ID")
    site_id: Mapped[int] = mapped_column(
        ForeignKey("m_sites.id"), nullable=False, comment="取得元サイトID"
    )
    property_type_id: Mapped[int] = mapped_column(
        ForeignKey("m_property_types.id"), nullable=False, comment="物件種別ID"
    )
    external_id: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="サイト固有の物件ID（サイト内で一意）"
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, comment="詳細ページURL")
    title: Mapped[str | None] = mapped_column(String(500), comment="物件名・掲載タイトル")

    # --- 価格 ---------------------------------------------------------------
    price: Mapped[int | None] = mapped_column(
        BigInteger,
        comment="現在価格（円）。賃貸=月額賃料、売買=物件価格。レンジ表示ならその下限。未定は NULL",
    )
    price_prev: Mapped[int | None] = mapped_column(
        BigInteger, comment="直前の価格（円）。価格変動通知の差分算出に使う"
    )
    price_min: Mapped[int | None] = mapped_column(
        BigInteger, comment="価格レンジ下限（円）。新築の棟単位掲載向け"
    )
    price_max: Mapped[int | None] = mapped_column(
        BigInteger, comment="価格レンジ上限（円）。新築の棟単位掲載向け"
    )
    mgmt_fee_monthly: Mapped[int | None] = mapped_column(
        BigInteger,
        comment="管理費・共益費（円/月）。賃貸・マンション売買の双方で使う",
    )
    repair_reserve_monthly: Mapped[int | None] = mapped_column(
        BigInteger, comment="修繕積立金（円/月）。マンション売買のみ"
    )
    deposit_amount: Mapped[int | None] = mapped_column(BigInteger, comment="敷金・保証金（円）")
    key_money_amount: Mapped[int | None] = mapped_column(BigInteger, comment="礼金（円）")
    rent_total: Mapped[int | None] = mapped_column(
        BigInteger,
        Computed(
            "CASE WHEN price IS NULL THEN NULL ELSE price + COALESCE(mgmt_fee_monthly, 0) END",
            persisted=True,
        ),
        comment="賃料＋管理費（円/月）の生成列。metric rent_total の入力。price が NULL なら NULL",
    )
    price_per_sqm: Mapped[float | None] = mapped_column(
        Numeric(12, 2),
        comment=(
            "㎡単価（円/㎡）。表示用の派生値であり metric にはしない"
            "（price と area に既に weight を配れるため二重に重みが掛かる）"
        ),
    )

    # --- 面積・間取り・築年 --------------------------------------------------
    area_sqm: Mapped[float | None] = mapped_column(
        Numeric(8, 2), comment="専有面積（㎡）。賃貸・マンションのみ。戸建てには使わない"
    )
    land_area_sqm: Mapped[float | None] = mapped_column(
        Numeric(10, 2), comment="土地面積（㎡）。戸建てのみ"
    )
    building_area_sqm: Mapped[float | None] = mapped_column(
        Numeric(10, 2), comment="建物面積（㎡）。戸建てのみ"
    )
    layout: Mapped[str | None] = mapped_column(String(50), comment="間取り（例: 1LDK）")
    floor_num: Mapped[int | None] = mapped_column(Integer, comment="所在階。地下は負値")
    total_floors: Mapped[int | None] = mapped_column(Integer, comment="建物の地上階数")
    built_on: Mapped[dt.date | None] = mapped_column(
        Date, comment="築年月。日は1日に固定して格納する"
    )
    age_years: Mapped[int | None] = mapped_column(
        Integer, comment="築年数（年）。築年月が取れないサイト向けに掲載値をそのまま保持"
    )

    # --- 立地 ---------------------------------------------------------------
    address: Mapped[str | None] = mapped_column(Text, comment="住所（掲載原文）")
    address_normalized: Mapped[str | None] = mapped_column(
        Text, comment="正規化住所。dedup_key の入力に使う"
    )
    prefecture: Mapped[str | None] = mapped_column(String(20), comment="都道府県名")
    city_id: Mapped[int | None] = mapped_column(
        ForeignKey("m_cities.id"), comment="市区町村ID。住所から解決できた場合のみ"
    )
    station_info: Mapped[str | None] = mapped_column(Text, comment="最寄り駅情報（掲載原文）")
    walk_minutes: Mapped[int | None] = mapped_column(
        Integer, comment="最寄り駅からの徒歩分数。複数駅ある場合は最短"
    )
    image_url: Mapped[str | None] = mapped_column(Text, comment="サムネイル画像URL")

    # --- 抽出・名寄せ --------------------------------------------------------
    raw_features_text: Mapped[str | None] = mapped_column(
        Text,
        comment=(
            "詳細ページの設備ブロック原文（テキストのみ。HTML全体は保存しない）。"
            "辞書を改善したとき再スクレイピングせず re-extract で全件再抽出するための保存"
        ),
    )
    type_specific_attrs: Mapped[dict | None] = mapped_column(
        JSONB,
        comment=(
            "種別固有の非正規化属性。接道・建ぺい率/容積率・権利形態・引渡時期・"
            "建築条件付きフラグ・price_undecided 等。表記揺れが激しく型付け列に向かない項目を置く"
        ),
    )
    dedup_key: Mapped[str | None] = mapped_column(
        String(64), comment="名寄せキー（SHA256 hex）。t_listing_groups.dedup_key と対応"
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("t_listing_groups.id", ondelete="SET NULL", use_alter=True),
        comment="名寄せグループID。未グループ化は NULL",
    )

    # --- 状態 ---------------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default=text("'active'"),
        comment="掲載状態。active=掲載中 / sold=成約済み / removed=掲載終了",
    )
    detail_fetched_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="詳細ページの最終取得日時。NULL=未取得（詳細取得キューの対象）",
    )
    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="掲載を初めて観測した日時",
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="一覧に掲載されていることを最後に確認した日時",
    )


class ListingFeature(TimestampMixin, Base):
    """物件から抽出した設備・特性。辞書マッチングの結果。"""

    __tablename__ = "t_listing_features"
    __table_args__ = (
        UniqueConstraint("listing_id", "condition_id"),
        {"comment": "物件の設備・特性抽出結果"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="抽出結果ID")
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("t_listings.id", ondelete="CASCADE"), nullable=False, comment="物件ID"
    )
    condition_id: Mapped[int] = mapped_column(
        ForeignKey("m_conditions.id"), nullable=False, comment="条件ID"
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="DETAIL",
        server_default=text("'DETAIL'"),
        comment=(
            "抽出元。LIST=一覧ページ / DETAIL=詳細ページ / SITE_TAG=サイトの構造化タグ / "
            "DERIVED=型付き列からの導出（2階以上・最上階など閾値条件）"
        ),
    )
    matched_text: Mapped[str | None] = mapped_column(
        Text, comment="マッチした原文の断片。辞書の誤爆調査に使う"
    )
    extracted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="抽出を実行した日時。辞書更新後の再抽出で更新される",
    )


class ListingScore(TimestampMixin, Base):
    """パターンごとのスコアリング結果。

    スコアはDB保存済みの物件属性と抽出済み features からの純関数のため、
    再計算はネットワーク不要のDBバッチで完結する。
    """

    __tablename__ = "t_listing_scores"
    __table_args__ = (
        UniqueConstraint("listing_id", "pattern_name"),
        CheckConstraint(
            "must_result IN ('pass', 'fail', 'unknown')", name="property_scores_must_result"
        ),
        Index("ix_t_listing_scores_pattern_score", "pattern_name", "score"),
        {"comment": "検索パターン別のスコアリング結果"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="スコアID")
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("t_listings.id", ondelete="CASCADE"), nullable=False, comment="物件ID"
    )
    pattern_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="検索パターン名（YAML の name）"
    )
    must_result: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment=(
            "MUST判定の3値。pass=全充足 / fail=未充足あり（詳細取得もスキップ） / "
            "unknown=判定不能項目あり。unknown の扱いは YAML の must.unknown_policy に従う"
        ),
    )
    score: Mapped[float | None] = mapped_column(
        Numeric(6, 3),
        comment="WANTスコア 0〜100点。must_result が fail の場合は NULL",
    )
    rank_in_pattern: Mapped[int | None] = mapped_column(
        Integer, comment="同一パターン内のスコア降順順位（1始まり）"
    )
    score_breakdown: Mapped[dict | None] = mapped_column(
        JSONB,
        comment=(
            "採点内訳。各項目の {code, name, weight, s, points, status: hit|miss|unknown}。"
            "欠損metricは missing:true を立て分子・分母の双方から除外して再正規化する"
        ),
    )
    config_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="YAML のスコア関連部分のハッシュ。不一致なら自動再スコアの対象になる",
    )
    scored_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="採点を実行した日時",
    )


class Notification(CreatedAtMixin, Base):
    """通知履歴（追記専用）。重複通知防止の判定にも使う。"""

    __tablename__ = "t_notifications"
    __table_args__ = (
        CheckConstraint(
            "notification_type IN ('new', 'sold', 'price_up', 'price_down', 'cheaper_listing')",
            name="notifications_type",
        ),
        Index("ix_t_notifications_property_type", "listing_id", "notification_type"),
        Index("ix_t_notifications_pattern_name", "pattern_name"),
        {"comment": "個別通知の送信履歴（追記専用）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="通知ID")
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("t_listings.id", ondelete="CASCADE"), nullable=False, comment="物件ID"
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("t_listing_groups.id", ondelete="SET NULL"),
        comment="名寄せグループID。重複抑制はグループ単位で行う",
    )
    pattern_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="検索パターン名")
    notification_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "通知種別。new=新着/再掲載 / sold=成約 / price_up=値上がり / "
            "price_down=値下がり / cheaper_listing=同一物件の他サイト安値掲載"
        ),
    )
    price_at_notify: Mapped[int | None] = mapped_column(
        BigInteger, comment="通知時点の価格（円）。価格変動の重複判定に使う"
    )
    score_at_notify: Mapped[float | None] = mapped_column(
        Numeric(6, 3), comment="通知時点のWANTスコア（0〜100）"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="sent",
        server_default=text("'sent'"),
        comment="送信結果。sent / failed",
    )
    notified_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="送信を試みた日時",
    )


class RankingDigest(CreatedAtMixin, Base):
    """ランキングダイジェストの送信履歴（追記専用）。"""

    __tablename__ = "t_ranking_digests"
    __table_args__ = {"comment": "日次ランキングダイジェストの送信履歴（追記専用）"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="ダイジェストID")
    pattern_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="検索パターン名")
    digest_group: Mapped[str | None] = mapped_column(
        String(100),
        comment=(
            "ダイジェストのセクション並記グループ。同一グループのパターンを1メッセージに並べる。"
            "スコアは種別間で混ぜない"
        ),
    )
    top_n: Mapped[int] = mapped_column(Integer, nullable=False, comment="掲載した件数")
    listing_ids: Mapped[list | None] = mapped_column(
        JSONB, comment="掲載した物件IDの配列（順位順）"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="sent",
        server_default=text("'sent'"),
        comment="送信結果。sent / failed",
    )
    sent_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="送信を試みた日時",
    )


class ScrapeRun(TimestampMixin, Base):
    """実行チェックポイント。中断・再開とレート制御の観測に使う。"""

    __tablename__ = "t_scrape_runs"
    __table_args__ = (
        Index("ix_t_scrape_runs_run_id", "run_id"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'aborted')", name="scrape_runs_status"
        ),
        {"comment": "スクレイピング実行のチェックポイント"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="実行レコードID")
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, comment="1回の実行を束ねる識別子"
    )
    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "実行モード。scan=増分 / seed=通知なしの記録専用 / full=全量 / "
            "check-sold=成約確認 / digest=ダイジェスト / rescore=再スコア"
        ),
    )
    pattern_name: Mapped[str | None] = mapped_column(String(255), comment="検索パターン名")
    site_id: Mapped[int | None] = mapped_column(ForeignKey("m_sites.id"), comment="対象サイトID")
    phase: Mapped[str | None] = mapped_column(
        String(30), comment="処理フェーズ。list / detail / extract / score / notify"
    )
    cursor: Mapped[str | None] = mapped_column(
        Text, comment="再開位置（ページ番号・最終処理物件ID等）"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="running",
        server_default=text("'running'"),
        comment="実行状態",
    )
    items_seen: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="観測した件数"
    )
    items_new: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="新規登録した件数",
    )
    items_failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="処理に失敗した件数",
    )
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="開始日時"
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), comment="終了日時。running の間は NULL"
    )


class ScrapeLog(CreatedAtMixin, Base):
    """スクレイプログ（全件永久保持）。"""

    __tablename__ = "t_scrape_logs"
    __table_args__ = (
        CheckConstraint("level IN ('INFO', 'WARN', 'ERROR')", name="scrape_logs_level"),
        Index("ix_t_scrape_logs_created_at", "created_at"),
        Index("ix_t_scrape_logs_level", "level"),
        {"comment": "スクレイピングの実行ログ（全件永久保持）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="ログID")
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), comment="t_scrape_runs.run_id と対応する実行識別子"
    )
    level: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="ログレベル。INFO / WARN / ERROR"
    )
    site_code: Mapped[str | None] = mapped_column(String(50), comment="サイトコード")
    pattern_name: Mapped[str | None] = mapped_column(String(255), comment="検索パターン名")
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="ログ本文")
    detail: Mapped[dict | None] = mapped_column(JSONB, comment="付随情報（URL・例外内容等）")


class UnknownToken(TimestampMixin, Base):
    """辞書のどのパターンにもマッチしなかった表記。

    ``report-unknown`` で出現回数順に一覧し、辞書YAMLへ追記 → ``sync-dict`` →
    ``re-extract`` で反映する運用ループの入口。
    """

    __tablename__ = "t_unknown_tokens"
    __table_args__ = (
        UniqueConstraint("token", "site_id"),
        Index("ix_t_unknown_tokens_occurrence_count", "occurrence_count"),
        {"comment": "辞書未登録の設備表記（辞書育成の入力）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="未知表記ID")
    token: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="正規化済みトークン（NFKC・小文字化後）"
    )
    site_id: Mapped[int] = mapped_column(
        ForeignKey("m_sites.id"), nullable=False, comment="観測したサイトID"
    )
    property_family: Mapped[str | None] = mapped_column(
        String(20), comment="観測した種別ファミリ。辞書の賃貸/売買どちらに足すかの判断材料"
    )
    occurrence_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="累計出現回数",
    )
    sample_url: Mapped[str | None] = mapped_column(Text, comment="出現した物件の詳細URL（例示用）")
    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="初回観測日時"
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), comment="最終観測日時"
    )


# ruff が未使用と誤認しないよう、CHECK制約で使う定数をエクスポートしておく。
__all__ = [
    "LOG_LEVELS",
    "MUST_RESULTS",
    "NOTIFICATION_TYPES",
    "PROPERTY_STATUSES",
    "RUN_STATUSES",
    "Notification",
    "Listing",
    "ListingFeature",
    "ListingGroup",
    "ListingScore",
    "RankingDigest",
    "ScrapeLog",
    "ScrapeRun",
    "UnknownToken",
]


class ListingStation(TimestampMixin, Base):
    """掲載 → 駅（グループ）の同定結果。

    掲載の駅表記は ``t_listings.station_info`` に原文で入っており、サイトごとに大きくばらつく
    （全角ＪＲ・会社名の前置・区切り文字なしの連結・「駅」の省略・バス停の併記）。
    同定は**保存済みの原文からの純関数**（``commute/matcher.py``）で行うので、
    アダプタには手を入れない。辞書を直したら ``resolve-stations`` で作り直せる
    （``raw_features_text`` から再抽出する設備の運用と同じ考え方）。

    ⚠ ``station_g_cd`` に外部キーは張れない。``m_stations`` の主キーは ``station_cd`` で、
    ``station_g_cd`` は一意でないため（同一グループに路線ごとの行が並ぶ）。
    """

    __tablename__ = "t_listing_stations"
    __table_args__ = (
        UniqueConstraint("listing_id", "position"),
        CheckConstraint(
            "match_status IN ('matched', 'ambiguous', 'unmatched')",
            name="listing_stations_match_status",
        ),
        Index(
            "ix_t_listing_stations_station_g_cd",
            "station_g_cd",
            postgresql_where="station_g_cd IS NOT NULL",
        ),
        {"comment": "掲載の駅表記と駅マスタの同定結果"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="同定結果ID")
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("t_listings.id", ondelete="CASCADE"), nullable=False, comment="掲載ID"
    )
    position: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="station_info 内での出現順（0始まり）"
    )
    raw_station_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="抽出した駅名の原文。同定に失敗した表記を後から調べるために残す",
    )
    station_g_cd: Mapped[int | None] = mapped_column(
        Integer, comment="同定できた駅グループコード。ambiguous / unmatched では NULL"
    )
    match_status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment=(
            "matched=一意に同定 / ambiguous=同名の駅が複数あり路線でも絞れない / "
            "unmatched=マスタに無い（バス停・施設名など）"
        ),
    )
    walk_minutes: Mapped[int | None] = mapped_column(
        SmallInteger,
        comment=(
            "その駅からの徒歩分数。バス便・判別不能は NULL。"
            "t_listings.walk_minutes はバス停からの徒歩を採っていることがあるため"
            "（実測 3,090件）、採点はこちらの最小値を使う"
        ),
    )


class StationCommute(TimestampMixin, Base):
    """駅ペアの通勤所要時間キャッシュ。

    採点と再採点をネットワークにもCSVにも依存させないための表。``rescore`` が
    「DB保存済みの属性からの純関数」であることは v2 の設計上の性質なので、
    所要時間は**駅グループのペアごとに一度だけ**求めてここへ落とす。

    ⚠ **算出は Google Maps ではない。** Routes API・Directions API とも
    **日本の公共交通経路を返さない**ことを実測で確認した（米国の同じ呼び出しは
    経路を返し、日本は HTTP 200 のまま本文が空。DRIVE なら日本でも返る）。
    駅データ.jp の接続情報から自前で経路を探索している（→ ADR 0016）。

    目的地も駅グループコードで持つので、**勤務先が変わっても行が増えるだけ**で
    既存のキャッシュは無効にならない。
    """

    __tablename__ = "t_station_commutes"
    __table_args__ = (
        UniqueConstraint("origin_station_g_cd", "destination_station_g_cd"),
        CheckConstraint("status IN ('ok', 'no_route')", name="station_commutes_status"),
        {"comment": "駅ペアの通勤所要時間キャッシュ"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="キャッシュID")
    origin_station_g_cd: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="出発駅の駅グループコード（物件側の最寄り駅）"
    )
    destination_station_g_cd: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="到着駅の駅グループコード（勤務先の最寄り駅）"
    )
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment=(
            "ok=所要時間を算出できた / no_route=線路がつながっておらず到達できない。"
            "到達不能を明示的に記録し、欠損と区別する"
        ),
    )
    commute_minutes: Mapped[int | None] = mapped_column(
        Integer, comment="所要時間（分）。status='ok' のときだけ入る"
    )
    transfers: Mapped[int | None] = mapped_column(
        Integer, comment="乗換回数。所要時間の内訳を人が確かめるために持つ"
    )
    distance_km: Mapped[float | None] = mapped_column(
        Numeric(7, 2), comment="経路上の駅間距離の合計（km）。校正のときに使う"
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="算出元（rail_graph=駅データ.jpの接続情報からの自前計算）",
    )
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="算出した時刻。パラメータを変えて計算し直したときの区別に使う",
    )


class NavitimeRoute(TimestampMixin, Base):
    """NAVITIME の乗換案内が返した経路の原文（Phase 5D）。

    **原文をそのまま残す**のは設備の ``raw_features_text`` と同じ考え方で、
    パーサを直したときに再取得せず DB 内から作り直せるようにするため。
    1回の検索で候補が4〜5本返るので、その全部を rank 付きで持つ。

    ⚠ **``origin_label`` を必ず見ること。** NAVITIME は同名異駅を黙って別の駅として
    処理し、HTTP 200 で普通の結果を返す（``大久保`` → ``大久保（東京都）``）。
    どの駅として解決されたかを残しておかないと、間違った所要時間だと気づけない。
    """

    __tablename__ = "t_navitime_routes"
    __table_args__ = (
        UniqueConstraint(
            "origin_station_g_cd",
            "destination_station_g_cd",
            "depart_on",
            "depart_at",
            "rank",
        ),
        {"comment": "NAVITIME の乗換案内が返した経路候補の原文"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="経路ID")
    origin_station_g_cd: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="出発駅の駅グループコード"
    )
    destination_station_g_cd: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="到着駅の駅グループコード（勤務先の最寄り駅）"
    )
    depart_on: Mapped[dt.date] = mapped_column(
        Date, nullable=False, comment="検索した出発日。曜日でダイヤが変わるため条件の一部"
    )
    depart_at: Mapped[dt.time] = mapped_column(
        Time, nullable=False, comment="検索した出発時刻（この時刻以降の便を探す）"
    )
    rank: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="NAVITIME が並べた順（1始まり）"
    )
    total_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="所要時間（分）。乗換の待ち時間を含む"
    )
    transfers: Mapped[int] = mapped_column(Integer, nullable=False, comment="乗換回数")
    distance_km: Mapped[float | None] = mapped_column(Numeric(7, 2), comment="経路の距離（km）")
    fare_yen: Mapped[int | None] = mapped_column(Integer, comment="きっぷ運賃（円）")
    route_depart_at: Mapped[str] = mapped_column(
        String(5), nullable=False, comment="実際の出発時刻（HH:MM）"
    )
    route_arrive_at: Mapped[str] = mapped_column(
        String(5), nullable=False, comment="実際の到着時刻（HH:MM）"
    )
    origin_label: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=(
            "NAVITIME が解決した出発駅の表記。同名異駅では『大久保（東京都）』のように"
            "都道府県が付く。意図した駅かを人が確かめるために残す"
        ),
    )
    destination_label: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="NAVITIME が解決した到着駅の表記"
    )
    origin_node_code: Mapped[str | None] = mapped_column(
        String(20), comment="NAVITIME の駅ノードコード。次回以降の厳密指定に使える"
    )
    destination_node_code: Mapped[str | None] = mapped_column(
        String(20), comment="到着駅の NAVITIME 駅ノードコード"
    )
    route_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="経路の原文（発着時刻・路線・区間所要が1本のテキストで並ぶ）。再解析の入力",
    )
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="取得した時刻"
    )


class RailSegment(TimestampMixin, Base):
    """乗車区間（駅間）の実所要時間（Phase 5D）。

    NAVITIME の経路から「乗った駅 → 降りた駅」を1本の辺として採る。急行が通過する
    駅を飛ばした区間はそのまま1本の辺になるので、**種別を表現する列を持たなくても
    優等列車が経路に乗る**。目的地を変えたときは、この辺の重みでダイクストラを
    回し直せば取得のやり直しが要らない。

    ⚠ **重みに待ち時間は入っていない**（発→着はひと続きの乗車のため）。
    足し合わせても二重計上にならない代わりに、**乗換の待ちは別に足す必要がある**。
    ⚠ **直通運転で列車が変わる地点でも辺は切れる**（``（直通）東京``）。
    その地点の停車時間は辺に含まれないので、辺の合計は実所要をわずかに下回る。
    """

    __tablename__ = "t_rail_segments"
    __table_args__ = (
        UniqueConstraint("from_station_g_cd", "to_station_g_cd", "line_name"),
        {"comment": "乗車区間（駅間）の実所要時間"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="区間ID")
    from_station_g_cd: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="乗車駅の駅グループコード"
    )
    to_station_g_cd: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="降車駅の駅グループコード"
    )
    line_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="路線名。種別を含む表記のまま持つ（『都営三田線急行』）。徒歩は『徒歩』",
    )
    ride_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="乗車時間（分）の最小観測値。辺の重みに使う代表値",
    )
    ride_minutes_max: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="同区間で観測した最大値。ばらつきを人が確かめるために持つ",
    )
    samples: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="観測回数。1件しか無い区間は信用度が低い"
    )
    is_walk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="乗換の徒歩区間か（列車ではない）"
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="採取元（navitime=乗換案内の経路から採取）"
    )
    observed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="最後に観測した時刻"
    )


class SiteScanCursor(TimestampMixin, Base):
    """市区ローテーションのカーソル（→ 課題#36・Phase 5E）。

    HOMES・ATHOME は**1回の実行で取れるリクエスト数に上限がある**
    （実測 2026-09-03。HOMES 5件・ATHOME 4件で、超えるとそれぞれ HTTP 202＋
    空ボディ／パズル認証ページになる）。⚠ **間隔を広げても上限は動かない**ので、
    82市区を毎回先頭から舐める限り後ろの市区は永久に取れない。
    そこで1回の実行では上限ぶんの市区だけ取り、**次回は続きの市区から**始める。

    ⚠ **カーソルは位置番号でなく JIS5桁で持つ。** 市区リストは YAML 編集で
    増減する（課題#32 で実際に4市区を外した）ため、番号だと編集のたびに
    ずれて別の市区へ飛ぶ。``resolve_areas`` は ``jis_code`` 順に決定的へ並ぶので、
    「カーソルより大きい最初の JIS から n 件、末尾に達したら先頭へ戻る」で周回できる。

    ⚠ **キーが (サイト, パターン) なのは帯が2つあるため。** HOMES は両帯の
    ``sites:`` に載っており、素朴に実装すると1回の ``scan`` で 5+5=10 リクエストが
    飛んで後半の帯が全部 202 になる。``last_run_id`` で同一実行の二重消費を防ぎ、
    ``last_scanned_at`` の古い帯から順に回す。

    ⚠ ``m_sites`` へ列を足すのではなくテーブルを新設したのは、監査カラムを
    最終列に保つための**テーブル再作成コストを避ける**ため（Phase 5C・5D と同じ判断）。
    """

    __tablename__ = "t_site_scan_cursors"
    __table_args__ = {"comment": "サイト×検索パターンごとの市区ローテーション位置"}

    site_id: Mapped[int] = mapped_column(
        ForeignKey("m_sites.id", ondelete="CASCADE"),
        primary_key=True,
        comment="対象サイトID",
    )
    pattern_name: Mapped[str] = mapped_column(
        String(255), primary_key=True, comment="検索パターン名（YAML の name）"
    )
    last_city_jis: Mapped[str | None] = mapped_column(
        String(5),
        comment="最後に取得した市区のJIS5桁。次回はこれより大きい最初の市区から始める",
    )
    last_scanned_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="この組で最後にローテーションを回した日時。NULL=未実行（最優先で回す）",
    )
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        comment="最後にローテーションを回した実行ID。同一実行で予算を二重消費しないための印",
    )
