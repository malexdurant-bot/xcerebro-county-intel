@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

echo === Probate detail page unit tests ===
python -X utf8 runs\maricopa_az\test_probate_detail_parser.py
if %errorlevel% neq 0 (
    echo TESTS FAILED — do not proceed with enrichment.
    exit /b 1
)

echo.
echo === Bounded probate detail enrichment (18 records, 1s delay) ===
python -X utf8 runs\maricopa_az\enrich_probate_detail.py --max-records 18 --delay 1.0

echo.
echo === Done. Report aggregate stats to operator before pipeline run. ===
