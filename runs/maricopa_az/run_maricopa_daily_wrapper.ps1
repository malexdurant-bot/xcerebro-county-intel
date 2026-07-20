<#
.SYNOPSIS
  Log-capturing wrapper for run_maricopa_daily_incremental.cmd.
  Called by Windows Task Scheduler (MaricopaDailyLeadRun).

  On each successful run:
    1. Runs the daily incremental pipeline
    2. Refreshes dashboard/data/leads.json
    3. Copies new_leads CSV to the Desktop
    4. Creates the Maricopa Dashboard shortcut on the Desktop (once)
    5. Sends a Windows toast notification
#>

$RepoRoot       = "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"
$CmdFile        = "$RepoRoot\runs\maricopa_az\run_maricopa_daily_incremental.cmd"
$DashboardPy    = "$RepoRoot\runs\maricopa_az\generate_dashboard.py"
$StartDashCmd   = "$RepoRoot\runs\maricopa_az\start_dashboard.cmd"
$DashboardJson  = "$RepoRoot\dashboard\data\leads.json"
$ExportsLatest  = "$RepoRoot\data\exports\maricopa_az\latest"
$LogDir         = "$RepoRoot\data\logs\maricopa_az"
$RunDate        = Get-Date -Format "yyyy-MM-dd"
$LogFile        = "$LogDir\run_$RunDate.log"
$Desktop        = [Environment]::GetFolderPath("Desktop")
$ShortcutPath   = "$Desktop\Maricopa Dashboard.lnk"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message"
    $line | Out-File -FilePath $LogFile -Append -Encoding UTF8
    Write-Host $line
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Log "=== MaricopaDailyLeadRun started ==="

# ------------------------------------------------------------------
# Step 1: Daily incremental pipeline
# ------------------------------------------------------------------
Write-Log "[pipeline] starting"

& cmd.exe /c "`"$CmdFile`"" 2>&1 | Tee-Object -FilePath $LogFile -Append

$PipelineExit = $LASTEXITCODE
Write-Log "[pipeline] finished (exit=$PipelineExit)"

if ($PipelineExit -ne 0) {
    Write-Log "[pipeline] ERROR -- skipping post-run steps"
    Write-Log "=== Run failed (exit=$PipelineExit) ==="
    exit $PipelineExit
}

# ------------------------------------------------------------------
# Step 2: Dashboard refresh
# ------------------------------------------------------------------
Write-Log "[dashboard] refreshing leads.json"

$DashOutput = & python.exe -X utf8 "$DashboardPy" 2>&1
$DashExit   = $LASTEXITCODE
$DashOutput | Out-File -FilePath $LogFile -Append -Encoding UTF8

$LeadTotal = 0
if ($DashExit -ne 0) {
    Write-Log "[dashboard] ERROR -- generate_dashboard.py failed (exit=$DashExit)"
} else {
    if (Test-Path $DashboardJson) {
        $Mtime     = (Get-Item $DashboardJson).LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
        try { $LeadTotal = (Get-Content $DashboardJson -Raw | ConvertFrom-Json).lead_total } catch {}
        Write-Log "[dashboard] OK -- leads.json updated $Mtime, lead_total=$LeadTotal"
    } else {
        Write-Log "[dashboard] WARNING -- leads.json not found after refresh"
    }
}

# ------------------------------------------------------------------
# Step 3: Copy new_leads CSV to Desktop
# ------------------------------------------------------------------
$CsvSource = "$ExportsLatest\new_leads.csv"
$CsvDest   = "$Desktop\Maricopa new_leads_$RunDate.csv"

if (Test-Path $CsvSource) {
    try {
        Copy-Item $CsvSource $CsvDest -Force
        $Rows = (Get-Content $CsvSource | Measure-Object -Line).Lines - 1
        Write-Log "[desktop] new_leads CSV copied ($Rows rows) -- $CsvDest"
    } catch {
        Write-Log "[desktop] WARNING -- CSV copy failed: $_"
    }
} else {
    Write-Log "[desktop] WARNING -- $CsvSource not found, skipping CSV copy"
}

# ------------------------------------------------------------------
# Step 4: Dashboard shortcut on Desktop (created once, idempotent)
# ------------------------------------------------------------------
if (-not (Test-Path $ShortcutPath)) {
    try {
        $WshShell              = New-Object -ComObject WScript.Shell
        $Shortcut              = $WshShell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath   = $StartDashCmd
        $Shortcut.WorkingDirectory = "$RepoRoot\dashboard"
        $Shortcut.Description  = "Open Maricopa lead dashboard"
        $Shortcut.Save()
        Write-Log "[desktop] shortcut created -- $ShortcutPath"
    } catch {
        Write-Log "[desktop] WARNING -- shortcut creation failed: $_"
    }
} else {
    Write-Log "[desktop] shortcut already exists"
}

# ------------------------------------------------------------------
# Step 5: Toast notification
# ------------------------------------------------------------------
$NewCount = 0
if (Test-Path $CsvSource) {
    try { $NewCount = (Get-Content $CsvSource | Measure-Object -Line).Lines - 1 } catch {}
}

try {
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

    $ToastXml = [Windows.Data.Xml.Dom.XmlDocument]::new()
    $ToastXmlStr = @'
<toast duration="long">
  <visual>
    <binding template="ToastGeneric">
      <text>Maricopa Daily Run Complete</text>
      <text>NEWCOUNT new leads today | LEADTOTAL total | CSV on Desktop</text>
    </binding>
  </visual>
</toast>
'@
    $ToastXmlStr = $ToastXmlStr -replace 'NEWCOUNT', $NewCount -replace 'LEADTOTAL', $LeadTotal
    $ToastXml.LoadXml($ToastXmlStr)

    $Toast = [Windows.UI.Notifications.ToastNotification]::new($ToastXml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Windows PowerShell").Show($Toast)
    Write-Log "[toast] sent -- $NewCount new leads, $LeadTotal total"
} catch {
    Write-Log "[toast] WARNING -- notification failed: $_"
}

# ------------------------------------------------------------------
# Done
# ------------------------------------------------------------------
Write-Log "=== Run complete (pipeline=0, dashboard=$DashExit) ==="
exit 0
