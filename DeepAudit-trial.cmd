@echo off
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
echo DeepAudit source trial - close the application window to exit.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B "tools\run_trial.py"
) else (
    python -B "tools\run_trial.py"
)
if errorlevel 1 pause
