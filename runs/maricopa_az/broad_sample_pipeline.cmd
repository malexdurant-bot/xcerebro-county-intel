@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

echo === Broad Mixed-Surname Probate Sample Pipeline ===
echo.
echo [1/3] Pull mixed-surname probate sample (Playwright)...
python -X utf8 runs\maricopa_az\pull_probate_sample_mixed.py
if %errorlevel% neq 0 (
    echo STEP 1 FAILED.
    exit /b 1
)

echo.
echo [2/3] Enrich probate detail pages...
python -X utf8 scrapers\superior_court_probate_detail_maricopa.py ^
    --base-jsonl data\raw\superior_court_probate_broad.jsonl ^
    --out data\raw\superior_court_probate_detail_broad.jsonl ^
    --max-records 100 ^
    --delay 1.0
if %errorlevel% neq 0 (
    echo STEP 2 FAILED.
    exit /b 1
)

echo.
echo [3/3] Run local decedent-to-parcel matching...
python -X utf8 runs\maricopa_az\enrich_probate_parcels_broad.py

echo.
echo === Done. Report aggregate results to operator. ===
