# ============================================================
# 物件検索通知システム v2 - タスクスケジューラから呼ばれる実行スクリプト
#
# 使い方（登録は register_tasks.ps1 が行う。手動確認にも使える）:
#   .\scripts\task_runner.ps1 -Task scan
#   .\scripts\task_runner.ps1 -Task check-sold
#   .\scripts\task_runner.ps1 -Task digest
#   .\scripts\task_runner.ps1 -Task backup
#   .\scripts\task_runner.ps1 -Task market-rates
#   .\scripts\task_runner.ps1 -Task buy-market-rates
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
    [ValidateSet("scan", "scan-buy", "sweep", "check-sold", "digest", "backup",
                 "market-rates", "buy-market-rates")]
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

# 1タスク＝1つ以上のステップ（順に実行し、終了コードは最初に0以外だったものを返す）
$steps = @()
switch ($Task) {
    # ⚠ 2時間ごとの scan は**賃貸だけ**（--family CHINTAI）。売買4パターンは
    # 4都県173市区へ広げたので同居させると所要が約95〜105分になり、
    # 上限 PT1H50M に迫る（→ 課題#4・2026-09-07）。売買は scan-buy が1日1回回す
    "scan"       {
        $steps += @{ Exe = $Python; Argv = @("-m", "house_search.cli", "scan", "--family", "CHINTAI") }
    }
    # 売買4パターン（マンション・戸建て）の日次スキャン。売買は掲載の回転が遅いので
    # 1日1回で足りる。一覧は新着順の1ページ目（→ suumo_buy.BUY_LIST_QUERY）。
    # ⚠ 続けて掲載終了の確認も行う（08:40 の check-sold は賃貸だけ）。
    #   上位30位＋古い順10件/パターンに絞ってあるのは、09:15 の scan が終わる
    #   10:25 から 11:15 の次の scan までの約50分に収めるため（超えると 11:15 の
    #   scan が pg_advisory_lock でスキップされる → ADR 0013 決定8）
    # ⚠⚠ 詳細上限を既定40から 200/パターンへ上げてある（→ 課題#4・2026-09-09）。
    #   売買の詳細キューは約27,000件（＝取得だけで19時間）あり、40件/パターンでは
    #   1日120件しか進まず消化に227日かかる。設備原文が付くまで掲載は設備12〜15項目が
    #   全部 unknown（0点・分母に残る）で上限が約62点に固定されるので、
    #   順位が「詳細が取れた掲載の中」でしか決まらない状態が続く。
    #   ⚠ 主力は手動の掃き出し（run_initial_scan.ps1 -Drain）で、これはその補助
    #   ⚠ 上げたぶん所要が +20分ほど増える見込み（160件 × 3パターン × 2.5秒）。
    #     ScanBuy はタスク登録が未実施でまだ一度も走っておらず**所要が未測定**なので、
    #     初回の実行ログで実測して上限（TimeLimit）ごと見直すこと
    "scan-buy"   {
        $steps += @{ Exe = $Python; Argv = @("-m", "house_search.cli", "scan",
                     "--family", "MANSION_BUY", "--family", "KODATE_BUY",
                     "--detail-limit", "200") }
        $steps += @{ Exe = $Python; Argv = @("-m", "house_search.cli", "check-sold",
                     "--family", "MANSION_BUY", "--family", "KODATE_BUY",
                     "--limit", "10", "--top-rank-limit", "30") }
    }
    # 在庫棚卸し。増分（一覧1ページ）が拾えるのは各市区の先頭だけなので、
    # 週に一度は5ページまで辿って在庫を舐め直す。scan とはDBのアドバイザリ
    # ロックで排他されるため、重なった側がスキップされて並走しない。
    # ⚠ 売買も含めて全パターンを回す（売買は 692 URL × 5ページで約2.5時間増える）
    "sweep"      {
        $steps += @{ Exe = $Python; Argv = @("-m", "house_search.cli", "scan", "--full", "--detail-limit", "400") }
    }
    # ⚠ 08:40 の check-sold も賃貸だけ。売買を含めると最大600件が加わり
    #   上限 PT1H を超える（→ 課題#26 で強制終了を実際に踏んだ）
    "check-sold" {
        $steps += @{ Exe = $Python; Argv = @("-m", "house_search.cli", "check-sold", "--family", "CHINTAI") }
    }
    "digest"     { $steps += @{ Exe = $Python; Argv = @("-m", "house_search.cli", "digest") } }
    "backup"     {
        $steps += @{ Exe = "powershell.exe"; Argv = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", "`"$(Join-Path $PSScriptRoot 'backup_db.ps1')`"") }
    }
    # 家賃相場の月次更新（取得 → CSV生成 → DB投入 → 課題#49）。
    # ⚠ SUUMO を叩くので定期スキャンと並走させない。毎月1日 04:30 に置いてある
    # （03:30 の backup の後・05:15 の scan の前）
    "market-rates" {
        $steps += @{ Exe = "powershell.exe"; Argv = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", "`"$(Join-Path $PSScriptRoot 'update_market_rates.ps1')`"") }
    }
    # 売買相場の四半期更新（国交省「不動産情報ライブラリ」→ CSV → DB → 課題#49 Step 8）。
    # ⚠ 家賃相場（毎月1日 04:30）と重ならないよう 1/4/7/10月の「2日」04:30 に置いてある。
    # ⚠ 新しい四半期が公開されていなければ CSV も DB も触らずに終わる（空振りは正常）
    "buy-market-rates" {
        $steps += @{ Exe = "powershell.exe"; Argv = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", "`"$(Join-Path $PSScriptRoot 'update_buy_market_rates.ps1')`"") }
    }
}

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] タスク '$Task' を開始します（$($steps.Count) ステップ）"
Write-Output "  標準出力 : $outLog"
Write-Output "  標準エラー: $errLog"

