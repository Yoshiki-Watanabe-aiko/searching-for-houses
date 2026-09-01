# ============================================================
# 物件検索通知システム v2 - DB・ロールセットアップ（冪等）
#
# 使い方: PowerShell で .\scripts\setup_db.ps1 を実行する
# 前提  : 資格情報の正典 ~/.claude/.env に
#         DB_ADMIN_USER / DB_ADMIN_PASSWORD / DB_APP_PASSWORD がある
#
# 本番DB   : searching_for_houses
# テストDB : searching_for_houses_test （統合テスト用。DB規約の {本番DB名}_test）
#
# 注意（いずれも過去に実際に踏んだ罠）:
#   - パスワードをコマンドライン引数に載せない。プロセス一覧とシェル履歴に平文で残るため
#     SQL は標準入力で流し、管理者パスワードは環境変数 PGPASSWORD で渡す
#   - psql には -w を付ける。付けないと認証情報が足りないときプロンプトで固まる
#   - CREATE DATABASE はトランザクションブロック内で実行できないので個別に流す
#   - Windows では psql.exe が PATH に無いことが多いのでフルパスで叩く
# ============================================================

param(
    [string]$PgHost = "localhost",
    [string]$PgPort = "5432",
    [string]$PsqlPath = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
)

$ErrorActionPreference = "Stop"

$AppRole   = "searching_for_houses"
$ProdDb    = "searching_for_houses"
$TestDb    = "searching_for_houses_test"
$CredFile  = Join-Path $env:USERPROFILE ".claude\.env"

if (-not (Test-Path $PsqlPath)) {
    throw "psql.exe が見つかりません: $PsqlPath （-PsqlPath で指定してください）"
}
if (-not (Test-Path $CredFile)) {
    throw "資格情報ファイルが見つかりません: $CredFile"
}

# ---- ~/.claude/.env から資格情報を読む ----------------------------------
$cred = @{}
foreach ($line in Get-Content $CredFile -Encoding UTF8) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $cred[$Matches[1]] = $Matches[2].Trim()
    }
}
foreach ($key in @("DB_ADMIN_USER", "DB_ADMIN_PASSWORD", "DB_APP_PASSWORD")) {
    if (-not $cred.ContainsKey($key) -or [string]::IsNullOrEmpty($cred[$key])) {
        throw "$CredFile に $key がありません"
    }
}
$AdminUser = $cred["DB_ADMIN_USER"]
$AppPass   = $cred["DB_APP_PASSWORD"]

# psql は PGPASSWORD を見る。引数には決して載せない。
$env:PGPASSWORD       = $cred["DB_ADMIN_PASSWORD"]
$env:PGCLIENTENCODING = "UTF8"

function Invoke-PsqlStdin {
    # SQL を標準入力から流す（引数に載せないため）
    param([string]$Database, [string]$Sql)
    $Sql | & $PsqlPath -h $PgHost -p $PgPort -U $AdminUser -d $Database -w -v ON_ERROR_STOP=1 -q
    if ($LASTEXITCODE -ne 0) { throw "psql の実行に失敗しました (db=$Database)" }
}

function Get-PsqlScalar {
    param([string]$Database, [string]$Sql)
    $result = & $PsqlPath -h $PgHost -p $PgPort -U $AdminUser -d $Database -w -tAc $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql の実行に失敗しました (db=$Database)" }
    return ($result | Out-String).Trim()
}

Write-Host "=== DBセットアップ開始 ($PgHost`:$PgPort) ===" -ForegroundColor Cyan

# ---- 1. アプリ用ロール（無ければ作成、あればパスワードを合わせる） ------
Write-Host "[1/4] ロール '$AppRole' を確認..." -ForegroundColor Yellow
# SQL リテラルへ埋めるためシングルクォートをエスケープする。
# （標準入力で流すので、コマンドライン引数に平文が載ることはない）
$AppPassSql = $AppPass.Replace("'", "''")
$roleSql = @"
DO `$do`$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$AppRole') THEN
        ALTER ROLE $AppRole WITH LOGIN PASSWORD '$AppPassSql';
        RAISE NOTICE 'role exists: password updated';
    ELSE
        CREATE ROLE $AppRole WITH LOGIN PASSWORD '$AppPassSql';
        RAISE NOTICE 'role created';
    END IF;
END
`$do`$;
"@
Invoke-PsqlStdin -Database "postgres" -Sql $roleSql

# ---- 2. データベース（CREATE DATABASE は個別に流す） --------------------
foreach ($db in @($ProdDb, $TestDb)) {
    Write-Host "[2/4] データベース '$db' を確認..." -ForegroundColor Yellow
    $exists = Get-PsqlScalar -Database "postgres" -Sql "SELECT 1 FROM pg_database WHERE datname='$db'"
    if ($exists -eq "1") {
        Write-Host "      既に存在します"
    } else {
        Invoke-PsqlStdin -Database "postgres" -Sql "CREATE DATABASE $db OWNER $AppRole ENCODING 'UTF8' TEMPLATE template0;"
        Write-Host "      作成しました"
    }
}

# ---- 3. エンコーディング確認（日本語コメント・データを入れるため） ------
Write-Host "[3/4] エンコーディングを確認..." -ForegroundColor Yellow
foreach ($db in @($ProdDb, $TestDb)) {
    $enc = Get-PsqlScalar -Database "postgres" -Sql "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='$db'"
    if ($enc -ne "UTF8") { throw "$db のエンコーディングが UTF8 ではありません: $enc" }
    Write-Host "      $db = $enc"
}

# ---- 4. スキーマ権限 ----------------------------------------------------
Write-Host "[4/4] スキーマ権限を付与..." -ForegroundColor Yellow
foreach ($db in @($ProdDb, $TestDb)) {
    Invoke-PsqlStdin -Database $db -Sql @"
ALTER SCHEMA public OWNER TO $AppRole;
GRANT ALL ON SCHEMA public TO $AppRole;
"@
}

Remove-Item Env:PGPASSWORD

Write-Host ""
Write-Host "=== 完了 ===" -ForegroundColor Green
Write-Host ".env に以下の2行があることを確認してください（パスワードは ~/.claude/.env の DB_APP_PASSWORD）:"
Write-Host "  DATABASE_URL=postgresql+psycopg://${AppRole}:<DB_APP_PASSWORD>@${PgHost}:${PgPort}/${ProdDb}"
Write-Host "  DATABASE_TEST_URL=postgresql+psycopg://${AppRole}:<DB_APP_PASSWORD>@${PgHost}:${PgPort}/${TestDb}"
Write-Host ""
Write-Host "続けてスキーマを適用する:"
Write-Host "  uv run alembic upgrade head"
Write-Host "  uv run alembic -x test=true upgrade head"
