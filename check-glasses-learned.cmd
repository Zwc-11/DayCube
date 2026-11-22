@echo off
rem Double-click: the glasses check with learned features, ALIKED + LightGlue on the GPU, in place
rem of SIFT (eval/glasses_sim.py --features aliked, docs/glasses.md). The first run sets up
rem services\recon: PyTorch is linked from the speech worker's copy when uv still has it, else
rem about 3 GB is downloaded. Results: vault\logs\glasses-sim-aliked.json (rewritten after each
rem mode), log: vault\logs\glasses-sim-aliked.log. The frames stay in vault\derived\glasses-sim.
setlocal
cd /d "%~dp0"
title DayCube glasses check (learned features)
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "LOG=%~dp0vault\logs\glasses-sim-aliked.log"
if not exist "%~dp0vault\logs" mkdir "%~dp0vault\logs"
echo Setting up the learned features' environment (the first time, a few minutes)...
call uv sync --project services\recon > "%LOG%" 2>&1
if errorlevel 1 (
  echo The environment could not be set up: see %LOG%
  goto :end
)
echo Rebuilding the trail with learned features, then training on it (about an hour).
echo Log: %LOG%
call uv run --project services\recon python eval\glasses_sim.py --features aliked --train %* >> "%LOG%" 2>&1
echo.
findstr /b /c:"photo" /c:"stream" /c:"checks" /c:"Written" /c:"Traceback" /c:"error" "%LOG%"
:end
echo.
echo Done. Results: %~dp0vault\logs\glasses-sim-aliked.json
call timeout /t 120
