"""実行時に共有するオブジェクト一式。

設定・DB接続・辞書・マスタ引き当て表・Discord送信をひとまとめにして、
各コマンドが同じ初期化を繰り返さないようにする。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from house_search.config.settings import Settings, load_settings
from house_search.db.session import create_db_engine
from house_search.extract.dictionary import FeatureDictionary, load_dictionary, load_from_db
from house_search.notify.discord import DiscordSender, build_sender
from house_search.pipeline import persist
from house_search.scrape.fetch import build_client

DICTIONARY_FILENAME = "feature_dictionary.yaml"


@dataclass(slots=True)
class Runtime:
    """1回の実行が使う道具立て。"""

    settings: Settings
    engine: Engine
    run_id: uuid.UUID
    dictionary: FeatureDictionary
    condition_ids: dict[str, int]
    site_ids: dict[str, int]
    property_type_ids: dict[str, int]
    # 全国分の索引。検索パターンごとの都道府県への絞り込みは
    # scan 側で scoped_to() して行う（パターンごとに対象県が違うため）。
    city_index: persist.CityIndex = field(
        default_factory=lambda: persist.CityIndex.build([])
    )
    _sender: DiscordSender | None = None

    @property
    def sender(self) -> DiscordSender:
        """Discord送信クライアント（初回参照時に作る）。"""
        if self._sender is None:
            self._sender = build_sender(user_agent=self.settings.user_agent)
        return self._sender

    def http_client(self, *, user_agent: str | None = None):
        """スクレイピング用のHTTPクライアント。

        ``user_agent`` を渡すとそのサイトだけ名乗りを差し替える
        （既定のUAを 403 で拒否するサイトがあるため）。
        """
        return build_client(
            user_agent=user_agent or self.settings.user_agent,
            timeout_sec=self.settings.request_timeout_sec,
        )

    def dictionary_path(self) -> Path:
        return self.settings.data_dir / DICTIONARY_FILENAME

    def notify_error(self, title: str, detail: str) -> None:
        """グローバルエラーチャンネルへ通知する（未設定なら何もしない）。"""
        from house_search.notify.format import build_error_message

        url = self.settings.discord_webhook_errors
        if not url:
            return
        self.sender.send(url, build_error_message(title=title, detail=detail))


def build_runtime(*, use_test_db: bool = False, prefer_db_dictionary: bool = True) -> Runtime:
    """設定を読み、DBに接続してランタイムを組み立てる。

    辞書はDB（``sync-dict`` 済み）を優先し、空ならYAMLから直接読む。
    初回実行で ``sync-dict`` を忘れていても動くようにするための保険で、
    その場合は呼び出し側が同期を促す。
    """
    settings = load_settings()
    url = settings.database_test_url if use_test_db else settings.database_url
    if not url:
        raise ValueError("DATABASE_TEST_URL が .env に設定されていません")
    engine = create_db_engine(url)

    dictionary = FeatureDictionary()
    if prefer_db_dictionary:
        dictionary = load_from_db(engine)
    if not dictionary.entries:
        path = settings.data_dir / DICTIONARY_FILENAME
        if path.exists():
            dictionary = load_dictionary(path)

    with engine.connect() as conn:
        condition_ids = persist.load_lookup(conn, "m_conditions")
        site_ids = persist.load_lookup(conn, "m_sites")
        property_type_ids = persist.load_lookup(conn, "m_property_types")
        city_index = persist.load_city_index(conn)

    return Runtime(
        settings=settings,
        engine=engine,
        run_id=uuid.uuid4(),
        dictionary=dictionary,
        condition_ids=condition_ids,
        site_ids=site_ids,
        property_type_ids=property_type_ids,
        city_index=city_index,
    )
