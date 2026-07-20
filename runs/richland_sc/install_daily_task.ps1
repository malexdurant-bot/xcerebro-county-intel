# Richland County SC — install Windows Task Scheduler daily run
#
# Run once as Administrator (or with Task Scheduler write permission):
#   powershell -ExecutionPolicy Bypass -File install_daily_task.ps1
#
# Schedules the pipeline to run every morning at 7:00 AM.
# Adjust $TriggerTime below to change the run time.

$TaskName   = "XCerebro-Richland-SC-Daily"
$TriggerTime = "07:00"
$RepoRoot   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$CmdScript  = Join-Path $PSScriptRoot "run_richland_daily.cmd"

# Remove existing task if it exists
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$CmdScript`""
$trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Limited `
    -Description "Daily Richland County SC lead intelligence pipeline (Columbia Star scraper)" `
    -Force | Out-Null

Write-Host "Installed task: $TaskName"
Write-Host "  Runs daily at: $TriggerTime"
Write-Host "  Script:        $CmdScript"
Write-Host "  Log:           $RepoRoot\runs\richland_sc\richland_pipeline.log"
Write-Host ""
Write-Host "To run immediately:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove:           Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
