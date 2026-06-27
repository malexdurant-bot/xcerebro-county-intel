@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

REM ============================================================
REM Maricopa Superior Court Civil Docket Fetcher
REM
REM REQUIRES: pip install playwright && playwright install chromium
REM
REM The portal uses Google reCAPTCHA v3 — Playwright required.
REM
REM SEARCH STRATEGY: For judicial foreclosure (FC) and lis pendens (LP)
REM leads, search by PLAINTIFF business name — the lender/servicer filing
REM the action. For civil judgments (CV), search by plaintiff (creditor).
REM
REM Case number prefixes:
REM   FC  — Judicial Foreclosure (lender plaintiff)
REM   LP  — Lis Pendens (plaintiff filing against property owner)
REM   CV  — Civil / Civil Judgment (plaintiff is creditor or servicer)
REM   MC  — Miscellaneous Civil
REM   SC  — Small Claims
REM
REM Example business searches (adapt to known Maricopa lenders/servicers):
REM   python -X utf8 scrapers\superior_court_civil_maricopa.py --business-name "WELLS FARGO" --max-features 50
REM   python -X utf8 scrapers\superior_court_civil_maricopa.py --business-name "BANK OF AMERICA" --max-features 50
REM   python -X utf8 scrapers\superior_court_civil_maricopa.py --business-name "NATIONSTAR" --max-features 50
REM   python -X utf8 scrapers\superior_court_civil_maricopa.py --business-name "PENNYMAC" --max-features 50
REM   python -X utf8 scrapers\superior_court_civil_maricopa.py --business-name "SELECT PORTFOLIO" --max-features 50
REM
REM Or search by individual last name (property owner as defendant):
REM   python -X utf8 scrapers\superior_court_civil_maricopa.py --last-name "SMITH" --max-features 50
REM
REM NOTE: Results listing does not include plaintiff/defendant role,
REM property address, filing date, or APN. All records route to
REM REVIEW_REQUIRED in the pipeline until detail-page parsing is added
REM to extract party roles and property address.
REM
REM Case type is inferred from the case_number prefix (FC/LP/CV/MC/SC).
REM TR/CR records are noise and are skipped by the civil adapter.
REM ============================================================

echo Checking Playwright availability...
python -X utf8 -c "from playwright.sync_api import sync_playwright; print('Playwright available')" 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Playwright not installed.
    echo Run: pip install playwright ^&^& playwright install chromium
    exit /b 1
)

echo.
echo Fetching civil court records (operator: replace business-name with actual lender/servicer entities)...
echo NOTE: Adapt the --business-name argument to known Maricopa lenders filing FC/LP/CV cases.
echo.

REM Placeholder — operator must supply actual lender/servicer/plaintiff names:
REM python -X utf8 scrapers\superior_court_civil_maricopa.py --business-name "YOUR_LENDER_ENTITY_HERE" --max-features 50

echo fetch_civil.cmd: No search executed — adapt the --business-name argument above and re-run.
echo See comments in this file for search strategy.