$exitCode = 0
$stepNo = 0
foreach ($step in $steps) {
    $stepNo += 1
    $exe  = $step.Exe
    $argv = $step.Argv
    Write-Output "  [$stepNo/$($steps.Count)] 実行: $exe $($argv -join ' ')"
    $started = Get-Date
    # -Wait でタスクの子として待たせる（切り離すと二重起動する）。
    # ⚠ 2ステップ目以降はログへ追記する（-RedirectStandardOutput は上書きするため、
    #   ステップごとに別ファイルへ落として最後に結合する）
    $stepOut = if ($stepNo -eq 1) { $outLog } else { "$outLog.step$stepNo" }
    $stepErr = if ($stepNo -eq 1) { $errLog } else { "$errLog.step$stepNo" }
    $proc = Start-Process -FilePath $exe `
                          -ArgumentList $argv `
                          -WorkingDirectory $RepoRoot `
                          -NoNewWindow `
                          -Wait -PassThru `
                          -RedirectStandardOutput $stepOut `
                          -RedirectStandardError  $stepErr
    $elapsed = (Get-Date) - $started
    if ($stepNo -gt 1) {
        Get-Content $stepOut | Add-Content $outLog
        Get-Content $stepErr | Add-Content $errLog
        Remove-Item $stepOut, $stepErr -Force -ErrorAction SilentlyContinue
    }
    Write-Output ("[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ステップ $stepNo 終了 コード=$($proc.ExitCode) 所要 {0:hh\:mm\:ss}" -f $elapsed)
    # 最初に 0 以外になったコードを返す（後続のステップは続ける。1ステップの失敗で
    # 掲載終了の確認まで捨てない）
    if ($exitCode -eq 0 -and $proc.ExitCode -ne 0) { $exitCode = $proc.ExitCode }
}

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] タスク '$Task' 終了 コード=$exitCode"

# 終了コードをそのまま返す（タスクの「前回の結果」に載せる）
exit $exitCode
