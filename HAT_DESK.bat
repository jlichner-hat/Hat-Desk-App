@echo off
rem HAT Desk tray app. First run installs what it needs. Add --sim to try it without a desk.
cd /d "%~dp0"
python -c "import PySide6, hid" 2>nul || python -m pip install -r requirements-desk.txt
start "" pythonw -m hat_desk %*
