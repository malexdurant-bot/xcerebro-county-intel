@echo off
setlocal
cd /d "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"

echo === Maricopa Full Rebuild: %DATE% %TIME% ===
echo WARNING: Manual-only. Full rebuild takes approximately 4 hours.
echo Press Ctrl+C to cancel, or wait 10 seconds to continue...
timeout /t 10
echo.

echo [1/5] Fetching NOTS records (90-day window)...
python -X utf8 scrapers\recorder_maricopa.py --days-back 90
if %ERRORLEVEL% neq 0 (
    echo ERROR: recorder scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [2/5] Treasurer tax lien fetch — SKIPPED by default (169K records, ~30 min).
echo        To enable: uncomment the block below and re-run.
REM echo [2/5] Fetching treasurer tax lien records (full dataset)...
REM python -X utf8 scrapers\treasurer_tax_lien_maricopa.py
REM if %ERRORLEVEL% neq 0 (
REM     echo ERROR: treasurer scraper failed ^(exit code %ERRORLEVEL%^).
REM     exit /b 1
REM )

echo.
echo [3/5] Running full pipeline with local parcel index...
python -X utf8 runs\maricopa_az\run_pipeline.py --use-local-index
if %ERRORLEVEL% neq 0 (
    echo ERROR: full pipeline failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [4/5] Rebuilding lead history from pipeline output...
python -X utf8 runs\maricopa_az\run_maricopa_daily_incremental.py --rebuild
if %ERRORLEVEL% neq 0 (
    echo ERROR: lead history rebuild failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [5/5] Exporting leads and generating dashboard...
python -X utf8 runs\maricopa_az\export_leads.py
python -X utf8 runs\maricopa_az\generate_dashboard.py

echo.
echo === Full Rebuild Done: %DATE% %TIME% ===
endlocal
