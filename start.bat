@echo off
REM start.bat — Shadow Fleet Tracker launcher (Windows Command Prompt)
REM
REM Usage:
REM   set AISSTREAM_API_KEY=your_key_here
REM   start.bat
REM
REM Opens the tracker and dashboard in two separate windows.

echo.
echo  Shadow Fleet Tracker
echo  formerlab.eu
echo.

REM Check we're in the right directory
if not exist shadow_tracker.py (
    echo  Error: run this script from the shadow-fleet-tracker directory.
    pause
    exit /b 1
)

REM Find Python
set PYTHON=
for %%C in (python python3 py) do (
    if not defined PYTHON (
        %%C -c "import sys; exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
        if not errorlevel 1 set PYTHON=%%C
    )
)

if not defined PYTHON (
    echo  Python 3.11+ not found.
    echo  Install from https://www.python.org/downloads/
    echo  Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM Virtual environment — create if none exists, activate if found
if exist .venv\Scripts\activate.bat (
    echo  Activating .venv
    call .venv\Scripts\activate.bat
    set PYTHON=python
) else if exist venv\Scripts\activate.bat (
    echo  Activating venv
    call venv\Scripts\activate.bat
    set PYTHON=python
) else (
    echo  No virtual environment found — creating .venv ...
    %PYTHON% -m venv .venv
    call .venv\Scripts\activate.bat
    set PYTHON=python
    echo  .venv created and activated
)

REM Install dependencies
echo.
echo  Checking dependencies...
%PYTHON% -m pip install -q -r requirements.txt
echo  Dependencies OK

REM Preflight checks
echo.
echo  Running preflight checks...
%PYTHON% check.py
if errorlevel 1 (
    echo.
    echo  Fix the issues above, then run start.bat again.
    pause
    exit /b 1
)

REM Launch tracker in new window
echo  Starting tracker...
start "Shadow Fleet Tracker" cmd /k "cd /d %CD% && python shadow_tracker.py"

REM Brief pause so tracker can render initial map
timeout /t 2 /nobreak >nul

REM Launch dashboard in new window
echo  Starting dashboard...
start "Shadow Fleet Dashboard" cmd /k "cd /d %CD% && python -m uvicorn webserver:app --host 0.0.0.0 --port 8000"

timeout /t 2 /nobreak >nul

echo.
echo  Tracker:   running in separate window
echo  Dashboard: running in separate window
echo.
echo  Dashboard: http://localhost:8000
echo  Map:       http://localhost:8000/map
echo.

REM Open browser
start http://localhost:8000/map

echo  Close the tracker and dashboard windows to stop the system.
echo.
pause