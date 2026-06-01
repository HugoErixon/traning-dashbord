@echo off
echo Startar Träningsdashboard...
echo.
start "" http://localhost:3000
python garmin_server.py
pause
