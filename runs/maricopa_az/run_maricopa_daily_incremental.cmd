@echo off
setlocal
cd /d "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"

echo === Maricopa Daily Incremental: %DATE% %TIME% ===
echo.

echo [1/3] Fetching NOTS records (last 10 days, catches delayed name indexing)...
python -X utf8 scrapers\recorder_maricopa.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo ERROR: recorder scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [2/3] Refreshing treasurer tax lien data (diffs against previous pull, new liens only)...
python -X utf8 scrapers\treasurer_tax_lien_maricopa.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: treasurer scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [3/3] Running daily incremental pipeline (processes only new records)...
python -X utf8 runs\maricopa_az\run_maricopa_daily_incremental.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: daily incremental pipeline failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo === Done: %DATE% %TIME% ===
endlocal
