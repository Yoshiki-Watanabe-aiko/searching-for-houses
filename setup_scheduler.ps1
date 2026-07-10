# Windows Task Scheduler setup for house-notifier
# Run as Administrator

$exePath = "$PSScriptRoot\house-notifier.exe"
$workDir = $PSScriptRoot

# --- Task 1: 通常スキャン（毎時） ---
$action1 = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $workDir
$trigger1 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Hours 1) -Once -At (Get-Date)
$settings1 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -RunOnlyIfNetworkAvailable $true
Register-ScheduledTask -TaskName "HouseNotifier-Scan" -Action $action1 -Trigger $trigger1 -Settings $settings1 -RunLevel Highest -Force
Write-Host "Task registered: HouseNotifier-Scan (hourly)"

# --- Task 2: 成約チェック（毎日 9:00） ---
$action2 = New-ScheduledTaskAction -Execute $exePath -Argument "--check-sold" -WorkingDirectory $workDir
$trigger2 = New-ScheduledTaskTrigger -Daily -At "09:00"
$settings2 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -RunOnlyIfNetworkAvailable $true
Register-ScheduledTask -TaskName "HouseNotifier-CheckSold" -Action $action2 -Trigger $trigger2 -Settings $settings2 -RunLevel Highest -Force
Write-Host "Task registered: HouseNotifier-CheckSold (daily 09:00)"

# --- Task 3: フルスキャン（毎日 3:00） ---
$action3 = New-ScheduledTaskAction -Execute $exePath -Argument "--full-scan" -WorkingDirectory $workDir
$trigger3 = New-ScheduledTaskTrigger -Daily -At "03:00"
$settings3 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RunOnlyIfNetworkAvailable $true
Register-ScheduledTask -TaskName "HouseNotifier-FullScan" -Action $action3 -Trigger $trigger3 -Settings $settings3 -RunLevel Highest -Force
Write-Host "Task registered: HouseNotifier-FullScan (daily 03:00)"

Write-Host "`nAll tasks registered. Check Task Scheduler to verify."
