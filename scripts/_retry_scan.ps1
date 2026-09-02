$ErrorActionPreference = "Continue"
$repo = "F:\searching-for-houses"
$py   = Join-Path $repo ".venv\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
foreach ($site in @("SUUMO", "HOMES")) {
    foreach ($pat in @("東京23区賃貸", "近郊45分圏賃貸")) {
        $out = Join-Path $repo "logs\retry_${site}_${stamp}.out.log"
        $err = Join-Path $repo "logs\retry_${site}_${stamp}.err.log"
        & $py -m house_search.cli scan --seed --full --site $site --pattern $pat --detail-limit 400 *>> $out
    }
}
