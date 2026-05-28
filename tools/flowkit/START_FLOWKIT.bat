@echo off
title FlowKit Server
cd /d "%~dp0"
echo ============================================================
echo   FlowKit Server - Starting...
echo ============================================================
echo.
python launcher.py %*
pause
