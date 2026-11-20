@echo off
rem Double-click to check the LINAK cable and leg. Installs hidapi the first time.
cd /d "%~dp0"
python -c "import hid" 2>nul || python -m pip install hidapi
python -m linak_leg.cli %*
pause
