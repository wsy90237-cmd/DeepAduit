@echo off
rem 双击启动 DeepAudit（源码版，不弹控制台窗口）
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo 未找到 .venv，请先按 requirements.txt 安装依赖。
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0tools\run_gui.py"
