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
#   ⚠ このPCの通常アカウント wy469 は BUILTIN\Administrators に入っていない。
#   → **本スクリプトは自動で昇格する。** 標準ユーザーのまま実行すると UAC の画面が出るので、
#     管理者アカウントの資格情報を入力する。昇格したウィンドウで登録が続行される。
#   ⚠ -DryRun（XML生成と整形式検査だけ）は権限が要らないので昇格しない。
#   同じXMLで LogonType を InteractiveToken にすれば標準ユーザーでも登録できるが、
#   ログオフ中にタスクが動かなくなる（03:30 の pg_dump が走らない）ため採らない。
#
# ★ 自己昇格の落とし穴（この対策が本スクリプトの肝）:
#   標準ユーザーが UAC で管理者の資格情報を入力すると、**昇格後のプロセスは
#   その管理者アカウントとして動く**。素朴に自己昇格して
#   WindowsIdentity::GetCurrent() を読むと、タスクが「管理者アカウントとして実行」で
#   登録されてしまい、意図した利用者の環境で動かなくなる。
#   そのため昇格前の利用者名を -TaskUser で子プロセスへ引き継いでいる。
#   登録後は必ず schtasks /query /fo LIST /v の「実行ユーザー」を確認すること。
#
# 登録されるタスク:
#   HouseSearch-Scan       2時間ごと 01:15起点  増分スキャン（一覧1ページ＋詳細40件/サイト）
#   HouseSearch-Sweep      毎週日曜 02:00      在庫棚卸し（一覧5ページ＋詳細400件/サイト）
#   HouseSearch-CheckSold  毎日 08:40           成約・掲載終了の確認
#   HouseSearch-Digest     毎日 20:00           日次ランキングダイジェスト
#   HouseSearch-Backup     毎日 03:30           pg_dump（課題#8）
#
# なぜ2時間ごとなのか:
#   増分スキャンは実測ベースで約72分かかる（一覧1116リクエスト＋詳細320リクエスト・
#   サイト直列）。毎時トリガーだと前回が終わる前に次が起動し、
#   MultipleInstances でスキップされて開始時刻が不定に揺れる。
#
# なぜ 01:15 起点なのか（偶数時ちょうどではない理由）:
#   check-sold が scan の実行帯に入るとスクレイピングが並走し、
#   同一サイトへの実効間隔が半分になる。レート制御はプロセス内にしかないため、
#   別プロセス同士では効かない。奇数時+15分起点なら 07:15 のスキャンが
#   08:08〜08:25 に終わり、その後ろに check-sold の隙間ができる。
#
# なぜ check-sold が 08:40 なのか（09:00 ではない理由）:
#   ⚠ 09:00 だと 09:15 の定期スキャンを毎日スキップさせていた（2026-09-04 実測 → 課題#26）。
#   check-sold は 200件（100件/パターン × 2帯）× 約4.5秒で **約15分**かかるため、
#   09:00 開始では 09:15 の起動に間に合わない。
#   ⚠ 原因は掲載数ではない。check_sold の limit は 100件/パターン固定で、
#   掲載が 8,429 → 18,800件に増えても対象は200件のまま。所要は起票時から変わっていない。
#   ⚠ 08:30 では 07:15 のスキャン（実測最長70分＝08:25終了）との余裕が5分しかない。
#   08:40 なら前に15分・後ろに20分の余裕が取れる。
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
    [switch]$EnableScraping,

    # タスクを「実行するアカウント」。省略時は現在のログオンユーザー。
    # ⚠ 自己昇格すると実行者が管理者アカウントに変わるため、
    #   昇格前の利用者をここで引き継ぐ（引き継がないとタスクが管理者として登録される）
    [string]$TaskUser,

    # 自己昇格した子プロセス側であることを示す内部用フラグ。手で指定しない
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$TaskRunner = Join-Path $PSScriptRoot "task_runner.ps1"

if (-not (Test-Path $TaskRunner)) {
    throw "task_runner.ps1 が見つかりません: $TaskRunner"
}

