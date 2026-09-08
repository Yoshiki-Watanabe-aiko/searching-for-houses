# ============================================================
# 物件検索通知システム v2 - 売買相場の四半期更新（課題#49 Step 8）
#
# 使い方:
#   .\scripts\update_buy_market_rates.ps1              # 取得 → CSV生成 → DB投入
#   .\scripts\update_buy_market_rates.ps1 -SkipFetch   # 保存済みJSONから作り直すだけ
#   .\scripts\update_buy_market_rates.ps1 -Force       # 新しい四半期が無くても作り直す
#
# 何をするか:
#   1. 国交省「不動産情報ライブラリ」XIT001 から最新4四半期を取得して
#      data/market_rates/raw/reinfolib/ へ保存（4都県×4期＝16ファイル・10秒間隔で約3分）
#   2. data/market_rates/buy_rates.csv を作り直す（市区×単価区分の中央値）
#   3. sync-market-rates --buy で m_market_rates へ投入する（売買ぶんを全置換）
#
# 注意（いずれも実際に踏んだ罠、または踏みうる罠）:
#   - ⚠⚠ 新しい四半期が公開されていなければ 2〜3 を飛ばす。
#     取得が「既存は飛ばす」実装なので、公開前に流すと中身が同じCSVを
#     acquired_on だけ変えて書き直すことになり、毎回コミットを促されて
#     「変わっていないこと」が読み取れなくなる。作り直したいときは -Force
#   - ⚠ 国交省の公開は四半期終了後2〜3ヶ月（実測では2026-09-08 時点で 2026Q1 が最新）。
#     四半期タスクで空振りすることは普通に起こる。異常ではない
#   - ⚠ APIキー（MLIT_REINFOLIB_API_KEY）は取得スクリプトが .env から読み、
#     ヘッダでのみ送る。このスクリプトはキーに触らない（ログにも出さない）
#   - ⚠ 家賃相場の更新（毎月1日 04:30）と同じ日に走らせない。
#     このタスクは 1/4/7/10月の「2日」04:30 に置いてある
#   - ⚠ uv ではなく .venv\Scripts\python.exe をフルパスで叩く。
#     タスクは PATH の通らない環境で動きうる
#   - ⚠ CSV は Git 管理下の生成物。更新されてもこのスクリプトはコミットしない
#     （自動コミットは差分をレビューできなくする）
#   - ⚠ 空振り（新しい四半期なし）でも data/market_rates/reinfolib_manifest.json の
#     inspected_on だけは更新される。これは「原典の版を検査した日」の記録なので
#     更新されるのが正しい（相場の値ではない）。CSVと違って書き分けていないのは
#     そのため。年4回なので差分のノイズも軽い
#   - ⚠ 失敗したら 0 以外で終わること。タスクスケジューラの「前回の結果」が
#     唯一の異常検知経路になる
# ============================================================

param(
    [switch]$SkipFetch,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Fetcher  = Join-Path $RepoRoot "scripts\tools\fetch_reinfolib_trades.py"
$Builder  = Join-Path $RepoRoot "scripts\tools\build_buy_market_rates.py"
$RawDir   = Join-Path $RepoRoot "data\market_rates\raw\reinfolib"
$Csv      = Join-Path $RepoRoot "data\market_rates\buy_rates.csv"

if (-not (Test-Path $Python)) {
    Write-Error "Python が見つかりません: $Python （uv sync を実行してください）"
    exit 1
}
foreach ($script in @($Fetcher, $Builder)) {
    if (-not (Test-Path $script)) {
        Write-Error "スクリプトが見つかりません: $script"
        exit 1
    }
}

function Get-RawCount {
    if (-not (Test-Path $RawDir)) { return 0 }
    return @(Get-ChildItem -Path $RawDir -Filter "xit001_*.json" -File -ErrorAction SilentlyContinue).Count
}

# 更新されたかを後で判定するために、実行前のハッシュを控える。
# ⚠ git に頼らない（タスクの実行環境で git.exe が PATH にあるとは限らない）
$before = if (Test-Path $Csv) { (Get-FileHash -Path $Csv -Algorithm SHA256).Hash } else { $null }

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 売買相場の更新を開始します"

# ---- 1. 取得 --------------------------------------------------------------
$rawBefore = Get-RawCount
if (-not $SkipFetch) {
    Write-Output "  不動産情報ライブラリから取得します（10秒間隔・最大約3分）"
    & $Python $Fetcher --fetch
    if ($LASTEXITCODE -ne 0) {
        Write-Error "取引価格の取得に失敗しました（終了コード $LASTEXITCODE）"
        exit $LASTEXITCODE
    }
} else {
    Write-Output "  取得を省き、保存済みJSONから作り直します"
}
$rawAfter = Get-RawCount
$added = $rawAfter - $rawBefore
Write-Output "  保存済みの応答: $rawBefore → $rawAfter ファイル（新規 $added）"

# ⚠ 新しい四半期が無ければここで終える。中身の同じCSVを acquired_on だけ
#   変えて書き直すと、本当に相場が動いたときの差分が埋もれる
if ($added -eq 0 -and -not $Force -and -not $SkipFetch) {
    Write-Output "  新しい四半期は公開されていません（国交省の公開は四半期終了後2〜3ヶ月）"
    Write-Output "  相場は変わらないのでCSVとDBはそのままにします（作り直すなら -Force）"
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 売買相場の更新が完了しました（更新なし）"
    exit 0
}

# ---- 2. CSV生成 -----------------------------------------------------------
Write-Output "  buy_rates.csv を作り直します（最新4四半期・150MB超のJSONを読むので1〜2分）"
& $Python $Builder
if ($LASTEXITCODE -ne 0) {
    Write-Error "売買相場CSVの生成に失敗しました（終了コード $LASTEXITCODE）"
    exit $LASTEXITCODE
}

# ---- 3. DBへ投入 ----------------------------------------------------------
Write-Output "  m_market_rates へ投入します（売買ぶん）"
& $Python -m house_search.cli sync-market-rates --buy
if ($LASTEXITCODE -ne 0) {
    Write-Error "売買相場のDB投入に失敗しました（終了コード $LASTEXITCODE）"
    exit $LASTEXITCODE
}

# ---- 後始末: CSVが変わったら知らせる --------------------------------------
$after = if (Test-Path $Csv) { (Get-FileHash -Path $Csv -Algorithm SHA256).Hash } else { $null }
if ($before -ne $after) {
    Write-Output ""
    Write-Output "  ⚠ buy_rates.csv が更新されました。Git 管理下の生成物なので"
    Write-Output "    内容を確認してコミットしてください:"
    Write-Output "      git diff --stat data/market_rates/buy_rates.csv"
    Write-Output "    ⚠ 相場の水準が動いたら、売買4パターンの best/worst を"
    Write-Output "      付け直すか検討してください（→ 課題#31・#34）"
} else {
    Write-Output "  buy_rates.csv に変化はありませんでした"
}

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 売買相場の更新が完了しました"
exit 0
