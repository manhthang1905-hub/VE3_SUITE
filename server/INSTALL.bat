@echo off
echo ============================================
echo   VE3 Tool - Install Dependencies
echo ============================================
echo.

cd /d "%~dp0"

echo Installing Python packages...
echo.

pip install -r requirements.txt

echo.
echo ============================================
if %ERRORLEVEL% EQU 0 (
    echo   Installation completed successfully!
) else (
    echo   Installation had errors. Check above.
)
echo ============================================
echo.
pause