function Test-Elevated {
    <#
        管理者として実行されているか。UACフィルタ済みの管理者トークンでは false になる。
    #>
    $identity  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

# タスクの実行アカウントは「昇格前の利用者」でなければならない。
# 自己昇格した子プロセスでは GetCurrent() が管理者アカウントを返すので、
# 親から -TaskUser で渡された値を優先する
if (-not $TaskUser) {
    $TaskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
}
$UserId = $TaskUser

# ---- 自己昇格 -------------------------------------------------------------
# -DryRun（XML生成と整形式検査だけ）は権限が要らないので昇格しない
if (-not $DryRun -and -not (Test-Elevated)) {
    if ($Elevated) {
        # 昇格したはずなのに管理者でない＝管理者以外の資格情報が入力された
        throw "昇格しましたが管理者権限がありません。管理者アカウントの資格情報で実行してください。"
    }

    Write-Host ""
    Write-Host "タスクの登録には管理者権限が必要です。UAC の画面で" -ForegroundColor Yellow
    Write-Host "管理者アカウントの資格情報を入力してください。" -ForegroundColor Yellow
    Write-Host "  タスクの実行アカウント: $TaskUser （昇格しても変わりません）" -ForegroundColor Cyan
    Write-Host ""

    $childArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit",
        "-File", "`"$PSCommandPath`"",
        "-Elevated", "-TaskUser", "`"$TaskUser`""
    )
    if ($Unregister)     { $childArgs += "-Unregister" }
    if ($EnableScraping) { $childArgs += "-EnableScraping" }

    try {
        # -NoExit で昇格した窓を開いたままにする（結果を読めるようにするため）
        Start-Process -FilePath "powershell.exe" -ArgumentList $childArgs -Verb RunAs | Out-Null
    }
    catch {
        Write-Error @"
昇格に失敗しました（UACをキャンセルした可能性があります）。
管理者アカウントで「管理者として実行」した PowerShell から、次を直接実行してください:
  .\scripts\register_tasks.ps1 -TaskUser "$TaskUser"
"@
        exit 1
    }

    Write-Host "昇格したウィンドウで登録を続行します。結果はそちらに表示されます。" -ForegroundColor Green
    exit 0
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
        Name        = "HouseSearch-Sweep"
        Description = "物件検索通知システム: 週次の在庫棚卸し（一覧5ページ）。増分が拾うのは各市区の先頭だけなので、週に一度だけ在庫を舐め直す。"
        TaskArg     = "sweep"
        Scraping    = $true
        # 日曜 02:00 起点。増分スキャンとはDBのアドバイザリロックで排他される
        # （重なった側がスキップされる）ので、時刻の分離だけに頼らない
        StartAt     = "2026-09-06T02:00:00"
        Repeat      = $null
        Weekly      = $true
        TimeLimit   = "PT10H"
    },
    @{
        Name        = "HouseSearch-CheckSold"
        Description = "物件検索通知システム: 成約・掲載終了の確認。scan の実行帯と重ならない 08:40 に置く（→ 課題#26）。"
        TaskArg     = "check-sold"
        Scraping    = $true
        StartAt     = "2026-09-02T08:40:00"
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
    & schtasks.exe /query /tn "HouseSearch-Scan" 2>&1 | Out-Null
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

    # 在庫棚卸しだけ週次。ScheduleByDay と ScheduleByWeek は排他なので切り替える
    $schedule = if ($Task.Weekly) {
        @"
      <ScheduleByWeek>
        <DaysOfWeek><Sunday /></DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
"@
    } else {
        @"
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
"@
    }

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
$schedule
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

# ---- 実行アカウントの確認 -------------------------------------------------
# 手動で「管理者として実行」した PowerShell から動かす場合、その窓が
# 別の管理者アカウントで開かれていると $TaskUser が管理者名になってしまい、
# タスクがその管理者名義で登録される（意図した利用者の環境で動かなくなる）。
# 黙って進むと気づけないので、登録の直前に必ず表示する
if (-not $DryRun) {
    $current = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    Write-Host ""
    Write-Host "タスクの実行アカウント: $TaskUser" -ForegroundColor Cyan
    if ($TaskUser -ne $current) {
        Write-Host "  （このウィンドウの実行者は $current。-TaskUser の指定が効いています）" -ForegroundColor DarkGray
    }
    elseif ($Elevated) {
        Write-Host "  （昇格前の利用者を引き継いでいます）" -ForegroundColor DarkGray
    }
    else {
        Write-Host "  ⚠ このウィンドウの実行者をそのまま使います。別の管理者アカウントで" -ForegroundColor Yellow
        Write-Host "    開いた PowerShell から実行している場合は、いったん中断して" -ForegroundColor Yellow
        Write-Host "    -TaskUser で本来の利用者を指定し直してください。" -ForegroundColor Yellow
    }
    Write-Host ""
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
    Write-Host "    .\scripts
egister_tasks.ps1 -EnableScraping"
}
exit 0
