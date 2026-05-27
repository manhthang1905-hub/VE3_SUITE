@echo off
chcp 65001 >nul 2>&1
title FlowKit Server — New Machine Setup
cd /d "%~dp0"

echo ============================================================
echo   FlowKit Server — Setup for New Machine
echo ============================================================
echo.

:: ─── Step 1: Check Python ────────────────────────────────────
echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found! Install Python 3.10+ and add to PATH.
    echo   Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo   [OK]
echo.

:: ─── Step 2: Install Python packages ─────────────────────────
echo [2/6] Installing Python dependencies...
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if errorlevel 1 (
    echo   [WARN] Some packages failed. Trying individually...
    pip install fastapi>=0.104.0
    pip install uvicorn>=0.24.0
    pip install websockets>=12.0
    pip install httpx>=0.25.0
    pip install pyyaml>=6.0
    pip install requests>=2.28.0
)
echo   [OK]
echo.

:: ─── Step 3: Check config.yaml ───────────────────────────────
echo [3/6] Checking config.yaml...
if not exist "config.yaml" (
    echo   [ERROR] config.yaml not found!
    echo   Copy config.yaml.example or create config.yaml with instance settings.
    pause
    exit /b 1
)
echo   [OK] config.yaml found
echo.

:: ─── Step 4: Check Chrome Portable instances ─────────────────
echo [4/6] Checking Chrome Portable instances...
set CHROME_COUNT=0
for /d %%D in ("GoogleChromePortable*") do (
    if exist "%%D\GoogleChromePortable.exe" (
        set /a CHROME_COUNT+=1
        echo   [OK] Found: %%D
    )
)
if %CHROME_COUNT%==0 (
    echo   [WARN] No Chrome Portable instances found.
    echo   Copy GoogleChromePortable folders and configure config.yaml.
)
echo.

:: ─── Step 5: Setup extensions ────────────────────────────────
echo [5/6] Setting up extensions...
if exist "flowkit_extensions" (
    echo   [OK] flowkit_extensions/ found
    python setup_extensions.py 2>nul
    if errorlevel 1 (
        echo   [WARN] Extension setup had issues. Run manually: python setup_extensions.py
    )
) else (
    echo   [WARN] flowkit_extensions/ not found.
)
echo.

:: ─── Step 6: Firewall rules ─────────────────────────────────
echo [6/6] Setting up firewall rules (requires Admin)...
echo   Adding FlowKit Gateway port 5100...
netsh advfirewall firewall show rule name="FlowKit Gateway 5100" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="FlowKit Gateway 5100" dir=in action=allow protocol=tcp localport=5100
) else (
    echo   [OK] Rule already exists
)

echo   Adding ICMP ping rule...
netsh advfirewall firewall show rule name="Allow Ping" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="Allow Ping" dir=in action=allow protocol=icmpv4
) else (
    echo   [OK] Rule already exists
)

echo   Adding agent ports 8100-8106...
for /L %%P in (8100,1,8106) do (
    netsh advfirewall firewall show rule name="FlowKit Agent %%P" >nul 2>&1
    if errorlevel 1 (
        netsh advfirewall firewall add rule name="FlowKit Agent %%P" dir=in action=allow protocol=tcp localport=%%P
    )
)
echo   [OK]
echo.

:: ─── Summary ─────────────────────────────────────────────────
echo ============================================================
echo   Setup Complete!
echo ============================================================
echo.
echo   Start FlowKit:
echo     1. START_FLOWKIT_GUI.bat   — GUI mode (recommended)
echo     2. START_FLOWKIT.bat       — Command line mode
echo     3. python launcher.py      — Direct Python
echo.
echo   First time:
echo     - Open Chrome Portable, login Google account
echo     - Enable extension in chrome://extensions
echo     - Extension will auto-connect to FlowKit agent
echo.
echo   Firewall ports opened:
echo     - 5100 (Gateway — VE3 connects here)
echo     - 8100-8106 (Agents — internal only)
echo     - ICMP ping (for VM connectivity check)
echo.
pause
