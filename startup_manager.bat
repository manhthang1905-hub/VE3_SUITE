@echo off
chcp 65001 >nul
title Startup Manager

:menu
cls
echo ============================================
echo       STARTUP MANAGER - Quan ly khoi dong
echo ============================================
echo.

set startup=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
echo Dang chay khi khoi dong:
echo ---
set count=0
for %%f in ("%startup%\*.lnk") do (
    set /a count+=1
    echo   [!count!] %%~nf
)
if %count%==0 echo   (trong)
echo ---
echo.
echo [1] Them app khoi dong cung Windows
echo [2] Xoa app khoi dong
echo [3] Mo thu muc Startup
echo [0] Thoat
echo.
set /p choice=Chon:

if "%choice%"=="1" goto add
if "%choice%"=="2" goto remove
if "%choice%"=="3" explorer "%startup%" & goto menu
if "%choice%"=="0" exit
goto menu

:add
echo.
set /p filepath=Nhap duong dan file (.bat, .exe, .py...):
if "%filepath%"=="" goto menu
set /p appname=Dat ten (VD: VE3 Suite):
if "%appname%"=="" goto menu

powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%startup%\%appname%.lnk'); $s.TargetPath='%filepath%'; $s.WorkingDirectory=Split-Path '%filepath%'; $s.Save()"
echo.
echo Da them: %appname% -> %filepath%
echo Khi may khoi dong se tu chay!
pause
goto menu

:remove
echo.
set /p delname=Nhap ten app can xoa:
if "%delname%"=="" goto menu
if exist "%startup%\%delname%.lnk" (
    del "%startup%\%delname%.lnk"
    echo Da xoa: %delname%
) else (
    echo Khong tim thay: %delname%
)
pause
goto menu
