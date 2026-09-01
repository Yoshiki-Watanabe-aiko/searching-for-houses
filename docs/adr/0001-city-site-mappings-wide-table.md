# city_site_mappings をワイドテーブルで持つ

> **状態: 撤回（Superseded by [ADR 0009](./0009-city-site-values-vertical.md)、2026-09-01）**
> 「対応サイト数は10で固定」という前提が崩れたため撤回した。以下は当時の記録として残す。

10サイト分のURLパスセグメント・JISコードなどの「市区町村ごとのサイト別検索値」を、EAV（`site_id, city_id, value`の3列で1サイト1行）ではなく、`city_id`を主キーにしてサイトごとにカラムを固定するワイドテーブル（`suumo`, `homes`, `athome`, ... 列）として持つ。

## 背景・検討した選択肢

- 対応サイト数は10で固定されており、新サイト追加の頻度は低い（サイト追加は`internal/scraper/{site}/`にパッケージを新設する規模の変更を伴うため、DBスキーマだけ動的にしても開発コストは変わらない）。
- EAV方式にすると「サイトが対応していない市区町村」を`NULL`ではなく行の欠如で表現することになり、`city_site_mappings`から都道府県フォールバックの判定（NULL=フォールバック）をSQL上で素直に書けなくなる。
- 1都市×10サイトのJOINをまとめて取得するクエリがワイドテーブルなら単純な`SELECT * FROM city_site_mappings WHERE city_id = ?`で済む。EAVだと`GROUP BY`かPIVOTが必要になる。

## Consequences

- 新しいスクレイピング対象サイトを追加する場合、`city_site_mappings`に`ALTER TABLE ... ADD COLUMN`が必要になる（マイグレーションが伴う）。サイト数が今後大きく増える場合はEAV方式への移行を再検討する。
- `city_site_mappings`のカラム名（`suumo`, `homes`等）は`site_master.code`の小文字と一致させる運用とする。両者がズレるとコード側のマッピング処理で気づきにくい不整合を生むため、サイト追加時は`db/06_site_mappings.sql`と`site_master`の両方を必ず同時に更新する。
