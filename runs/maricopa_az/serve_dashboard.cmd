@echo off
cd /d C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel
set PYTHONUTF8=1
echo Generating dashboard payload...
python -X utf8 runs\maricopa_az\generate_dashboard.py
echo.
echo Starting dashboard server at http://localhost:8765/
echo Press Ctrl+C to stop.
cd dashboard
python -m http.server 8765
