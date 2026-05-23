@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo             INSTALL VE3 SUITE REQUIREMENTS
echo ============================================================
echo.

echo [1/2] Installing SRT-to-Excel requirements...
python -m pip install -r "%~dp0tools\srt-to-excel\requirements.txt"
if errorlevel 1 goto fail

echo.
echo [2/2] Installing VE3 requirements...
python -m pip install -r "%~dp0tools\ve3\requirements.txt"
if errorlevel 1 goto fail

echo.
echo Requirements installed.
pause
exit /b 0

:fail
echo.
echo Install failed. Check Python, pip, network, and requirements files.
pause
exit /b 1
