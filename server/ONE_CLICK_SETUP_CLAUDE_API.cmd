@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title ONE CLICK SETUP - Claude API via CCS

set "PROFILE_NAME=digiclaude"
set "BASE_URL=http://3.7.62.19/claude"
set "MODEL=claude-sonnet-4-6"
set "MODEL_SONNET=claude-sonnet-4-6[1m]"
set "MODEL_OPUS=claude-opus-4-6[1m]"
set "MODEL_HAIKU=claude-haiku-4-5-20251001"
set "API_KEY=8V057GU3-CK44-K60M-T1SK-8ZRR8JWNH3B5"

echo =====================================================
echo  ONE CLICK SETUP - Claude API Profile for CCS
echo =====================================================
echo Profile : %PROFILE_NAME%
echo Base URL: %BASE_URL%
echo Model   : %MODEL%
echo Sonnet  : %MODEL_SONNET%
echo Opus    : %MODEL_OPUS%
echo Haiku   : %MODEL_HAIKU%
echo =====================================================
echo.

where npm >nul 2>&1
if errorlevel 1 (
  echo [WARN] Khong tim thay npm/Node.js. Thu cai Node.js LTS bang winget...
  where winget >nul 2>&1
  if not errorlevel 1 (
    winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
  )

  rem Refresh PATH for current cmd session
  if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%ProgramFiles%\nodejs;%PATH%"
  if exist "%AppData%\npm" set "PATH=%AppData%\npm;%PATH%"

  where npm >nul 2>&1
  if errorlevel 1 (
    echo [WARN] Winget khong kha dung hoac chua cai xong. Thu tai Node.js portable tu official source...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ErrorActionPreference='Stop';" ^
      "$base='https://nodejs.org/dist/latest-v20.x';" ^
      "$sum=Invoke-WebRequest -UseBasicParsing -Uri ($base + '/SHASUMS256.txt');" ^
      "$zipName=($sum.Content -split [Environment]::NewLine ^| Where-Object { $_ -match 'node-v.+-win-x64\.zip$' } ^| Select-Object -First 1).Split(' ')[-1];" ^
      "if (-not $zipName) { throw 'Cannot resolve Node.js zip from SHASUMS256.txt'; }" ^
      "$zipPath=Join-Path $env:TEMP $zipName;" ^
      "$outDir=Join-Path $env:LOCALAPPDATA 'Programs\nodejs-portable';" ^
      "$extractDir=Join-Path $outDir 'current';" ^
      "New-Item -ItemType Directory -Path $outDir -Force ^| Out-Null;" ^
      "Invoke-WebRequest -UseBasicParsing -Uri ($base + '/' + $zipName) -OutFile $zipPath;" ^
      "if (Test-Path $extractDir) { Remove-Item -LiteralPath $extractDir -Recurse -Force };" ^
      "Expand-Archive -LiteralPath $zipPath -DestinationPath $outDir -Force;" ^
      "$inner=Get-ChildItem -LiteralPath $outDir -Directory ^| Where-Object { $_.Name -like 'node-v*-win-x64' } ^| Select-Object -First 1;" ^
      "if (-not $inner) { throw 'Portable Node extract failed'; }" ^
      "Move-Item -LiteralPath $inner.FullName -Destination $extractDir -Force;" ^
      "Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue;" ^
      "$userPath=[Environment]::GetEnvironmentVariable('Path','User');" ^
      "if ($userPath -notlike ('*' + $extractDir + '*')) { [Environment]::SetEnvironmentVariable('Path', ($extractDir + ';' + $userPath), 'User') }"

    if exist "%LOCALAPPDATA%\Programs\nodejs-portable\current\npm.cmd" set "PATH=%LOCALAPPDATA%\Programs\nodejs-portable\current;%PATH%"
    if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%ProgramFiles%\nodejs;%PATH%"
    if exist "%AppData%\npm" set "PATH=%AppData%\npm;%PATH%"

    where npm >nul 2>&1
    if errorlevel 1 (
      echo [ERROR] Van khong tim thay Node.js/npm.
      echo Hay cai thu cong Node.js LTS: https://nodejs.org/en/download
      echo Sau do mo lai CMD va chay lai file nay.
      pause
      exit /b 1
    )
  )
)

