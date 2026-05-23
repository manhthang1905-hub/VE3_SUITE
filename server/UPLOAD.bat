@echo off
chcp 65001 >nul
echo ======================================================================
echo UPLOAD CODE LEN GITHUB
echo ======================================================================
echo.

REM Check git
git --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git chua duoc cai dat!
    echo.
    echo Tai Git tai: https://git-scm.com/download/win
    pause
    exit /b 1
)

echo [1] Checking changes...
git status --short
echo.

REM Check if there are changes
git diff-index --quiet HEAD --
if %errorlevel%==0 (
    echo [INFO] Khong co thay doi moi!
    echo.
    pause
    exit /b 0
)

echo [2] Adding all changes...
git add .
if errorlevel 1 (
    echo [ERROR] Git add failed!
    pause
    exit /b 1
)
echo    [OK] Added all changes
echo.

echo [3] Creating commit...
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c-%%a-%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a:%%b)
set commit_msg=Update: %mydate% %mytime%
git commit -m "%commit_msg%"
if errorlevel 1 (
    echo [WARN] Nothing to commit or commit failed
    echo.
)
echo.

echo [4] Pushing to GitHub...
git push origin main
if errorlevel 1 (
    echo [ERROR] Git push failed!
    echo.
    echo Hay kiem tra:
    echo   - Internet connection
    echo   - GitHub credentials
    echo   - Remote repository access
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo [SUCCESS] DA UPLOAD LEN GITHUB!
echo ======================================================================
echo.
echo Commit: %commit_msg%
echo Remote: https://github.com/nguyenvantuong161978-dotcom/ve3-tool-simple
echo.
pause
