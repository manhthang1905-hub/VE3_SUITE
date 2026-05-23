@echo off
chcp 65001 >nul
cd /d "%~dp0"
title VE3 SUITE

if not exist "%~dp0PROJECTS" mkdir "%~dp0PROJECTS"
set "GUI=%~dp0tools\ve3\ve3_gui.py"
if not exist "%GUI%" (
  echo [ERROR] Khong tim thay: %GUI%
  pause
  exit /b 1
)

set "VE3_GUI=%GUI%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$gui=[IO.Path]::GetFullPath($env:VE3_GUI).ToLowerInvariant(); $hits=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($gui) }); if($hits.Count -gt 0){ exit 7 } else { exit 0 }" >nul 2>nul
if "%ERRORLEVEL%"=="7" (
  echo [INFO] VE3 GUI dang mo san. Khong mo them instance moi.
  exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%GUI%"
  exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
  start "" pyw "%GUI%"
  exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
  python "%GUI%"
  exit /b %errorlevel%
)

where py >nul 2>nul
if not errorlevel 1 (
  py "%GUI%"
  exit /b %errorlevel%
)

echo [ERROR] Khong tim thay Python tren may.
pause
exit /b 1
