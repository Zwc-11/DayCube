@echo off
rem Double-click: writes yesterday's end-of-day review from this PC's vault now (vault\<date>\review.json,
rem with the day's timeline and its daily note), checks its words against the day's facts once more,
rem and prints what it says, how long each step took and any problem (docs/review.md). The words come
rem from the template unless DAYCUBE_SUMMARY_LLM is set in .env. Results: vault\logs\review-check.json,
rem messages: vault\logs\review-check.log.
setlocal
cd /d "%~dp0"
title DayCube review check
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "OUT=%~dp0vault\logs\review-check.json"
set "LOG=%~dp0vault\logs\review-check.log"
if not exist "%~dp0vault\logs" mkdir "%~dp0vault\logs"
echo Writing and checking yesterday's review (under a minute; a day not yet indexed takes longer).
call uv run daycube review --date yesterday --check > "%OUT%" 2> "%LOG%"
if errorlevel 1 (
  echo The review could not be written: see %LOG%
  type "%LOG%"
  goto :end
)
type "%OUT%"
echo.
findstr /c:"\"problems\": []" "%OUT%" >nul
if errorlevel 1 (
  echo The words did not pass the checks: see "problems" above.
) else (
  echo The words passed the checks.
)
:end
echo.
echo Results: %OUT%
call timeout /t 120
