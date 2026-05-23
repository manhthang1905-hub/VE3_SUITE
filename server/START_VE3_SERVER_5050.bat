@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "VE3_SERVER_PORT=5050"
python -u server/app.py --auto --chrome 1 --port 5050
pause
