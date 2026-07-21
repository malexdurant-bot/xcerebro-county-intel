<#
.SYNOPSIS
  Registers the ShelbyDailyLeadRun Windows Scheduled Task.

.DESCRIPTION
  Creates a daily task at 7:30 AM that runs all Shelby County scrapers
  and the lead pipeline. Output is captured to a dated log file.

  Attempts to register with S4U logon (runs whether logged in or not).
  Falls back to InteractiveToken if S4U is unavailable.

  Run this script once:
      powershell -ExecutionPolicy Bypass -File .\runs\shelby_tn\install_daily_task.ps1

  To uninstall:
      Unregister-ScheduledTask -TaskName "ShelbyDailyLeadRun" -Confirm:$false

  To run immediately:
      Start-ScheduledTask -TaskName "ShelbyDailyLeadRun"
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName    = "ShelbyDailyLeadRun"
$RepoRoot    = "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"
$WrapperPs1  = "$RepoRoot\runs\shelby_tn\run_shelby_daily_wrapper.ps1"
$LogDir      = "$RepoRoot\data\logs\shelby_tn"
$TriggerTime = "07:30"
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

Write-Host ""
Write-Host "=== Installing ShelbyDailyLeadRun ==="
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
    Write-Warning "S4U failed -- falling back to Interactive."

    $Principal = New-ScheduledTaskPrincipal `
        -UserId $CurrentUser -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Settings $Settings -Principal $Principal -Force | Out-Null

    $LogonMethod = "Interactive (runs only when logged in)"
    Write-Host "  Logon type: Interactive -- task runs only while logged in."
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
Write-Host "=== Manage ==="
Write-Host "  Run now    : Start-ScheduledTask -TaskName ShelbyDailyLeadRun"
Write-Host "  GUI        : taskschd.msc"
Write-Host "  Uninstall  : Unregister-ScheduledTask -TaskName ShelbyDailyLeadRun -Confirm:`$false"
Write-Host ""
