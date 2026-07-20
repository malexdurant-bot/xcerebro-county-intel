@echo off
REM Richland County SC — daily pipeline run
REM Called by Windows Task Scheduler or manually.
REM
REM Usage:
REM   run_richland_daily.cmd          (incremental)
REM   run_richland_daily.cmd --full   (full rebuild)

setlocal

set "REPO_ROOT=%~dp0..\.."
set "LOG_FILE=%REPO_ROOT%\runs\richland_sc\richland_pipeline.log"
set "PYTHON=python"

echo [%DATE% %TIME%] Starting Richland SC pipeline >> "%LOG_FILE%" 2>&1

%PYTHON% "%REPO_ROOT%\runs\richland_sc\run_pipeline.py" %* >> "%LOG_FILE%" 2>&1

if %ERRORLEVEL% EQU 0 (
    echo [%DATE% %TIME%] Pipeline completed successfully >> "%LOG_FILE%" 2>&1
) else (
    echo [%DATE% %TIME%] Pipeline exited with code %ERRORLEVEL% >> "%LOG_FILE%" 2>&1
)

endlocal
