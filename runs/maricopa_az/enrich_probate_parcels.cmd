@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

echo === Probate parcel matcher unit tests ===
python -X utf8 runs\maricopa_az\test_probate_parcel_matcher.py
if %errorlevel% neq 0 (
    echo TESTS FAILED — do not proceed with enrichment.
    exit /b 1
)

echo.
echo === Bounded probate decedent-to-parcel enrichment (7 estate cases) ===
python -X utf8 runs\maricopa_az\enrich_probate_parcels.py

echo.
echo === Done. Report aggregate counts to operator before pipeline integration. ===
