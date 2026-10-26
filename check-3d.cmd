@echo off
rem Double-click: a scan's 3D view measured on this PC's GPU (scripts/profile-splat.mjs), twice:
rem with the NVIDIA GPU (Edge started with --force_high_performance_gpu), then with the GPU Windows
rem gives Edge by default, as a browser opened by hand gets it. Each starts its own viewer on port
rem 3100 and opens Edge; leave the windows alone for about two minutes. The results are in
rem vault\logs\splat-perf-splat.json and vault\logs\splat-perf-splat-igpu.json.
setlocal
cd /d "%~dp0"
title DayCube 3D check
set "UV_CACHE_DIR=%~dp0.cache\uv"
echo Checking the viewer's packages...
call npm install --no-audit --no-fund > nul 2>&1
echo Measuring the 3D view with the NVIDIA GPU (about a minute; the browser window opens and closes itself).
call node scripts\profile-splat.mjs --gpu high --label splat %*
echo.
echo Measuring it again with the GPU Windows gives the browser by default (about a minute).
call node scripts\profile-splat.mjs --gpu default --label splat-igpu %*
echo.
call timeout /t 120
