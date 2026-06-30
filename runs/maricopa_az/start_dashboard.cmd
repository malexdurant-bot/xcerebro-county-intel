@echo off
cd /d "C:\Users\makia\OneDrive\Documents\GitHub\xcerebro-county-intel\dashboard"
echo Starting Maricopa dashboard at http://localhost:8765
echo Press Ctrl+C to stop the server.
start "" /B python -m http.server 8765
timeout /t 2 /nobreak >nul
start http://localhost:8765
python -m http.server 8765
