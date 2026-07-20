@echo off
REM ============================================================================
REM  Maricopa County, AZ — Local Production Run
REM  runs/maricopa_az/run_maricopa_production.cmd
REM
REM  Runs the full local production flow on existing raw files:
REM    [1] Combined pipeline (NOTS + Treasurer + Eviction + Civil + Probate)
REM    [2] Dashboard payload generation
REM    [3] Lead CSV exports
REM
REM  === Scraper refresh (NOT run here — Playwright-dependent, run separately) ===
REM
REM  Recorder NOTS:
REM    python -X utf8 scrapers\recorder_maricopa.py
REM    Output: data\raw\recorder_maricopa.jsonl
REM
REM  Treasurer tax lien:
REM    runs\maricopa_az\fetch_treasurer.cmd
REM    Output: data\raw\treasurer_tax_lien.jsonl
REM
REM  Probate listing + detail:
REM    runs\maricopa_az\fetch_probate.cmd
REM    runs\maricopa_az\enrich_probate_detail.cmd
REM    Output: data\raw\superior_court_probate.jsonl
REM            data\raw\superior_court_probate_detail.jsonl
REM
REM  Parcel owner index (build once, reuse — 1.6M records, takes 2+ hours):
REM    runs\maricopa_az\pull_parcel_owner_index.cmd
REM    Output: data\cache\parcel_owner_index.jsonl
REM
REM  Eviction + Civil listings (review-required sources):
REM    runs\maricopa_az\fetch_evictions.cmd
REM    runs\maricopa_az\fetch_civil.cmd
REM
REM  This script uses existing files in data\raw\ and data\cache\.
REM  It is safe to re-run without any scraper steps.
REM ============================================================================

cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

echo === Maricopa County Production Run ===
echo.

echo [1/3] Combined pipeline (NOTS + Treasurer + Eviction + Civil + Probate)...
python -X utf8 runs\maricopa_az\run_pipeline.py --max-records 10000
if %errorlevel% neq 0 (
    echo.
    echo STEP 1 FAILED — pipeline error.
    exit /b 1
)

echo.
echo [2/3] Generate dashboard payload...
python -X utf8 runs\maricopa_az\generate_dashboard.py
if %errorlevel% neq 0 (
    echo.
    echo STEP 2 FAILED — dashboard generation error.
    exit /b 1
)

echo.
echo [3/3] Export leads to CSV...
python -X utf8 runs\maricopa_az\export_leads.py
if %errorlevel% neq 0 (
    echo.
    echo STEP 3 FAILED — export error.
    exit /b 1
)

echo.
echo === Done ===
echo   Pipeline output:  runs\maricopa_az\pipeline_output\
echo   Dashboard:        dashboard\data\leads.json
echo   Lead exports:     data\exports\maricopa_az\
echo.
echo   To serve dashboard:
echo     cd dashboard ^&^& python -m http.server 8765
echo     Open: http://localhost:8765/
