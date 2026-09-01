# data/

Git管理する運用データの置き場。

| ファイル | 内容 | 追加予定 |
|---|---|---|
| `feature_dictionary.yaml` | 設備抽出辞書（条件コード → 表記パターン）。ここが正典で、`house-search sync-dict` が `m_condition_synonyms` へupsertする | Phase 1（賃貸ブロック）/ Phase 6（売買ブロック） |

辞書をGit管理YAMLで持つのは、変更をdiffレビューできるようにするため。
実行時はDBを参照してJOINする。
