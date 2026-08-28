@echo off
REM Dallas County TX — daily pipeline run
REM Called by Windows Task Scheduler or manually.
REM
REM Usage:
REM   run_dallas_daily.cmd                     (all sources, incl. weekly TRW re-scrape)
REM   run_dallas_daily.cmd --skip-tax-collector (skip the ~10-20min weekly TRW re-parse;
REM                                              use on days other than the weekly refresh
REM                                              day to keep the daily run fast)

setlocal

set "REPO_ROOT=%~dp0..\.."
set "LOG_FILE=%REPO_ROOT%\runs\dallas_tx\dallas_pipeline.log"
set "PYTHON=python"

echo [%DATE% %TIME%] Starting Dallas TX pipeline >> "%LOG_FILE%" 2>&1

%PYTHON% "%REPO_ROOT%\runs\dallas_tx\run_pipeline.py" %* >> "%LOG_FILE%" 2>&1

if %ERRORLEVEL% EQU 0 (
    echo [%DATE% %TIME%] Pipeline completed successfully >> "%LOG_FILE%" 2>&1
) else (
    echo [%DATE% %TIME%] Pipeline exited with code %ERRORLEVEL% >> "%LOG_FILE%" 2>&1
)

endlocal
