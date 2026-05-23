@echo off
echo ============================================================
echo  Suno Chrome - Khoi dong de bat token tu API request
echo ============================================================
echo.
echo Chrome Portable se mo tai suno.com/create
echo Sau do vao script: python intercept_real_token.py
echo.
set "LOCAL_CHROME=%~dp0GoogleChromePortable\GoogleChromePortable.exe"
set "CHROME_EXE=%LOCAL_CHROME%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'chrome.exe' -or $_.Name -eq 'GoogleChromePortable.exe') -and $_.CommandLine -and $_.CommandLine -like '*tools\suno\GoogleChromePortable*' -and $_.CommandLine -like '*remote-debugging-port=9444*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

"%CHROME_EXE%" ^
  --remote-debugging-port=9444 ^
  --no-first-run ^
  --new-window ^
  --window-size=1600,1200 ^
  --window-position=3200,40 ^
  https://suno.com/create
