@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "main.py" gui
    exit /b 0
)

where uv >nul 2>nul
if errorlevel 1 (
    echo SAC could not start: neither .venv nor uv was found.
    echo Run: uv sync
    pause
    exit /b 1
)

uv run python "main.py" gui
