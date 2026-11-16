@echo off
rem Double-click after an update is pushed. It pulls the update, installs the viewer's packages when
rem they changed, syncs the receiver's, the eye tracker's and (once installed) the speech worker's
rem environments, runs the speech check on the
rem GPU when the speech code changed, restarts the receiver and eye tracker on the new code, waits
rem for them, and runs doctor. Log: vault\logs\update.log (docs/capture.md, "Updating").
rem Commands are run with call, so one that is a .cmd (some git installs) returns here.
setlocal
cd /d "%~dp0"
title DayCube update
set "LOG=%~dp0vault\logs\update.log"
if not exist "%~dp0vault\logs" mkdir "%~dp0vault\logs"
set "UV_CACHE_DIR=%~dp0.cache\uv"
echo Updating DayCube. Log: %LOG%
echo [%date% %time%] update> "%LOG%"

set "OLD="
for /f %%i in ('git rev-parse HEAD') do set "OLD=%%i"
call git pull --ff-only >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] git pull failed: see above>> "%LOG%"
  goto :end
)
call git log --oneline -1 >> "%LOG%" 2>&1

rem The viewer's packages, when they changed (a running viewer.cmd needs restarting after this).
if not defined OLD goto :sync
call git diff --quiet %OLD% HEAD -- package-lock.json
if not errorlevel 1 goto :sync
echo [%date% %time%] the viewer's packages changed: npm install>> "%LOG%"
call npm install --no-audit --no-fund >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] npm install failed>> "%LOG%"
  goto :end
)
echo [%date% %time%] restart viewer.cmd to use them>> "%LOG%"

:sync

echo [%date% %time%] sync the receiver>> "%LOG%"
call uv sync --project services\capture-win >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] sync of the receiver failed>> "%LOG%"
  goto :end
)
echo [%date% %time%] sync the eye tracker>> "%LOG%"
call uv sync --project services\gaze >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] sync of the eye tracker failed>> "%LOG%"
  goto :end
)

rem The learned features' environment (check-glasses-learned.cmd), once set up.
if not exist "services\recon\.venv" goto :speech
echo [%date% %time%] sync the learned features>> "%LOG%"
call uv sync --project services\recon >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] sync of the learned features failed>> "%LOG%"
  goto :end
)

:speech
if not exist "services\speech\.venv" goto :restart
echo [%date% %time%] sync the speech worker>> "%LOG%"
call uv sync --project services\speech >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] sync of the speech worker failed>> "%LOG%"
  goto :end
)
if not defined OLD goto :restart
call git diff --quiet %OLD% HEAD -- services/speech
if not errorlevel 1 goto :restart
echo [%date% %time%] the speech code changed: speech check on the GPU>> "%LOG%"
echo Checking the speech models (about 2 min).
call uv run --project services\speech python -m speech canary >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] speech check: FAILED, see above>> "%LOG%"
) else (
  echo [%date% %time%] speech check: ok>> "%LOG%"
)

:restart
echo [%date% %time%] restart the receiver and eye tracker>> "%LOG%"
call "%~dp0capture.cmd" restart >> "%LOG%" 2>&1
echo [%date% %time%] waiting 45 s for them to start>> "%LOG%"
echo Waiting 45 s for the receiver and eye tracker to start.
call ping -n 46 127.0.0.1 >nul
echo [%date% %time%] doctor>> "%LOG%"
call "%~dp0capture.cmd" doctor >> "%LOG%" 2>&1

:end
echo [%date% %time%] done>> "%LOG%"
echo.
findstr /b /l /c:"FAIL" /c:"warn" /c:"pass" /c:"[" "%LOG%"
echo.
echo Done. Log: %LOG%
call timeout /t 60
