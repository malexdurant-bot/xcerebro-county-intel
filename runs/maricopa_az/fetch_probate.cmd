@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

REM ============================================================
REM Maricopa Superior Court Probate Docket Fetcher
REM
REM REQUIRES: pip install playwright && playwright install chromium
REM
REM The portal uses Google reCAPTCHA v3 — Playwright required.
REM
REM SEARCH STRATEGY: Search by decedent's last name. Common surnames
REM yield estate records that may involve Maricopa County property.
REM Focus on names associated with known real estate activity.
REM
REM Portal: https://www.superiorcourt.maricopa.gov/docket/ProbateCourtCases/caseSearch.asp
REM
REM Case number format: PB<YEAR>-<NUMBER>  (e.g. PB2024-001234)
REM All PB-prefix records are treated as letters_testamentary (default) by
REM the probate adapter until detail-page parsing reveals the specific
REM case type (letters_testamentary / letters_of_administration /
REM determination_of_heirship / muniment_of_title).
REM
REM PRIVACY NOTE: The portal may return "Minor, Minor - DOB: X/XXXX" for
REM protected minor cases. The probate adapter detects and strips these
REM placeholders — they do not generate party entries in the pipeline.
REM
REM Example last-name searches:
REM   python -X utf8 scrapers\superior_court_probate_maricopa.py --last-name "SMITH" --max-features 50
REM   python -X utf8 scrapers\superior_court_probate_maricopa.py --last-name "JOHNSON" --max-features 50
REM   python -X utf8 scrapers\superior_court_probate_maricopa.py --last-name "GARCIA" --max-features 50
REM   python -X utf8 scrapers\superior_court_probate_maricopa.py --last-name "MARTINEZ" --max-features 50
REM
REM Or look up a specific case number:
REM   python -X utf8 scrapers\superior_court_probate_maricopa.py --case-number "PB2024-001234"
REM
REM Run multiple searches and results are deduplicated/merged automatically.
REM ============================================================

echo Checking Playwright availability...
python -X utf8 -c "from playwright.sync_api import sync_playwright; print('Playwright available')" 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Playwright not installed.
    echo Run: pip install playwright ^&^& playwright install chromium
    exit /b 1
)

echo.
echo Fetching probate records (operator: replace last-name with target decedent surnames)...
echo NOTE: Adapt the --last-name argument to known Maricopa decedent surnames or estate names.
echo.

REM Placeholder — operator must supply actual decedent/estate last names:
REM python -X utf8 scrapers\superior_court_probate_maricopa.py --last-name "YOUR_DECEDENT_NAME_HERE" --max-features 50

echo fetch_probate.cmd: No search executed — adapt the --last-name argument above and re-run.
echo See comments in this file for search strategy.
