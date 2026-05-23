@echo off
cd /d "%~dp0"
set "GUI=%~dp0ve3_gui.py"
set "VE3_GUI=%GUI%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$gui=[IO.Path]::GetFullPath($env:VE3_GUI).ToLowerInvariant(); $hits=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($gui) }); if($hits.Count -gt 0){ exit 7 } else { exit 0 }" >nul 2>nul
if "%ERRORLEVEL%"=="7" (
  echo [INFO] VE3 GUI dang mo san. Khong mo them instance moi.
  exit /b 0
)
start "" pythonw "%GUI%"
