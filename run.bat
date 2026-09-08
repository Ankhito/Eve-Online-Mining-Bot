@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "EVE Mining" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
) else (
    where pythonw.exe >nul 2>&1
    if errorlevel 1 (
        echo EVE Mining could not find Python. Install Python 3.10 or newer and try again.
        pause
        exit /b 1
    )
    start "EVE Mining" pythonw.exe "%~dp0main.py"
)
