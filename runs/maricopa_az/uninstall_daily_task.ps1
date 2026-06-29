<#
.SYNOPSIS
  Removes the MaricopaDailyLeadRun Windows Scheduled Task.

.DESCRIPTION
  Unregisters the task from Task Scheduler. Log files are preserved.

  Usage:
      powershell -ExecutionPolicy Bypass -File .\runs\maricopa_az\uninstall_daily_task.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "MaricopaDailyLeadRun"

Write-Host ""
Write-Host "=== Uninstalling $TaskName ==="

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    Write-Host "  Task '$TaskName' not found — nothing to remove."
    exit 0
}

Write-Host "  Found task (state: $($Task.State))"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

$Check = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Check) {
    Write-Error "Task still present after unregister attempt."
    exit 1
}

Write-Host "  Removed successfully."
Write-Host "  Log files preserved at: data\logs\maricopa_az\"
Write-Host "  Reinstall: powershell -ExecutionPolicy Bypass -File .\runs\maricopa_az\install_daily_task.ps1"
Write-Host ""
