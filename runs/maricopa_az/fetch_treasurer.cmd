@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1
echo Pulling Maricopa Treasurer lien/delinquent records (max 34 per layer, ~100 total)...
python -X utf8 scrapers\treasurer_tax_lien_maricopa.py --max-features 34
echo.
echo Done. Records written to data\raw\treasurer_tax_lien.jsonl
