@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 goto error

".venv\Scripts\python.exe" -c "import PySide6; import PIL" >nul 2>&1
if errorlevel 1 (
    echo Installing SynCanvas dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" run_client.py
set "exit_code=%errorlevel%"
if not "%exit_code%"=="0" pause
exit /b %exit_code%

:error
echo Could not prepare SynCanvas. Check that Python 3.11 or newer is installed.
pause
exit /b 1
