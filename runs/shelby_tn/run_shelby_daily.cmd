@echo off
REM Shelby County TN — daily pipeline run
REM Called by Windows Task Scheduler (ShelbyDailyLeadRun) or manually.
REM
REM Each scraper step is non-fatal: a single source's transient failure
REM (e.g. a court portal DNS/connectivity blip) no longer aborts the whole
REM day's run. Whatever sources succeed still feed the pipeline in step 6/6,
REM so one bad portal doesn't cost you every other source's leads for the day.
setlocal

cd /d "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"

echo === Shelby Daily Lead Run: %DATE% %TIME% ===
echo.

echo [1/6] Tax sale list (CSV from Shelby County Trustee)...
python -X utf8 scrapers\trustee_tax_sale_shelby.py
if %ERRORLEVEL% neq 0 (
    echo WARNING: tax sale scraper failed ^(exit code %ERRORLEVEL%^) — continuing.
)

echo.
echo [2/6] Register of Deeds ^(APPT / IRS / LIEN / NCTS / TNTX / STR, last 10 days^)...
python -X utf8 scrapers\register_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo WARNING: register scraper failed ^(exit code %ERRORLEVEL%^) — continuing.
)

echo.
echo [3/6] General Sessions Civil ^(evictions / FED, last 10 days^)...
python -X utf8 scrapers\general_sessions_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo WARNING: general sessions scraper failed ^(exit code %ERRORLEVEL%^) — continuing.
)

echo.
echo [4/6] Chancery Court ^(FO / PA / QT, last 10 days^)...
python -X utf8 scrapers\chancery_court_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo WARNING: chancery scraper failed ^(exit code %ERRORLEVEL%^) — continuing.
)

echo.
echo [5/6] Probate Court ^(CourtConnect, estate cases last 10 days^)...
python -X utf8 scrapers\probate_court_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo WARNING: probate scraper failed ^(exit code %ERRORLEVEL%^) — continuing.
)

echo.
echo [6/6] Running lead pipeline...
python -X utf8 runs\shelby_tn\run_pipeline.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: pipeline failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo === Done: %DATE% %TIME% ===
endlocal
