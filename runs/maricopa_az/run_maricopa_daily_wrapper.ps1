<#
.SYNOPSIS
  Log-capturing wrapper for run_maricopa_daily_incremental.cmd.
  Called by Windows Task Scheduler (MaricopaDailyLeadRun).
  Writes dated log to data/logs/maricopa_az/run_YYYY-MM-DD.log.
  On success, refreshes dashboard/data/leads.json via generate_dashboard.py.
#>

$RepoRoot      = "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"
$CmdFile       = "$RepoRoot\runs\maricopa_az\run_maricopa_daily_incremental.cmd"
$DashboardPy   = "$RepoRoot\runs\maricopa_az\generate_dashboard.py"
$DashboardJson = "$RepoRoot\dashboard\data\leads.json"
$LogDir        = "$RepoRoot\data\logs\maricopa_az"
$RunDate       = Get-Date -Format "yyyy-MM-dd"
$LogFile       = "$LogDir\run_$RunDate.log"

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
    Write-Log "[pipeline] ERROR -- skipping dashboard refresh"
    Write-Log "=== Run failed (exit=$PipelineExit) ==="
    exit $PipelineExit
}

# ------------------------------------------------------------------
# Step 2: Dashboard refresh (only on pipeline success)
# ------------------------------------------------------------------
Write-Log "[dashboard] refreshing leads.json"

$DashOutput = & python.exe -X utf8 "$DashboardPy" 2>&1
$DashExit   = $LASTEXITCODE
$DashOutput | Out-File -FilePath $LogFile -Append -Encoding UTF8

if ($DashExit -ne 0) {
    Write-Log "[dashboard] ERROR -- generate_dashboard.py failed (exit=$DashExit)"
} else {
    if (Test-Path $DashboardJson) {
        $Mtime      = (Get-Item $DashboardJson).LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
        $LeadCount  = (Get-Content $DashboardJson -Raw | ConvertFrom-Json).lead_total
        Write-Log "[dashboard] OK -- leads.json updated $Mtime, lead_total=$LeadCount"
    } else {
        Write-Log "[dashboard] WARNING -- leads.json not found after refresh"
    }
}

Write-Log "=== Run complete (pipeline=0, dashboard=$DashExit) ==="
exit 0
