# ============================================================
# 物件検索通知システム v2 - タスクスケジューラ登録（4本）
#
# 使い方:
#   .\scripts\register_tasks.ps1 -DryRun          # XMLを生成して検証するだけ（権限不要）
#   .\scripts\register_tasks.ps1                  # 登録する（★管理者権限が要る）
#   .\scripts\register_tasks.ps1 -EnableScraping  # 取得を伴う2本を有効化する
#   .\scripts\register_tasks.ps1 -Unregister      # 4本とも削除する
#
# ★ 登録には管理者権限が必要（2026-09-02 に実測して判明）:
#   LogonType が S4U（＝ログオフ中も実行・パスワード保存なし）のタスクを作るには
#   管理者権限（SeTcbPrivilege）が要る。標準ユーザーで実行すると schtasks が
#   「エラー: アクセスが拒否されました。」で失敗する。
#   ⚠ このPCの通常アカウント wy469 は BUILTIN\Administrators に入っていないため、
#     管理者アカウントで「管理者として実行」した PowerShell から登録すること。
#   ⚠ -DryRun（XML生成と整形式検査だけ）は標準ユーザーでも通る。
#   同じXMLで LogonType を InteractiveToken にすれば標準ユーザーでも登録できるが、
#   ログオフ中にタスクが動かなくなる（03:30 の pg_dump が走らない）ため採らない。
#
# 登録されるタスク:
#   HouseSearch-Scan       2時間ごと 01:15起点  増分スキャン（一覧1ページ＋詳細40件/サイト）
#   HouseSearch-CheckSold  毎日 09:00           成約・掲載終了の確認
#   HouseSearch-Digest     毎日 20:00           日次ランキングダイジェスト
#   HouseSearch-Backup     毎日 03:30           pg_dump（課題#8）
#
# なぜ2時間ごとなのか:
#   増分スキャンは実測ベースで約72分かかる（一覧1116リクエスト＋詳細320リクエスト・
#   サイト直列）。毎時トリガーだと前回が終わる前に次が起動し、
#   MultipleInstances でスキップされて開始時刻が不定に揺れる。
#
# なぜ 01:15 起点なのか（偶数時ちょうどではない理由）:
#   09:00 の check-sold が scan の実行帯に入るとスクレイピングが並走し、
#   同一サイトへの実効間隔が半分になる。レート制御はプロセス内にしかないため、
#   別プロセス同士では効かない。奇数時+15分起点なら 07:15（〜08:27）と 09:15 の
#   間に 09:00 が収まる。
#
# 注意（いずれも実際に踏んだ罠）:
#   - Register-ScheduledTask（PowerShell の CIM 経由）は自分自身のタスクを
#     登録するだけでも「アクセスが拒否されました」（0x80070005）になることがある。
#     同じ端末・同じユーザーでも schtasks.exe なら通るので、
#     XML を UTF-16 で書き出して schtasks /create /XML を使う（UTF-8 だと読めない）
#   - アイドル条件（RunOnlyIfIdle）は付けない。付けると手動の
#     Start-ScheduledTask / schtasks /run でも Queued のまま走らない。
#     このシステムはレート待ちの sleep が大半でCPUをほぼ使わないため、
#     手元の作業とCPUを奪い合う心配もない
#   - 実体は task_runner.ps1（-Wait で待つ側）。run_initial_scan.ps1（切り離す側）を
#     指してはいけない。タスクが即完了扱いになり二重起動する
# ============================================================

param(
    [switch]$DryRun,
    [switch]$Unregister,
    # 取得を伴う2本（Scan / CheckSold）を有効化する。
    # 初回全件スキャンが終わってから実行すること（並走すると実効間隔が半分になる）
    [switch]$EnableScraping
)

$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$TaskRunner = Join-Path $PSScriptRoot "task_runner.ps1"
$UserId     = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path $TaskRunner)) {
    throw "task_runner.ps1 が見つかりません: $TaskRunner"
}

# ---- タスク定義 -----------------------------------------------------------
# StartBoundary の日付は「この日時以降に有効」の意味しか持たない（過去日でよい）
$Tasks = @(
    @{
        Name        = "HouseSearch-Scan"
        Description = "物件検索通知システム: 増分スキャン（一覧取得→MUST判定→詳細→抽出→スコア→通知）。約72分かかるため2時間ごと。"
        TaskArg     = "scan"
        Scraping    = $true
        StartAt     = "2026-09-02T01:15:00"
        Repeat      = "PT2H"
        TimeLimit   = "PT1H50M"
    },
    @{
        Name        = "HouseSearch-CheckSold"
        Description = "物件検索通知システム: 成約・掲載終了の確認。scan の実行帯と重ならない 09:00 に置く。"
        TaskArg     = "check-sold"
        Scraping    = $true
        StartAt     = "2026-09-02T09:00:00"
        Repeat      = $null
        TimeLimit   = "PT1H"
    },
    @{
        Name        = "HouseSearch-Digest"
        Description = "物件検索通知システム: 日次ランキングダイジェスト（上位15件）をDiscordへ送信する。"
        TaskArg     = "digest"
        Scraping    = $false
        StartAt     = "2026-09-02T20:00:00"
        Repeat      = $null
        TimeLimit   = "PT30M"
    },
    @{
        Name        = "HouseSearch-Backup"
        Description = "物件検索通知システム: PostgreSQL の pg_dump バックアップ（14世代保持・課題#8）。"
        TaskArg     = "backup"
        Scraping    = $false
        StartAt     = "2026-09-02T03:30:00"
        Repeat      = $null
        TimeLimit   = "PT30M"
    }
)

