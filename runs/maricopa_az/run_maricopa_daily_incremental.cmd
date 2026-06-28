@echo off
setlocal
cd /d "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"

echo === Maricopa Daily Incremental: %DATE% %TIME% ===
echo.

echo [1/2] Fetching new NOTS records (last 24h)...
python -X utf8 scrapers\recorder_maricopa.py --days-back 1
if %ERRORLEVEL% neq 0 (
    echo ERROR: recorder scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [2/2] Running daily incremental pipeline...
python -X utf8 runs\maricopa_az\run_maricopa_daily_incremental.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: daily incremental pipeline failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo === Done: %DATE% %TIME% ===
endlocal
