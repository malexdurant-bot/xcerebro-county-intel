@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

REM ============================================================
REM Maricopa Justice Court Eviction Fetcher
REM
REM REQUIRES: pip install playwright && playwright install chromium
REM
REM The portal uses Google reCAPTCHA v3 — Playwright required.
REM
REM SEARCH STRATEGY: Use --business-name with known landlord
REM property management entities operating in Maricopa County.
REM
REM Replace the --business-name values below with actual PM companies
REM or landlord entity names known to file FED cases in Maricopa.
REM
REM For FED (Forcible Entry and Detainer) eviction cases, search
REM for the PLAINTIFF (landlord) rather than the defendant (tenant).
REM
REM Example business searches (adapt to known local entities):
REM   python -X utf8 scrapers\justice_court_evictions_maricopa.py --business-name "INVITATION HOMES" --max-features 50
REM   python -X utf8 scrapers\justice_court_evictions_maricopa.py --business-name "PROGRESS RESIDENTIAL" --max-features 50
REM   python -X utf8 scrapers\justice_court_evictions_maricopa.py --business-name "GREYSTAR" --max-features 50
REM
REM Or search by individual landlord last name:
REM   python -X utf8 scrapers\justice_court_evictions_maricopa.py --last-name "SMITH" --max-features 50
REM
REM NOTE: Results listing does not include plaintiff/defendant role,
REM property address, filing date, or APN. All records will route
REM to REVIEW_REQUIRED in the pipeline until detail-page parsing
REM is added to extract PL role and property address.
REM ============================================================

echo Checking Playwright availability...
python -X utf8 -c "from playwright.sync_api import sync_playwright; print('Playwright available')" 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Playwright not installed.
    echo Run: pip install playwright ^&^& playwright install chromium
    exit /b 1
)

echo.
echo Fetching eviction records (operator: replace business-name with actual landlord entities)...
echo NOTE: Adapt the --business-name argument to known Maricopa landlord entities.
echo.

REM Placeholder — operator must supply actual business/landlord names:
REM python -X utf8 scrapers\justice_court_evictions_maricopa.py --business-name "YOUR_LANDLORD_ENTITY_HERE" --max-features 50

echo fetch_evictions.cmd: No search executed — adapt the --business-name argument above and re-run.
echo See comments in this file for search strategy.
