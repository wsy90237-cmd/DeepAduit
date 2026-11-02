@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
echo DeepAudit 0.5.4 - 启动中，关闭程序窗口即退出
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=.venv\Scripts\python.exe"
)
"%PYTHON_EXE%" -B "tools\run_trial.py"
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo 启动失败，错误码 %CODE%
    pause
)
exit /b %CODE%
