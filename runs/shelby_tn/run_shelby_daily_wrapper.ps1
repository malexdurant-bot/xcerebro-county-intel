<#
.SYNOPSIS
  Log-capturing wrapper for run_shelby_daily.cmd.
  Called by Windows Task Scheduler (ShelbyDailyLeadRun).

  On each successful run:
    1. Runs all Shelby scrapers + lead pipeline
    2. Sends a Windows toast notification with lead count
#>

$RepoRoot       = "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"
$CmdFile        = "$RepoRoot\runs\shelby_tn\run_shelby_daily.cmd"
$ScoredLeads    = "$RepoRoot\runs\shelby_tn\pipeline_output\scored_leads.json"
$LogDir         = "$RepoRoot\data\logs\shelby_tn"
$RunDate        = Get-Date -Format "yyyy-MM-dd"
$LogFile        = "$LogDir\run_$RunDate.log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message"
    $line | Out-File -FilePath $LogFile -Append -Encoding UTF8
    Write-Host $line
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Log "=== ShelbyDailyLeadRun started ==="

# ------------------------------------------------------------------
# Step 1: Scrapers + pipeline
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
# Step 2: Toast notification
# ------------------------------------------------------------------
$LeadCount = 0
if (Test-Path $ScoredLeads) {
    try {
        $leads = Get-Content $ScoredLeads -Raw | ConvertFrom-Json
        $LeadCount = if ($leads -is [array]) { $leads.Count } else { 1 }
    } catch {}
}

try {
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]

    $ToastXml = [Windows.Data.Xml.Dom.XmlDocument]::new()
    $ToastXmlStr = @'
<toast duration="long">
  <visual>
    <binding template="ToastGeneric">
      <text>Shelby County Daily Run Complete</text>
      <text>LEADCOUNT scored leads | Log: data/logs/shelby_tn/</text>
    </binding>
  </visual>
</toast>
'@
    $ToastXmlStr = $ToastXmlStr -replace 'LEADCOUNT', $LeadCount
    $ToastXml.LoadXml($ToastXmlStr)

    $Toast = [Windows.UI.Notifications.ToastNotification]::new($ToastXml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Windows PowerShell").Show($Toast)
    Write-Log "[toast] sent -- $LeadCount scored leads"
} catch {
    Write-Log "[toast] WARNING -- notification failed: $_"
}

# ------------------------------------------------------------------
# Done
# ------------------------------------------------------------------
Write-Log "=== Run complete ==="
exit 0
