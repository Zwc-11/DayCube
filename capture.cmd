@echo off
rem From the repository folder: .\capture doctor, .\capture restart, ... in the capture
rem receiver's own environment, not the system Python (which has no `capture` module).
uv run --project "%~dp0services\capture-win" python -m capture %*
