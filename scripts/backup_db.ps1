# ============================================================
# 物件検索通知システム v2 - PostgreSQL 定期バックアップ（課題#8）
#
# 使い方:
#   .\scripts\backup_db.ps1                       # 既定の設定で1回取る
#   .\scripts\backup_db.ps1 -RetentionDays 30
#   .\scripts\backup_db.ps1 -BackupDir "D:\backups\house"
#
# 何をするか:
#   1. .env の DATABASE_URL から接続情報を取り出す（アプリロール＝最小権限）
#   2. pg_dump -Fc（カスタム形式・圧縮）でダンプする
#   3. pg_restore --list で中身を読み直して無傷を検証する
#      ⚠ ここを省くと「0バイトのファイルが毎日増えるだけ」に気づけない
#   4. 保持日数を超えた世代を削除する
#
# 注意（いずれも実際に踏んだ罠）:
#   - パスワードをコマンドライン引数に載せない。プロセス一覧とシェル履歴に
#     平文で残るため、環境変数 PGPASSWORD で渡し、終了時に必ず消す
#   - pg_dump には -w を付ける。付けないと認証情報が足りないとき
#     プロンプトで固まり、タスクが実行時間の上限まで居座る
#   - Windows では pg_dump.exe が PATH に無いことが多いのでフルパスで叩く
#   - 失敗したら 0 以外で終わること。タスクスケジューラの「前回の結果」が
#     唯一の異常検知経路になる
# ============================================================

param(
    [string]$BackupDir     = "F:\backups\searching-for-houses",
    [int]   $RetentionDays = 14,
    [string]$PgDumpPath    = "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe",
    [string]$PgRestorePath = "C:\Program Files\PostgreSQL\18\bin\pg_restore.exe"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile  = Join-Path $RepoRoot ".env"

foreach ($exe in @($PgDumpPath, $PgRestorePath)) {
    if (-not (Test-Path $exe)) {
        throw "実行ファイルが見つかりません: $exe （-PgDumpPath / -PgRestorePath で指定してください）"
    }
}
if (-not (Test-Path $EnvFile)) {
    throw ".env が見つかりません: $EnvFile"
}

# ---- .env の DATABASE_URL から接続情報を取り出す --------------------------
# postgresql+psycopg://user:pass@host:port/dbname
$databaseUrl = $null
foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
    if ($line -match '^\s*DATABASE_URL\s*=\s*(.+?)\s*$') {
        $databaseUrl = $Matches[1]
        break
    }
}
if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    throw ".env に DATABASE_URL がありません"
}
if ($databaseUrl -notmatch '^postgres(?:ql)?(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/([^\?\s]+)') {
    throw "DATABASE_URL の形式を解釈できません（postgresql+psycopg://user:pass@host:port/dbname）"
}
$DbUser = $Matches[1]
$DbPass = $Matches[2]
$DbHost = $Matches[3]
$DbPort = $Matches[4]
$DbName = $Matches[5]

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$stamp    = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpFile = Join-Path $BackupDir "$DbName`_$stamp.dump"

Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] バックアップ開始: $DbName@$DbHost`:$DbPort → $dumpFile"

# パスワードは環境変数でのみ渡す（引数には決して載せない）
$env:PGPASSWORD       = $DbPass
$env:PGCLIENTENCODING = "UTF8"

try {
    # native コマンドの stderr で止まらないようにし、終了コードで判定する
    $ErrorActionPreference = "Continue"

    & $PgDumpPath -h $DbHost -p $DbPort -U $DbUser -d $DbName -F c -Z 6 -w -f $dumpFile
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump が失敗しました（終了コード $LASTEXITCODE）"
    }

    $size = (Get-Item $dumpFile).Length
    if ($size -le 0) {
        throw "ダンプが空です: $dumpFile"
    }

    # 無傷の検証。ここを省くと壊れたダンプが毎日増えるだけの状態に気づけない
    $entries = & $PgRestorePath -l $dumpFile
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore --list が失敗しました（終了コード $LASTEXITCODE）。ダンプが壊れている可能性がある"
    }
    $tableCount = ($entries | Select-String -SimpleMatch "TABLE DATA").Count
    if ($tableCount -lt 17) {
        throw "TABLE DATA が $tableCount 件しかありません（マスタ8＋トランザクション9＝17以上を期待）"
    }

    Write-Output ("[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 検証OK: {0:N0} bytes / TABLE DATA {1} 件" -f $size, $tableCount)

    # ---- 世代管理 --------------------------------------------------------
    $ErrorActionPreference = "Stop"
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $stale  = Get-ChildItem -Path $BackupDir -Filter "$DbName`_*.dump" -File |
              Where-Object { $_.LastWriteTime -lt $cutoff }
    foreach ($file in $stale) {
        Remove-Item $file.FullName -Force
        Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 保持期間超過を削除: $($file.Name)"
    }
    $remaining = (Get-ChildItem -Path $BackupDir -Filter "$DbName`_*.dump" -File).Count
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 完了。保持 $remaining 世代（保持日数 $RetentionDays 日）"
    exit 0
}
catch {
    Write-Error "バックアップに失敗しました: $_"
    # 中途半端なファイルを残すと「世代がある」と誤認するので消す
    if ((Test-Path $dumpFile) -and ((Get-Item $dumpFile).Length -le 0)) {
        Remove-Item $dumpFile -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGCLIENTENCODING -ErrorAction SilentlyContinue
}
