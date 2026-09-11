# ============================================================
# 物件検索通知システム v2 - 初回全件スキャン（シードモード）
#
# 使い方:
#   .\scripts\run_initial_scan.ps1              # 初回（一覧5ページ＋詳細800件/サイト）
#   .\scripts\run_initial_scan.ps1 -Drain       # 2晩目以降（詳細キューの掃き出し）
#   .\scripts\run_initial_scan.ps1 -Site NIFTY  # 1サイトだけ取り直す
#   .\scripts\run_initial_scan.ps1 -DetailLimit 400
#
# 何をするか:
#   1. sync-dict          辞書YAML → DB（辞書が空だと scan がエラー終了するため先に流す）
#   2. scan --seed --full 通知を送らず記録だけ行う（→ ADR 0006）
#   3. scan --seed --site CHINTAI_EX  賃貸EXの観測（本採用判断の材料 → 課題#5）
#
# 所要時間の目安（2026-09-02 時点の m_sites 設定・対象4都県での試算）:
#   初回   : 一覧 約4時間 ＋ 詳細800件/サイト 約5時間 = 約9時間
#   -Drain : 一覧 約1時間 ＋ 詳細（-DetailLimit ぶん）
#   → 夜間に開始すること。詳細キューは1回で掃き切れない前提で、
#     残りは -Drain の再実行か、定期スキャン（40件/サイト/回）に任せる
#
# 注意（いずれも実際に踏んだ罠）:
#   - エージェント（Claude Code）のバックグラウンドから起動しない。
#     標準出力がパイプになり、読み手が居なくなった時点で print がブロックし
#     例外も出さずに止まる。本スクリプトは Start-Process で切り離して
#     stdout/stderr を「別々のファイル」へ落とす（PowerShell 5.1 は
#     out と err に同じファイルを指定できない）
#   - 止まったかどうかを CPU 使用率で判断しない。この処理は
#     レート待ちの sleep が大半で、正常でも CPU はほぼ 0 になる。
#     生死の判断はログファイルの更新時刻と t_scrape_runs の行で行う
#   - タスクスケジューラからは呼ばない。切り離し版はタスクが即「完了」扱いになり
#     実行時間の上限も二重起動の抑止も効かなくなる（タスク用は task_runner.ps1）
#   - 定期スキャンのタスクを登録する前に走らせること。レート制御は
#     プロセス内にしかないため、並走すると同一サイトへの実効間隔が半分になる
# ============================================================

param(
    # ワーカーとして動く（ランチャーが内部的に付ける。手で指定しない）
    [switch]$Worker,
    # 詳細キューの掃き出しモード。一覧は1ページだけ見て詳細取得に時間を割く
    [switch]$Drain,
    # 詳細ページを取りに行く上限（サイトあたり）
    [int]$DetailLimit = 800,
    # 対象サイトを1つに絞る（例: NIFTY の取り直し）。省略時は全サイト
    [string]$Site = "",
    # 種別ファミリで絞る（CHINTAI / MANSION_BUY / KODATE_BUY。複数可）。省略時は全パターン。
    # ⚠ 売買の詳細キューを掃き出すときは賃貸を含めない（賃貸の一覧を1ページ取り直すだけで
    #   約1時間かかり、取得ロックを握る時間が伸びて定期スキャンを余分に飛ばす → 課題#4）
    [string[]]$Family = @(),
    # 検索パターンYAMLのディレクトリを差し替える（CONFIGS_DIR）。
    # ⚠ 新しいパターンや市区を広げたパターンは、configs/ 直下へ置く前に
    #   ここへ一時ディレクトリを渡して seed を流す（置いた瞬間から定期スキャンが
    #   新規掲載を全件通知として飛ばすため → ADR 0006・課題#4）
    [string]$ConfigsDir = ""
)

$ErrorActionPreference = "Stop"

