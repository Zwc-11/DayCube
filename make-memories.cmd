@echo off
rem Double-click: photos you can step into (docs/memories.md). Every photo dropped in
rem vault\inbox\memories (JPEG, PNG, iPhone HEIC) becomes a 3D scene with SHARP on the GPU, in the
rem day it was taken; the first time, with no photos there, the sharpest frames of the
rem 2026-10-24 trail scan are made into memories instead. Open them at
rem http://127.0.0.1:3000/?view=memories (viewer.cmd running). Log: vault\logs\memories.log.
setlocal
cd /d "%~dp0"
title DayCube memories
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "LOG=%~dp0vault\logs\memories.log"
if not exist "%~dp0vault\logs" mkdir "%~dp0vault\logs"
if not exist "%~dp0vault\inbox\memories" mkdir "%~dp0vault\inbox\memories"
echo Setting up (the first time, a few minutes)...
call uv sync --project services\recon > "%LOG%" 2>&1
if errorlevel 1 (
  echo The environment could not be set up: see %LOG%
  goto :end
)
echo Making memories from vault\inbox\memories...
call uv run --project services\recon python -m ingest.recon.memory --inbox --scan 2026-10-24 --count 6 --if-none %* >> "%LOG%" 2>&1
echo.
findstr /b /c:"memory " /c:"Traceback" /c:"Error" "%LOG%"
findstr /c:"memories made" "%LOG%"
:end
echo.
echo Open http://127.0.0.1:3000/?view=memories
call timeout /t 120
