@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1
python -X utf8 scaffold\tests\run_all.py
echo.
echo === APN resolver unit tests ===
python -X utf8 runs\maricopa_az\test_apn_resolver.py
echo.
echo === Running combined NOTS + Treasurer + Eviction + Civil + Probate pipeline ===
python -X utf8 runs\maricopa_az\run_pipeline.py --max-records 100
echo.
echo === Generating dashboard payload ===
python -X utf8 runs\maricopa_az\generate_dashboard.py