# ---- 削除 -----------------------------------------------------------------
if ($Unregister) {
    foreach ($task in $Tasks) {
        & schtasks.exe /delete /tn $task.Name /f 2>&1 | Out-String | Write-Output
    }
    Write-Host "4本のタスクを削除しました" -ForegroundColor Green
    exit 0
}

# ---- 既存タスクの有効化だけを行う ----------------------------------------
if ($EnableScraping -and -not $DryRun) {
    $existing = & schtasks.exe /query /tn "HouseSearch-Scan" 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        foreach ($task in ($Tasks | Where-Object { $_.Scraping })) {
            & schtasks.exe /change /tn $task.Name /enable 2>&1 | Out-String | Write-Output
            Write-Host "[有効化] $($task.Name)" -ForegroundColor Green
        }
        Write-Host ""
        Write-Host "取得を伴うタスクを有効化しました。初回全件スキャンとの並走に注意してください。" -ForegroundColor Cyan
        exit 0
    }
    # 未登録なら通常の登録処理へ進む（Enabled=true で作られる）
}

function New-TaskXml {
    param([hashtable]$Task)

    # 繰り返し設定。1日ぶんを繰り返せば、翌日は次のトリガーが引き継ぐ
    $repetition = ""
    if ($Task.Repeat) {
        $repetition = @"

      <Repetition>
        <Interval>$($Task.Repeat)</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
"@
    }

    $arguments = "-NoProfile -ExecutionPolicy Bypass -File &quot;$TaskRunner&quot; -Task $($Task.TaskArg)"

    # 取得を伴うタスクは無効で登録する。初回全件スキャンと並走させないため
    # （レート制御はプロセス内にしかなく、別プロセス同士では効かない）。
    # 初回スキャンの完了後に -EnableScraping で有効化する
    $enabled = if ($Task.Scraping -and -not $EnableScraping) { "false" } else { "true" }

    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$($Task.Description)</Description>
    <URI>\$($Task.Name)</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$($Task.StartAt)</StartBoundary>
      <Enabled>true</Enabled>$repetition
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$UserId</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>$enabled</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>$($Task.TimeLimit)</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$arguments</Arguments>
      <WorkingDirectory>$RepoRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

$tempDir = Join-Path $env:TEMP "house-search-tasks"
if (-not (Test-Path $tempDir)) {
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
}

$failed = 0
foreach ($task in $Tasks) {
    $xml     = New-TaskXml -Task $task
    $xmlPath = Join-Path $tempDir "$($task.Name).xml"

    # schtasks /XML は UTF-16 でないと読めない（UTF-8 だと「無効な XML」になる）
    $xml | Out-File -FilePath $xmlPath -Encoding Unicode -Force

    # 整形式の検査。ここで落とせば schtasks の分かりにくいエラーを避けられる
    try {
        [xml](Get-Content $xmlPath -Encoding Unicode -Raw) | Out-Null
    }
    catch {
        Write-Error "$($task.Name): XML が不正です — $_"
        $failed++
        continue
    }

    if ($DryRun) {
        Write-Host "[検証OK] $($task.Name)" -ForegroundColor Green
        Write-Host "  XML      : $xmlPath"
        Write-Host "  実行コマンド: schtasks.exe /create /XML `"$xmlPath`" /tn $($task.Name) /ru `"$UserId`" /f"
        continue
    }

    # /ru をユーザー名だけ渡し /rp を渡さないのが S4U（パスワード保存なし）の指定方法
    $output = & schtasks.exe /create /XML $xmlPath /tn $task.Name /ru $UserId /f 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$($task.Name): 登録に失敗しました — $output"
        $failed++
    }
    else {
        Write-Host "[登録] $($task.Name)" -ForegroundColor Green
    }
}

if ($DryRun) {
    Write-Host ""
    Write-Host "-DryRun のため登録していません。生成したXMLは $tempDir にあります。" -ForegroundColor Cyan
    exit $(if ($failed) { 1 } else { 0 })
}

if ($failed) {
    Write-Error "$failed 本の登録に失敗しました"
    exit 1
}

Write-Host ""
Write-Host "4本のタスクを登録しました。確認と手動起動:" -ForegroundColor Cyan
foreach ($task in $Tasks) {
    Write-Host "  schtasks /query /tn $($task.Name) /fo LIST /v"
}
Write-Host "  schtasks /run /tn HouseSearch-Backup      # 一番安全な疎通確認"
if (-not $EnableScraping) {
    Write-Host ""
    Write-Host "⚠ HouseSearch-Scan と HouseSearch-CheckSold は無効で登録しました。" -ForegroundColor Yellow
    Write-Host "  初回全件スキャン（run_initial_scan.ps1）が終わってから有効化してください:" -ForegroundColor Yellow
    Write-Host "    .\scriptsegister_tasks.ps1 -EnableScraping"
}
exit 0
