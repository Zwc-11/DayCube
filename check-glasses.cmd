@echo off
rem Double-click: a glasses walk simulated from the 2026-10-24 trail scan (eval/glasses_sim.py,
rem docs/glasses.md). The scan's video is cut as glasses would take it (a photo every 3 s, every
rem second, a 5 frames-a-second stream), rebuilt from the images alone with COLMAP, put on the map
rem with the GPS, and scored against the phone's own poses; then Brush trains on each, with the
rem rebuilt poses and with the phone's. About an hour. Results: vault\logs\glasses-sim.json
rem (rewritten after each mode), log: vault\logs\glasses-sim.log. The frames stay in
rem vault\derived\glasses-sim.
setlocal
cd /d "%~dp0"
title DayCube glasses check
set "UV_CACHE_DIR=%~dp0.cache\uv"
if not exist "%~dp0vault\logs" mkdir "%~dp0vault\logs"
echo Rebuilding the trail from images alone, then training on it (about an hour).
echo Log: %~dp0vault\logs\glasses-sim.log
call uv run --project services\capture-win python eval\glasses_sim.py --train %* > "%~dp0vault\logs\glasses-sim.log" 2>&1
echo.
findstr /b /c:"photo" /c:"stream" /c:"checks" /c:"Written" /c:"Traceback" /c:"error" "%~dp0vault\logs\glasses-sim.log"
echo.
echo Done. Results: %~dp0vault\logs\glasses-sim.json
call timeout /t 120