set "TMP_PS1=%TEMP%\ccs_setup_%RANDOM%_%RANDOM%.ps1"
> "%TMP_PS1%" echo $ErrorActionPreference = "Stop"
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo $profileName = "%PROFILE_NAME%"
>> "%TMP_PS1%" echo $baseUrl = "%BASE_URL%"
>> "%TMP_PS1%" echo $model = "%MODEL%"
>> "%TMP_PS1%" echo $modelSonnet = "%MODEL_SONNET%"
>> "%TMP_PS1%" echo $modelOpus = "%MODEL_OPUS%"
>> "%TMP_PS1%" echo $modelHaiku = "%MODEL_HAIKU%"
>> "%TMP_PS1%" echo $apiKey = "%API_KEY%"
>> "%TMP_PS1%" echo $headers = @{"x-api-key"=$apiKey;"anthropic-version"="2023-06-01";"content-type"="application/json"}
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[1/6] Installing CCS globally..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo npm install -g @kaitranntt/ccs ^| Out-Host
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[2/6] Creating API profile if missing..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo try { ccs api create $profileName --base-url $baseUrl --api-key $apiKey --model $model --target claude --force --yes ^| Out-Host } catch { Write-Host "Create warning: $($_.Exception.Message)" -ForegroundColor Yellow }
>> "%TMP_PS1%" echo try { ccs api discover --register ^| Out-Host } catch { Write-Host "Discover warning: $($_.Exception.Message)" -ForegroundColor Yellow }
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[2.5/6] Probing model compatibility..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo $candidates = @($model, "claude-sonnet-4-6", "claude-sonnet-4-6[1m]", $modelOpus, $modelHaiku) ^| Select-Object -Unique
>> "%TMP_PS1%" echo $probeOk = $false
>> "%TMP_PS1%" echo foreach($m in $candidates){
>> "%TMP_PS1%" echo   try {
>> "%TMP_PS1%" echo     $probe = @{model=$m;max_tokens=8;messages=@(@{role="user";content="Reply exactly OK"})} ^| ConvertTo-Json -Depth 6
>> "%TMP_PS1%" echo     $null = Invoke-RestMethod -Method Post -Uri ($baseUrl + "/v1/messages") -Headers $headers -Body $probe
>> "%TMP_PS1%" echo     $model = $m
>> "%TMP_PS1%" echo     $probeOk = $true
>> "%TMP_PS1%" echo     Write-Host "Using model: $model" -ForegroundColor Green
>> "%TMP_PS1%" echo     break
>> "%TMP_PS1%" echo   } catch {}
>> "%TMP_PS1%" echo }
>> "%TMP_PS1%" echo if(-not $probeOk){ throw "No compatible Claude model found for this key/provider." }
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo $ccsDir = Join-Path $env:USERPROFILE ".ccs"
>> "%TMP_PS1%" echo New-Item -ItemType Directory -Path $ccsDir -Force ^| Out-Null
>> "%TMP_PS1%" echo $settingsPath = Join-Path $ccsDir "$profileName.settings.json"
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[3/6] Writing profile settings..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo $obj = @{
>> "%TMP_PS1%" echo   env = @{
>> "%TMP_PS1%" echo     ANTHROPIC_BASE_URL = $baseUrl
>> "%TMP_PS1%" echo     ANTHROPIC_AUTH_TOKEN = $apiKey
>> "%TMP_PS1%" echo     ANTHROPIC_MODEL = $model
>> "%TMP_PS1%" echo     ANTHROPIC_DEFAULT_SONNET_MODEL = $modelSonnet
>> "%TMP_PS1%" echo     ANTHROPIC_DEFAULT_OPUS_MODEL = $modelOpus
>> "%TMP_PS1%" echo     ANTHROPIC_DEFAULT_HAIKU_MODEL = $modelHaiku
>> "%TMP_PS1%" echo   }
>> "%TMP_PS1%" echo }
>> "%TMP_PS1%" echo $json = $obj ^| ConvertTo-Json -Depth 6
>> "%TMP_PS1%" echo [IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
>> "%TMP_PS1%" echo Write-Host "Saved: $settingsPath" -ForegroundColor Green
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[3.5/6] Writing Claude Code local settings..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo $claudeDir = Join-Path $env:USERPROFILE ".claude"
>> "%TMP_PS1%" echo New-Item -ItemType Directory -Path $claudeDir -Force ^| Out-Null
>> "%TMP_PS1%" echo $claudeSettingsPath = Join-Path $claudeDir "settings.json"
>> "%TMP_PS1%" echo $claudeCredPath = Join-Path $claudeDir ".credentials.json"
>> "%TMP_PS1%" echo $existing = @{}
>> "%TMP_PS1%" echo if (Test-Path $claudeSettingsPath) {
>> "%TMP_PS1%" echo   try {
>> "%TMP_PS1%" echo     $raw = Get-Content -Raw $claudeSettingsPath
>> "%TMP_PS1%" echo     if (($raw -ne $null) -and $raw.Trim()) { $existing = $raw ^| ConvertFrom-Json -AsHashtable }
>> "%TMP_PS1%" echo   } catch {}
>> "%TMP_PS1%" echo }
>> "%TMP_PS1%" echo if (-not $existing -or $existing.GetType().Name -ne 'Hashtable') { $existing = @{} }
>> "%TMP_PS1%" echo if (-not $existing.ContainsKey("env") -or $existing.env -eq $null -or $existing.env.GetType().Name -ne 'Hashtable') { $existing.env = @{} }
>> "%TMP_PS1%" echo $existing.env["ANTHROPIC_BASE_URL"] = $baseUrl
>> "%TMP_PS1%" echo $existing.env["ANTHROPIC_AUTH_TOKEN"] = $apiKey
>> "%TMP_PS1%" echo $existing.env["ANTHROPIC_MODEL"] = $model
>> "%TMP_PS1%" echo $existing.env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = $modelSonnet
>> "%TMP_PS1%" echo $existing.env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = $modelOpus
>> "%TMP_PS1%" echo $existing.env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = $modelHaiku
>> "%TMP_PS1%" echo [IO.File]::WriteAllText($claudeSettingsPath, ($existing ^| ConvertTo-Json -Depth 16), (New-Object System.Text.UTF8Encoding($false)))
>> "%TMP_PS1%" echo [IO.File]::WriteAllText($claudeCredPath, ('{\"apiKey\":\"' + $apiKey + '\"}'), (New-Object System.Text.UTF8Encoding($false)))
>> "%TMP_PS1%" echo Write-Host "Saved: $claudeSettingsPath" -ForegroundColor Green
>> "%TMP_PS1%" echo Write-Host "Saved: $claudeCredPath" -ForegroundColor Green
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[4/6] Optional persist to Claude Code profile..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo try { ccs persist $profileName --yes *^> $null; Write-Host "Persisted." -ForegroundColor Green } catch { Write-Host "Skip persist (API profile khong can persist)." -ForegroundColor Yellow }
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[5/6] Setting user env vars for VS Code / terminals..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo [Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", $baseUrl, "User")
>> "%TMP_PS1%" echo [Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", $apiKey, "User")
>> "%TMP_PS1%" echo [Environment]::SetEnvironmentVariable("ANTHROPIC_MODEL", $model, "User")
>> "%TMP_PS1%" echo [Environment]::SetEnvironmentVariable("ANTHROPIC_DEFAULT_SONNET_MODEL", $modelSonnet, "User")
>> "%TMP_PS1%" echo [Environment]::SetEnvironmentVariable("ANTHROPIC_DEFAULT_OPUS_MODEL", $modelOpus, "User")
>> "%TMP_PS1%" echo [Environment]::SetEnvironmentVariable("ANTHROPIC_DEFAULT_HAIKU_MODEL", $modelHaiku, "User")
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "[6/6] Verifying ccs command..." -ForegroundColor Cyan
>> "%TMP_PS1%" echo try { ccs --version ^| Out-Host } catch { Write-Host "ccs command check failed: $($_.Exception.Message)" -ForegroundColor Yellow }
>> "%TMP_PS1%" echo.
>> "%TMP_PS1%" echo Write-Host "DONE." -ForegroundColor Green
>> "%TMP_PS1%" echo Write-Host "Open NEW terminal/VS Code window to load env vars." -ForegroundColor Yellow
>> "%TMP_PS1%" echo Write-Host "Run profile: ccs $profileName" -ForegroundColor Yellow

if not exist "%TMP_PS1%" (
  echo [ERROR] Khong tao duoc file tam: %TMP_PS1%
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%TMP_PS1%"
set "EC=%ERRORLEVEL%"
del /f /q "%TMP_PS1%" >nul 2>&1

if not "%EC%"=="0" (
  echo.
  echo [ERROR] Setup failed with exit code %EC%.
  pause
  exit /b %EC%
)

echo.
echo [OK] Setup completed.
pause
exit /b 0
