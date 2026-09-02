# ============================================================
# 物件検索通知システム v2 - タスクスケジューラから呼ばれる実行スクリプト
#
# 使い方（登録は register_tasks.ps1 が行う。手動確認にも使える）:
#   .\scripts\task_runner.ps1 -Task scan
#   .\scripts\task_runner.ps1 -Task check-sold
#   .\scripts\task_runner.ps1 -Task digest
#   .\scripts\task_runner.ps1 -Task backup
#
# ⚠ run_initial_scan.ps1 を流用してはいけない。
#   あちらは Start-Process で処理を「切り離す」ため、タスクから呼ぶと
#   タスクが即「完了」扱いになり、
#     ① 実行時間の上限（ExecutionTimeLimit）が一切効かない
#     ② 実行中とみなされず次のトリガーで二重に起動する
#   本スクリプトは -Wait でタスクの子として待たせる。
#
# 注意:
#   - .venv\Scripts\python.exe をフルパスで叩く。タスクは PATH が対話シェルと違う
#   - 標準出力と標準エラーは別ファイルへ（PowerShell 5.1 は同一ファイル不可）
#   - 子プロセスの終了コードをそのまま返す。タスクの「前回の結果」が
#     唯一の異常検知経路になるため、握り潰さない
#   - scan は1サイトでもエラーがあれば 1 を返す。HOME'S の WAF（課題#17）で
#     1 になることがあるが、他サイトの取得は完了している
# ============================================================

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("scan", "sweep", "check-sold", "digest", "backup")]
    [string]$Task,

    # ログの保持日数。超過した task_*.log を起動時に掃除する
    [int]$LogRetentionDays = 30
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir   = Join-Path $RepoRoot "logs"

if (-not (Test-Path $Python)) {
    Write-Error "Python が見つかりません: $Python （uv sync を実行してください）"
    exit 1
}
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# ---- 古いログの掃除 -------------------------------------------------------
$cutoff = (Get-Date).AddDays(-$LogRetentionDays)
Get-ChildItem -Path $LogDir -Filter "task_*.log" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# ---- 実行するコマンドを決める ---------------------------------------------
# scan は増分（一覧1ページ）。--full は所要時間が約5倍になり2時間枠に収まらない。
# 取りこぼしは全件スキャンの再実行で補う（→ 課題#22 の観測対象）
$stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$outLog = Join-Path $LogDir "task_$Task`_$stamp.out.log"
$errLog = Join-Path $LogDir "task_$Task`_$stamp.err.log"

switch ($Task) {
    "scan"       { $exe = $Python; $argv = @("-m", "house_search.cli", "scan") }
    # 在庫棚卸し。増分（一覧1ページ）が拾えるのは各市区の先頭だけなので、
    # 週に一度は5ページまで辿って在庫を舐め直す。scan とはDBのアドバイザリ
    # ロックで排他されるため、重なった側がスキップされて並走しない
    "sweep"      {
        $exe = $Python
        $argv = @("-m", "house_search.cli", "scan", "--full", "--detail-limit", "400")
    }
    "check-sold" { $exe = $Python; $argv = @("-m", "house_search.cli", "check-sold") }
    "digest"     { $exe = $Python; $argv = @("-m", "house_search.cli", "digest") }
    "backup"     {
        $exe  = "powershell.exe"
        $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", "`"$(Join-Path $PSScriptRoot 'backup_db.ps1')`"")
    }
}

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] タスク '$Task' を開始します"
Write-Output "  実行     : $exe $($argv -join ' ')"
Write-Output "  標準出力 : $outLog"
Write-Output "  標準エラー: $errLog"

$started = Get-Date
# -Wait でタスクの子として待たせる（切り離すと二重起動する）
$proc = Start-Process -FilePath $exe `
                      -ArgumentList $argv `
                      -WorkingDirectory $RepoRoot `
                      -NoNewWindow `
                      -Wait -PassThru `
                      -RedirectStandardOutput $outLog `
                      -RedirectStandardError  $errLog
$elapsed = (Get-Date) - $started

Write-Output ("[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] タスク '$Task' 終了 コード=$($proc.ExitCode) 所要 {0:hh\:mm\:ss}" -f $elapsed)

# 終了コードをそのまま返す（タスクの「前回の結果」に載せる）
exit $proc.ExitCode
