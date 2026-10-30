@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
echo DeepAudit 0.5.4 - 启动中，关闭程序窗口即退出
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B "tools\run_trial.py"
) else (
    python -B "tools\run_trial.py"
)
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo 启动失败，错误码 %CODE%
    pause
)
exit /b %CODE%
