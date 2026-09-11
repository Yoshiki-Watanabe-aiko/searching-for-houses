# ============================================================
# 物件検索通知システム v2 - 家賃相場の月次更新（課題#49）
#
# 使い方:
#   .\scripts\update_market_rates.ps1              # 取得 → CSV生成 → DB投入
#   .\scripts\update_market_rates.ps1 -SkipFetch   # 保存済みHTMLから作り直すだけ
#
# 何をするか:
#   1. SUUMO の家賃相場ページから取得して data/market_rates/raw/{YYYY-MM}/ へ保存
#      （全国47都道府県・1,277市区＋アパート相場の補完。3秒間隔で約90分）
#   2. data/market_rates/rent_rates.csv を作り直す
#   3. sync-market-rates で m_market_rates へ投入する
#      （⚠ 全置換ではなく upsert。period が違えば別の行として残る）
#
# 注意（いずれも実際に踏んだ罠）:
#   - ⚠⚠ **毎月ぶんを取り直す**（2026-09-09 に修正）。保存先を月ディレクトリに
#     したのがその実体で、旧実装は月を含まない raw/{jis}.html を見ていたため
#     **毎月1日にタスクが走っても1本も取得していなかった**。それでいて period は
#     生成時の年月になるので、古い相場が新しい月のラベルでDBに入っていた（→ 課題#49）
#   - ⚠ 取得は scraping_lock を取るので定期スキャンとは構造的に排他される
#     （レート制御は SiteFetcher のプロセス内にしかない → ADR 0013 決定8）。
#     全国だと約90分かかり、**05:15 の定期スキャンが1回スキップされる**
#     （07:15 には間に合う。データは壊れず新着の検知が2時間遅れるだけ）
#   - ⚠ ロックが取れなければ**非0で終わる**。月1回の処理なので黙って飛ばすと
#     1ヶ月ぶん古い相場のまま気づけない（scan は2時間後に再実行されるので 0 でよい）
#   - ⚠ raw は当月＋先月の2ヶ月ぶんだけ残す（全国だと1ヶ月あたり約100MB）。
#     売買（窓4四半期を集計に使うので消さない）と方針が違うのは、賃貸は
#     **当月ぶんしか集計に使わない**ため
#   - ⚠ uv ではなく .venv\Scripts\python.exe をフルパスで叩く。
#     タスクは PATH の通らない環境で動きうる
#   - ⚠ CSV は Git 管理下の生成物。更新されてもこのスクリプトはコミットしない
#     （自動コミットは差分をレビューできなくする）。変わったら最後に知らせるので、
#     内容を確認してから手でコミットする
#   - ⚠ 失敗したら 0 以外で終わること。タスクスケジューラの「前回の結果」が
#     唯一の異常検知経路になる
# ============================================================

param(
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"

# ⚠ 出力を UTF-8 に揃える（揃えないとタスクのログで cp932 と UTF-8 が混在する → lib\utf8_output.ps1）
. (Join-Path $PSScriptRoot "lib\utf8_output.ps1")
Set-Utf8ConsoleOutput

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Builder  = Join-Path $RepoRoot "scripts\tools\build_market_rates.py"
$Csv      = Join-Path $RepoRoot "data\market_rates\rent_rates.csv"

if (-not (Test-Path $Python)) {
    Write-Error "Python が見つかりません: $Python （uv sync を実行してください）"
    exit 1
}
if (-not (Test-Path $Builder)) {
    Write-Error "生成スクリプトが見つかりません: $Builder"
    exit 1
}

# 更新されたかを後で判定するために、実行前のハッシュを控える。
# ⚠ git に頼らない（タスクの実行環境で git.exe が PATH にあるとは限らない）
$before = if (Test-Path $Csv) { (Get-FileHash -Path $Csv -Algorithm SHA256).Hash } else { $null }

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 家賃相場の更新を開始します"

# ---- 1〜2. 取得とCSV生成 --------------------------------------------------
$buildArgs = @($Builder)
if (-not $SkipFetch) {
    $buildArgs += "--fetch"
    Write-Output "  SUUMO から取得します（全国・3秒間隔・約90分）"
} else {
    Write-Output "  取得を省き、保存済みHTMLから作り直します"
}

& $Python @buildArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "相場CSVの生成に失敗しました（終了コード $LASTEXITCODE）"
    exit $LASTEXITCODE
}

# ---- 3. DBへ投入 ----------------------------------------------------------
Write-Output "  m_market_rates へ投入します"
& $Python -m house_search.cli sync-market-rates
if ($LASTEXITCODE -ne 0) {
    Write-Error "相場のDB投入に失敗しました（終了コード $LASTEXITCODE）"
    exit $LASTEXITCODE
}

# ---- 後始末: CSVが変わったら知らせる --------------------------------------
$after = if (Test-Path $Csv) { (Get-FileHash -Path $Csv -Algorithm SHA256).Hash } else { $null }
if ($before -ne $after) {
    Write-Output ""
    Write-Output "  ⚠ rent_rates.csv が更新されました。Git 管理下の生成物なので"
    Write-Output "    内容を確認してコミットしてください:"
    Write-Output "      git diff --stat data/market_rates/rent_rates.csv"
} else {
    Write-Output "  rent_rates.csv に変化はありませんでした"
}

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 家賃相場の更新が完了しました"
exit 0
