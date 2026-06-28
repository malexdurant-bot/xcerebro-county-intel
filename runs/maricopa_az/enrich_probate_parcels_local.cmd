@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

echo === Local parcel index unit tests ===
python -X utf8 runs\maricopa_az\test_local_parcel_index.py
if %errorlevel% neq 0 (
    echo TESTS FAILED — do not proceed with enrichment.
    exit /b 1
)

echo.
echo === Probate parcel matcher unit tests ===
python -X utf8 runs\maricopa_az\test_probate_parcel_matcher.py
if %errorlevel% neq 0 (
    echo TESTS FAILED — do not proceed with enrichment.
    exit /b 1
)

echo.
echo === Bounded probate decedent-to-parcel enrichment (local index) ===
python -X utf8 runs\maricopa_az\enrich_probate_parcels_local.py

echo.
echo === Done. Report aggregate counts to operator before pipeline integration. ===
