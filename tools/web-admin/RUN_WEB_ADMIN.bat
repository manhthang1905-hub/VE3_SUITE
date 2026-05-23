@echo off
setlocal
cd /d "%~dp0..\.."
set WEB_ADMIN_HOST=127.0.0.1
set WEB_ADMIN_PORT=5070
python tools\web-admin\app.py
