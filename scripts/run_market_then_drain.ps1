# ============================================================
# 物件検索通知システム v2 - 相場の更新 → 売買の掃き出し（夜間の臨時実行）
#
# 使い方（1行ずつ実行する。&& は使えない）:
#   .\scripts\run_market_then_drain.ps1               # 相場（約90分）→ 掃き出し
#   .\scripts\run_market_then_drain.ps1 -SkipMarket   # 掃き出しだけ
#   .\scripts\run_market_then_drain.ps1 -SkipDrain    # 相場だけ
#
# なぜ1本にまとめるか:
#   ⚠ どちらも取得ロック（pg_advisory_lock）を取るので**同時には走れない**。
#     別々に起動すると後から起動したほうがロックを取れずに終わる。
#   ⚠ `run_initial_scan.ps1` はランチャー（Start-Process で切り離して即座に戻る）なので、
#     単純に2本並べても順次実行にならない。ここでは -Worker を付けて同期実行する。
#
# 注意（いずれも実際に踏んだ罠）:
#   - ⚠ エージェント（Claude Code）のバックグラウンドから起動しない。
#     標準出力がパイプになり、読み手がいなくなると print がブロックして
#     **例外も出さずに止まる**。本スクリプトは Start-Process で切り離す
#   - ⚠ stdout と stderr は別ファイルへ（PowerShell 5.1 は同一ファイルを指定できない）
#   - ⚠ 相場の更新は毎月1日 04:30 のタスクでも走る。本スクリプトは
#     **初回・臨時用**（タスクの再登録前や、その月ぶんを前倒しで取りたいとき）
# ============================================================

param(
    # ワーカーとして動く（ランチャーが内部的に付ける。手で指定しない）
    [switch]$Worker,
    # 相場の更新を飛ばす
    [switch]$SkipMarket,
    # 掃き出しを飛ばす
    [switch]$SkipDrain,
    # 掃き出しの詳細取得の上限（サイトあたり）
    [int]$DetailLimit = 2000,
    # 掃き出しの対象ファミリ
    [string[]]$Family = @("MANSION_BUY", "KODATE_BUY")
)

$ErrorActionPreference = "Stop"

# ⚠ -File 経由だと "A,B" は分割されず1要素で届く（→ 課題#4）
$Family = @($Family | ForEach-Object { $_ -split "," } | Where-Object { $_ } | ForEach-Object { $_.Trim() })

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir   = Join-Path $RepoRoot "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# ---- ランチャー: 切り離して即座に戻る --------------------------------------
if (-not $Worker) {
    $stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $LogDir "market_then_drain_$stamp.out.log"
    $errLog = Join-Path $LogDir "market_then_drain_$stamp.err.log"

    $childArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"", "-Worker",
        "-DetailLimit", $DetailLimit
    )
    if ($SkipMarket) { $childArgs += "-SkipMarket" }
    if ($SkipDrain)  { $childArgs += "-SkipDrain" }
    if ($Family.Count -gt 0) { $childArgs += @("-Family", ($Family -join ",")) }

    $proc = Start-Process -FilePath "powershell.exe" `
                          -ArgumentList $childArgs `
                          -WindowStyle Hidden `
                          -RedirectStandardOutput $outLog `
                          -RedirectStandardError  $errLog `
                          -PassThru

    Write-Host ""
    Write-Host "夜間バッチを切り離して開始しました（PID $($proc.Id)）" -ForegroundColor Green
    if (-not $SkipMarket) { Write-Host "  1. 家賃相場の全国更新（約90分）" }
    if (-not $SkipDrain)  { Write-Host "  2. 売買の掃き出し（詳細 $DetailLimit 件/サイト・ファミリ $($Family -join ',')）" }
    Write-Host "  標準出力    : $outLog"
    Write-Host "  標準エラー  : $errLog"
    Write-Host ""
    Write-Host "進捗の追い方:" -ForegroundColor Cyan
    Write-Host "  Get-Content `"$outLog`" -Tail 20 -Wait"
    Write-Host ""
    Write-Host "⚠ 定期スキャンは取得ロックでスキップされます（データは壊れません）"
    exit 0
}

# ---- ワーカー: ここから同期実行 --------------------------------------------
function Write-Step {
    param([string]$Message)
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

Write-Step "==== 夜間バッチ開始 ===="
$failed = @()

if (-not $SkipMarket) {
    Write-Step "---- 1. 家賃相場の全国更新 ----"
    & (Join-Path $PSScriptRoot "update_market_rates.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Step "⚠ 家賃相場の更新が失敗しました（終了コード $LASTEXITCODE）"
        $failed += "market-rates"
    } else {
        Write-Step "家賃相場の更新が完了しました"
    }
}

if (-not $SkipDrain) {
    Write-Step "---- 2. 売買の掃き出し ----"
    # ⚠ -Worker を付けて同期実行する（付けないとランチャーが切り離して即座に戻り、
    #   相場の直後に起動して取得ロックの取り合いになる）
    # ⚠⚠ **ハッシュテーブルで渡す。配列にしてはいけない。**
    #   PowerShell の配列 splatting（@配列）は中身を**すべて位置引数**として展開するため、
    #   "-Worker" が [int]$DetailLimit の値に食われて型変換で落ちる。
    #   2026-09-09 に実際に起きた（→ 課題#4）:
    #     「パラメーター 'DetailLimit' の引数変換を処理できません。
    #       値 "-Worker" を型 "System.Int32" に変換できません」
    #   ⚠ ランチャーは子の失敗を検知しないので、**呼び出し側は終了コード0で正常終了する**。
    #     ログを読まないと気づけない。
    $drainArgs = @{ Worker = $true; Drain = $true; DetailLimit = $DetailLimit }
    # ⚠ Family は文字列で渡す（run_initial_scan.ps1 が -File 経由の "A,B" を
    #   カンマで割る実装に合わせてある）
    if ($Family.Count -gt 0) { $drainArgs["Family"] = ($Family -join ",") }
    & (Join-Path $PSScriptRoot "run_initial_scan.ps1") @drainArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Step "⚠ 掃き出しが失敗しました（終了コード $LASTEXITCODE）"
        $failed += "drain"
    } else {
        Write-Step "掃き出しが完了しました"
    }
}

Write-Step "==== 夜間バッチ終了 ===="
if ($failed.Count -gt 0) {
    Write-Step "失敗: $($failed -join ', ')"
    exit 1
}
Write-Step "次にやること: 相場の解決率を market-stats で確認 / 残キューを数える"
exit 0
