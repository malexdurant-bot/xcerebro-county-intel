@echo off
setlocal
cd /d "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel"

echo === Shelby Daily Lead Run: %DATE% %TIME% ===
echo.

echo [1/6] Tax sale list (CSV from Shelby County Trustee)...
python -X utf8 scrapers\trustee_tax_sale_shelby.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: tax sale scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [2/6] Register of Deeds ^(APPT / IRS / LIEN / NCTS / TNTX / STR, last 10 days^)...
python -X utf8 scrapers\register_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo ERROR: register scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [3/6] General Sessions Civil ^(evictions / FED, last 10 days^)...
python -X utf8 scrapers\general_sessions_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo ERROR: general sessions scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [4/6] Chancery Court ^(FO / PA / QT, last 10 days^)...
python -X utf8 scrapers\chancery_court_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo ERROR: chancery scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
)

echo.
echo [5/6] Probate Court ^(CourtConnect, estate cases last 10 days^)...
python -X utf8 scrapers\probate_court_shelby.py --days-back 10
if %ERRORLEVEL% neq 0 (
    echo ERROR: probate scraper failed ^(exit code %ERRORLEVEL%^).
    exit /b 1
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
