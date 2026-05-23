@echo off
echo ========================================
echo MANUAL UPDATE - VE3 TOOL SIMPLE
echo ========================================
echo.
echo Dang download phien ban moi tu GitHub...
echo.

cd /d "%~dp0"

REM Download ZIP moi nhat (with cache buster)
powershell -Command "& {$timestamp = [int](Get-Date -UFormat %%s); Invoke-WebRequest -Uri 'https://github.com/nguyenvantuong161978-dotcom/ve3-tool-simple/archive/refs/heads/main.zip?t=$timestamp' -OutFile 'update_temp.zip'}"

if not exist update_temp.zip (
    echo [ERROR] Khong download duoc ZIP!
    pause
    exit /b 1
)

echo.
echo Dang giai nen...
powershell -Command "Expand-Archive -Path update_temp.zip -DestinationPath update_temp -Force"

if not exist update_temp\ve3-tool-simple-main (
    echo [ERROR] Khong giai nen duoc!
    pause
    exit /b 1
)

echo.
echo Dang cap nhat files...

REM Copy files
copy /Y "update_temp\ve3-tool-simple-main\VERSION.txt" "VERSION.txt"
copy /Y "update_temp\ve3-tool-simple-main\vm_manager.py" "vm_manager.py"
copy /Y "update_temp\ve3-tool-simple-main\vm_manager_gui.py" "vm_manager_gui.py"
copy /Y "update_temp\ve3-tool-simple-main\run_excel_api.py" "run_excel_api.py"
copy /Y "update_temp\ve3-tool-simple-main\run_worker.py" "run_worker.py"
copy /Y "update_temp\ve3-tool-simple-main\START.py" "START.py"
copy /Y "update_temp\ve3-tool-simple-main\START.bat" "START.bat"
copy /Y "update_temp\ve3-tool-simple-main\_run_chrome1.py" "_run_chrome1.py"
copy /Y "update_temp\ve3-tool-simple-main\_run_chrome2.py" "_run_chrome2.py"
copy /Y "update_temp\ve3-tool-simple-main\google_login.py" "google_login.py"

REM Copy modules
xcopy /Y /E "update_temp\ve3-tool-simple-main\modules\*.py" "modules\"

REM Copy server (v1.0.640)
if exist "update_temp\ve3-tool-simple-main\server" (
    xcopy /Y "update_temp\ve3-tool-simple-main\server\*.py" "server\"
    echo Server files updated!
)

echo.
echo Dang xoa temp files...
del /F /Q update_temp.zip
rmdir /S /Q update_temp

echo.
echo ========================================
echo CAP NHAT XONG!
echo ========================================
echo.
echo Phien ban moi:
type VERSION.txt 2>nul
echo.
echo Git commit:
git rev-parse --short HEAD 2>nul
echo.
echo Lan sau co the dung nut UPDATE trong GUI.
echo.
pause
