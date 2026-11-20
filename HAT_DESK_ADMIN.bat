@echo off
rem HAT Desk Admin console: find desks on the network, move them, set schedules.
cd /d "%~dp0"
python -c "import PySide6" 2>nul || python -m pip install -r requirements-desk.txt
start "" pythonw -m hat_desk.admin
