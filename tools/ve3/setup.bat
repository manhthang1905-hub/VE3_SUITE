@echo off
title VE3 Studio - Setup

echo ============================================
echo    VE3 Studio - Auto Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo.
    echo Download Python: https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" when installing.
    echo.
    pause
    exit /b 1
)

echo [1/2] Installing dependencies...
echo.
python -m pip install -r requirements.txt
echo.

if errorlevel 1 (
    echo [ERROR] Install failed!
    pause
    exit /b 1
)

echo [2/2] Creating folders...
if not exist "PROJECTS" mkdir PROJECTS
if not exist "templates" mkdir templates
if not exist "config" mkdir config

:: Create settings.yaml from example if not exists
if not exist "config\settings.yaml" (
    if exist "config\settings.example.yaml" (
        copy "config\settings.example.yaml" "config\settings.yaml" >nul
        echo Created config\settings.yaml
    )
)

echo.
echo ============================================
echo    SETUP COMPLETE!
echo ============================================
echo.
echo Run tool: double-click RUN.bat
echo.
pause
