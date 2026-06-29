<#
.SYNOPSIS
  Log-capturing wrapper for run_maricopa_daily_incremental.cmd.
  Called by Windows Task Scheduler (MaricopaDailyLeadRun).
  Writes dated log to data/logs/maricopa_az/run_YYYY-MM-DD.log.
#>

$RepoRoot = "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"
$CmdFile  = "$RepoRoot\runs\maricopa_az\run_maricopa_daily_incremental.cmd"
$LogDir   = "$RepoRoot\data\logs\maricopa_az"
$RunDate  = Get-Date -Format "yyyy-MM-dd"
$LogFile  = "$LogDir\run_$RunDate.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

"=== MaricopaDailyLeadRun started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -FilePath $LogFile -Append -Encoding UTF8

& cmd.exe /c "`"$CmdFile`"" 2>&1 |
    Tee-Object -FilePath $LogFile -Append

$ExitCode = $LASTEXITCODE

"=== Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') (exit=$ExitCode) ===" |
    Out-File -FilePath $LogFile -Append -Encoding UTF8

exit $ExitCode
