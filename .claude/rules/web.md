---
paths:
  - "src/house_search/web/**"
  - "src/house_search/marks.py"
  - "src/house_search/notify/**"
  - "scripts/run_web.ps1"
  - "tests/test_web*.py"
---

# 閲覧画面・印・ダイジェストの除外の注意

CLAUDE.md の「実装上の注意」から領域別に分けたもの（→ CLAUDE.md の表）。
実際に踏んだ落とし穴なので、該当する処理を書く・直す前に読む。

- ⚠⚠ **閲覧画面の印（お気に入り・除外・メモ）はグループIDに付けない**（→ ADR 0026・課題#68）。`sync_groups` は
  メンバー0件のグループを消し、組み直しでIDが振り直されうるので、付けると**印が黙って外れる**。`t_listing_marks` は
  `listing_id` に付け、グループへの効果は読み出し時に導く。⚠ 「同じグループのどれか1件に印」の判定は
  `marks.mark_exists_sql` の1箇所（閲覧画面の一覧と日次ダイジェストが共用。片方だけ変えると画面とダイジェストが食い違う）
- ⚠⚠ **ダイジェストの除外は LIMIT の前で抜く**（後から捨てると上位N件が減る）。順位（`rank_in_pattern`）・採点・
  個別通知・`check-sold` には効かせない。⚠ `t_listing_marks` を読むので、**本番DBへのマイグレーションは main への
  マージより前**に流す（逆だと 20:00 のダイジェストが落ちる）
- ⚠ **閲覧画面（`web/`）はローカル限定でも `Host` ヘッダと CSRF トークンを検証する**（悪意あるサイトを開いたブラウザが
  127.0.0.1 を読み書きできる → DNS リバインディング・CSRF）。⚠ テンプレートに `|safe` を足さない（物件名・設備原文は
  スクレイピング由来。`tests/test_web_security.py` が禁止を固定）。⚠ JavaScript・インラインスタイルは CSP で止まる。
  ⚠⚠ **`Referrer-Policy` を `no-referrer` にしない**（ブラウザが同じサイトへの POST に `Origin: null` を付け、印が全部 403 になる。
  テストのクライアントは `Origin` を手で付けるので検出できない → 課題#68）。⚠ テンプレートへ None を渡さない（Jinja2 は「None」と描画し、
  フォームの初期値なら次の保存でそのまま書き込まれる）
- ⚠ **flask は閲覧画面だけの依存。** 定期タスクの経路（`scan` / `digest` など）から `house_search.web` や flask を
  モジュール先頭で import しない（`cli._cmd_web` だけが遅延 import。AST テストが固定）。
  ⚠ main の `.venv` への `uv sync` は**走行中の python が無いことを確かめてから**（定期タスクは同じ `.venv` を使う）。
  `run_web.ps1` が `uv run` ではなく `.venv` の python を直接呼ぶのも同じ理由（`uv run` は起動のたびに同期しうる）
