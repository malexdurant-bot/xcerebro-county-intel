@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

echo === Bounded probate pull: last-name "Garcia", max 50 records ===
echo Search seed is a common Maricopa surname — aggregate stats only, no record printing.
echo Output: data\raw\superior_court_probate.jsonl
echo.

python -X utf8 scrapers\superior_court_probate_maricopa.py ^
    --last-name "Garcia" ^
    --max-features 50

echo.
echo === Pull complete. Run analyze_probate_sample.py next. ===
