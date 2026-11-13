@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto error
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 goto error

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

echo.
echo Dependencies installed. You can now open scripts\Run_Client.bat or scripts\Run_Server.bat.
pause
exit /b 0

:error
echo Could not install dependencies. Check that Python 3.11 or newer is installed.
pause
exit /b 1