# ⚠ -File 経由だと "MANSION_BUY,KODATE_BUY" は分割されず1要素の配列で届く。
#   ランチャー→ワーカーの引き渡しも -File なので、ここで必ずカンマで割る
$Family = @($Family | ForEach-Object { $_ -split "," } | Where-Object { $_ } | ForEach-Object { $_.Trim() })

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
    $stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
    $prefix  = if ($Drain) { "drain_scan" } else { "initial_scan" }
    if ($Site) { $prefix = "$prefix`_$($Site.ToLower())" }
    $outLog  = Join-Path $LogDir "$prefix`_$stamp.out.log"
    $errLog  = Join-Path $LogDir "$prefix`_$stamp.err.log"

    $childArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"", "-Worker",
        "-DetailLimit", $DetailLimit
    )
    if ($Drain) { $childArgs += "-Drain" }
    if ($Site)  { $childArgs += @("-Site", $Site) }
    if ($Family.Count -gt 0) { $childArgs += @("-Family", ($Family -join ",")) }
    if ($ConfigsDir) { $childArgs += @("-ConfigsDir", "`"$ConfigsDir`"") }

    # out と err は必ず別ファイル（5.1 は同一ファイルを指定できない）
    $proc = Start-Process -FilePath "powershell.exe" `
                          -ArgumentList $childArgs `
                          -WindowStyle Hidden `
                          -RedirectStandardOutput $outLog `
                          -RedirectStandardError  $errLog `
                          -PassThru

    Write-Host ""
    Write-Host "初回全件スキャンを切り離して開始しました（PID $($proc.Id)）" -ForegroundColor Green
    Write-Host "  モード      : $(if ($Drain) { '掃き出し（一覧1ページ）' } else { '初回（一覧5ページ）' })"
    Write-Host "  詳細の上限  : $DetailLimit 件/サイト"
    if ($Site) { Write-Host "  対象サイト  : $Site" }
    if ($Family.Count -gt 0) { Write-Host "  ファミリ    : $($Family -join ',')" }
    if ($ConfigsDir) { Write-Host "  パターン    : $ConfigsDir（CONFIGS_DIR を差し替え）" }
    Write-Host "  標準出力    : $outLog"
    Write-Host "  標準エラー  : $errLog"
    Write-Host ""
    Write-Host "進捗の追い方:" -ForegroundColor Cyan
    # ⚠ ワーカーの出力は UTF-8。PowerShell 5.1 の Get-Content は既定で cp932 として読むので指定する
    Write-Host "  Get-Content `"$outLog`" -Tail 20 -Wait -Encoding UTF8"
    Write-Host "  Get-Process -Id $($proc.Id) -ErrorAction SilentlyContinue   # 生きているか"
    Write-Host ""
    Write-Host "⚠ CPU 使用率で生死を判断しないこと。レート待ちの sleep が大半で" -ForegroundColor Yellow
    Write-Host "  正常でも CPU はほぼ 0 になる。ログの更新時刻と t_scrape_runs を見る。" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "完了後にやること:" -ForegroundColor Cyan
    Write-Host "  uv run house-search regroup      # 名寄せの再構築（通知なし）"
    Write-Host "  uv run house-search rescore      # 再採点"
    Write-Host "  uv run house-search dedup-stats  # 賃貸EXのユニーク率（課題#5）"
    Write-Host "  uv run house-search coverage     # サイト別の抽出充足率"
    exit 0
}

# ---- ワーカー: ここから先は切り離されたプロセスで動く ----------------------
# 標準出力・標準エラーはランチャーが「ファイル」へ向けている（パイプではない）ため
# ここで直接 & 呼び出ししても詰まらない。
# native コマンドの stderr で止まらないよう Continue にし、終了コードは自分で見る
$ErrorActionPreference = "Continue"

# ⚠ 出力を UTF-8 に揃える（揃えないと out.log の中で cp932 と UTF-8 が混在する → lib\utf8_output.ps1）
. (Join-Path $PSScriptRoot "lib\utf8_output.ps1")
Set-Utf8ConsoleOutput

function Write-Step {
    param([string]$Message)
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

function Invoke-HouseSearch {
    param([string]$Label, [string[]]$Arguments)

    Write-Step "▶ $Label"
    Write-Step "  コマンド: python.exe -m house_search.cli $($Arguments -join ' ')"
    $started = Get-Date
    & $Python -m house_search.cli @Arguments
    $code = $LASTEXITCODE
    $elapsed = (Get-Date) - $started
    Write-Step ("◀ $Label 終了コード=$code 所要 {0:hh\:mm\:ss}" -f $elapsed)
    if ($code -ne 0) {
        # 1サイトの失敗で全体を捨てない。scan は1件でもエラーがあれば 1 を返す
        Write-Step "  ⚠ 終了コードが 0 ではないが後続ステップは続行する"
    }
    # ⚠ 終了コードを return しない。PowerShell の関数は出力をすべて戻り値として返すので、
    #   呼び出し側で捨てる（| Out-Null）と「▶／◀」の行と python の標準出力（scan の
    #   実行サマリ）まで一緒に消える（2026-09-11 の13市町 seed で実際に集計が残らなかった）
}

Set-Location $RepoRoot
Write-Step "==== 初回全件スキャン開始 ===="
Write-Step "リポジトリ: $RepoRoot"
Write-Step "モード    : $(if ($Drain) { '掃き出し（一覧1ページ）' } else { '初回（一覧5ページ）' })"
Write-Step "詳細上限  : $DetailLimit 件/サイト"
if ($Site) { Write-Step "対象サイト: $Site" }
if ($Family.Count -gt 0) { Write-Step "ファミリ  : $($Family -join ',')" }
if ($ConfigsDir) {
    # 環境変数は .env の値より優先される（pydantic-settings）。子の python にだけ効く
    $env:CONFIGS_DIR = $ConfigsDir
    Write-Step "パターン  : $ConfigsDir（CONFIGS_DIR を差し替え）"
}

Invoke-HouseSearch -Label "辞書の同期" -Arguments @("sync-dict")

$scanArgs = @("scan", "--seed", "--detail-limit", "$DetailLimit")
if (-not $Drain) { $scanArgs += "--full" }
# 1サイトだけ取り直す逃げ道（NIFTY の405解消後の取り直しで使った）
if ($Site) { $scanArgs += @("--site", $Site) }
# 種別ファミリで絞る（-Family MANSION_BUY,KODATE_BUY で売買だけを掃き出す）。
# ⚠ 絞った結果が空なら scan 側が例外にする（黙って0件で正常終了しない → 課題#4）
foreach ($fam in $Family) { $scanArgs += @("--family", $fam) }
Invoke-HouseSearch -Label "全サイトのシードスキャン" -Arguments $scanArgs

Write-Step "==== 初回全件スキャン終了 ===="
Write-Step "次にやること: regroup → rescore → dedup-stats → coverage"
exit 0
