@echo off
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHON_EXE=%~dp0runtime\pythonw.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYTHON_EXE%" (
    echo DeepAudit runtime is missing. Extract the whole developer ZIP first.
    echo For a source checkout, install requirements.txt in .venv.
    pause
    exit /b 1
)
start "" "%PYTHON_EXE%" -I -B "%~dp0tools\run_gui.py" %*
