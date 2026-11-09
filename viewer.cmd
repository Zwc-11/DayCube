@echo off
rem Double-click: the vault's API (127.0.0.1:7701) and the viewer (http://127.0.0.1:3000), with
rem their output here and in vault\logs\viewer.log. Close this window to stop them.
setlocal
cd /d "%~dp0"
title DayCube viewer
set "UV_CACHE_DIR=%~dp0.cache\uv"
if not exist "%~dp0vault\logs" mkdir "%~dp0vault\logs"
echo Checking the viewer's packages...
call npm install --no-audit --no-fund > "%~dp0vault\logs\viewer-install.log" 2>&1
echo DayCube viewer at http://127.0.0.1:3000 - close this window to stop it.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $env:FORCE_COLOR='0'; npm run demo 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath '%~dp0vault\logs\viewer.log' }"
echo.
echo The viewer stopped. See the lines above, or vault\logs\viewer.log.
pause
