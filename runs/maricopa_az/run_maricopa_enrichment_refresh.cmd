@echo off
setlocal
cd /d "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"

echo === Maricopa Enrichment Refresh: %DATE% %TIME% ===
echo Refreshes parcel enrichment for all existing leads using local index.
echo Does not re-fetch raw sources. Takes approximately 4 hours.
echo Press Ctrl+C to cancel, or wait 10 seconds to continue...
timeout /t 10
echo.

echo [1/4] Running full pipeline with local parcel index (re-enrichment)...
python -X utf8 runs\maricopa_az\run_pipeline.py --use-local-index
if %ERRORLEVEL% neq 0 (
    echo ERROR: pipeline failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [2/4] Updating lead history with refreshed scores...
python -X utf8 runs\maricopa_az\run_maricopa_daily_incremental.py --rebuild
if %ERRORLEVEL% neq 0 (
    echo ERROR: lead history update failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [3/4] Exporting refreshed leads to CSV...
python -X utf8 runs\maricopa_az\export_leads.py

echo.
echo [4/4] Regenerating dashboard...
python -X utf8 runs\maricopa_az\generate_dashboard.py

echo.
echo === Enrichment Refresh Done: %DATE% %TIME% ===
endlocal
