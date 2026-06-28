@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1

echo === Maricopa Residential Parcel Owner Index Pull ===
echo Filter:  PUC LIKE '0%%' (residential/condo 0xxx codes — 1.6M of 1.76M parcels)
echo Cap:     600,000 records
echo Delay:   0.5s per page (~1,759 pages, ~15 min)
echo Output:  data\cache\parcel_owner_index.jsonl  (gitignored)
echo.
echo Starting pull... (Ctrl+C to interrupt; re-run with --resume to continue)
echo.

python -X utf8 runs\maricopa_az\pull_parcel_owner_index.py %*

echo.
echo === Pull done. Run enrich_probate_parcels_local.cmd next. ===
