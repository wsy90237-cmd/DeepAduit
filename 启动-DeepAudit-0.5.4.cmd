@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
echo DeepAudit 0.5.4 源码试用版 - 关闭程序窗口即退出（此窗口请保留）
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B "tools\run_trial.py"
) else (
    python -B "tools\run_trial.py"
)
if errorlevel 1 pause
