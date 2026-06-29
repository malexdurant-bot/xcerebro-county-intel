<#
.SYNOPSIS
  Registers the MaricopaDailyLeadRun Windows Scheduled Task.

.DESCRIPTION
  Creates a daily task at 7:00 AM that runs the Maricopa County incremental
  lead pipeline. Output is captured to a dated log file.

  Attempts to register with S4U logon (runs whether logged in or not).
  Falls back to InteractiveToken if S4U is unavailable.

  Run this script once:
      powershell -ExecutionPolicy Bypass -File .\runs\maricopa_az\install_daily_task.ps1

  To uninstall:
      powershell -ExecutionPolicy Bypass -File .\runs\maricopa_az\uninstall_daily_task.ps1

  To run the task immediately:
      Start-ScheduledTask -TaskName "MaricopaDailyLeadRun"
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName    = "MaricopaDailyLeadRun"
$RepoRoot    = "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"
$WrapperPs1  = "$RepoRoot\runs\maricopa_az\run_maricopa_daily_wrapper.ps1"
$LogDir      = "$RepoRoot\data\logs\maricopa_az"
$TriggerTime = "07:00"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

Write-Host ""
Write-Host "=== Installing MaricopaDailyLeadRun ==="
Write-Host "  Task name : $TaskName"
Write-Host "  User      : $CurrentUser"
Write-Host "  Trigger   : Daily at $TriggerTime"
Write-Host "  Wrapper   : $WrapperPs1"
Write-Host "  Log dir   : $LogDir"
Write-Host ""

if (-not (Test-Path $WrapperPs1)) {
    Write-Error "Wrapper script not found: $WrapperPs1"
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$TaskArg = '-NonInteractive -ExecutionPolicy Bypass -File "' + $WrapperPs1 + '"'

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $TaskArg `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -MultipleInstances IgnoreNew

$LogonMethod = ""

try {
    $Principal = New-ScheduledTaskPrincipal `
        -UserId $CurrentUser -LogonType S4U -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Settings $Settings -Principal $Principal -Force | Out-Null

    $LogonMethod = "S4U (runs whether logged in or not)"
    Write-Host "  Logon type: S4U -- task runs even when logged out."
}
catch {
    Write-Warning "S4U failed -- falling back to InteractiveToken."

    $Principal = New-ScheduledTaskPrincipal `
        -UserId $CurrentUser -LogonType InteractiveToken -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Settings $Settings -Principal $Principal -Force | Out-Null

    $LogonMethod = "InteractiveToken (runs only when logged in)"
    Write-Host "  Logon type: InteractiveToken -- task runs only while logged in."
}

$Task     = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$TaskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
$NextRun  = if ($TaskInfo.NextRunTime) { $TaskInfo.NextRunTime.ToString("yyyy-MM-dd HH:mm:ss") } else { "(pending)" }

Write-Host ""
Write-Host "=== Installed Successfully ==="
Write-Host "  Task name  : $TaskName"
Write-Host "  Status     : $($Task.State)"
Write-Host "  Logon      : $LogonMethod"
Write-Host "  Trigger    : Daily at $TriggerTime"
Write-Host "  Next run   : $NextRun"
Write-Host "  Execute    : powershell.exe"
Write-Host ("  Argument   : " + $TaskArg)
Write-Host "  WorkingDir : $RepoRoot"
Write-Host "  Log dir    : $LogDir"
Write-Host ""
Write-Host '=== Manage ==='
Write-Host '  Run now    : Start-ScheduledTask -TaskName MaricopaDailyLeadRun'
Write-Host '  GUI        : taskschd.msc'
Write-Host '  Uninstall  : powershell -ExecutionPolicy Bypass -File .\runs\maricopa_az\uninstall_daily_task.ps1'
Write-Host ""
