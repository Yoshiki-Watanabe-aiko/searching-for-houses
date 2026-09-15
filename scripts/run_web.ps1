# ============================================================
# 物件検索通知システム v2 - ブラウザ閲覧画面を起動する（課題#68・ADR 0026）
#
# 使い方（このウィンドウで前面に起動し、Ctrl+C で止める）:
#   .\scripts\run_web.ps1                  # 起動してブラウザで開く
#   .\scripts\run_web.ps1 -Port 8800       # ポートを変える
#   .\scripts\run_web.ps1 -NoBrowser       # ブラウザを開かない
#   .\scripts\run_web.ps1 -TestDb          # テストDBを読む
#
# 注意:
#   - 待ち受けは 127.0.0.1 だけ（LAN の他端末からは見えない）。常駐はさせない
#   - 取得ロックは取らないので、定期スキャンの最中に起動してよい
#   - `uv run` ではなく .venv の python を直接呼ぶ。`uv run` は起動のたびに依存を
#     同期しうるため、定期タスクが同じ .venv を使っている最中に差し替えが走らないようにする
#   - 依存（flask）が入っていなければ「uv sync を実行してください」と出て終わる。
#     uv sync は定期スキャンが走っていない時間に行う
# ============================================================

param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$TestDb
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\utf8_output.ps1")
Set-Utf8ConsoleOutput

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Output "python が見つかりません: $Python （uv sync を実行してください）"
    exit 1
}

$arguments = @("-m", "house_search.cli", "web", "--port", "$Port")
if (-not $NoBrowser) { $arguments += "--open" }
if ($TestDb) { $arguments += "--test-db" }

Push-Location $RepoRoot
try {
    & $Python @arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
