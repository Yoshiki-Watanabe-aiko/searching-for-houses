# 賃料上限(ct)の修正前に走って0件だった SUUMO・HOMES を取り直す一時スクリプト。
# --pattern は渡さない（省略すると全パターンを回る）。日本語や区切り文字を
# コマンドライン・パス文字列へ直接書かず、Join-Path で組み立てる。
$ErrorActionPreference = "Continue"
$repo   = "F:/searching-for-houses"
$py     = Join-Path $repo ".venv/Scripts/python.exe"
$logDir = Join-Path $repo "logs"
$stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
foreach ($site in @("SUUMO", "HOMES")) {
    $out = Join-Path $logDir ("retry_" + $site + "_" + $stamp + ".log")
    & $py -m house_search.cli scan --seed --full --site $site --detail-limit 400 *>> $out
}
