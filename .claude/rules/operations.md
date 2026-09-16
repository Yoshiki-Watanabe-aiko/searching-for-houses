---
paths:
  - "scripts/*.ps1"
  - "scripts/lib/**"
  - "src/house_search/cli.py"
  - "src/house_search/pipeline/runtime.py"
  - "src/house_search/pipeline/tasks.py"
---

# 定期タスク・運用スクリプト・CLI の注意

CLAUDE.md の「実装上の注意」から領域別に分けたもの（→ CLAUDE.md の表）。
実際に踏んだ落とし穴なので、該当する処理を書く・直す前に読む。

- ⚠ **売買4パターンは2時間ごとの `scan` に同居させない**（`--family` で分ける → 課題#4）。
  4都県173市区で一覧692リクエスト≒30分が加わり上限 PT1H50M に迫る。
  `HouseSearch-ScanBuy`（毎日 10:25）が `scan --family MANSION_BUY --family KODATE_BUY --family TOCHI_BUY` →
  `check-sold`（同・上位30＋古い順10/パターン）を回す。⚠ 08:40 の check-sold に売買を含めると
  最大600件で PT1H を超える。⚠ `--family` で絞った結果が空なら例外にする（黙って0件で正常終了しない）
- **レート制御は `SiteFetcher` のプロセス内にしかない。** 別プロセスの `scan` 同士や
  `scan` と `check-sold` が並走すると同一サイトへの実効間隔が半分になる。
  タスクのトリガー時刻を分離し、初回スキャン中は取得タスクを無効にしてあるのはこのため。
  ⚠ **`scan` / `check-sold` は `pg_advisory_lock` でも排他されるが、`fetch-commutes`
  はこのロックを取らない。** NAVITIME と物件サイトは別ホストなので定期スキャンとの
  並走は意図どおりだが、⚠ **`fetch-commutes` どうしの並走は禁止**（実効間隔が
  15秒 → 7.5秒になり `Crawl-delay: 10` を破る）。地方の取り直しは進行中の取得の完了後に行う
- ⚠ **`--pattern` を複数指定できるのは `scan` / `check-sold` / `digest` / `rescore` だけ**
  （→ 課題#60）。`fetch-commutes` などは「対象を1つに絞る」仕様なので**最後の1つ**になる。
  ⚠ 4つは1つでも見つからなければ例外にする（綴り違いを黙って捨てない）
- ⚠ **`scan` が終わったかを `t_scrape_runs.status` で判定しない。** この表は
  **サイトごとの run** を持つので、全サイトが `completed` になっても本体は
  名寄せ・採点・通知の後処理を続けており、**取得ロックは解放されていない**。
  手動スキャンを流したいときは **タスクの `State`**（`Get-ScheduledTask`）か
  **プロセスの生存**で判定する。⚠ `pg_stat_activity` で
  `pg_try_advisory_lock` を保持したまま `idle in transaction` の行が見えるが、
  **これは死んだセッションではなく正常な保持中**なので `pg_terminate_backend` しない
  （実測 2026-09-04: 03:15 起動の定期スキャンが 04:0x まで保持していた）
- ⚠⚠ **タスクが実行時間の上限で打ち切られても、子の python は生き残って走り続ける**（→ 課題#67）。
  止められるのは親の `task_runner.ps1` だけで、タスクの `State` は `Ready`・前回の結果は 267014 になるが、
  **python は取得ロックを握ったまま後処理まで完走する**（実測 2026-09-15: 11:05 に上限到達 → python は 11:10 に終了）。
  ⚠ **このときだけはタスクの `State` で完了を判定できない**（プロセスの親子関係か `pg_locks` で見る）。
  ⚠ 子まで止める対策は採らない（後処理の前に落ちると新着通知が永久に失われる → 課題#63）。
  上限を超えさせないために、**打ち切ったサイトは同じ実行の後続パターンで取りに行かない**（`Runtime.aborted_sites`）
- **`scan` はサイトを直列に回す。** 増分でも約72分かかるので毎時実行には収まらない
  （一覧1116リクエスト＋詳細320リクエスト）。タスクは2時間ごと
- **PowerShell 5.1 は stdout と stderr に同じファイルを指定できない。**
  `Start-Process` のリダイレクトは必ず別ファイルにする
- ⚠ **python を `& $Python` で呼ぶ運用スクリプトは、冒頭（ワーカー部）で出力を UTF-8 に揃える**
  （`. (Join-Path $PSScriptRoot "lib\utf8_output.ps1")` → `Set-Utf8ConsoleOutput`）。
  揃えないと PowerShell 自身の行（cp932）と python の行（UTF-8）が同じログで混在し、▶ は「?」に化ける。
  ⚠ **関数の出力を `| Out-Null` で捨てない**——PowerShell の関数は出力をすべて戻り値として返すので、
  終了コードのつもりで捨てると python の標準出力（`scan` の実行サマリ）まで消える（→ 課題#61）。
  ⚠ ログを追うときは `Get-Content … -Wait -Encoding UTF8`
- ⚠⚠ **タスクの定義（時刻・上限・引数）を直したら「登録」を通す。`-EnableOnly` では反映されない**
  （→ 課題#71）。`-EnableOnly` は `schtasks /change /enable` だけを行う経路で、
  ⚠ **画面には「成功: …のパラメーターは変更されました。」「[有効化] …」が並び終了コードも0**なので
  **成功に見えるまま古い定義が残る**（2026-09-16 に棚卸しの 02:35 が2回とも反映されなかった）。
  反映は `(Get-ScheduledTask -TaskName 'HouseSearch-Sweep').Triggers.StartBoundary` で確かめる。
  ⚠ 登録は定義を置き換えるので**走行中のタスクがあるときは行わない**（既定で拒否する）
- **タスク用スクリプトと切り離し用スクリプトを流用し合わない。**
  `run_initial_scan.ps1` は `Start-Process` で切り離す側、`task_runner.ps1` は
  `-Wait` で待つ側。前者をタスクから呼ぶと即完了扱いになり二重起動する
- **S4U のタスク登録には管理者権限が要る**（`SeTcbPrivilege`）。
  通常アカウント `wy469` は標準ユーザーで `BUILTIN\Administrators` に入っていない
- **定期タスクはコンソールウィンドウを出さないので、共通の無画面ランチャー
  （`~/.claude/scripts/hidden_launcher.py`）で包まない**（→ 課題#72）。8本とも `LogonType` が **S4U** で、
  実行中の powershell・python は **SessionId 0**（非対話）で動く（2026-09-17 実測。利用者のシェルは SessionId 1・
  可視ウィンドウ0）。対話デスクトップにウィンドウを出す経路がそもそも無い。
  ⚠ **`LogonType` を `InteractiveToken` に変えるとウィンドウが出るようになる**（標準ユーザーでも登録できる代わりに
  ログオン中のデスクトップで動く）。そのときに初めてランチャーで包む
