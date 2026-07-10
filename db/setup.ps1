# ============================================================
# 物件検索通知システム - DB・テーブルセットアップスクリプト
# 使用方法: PowerShell で .\db\setup.ps1 を実行
# 前提: PostgreSQL の postgres スーパーユーザーパスワードが必要
# ============================================================

param(
    [string]$PgSuperPassword = "",
    [string]$PgSuperUser    = "postgres",
    [string]$PgHost         = "localhost",
    [string]$PgPort         = "5432"
)

$env:PATH = "C:\Program Files\PostgreSQL\18\bin;" + $env:PATH

# .envからDB接続情報を読み取る
$envFile = Join-Path $PSScriptRoot ".." ".env"
$dbUser = "search_house"
$dbPass = "sh_pass_2025"
$dbName = "searching_for_houses"

if (Test-Path $envFile) {
    $envContent = Get-Content $envFile | Where-Object { $_ -match "DATABASE_URL" }
    if ($envContent -match "://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)") {
        $dbUser = $Matches[1]
        $dbPass = $Matches[2]
        $PgHost = $Matches[3]
        $PgPort = $Matches[4]
        $dbName = $Matches[5]
    }
}

Write-Host "=== セットアップ開始 ===" -ForegroundColor Cyan
Write-Host "DB: $dbName  User: $dbUser  Host: ${PgHost}:${PgPort}"

# postgres スーパーユーザーパスワードの入力
if (-not $PgSuperPassword) {
    $secPass = Read-Host "PostgreSQL スーパーユーザー($PgSuperUser)のパスワードを入力してください" -AsSecureString
    $PgSuperPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secPass)
    )
}

$env:PGPASSWORD = $PgSuperPassword

# Step 1: ユーザー作成
Write-Host "`n[1/8] ユーザー '$dbUser' を作成..." -ForegroundColor Yellow
$createUser = @"
DO `$`$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$dbUser') THEN
        CREATE ROLE $dbUser WITH LOGIN PASSWORD '$dbPass';
        RAISE NOTICE 'ユーザー $dbUser を作成しました';
    ELSE
        RAISE NOTICE 'ユーザー $dbUser は既に存在します';
    END IF;
END
`$`$;
"@
$createUser | psql -h $PgHost -p $PgPort -U $PgSuperUser -d postgres
if ($LASTEXITCODE -ne 0) {
    Write-Host "エラー: ユーザー作成に失敗しました" -ForegroundColor Red
    exit 1
}

# Step 2: データベース作成
Write-Host "`n[2/8] データベース '$dbName' を作成..." -ForegroundColor Yellow
$checkDb = psql -h $PgHost -p $PgPort -U $PgSuperUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$dbName'"
if ($checkDb -eq "1") {
    Write-Host "データベース '$dbName' は既に存在します"
} else {
    psql -h $PgHost -p $PgPort -U $PgSuperUser -d postgres -c "CREATE DATABASE $dbName OWNER $dbUser ENCODING 'UTF8' LC_COLLATE 'Japanese_Japan.932' LC_CTYPE 'Japanese_Japan.932' TEMPLATE template0;" 2>&1
    if ($LASTEXITCODE -ne 0) {
        # 日本語ロケール失敗時はデフォルトで再試行
        Write-Host "日本語ロケールで失敗。デフォルトロケールで再試行..." -ForegroundColor Yellow
        psql -h $PgHost -p $PgPort -U $PgSuperUser -d postgres -c "CREATE DATABASE $dbName OWNER $dbUser;"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "エラー: データベース作成に失敗しました" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host "データベース '$dbName' を作成しました" -ForegroundColor Green
}

# Step 3: スキーマ作成
Write-Host "`n[3/8] テーブル作成 (01_schema.sql)..." -ForegroundColor Yellow
$env:PGPASSWORD = $dbPass
$schemaFile = Join-Path $PSScriptRoot "01_schema.sql"
psql -h $PgHost -p $PgPort -U $dbUser -d $dbName -f $schemaFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "エラー: スキーマ作成に失敗しました" -ForegroundColor Red
    exit 1
}
Write-Host "テーブル作成 完了" -ForegroundColor Green

# Step 4: マスタデータ投入
Write-Host "`n[4/8] マスタデータ投入 (02_master_data.sql)..." -ForegroundColor Yellow
$dataFile = Join-Path $PSScriptRoot "02_master_data.sql"
psql -h $PgHost -p $PgPort -U $dbUser -d $dbName -f $dataFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "エラー: マスタデータ投入に失敗しました" -ForegroundColor Red
    exit 1
}
Write-Host "マスタデータ投入 完了" -ForegroundColor Green

# Step 5: アプリテーブル作成
Write-Host "`n[5/8] アプリテーブル作成 (03_app_tables.sql)..." -ForegroundColor Yellow
$appTableFile = Join-Path $PSScriptRoot "03_app_tables.sql"
psql -h $PgHost -p $PgPort -U $dbUser -d $dbName -f $appTableFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "エラー: アプリテーブル作成に失敗しました" -ForegroundColor Red
    exit 1
}
Write-Host "アプリテーブル作成 完了" -ForegroundColor Green

# Step 6: 市区町村テーブル作成
Write-Host "`n[6/8] 市区町村テーブル作成 (04_cities.sql)..." -ForegroundColor Yellow
$citiesFile = Join-Path $PSScriptRoot "04_cities.sql"
psql -h $PgHost -p $PgPort -U $dbUser -d $dbName -f $citiesFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "エラー: 市区町村テーブル作成に失敗しました" -ForegroundColor Red
    exit 1
}
Write-Host "市区町村テーブル作成 完了" -ForegroundColor Green

# Step 7: 市区町村データ投入
Write-Host "`n[7/8] 市区町村データ投入 (05_city_data.sql)..." -ForegroundColor Yellow
$cityDataFile = Join-Path $PSScriptRoot "05_city_data.sql"
psql -h $PgHost -p $PgPort -U $dbUser -d $dbName -f $cityDataFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "エラー: 市区町村データ投入に失敗しました" -ForegroundColor Red
    exit 1
}
Write-Host "市区町村データ投入 完了" -ForegroundColor Green

# Step 8: サイト別URLマッピング投入
Write-Host "`n[8/8] サイト別URLマッピング投入 (06_site_mappings.sql)..." -ForegroundColor Yellow
$siteMappingsFile = Join-Path $PSScriptRoot "06_site_mappings.sql"
psql -h $PgHost -p $PgPort -U $dbUser -d $dbName -f $siteMappingsFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "エラー: サイト別URLマッピング投入に失敗しました" -ForegroundColor Red
    exit 1
}
Write-Host "サイト別URLマッピング投入 完了" -ForegroundColor Green

# 件数確認
Write-Host "`n=== 投入件数確認 ===" -ForegroundColor Cyan
$query = @"
SELECT 'property_type_master'       AS table_name, COUNT(*) AS rows FROM property_type_master
UNION ALL
SELECT 'site_master',                 COUNT(*) FROM site_master
UNION ALL
SELECT 'condition_category_master',   COUNT(*) FROM condition_category_master
UNION ALL
SELECT 'condition_master',            COUNT(*) FROM condition_master
UNION ALL
SELECT 'condition_property_type_map', COUNT(*) FROM condition_property_type_map
UNION ALL
SELECT 'cities',                      COUNT(*) FROM cities
UNION ALL
SELECT 'city_site_mappings',          COUNT(*) FROM city_site_mappings
ORDER BY 1;
"@
$query | psql -h $PgHost -p $PgPort -U $dbUser -d $dbName

Write-Host "`n=== セットアップ完了 ===" -ForegroundColor Green
