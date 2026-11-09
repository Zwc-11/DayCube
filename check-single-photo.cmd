@echo off
rem Double-click: one photo of the 2026-10-24 trail, two ways (eval/single_photo.py, docs/glasses.md
rem "One photo"). Each photo is lifted to 3D alone (the LiDAR depth as a reference, Depth Pro,
rem Depth Anything V2 metric, SHARP) and rendered where the phone really was 0.25-4 m on; then it is
rem located in the scan made of the same walk's other frames. The first run downloads the models
rem (about 6 GB). Results: vault\logs\single-photo.json, log: vault\logs\single-photo.log. The
rem renders stay in vault\derived\single-photo.
setlocal
cd /d "%~dp0"
title DayCube one-photo check
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "LOG=%~dp0vault\logs\single-photo.log"
if not exist "%~dp0vault\logs" mkdir "%~dp0vault\logs"
echo Setting up the environment (the first time, a few minutes)...
call uv sync --project services\recon > "%LOG%" 2>&1
if errorlevel 1 (
  echo The environment could not be set up: see %LOG%
  goto :end
)
echo One photo alone, then one photo in the scan (about 15 minutes).
echo Log: %LOG%
call uv run --project services\recon python eval\single_photo.py %* >> "%LOG%" 2>&1
echo.
findstr /b /c:"flat" /c:"lidar" /c:"depth" /c:"dav2" /c:"sharp" /c:"locate" /c:"Written" /c:"Traceback" "%LOG%"
:end
echo.
echo Done. Results: %~dp0vault\logs\single-photo.json
call timeout /t 120
