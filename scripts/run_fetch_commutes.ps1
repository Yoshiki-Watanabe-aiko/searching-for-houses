# ============================================================
# 物件検索通知システム v2 - 通勤時間の実ダイヤ取得（Phase 5D）
#
# 使い方:
#   .\scripts\run_fetch_commutes.ps1                     # 未取得の駅を全部
#   .\scripts\run_fetch_commutes.ps1 -Pattern 東京23区賃貸
#   .\scripts\run_fetch_commutes.ps1 -Limit 50           # 試し取り
#
# 何をするか:
#   NAVITIME の乗換案内から、掲載が最寄り駅として挙げている駅ぶんだけ
#   「その駅 → 勤務先の最寄り駅」の実ダイヤを取り、次の3つへ落とす。
#     t_navitime_routes … 経路の原文（パーサを直したら再解析できる）
#     t_rail_segments   … 乗車区間（駅間）の実所要時間
#     t_station_commutes… 採点が読む所要時間（source='navitime'）
#
# 所要時間の目安:
#   1駅あたり15秒（robots.txt の Crawl-delay 10 秒を、±30%のジッタが
#   下振れしても割らない値）。1,150駅で約4.8時間。
#   → 夜間に開始すること。1駅ごとにコミットするので途中で止めても
#     再実行すれば続きから進む（取得済みの駅は自動でスキップされる）。
#
# 注意（いずれも実際に踏んだ罠）:
#   - エージェント（Claude Code）のバックグラウンドから起動しない。
#     標準出力がパイプになり、読み手が居なくなった時点で print がブロックし
#     例外も出さずに止まる。本スクリプトは Start-Process で切り離して
#     stdout/stderr を「別々のファイル」へ落とす（PowerShell 5.1 は
#     out と err に同じファイルを指定できない）
#   - 止まったかどうかを CPU 使用率で判断しない。この処理はレート待ちの
#     sleep が大半で、正常でも CPU はほぼ 0 になる。生死はログの更新時刻と
#     t_navitime_routes の行数で見る
#   - タスクスケジューラからは呼ばない（切り離し版はタスクが即「完了」扱いになる）
#   - 取得タスク（HouseSearch-Scan）と並走させない。レート制御はプロセス内に
#     しかないので、並走すると同一サイトへの実効間隔が半分になる
#   - 出発日 (-DepartOn) を変えると取得のキーが変わり全駅を取り直すことになる。
#     既定の 2026-09-09（水）から動かす理由が無いなら触らない
# ============================================================

param(
    # ワーカーとして動く（ランチャーが内部的に付ける。手で指定しない）
    [switch]$Worker,
    # 対象を1つの検索パターンに絞る（省略時は掲載が挙げる全駅）
    [string]$Pattern = "",
    # 取得する駅数の上限（試し取り用。0 なら残り全部）
    [int]$Limit = 0,
    # 出発日。⚠ 変えると全駅を取り直すことになる
    [string]$DepartOn = "2026-09-09",
    # 出発時刻
    [string]$DepartAt = "08:30"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir   = Join-Path $RepoRoot "logs"

if (-not (Test-Path $Python)) {
    throw "Python が見つかりません: $Python （uv sync を実行してください）"
}
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# ---- ランチャー: 切り離して即座に戻る ------------------------------------
if (-not $Worker) {
    $stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $LogDir "fetch_commutes`_$stamp.out.log"
    $errLog = Join-Path $LogDir "fetch_commutes`_$stamp.err.log"

    $childArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"", "-Worker",
        "-DepartOn", $DepartOn, "-DepartAt", $DepartAt, "-Limit", $Limit
    )
    if ($Pattern -ne "") { $childArgs += @("-Pattern", $Pattern) }

    # out と err は必ず別ファイル（5.1 は同一ファイルを指定できない）
    $proc = Start-Process -FilePath "powershell.exe" `
                          -ArgumentList $childArgs `
                          -WindowStyle Hidden `
                          -RedirectStandardOutput $outLog `
                          -RedirectStandardError  $errLog `
                          -PassThru

    Write-Host ""
    Write-Host "通勤時間の実ダイヤ取得を切り離して開始しました（PID $($proc.Id)）" -ForegroundColor Green
    Write-Host "  対象パターン: $(if ($Pattern -ne '') { $Pattern } else { '（全パターン）' })"
    Write-Host "  出発         : $DepartOn $DepartAt"
    Write-Host "  駅数の上限   : $(if ($Limit -gt 0) { $Limit } else { '（残り全部）' })"
    Write-Host "  標準出力     : $outLog"
    Write-Host "  標準エラー   : $errLog"
    Write-Host ""
    Write-Host "進捗の追い方:" -ForegroundColor Cyan
    Write-Host "  Get-Content `"$outLog`" -Tail 20 -Wait"
    Write-Host "  Get-Process -Id $($proc.Id) -ErrorAction SilentlyContinue   # 生きているか"
    Write-Host ""
    Write-Host "⚠ CPU 使用率で生死を判断しないこと。レート待ちの sleep が大半で" -ForegroundColor Yellow
    Write-Host "  正常でも CPU はほぼ 0 になる。ログの更新時刻を見る。" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "完了後にやること:" -ForegroundColor Cyan
    Write-Host "  uv run house-search commute-stats   # 分布を見て best/worst を決め直す"
    Write-Host "  uv run house-search rescore         # 実ダイヤで再採点"
    exit 0
}

# ---- ワーカー: ここから先は切り離されたプロセスで動く ----------------------
# 標準出力・標準エラーはランチャーが「ファイル」へ向けている（パイプではない）ため
# ここで直接 & 呼び出ししても詰まらない。
$ErrorActionPreference = "Continue"

function Write-Step {
    param([string]$Message)
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

Set-Location $RepoRoot
Write-Step "==== 通勤時間の実ダイヤ取得 開始 ===="
Write-Step "リポジトリ: $RepoRoot"
Write-Step "出発      : $DepartOn $DepartAt"

$fetchArgs = @("fetch-commutes", "--depart-on", $DepartOn, "--depart-at", $DepartAt)
if ($Pattern -ne "") { $fetchArgs += @("--pattern", $Pattern) }
if ($Limit -gt 0)    { $fetchArgs += @("--limit", "$Limit") }

$started = Get-Date
& $Python -m house_search.cli @fetchArgs
$code = $LASTEXITCODE
$elapsed = (Get-Date) - $started
Write-Step ("==== 通勤時間の実ダイヤ取得 終了 コード=$code 所要 {0:hh\:mm\:ss}" -f $elapsed)
Write-Step "次にやること: commute-stats → rescore"
exit $code
