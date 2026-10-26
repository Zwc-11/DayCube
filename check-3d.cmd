@echo off
rem Double-click: a scan's 3D view measured on this PC's GPU (scripts/profile-splat.mjs). It starts
rem its own viewer on port 3100 and opens Edge; leave the window alone for about a minute. The
rem result is in vault\logs\splat-perf-splat.json.
setlocal
cd /d "%~dp0"
title DayCube 3D check
set "UV_CACHE_DIR=%~dp0.cache\uv"
echo Checking the viewer's packages...
call npm install --no-audit --no-fund > nul 2>&1
echo Measuring the 3D view on this GPU (about a minute; the browser window opens and closes itself).
call node scripts\profile-splat.mjs %*
echo.
call timeout /t 120
