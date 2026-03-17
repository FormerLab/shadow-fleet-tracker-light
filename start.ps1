# start.ps1 — Shadow Fleet Tracker launcher (Windows PowerShell)
#
# Usage:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # run once if needed
#   $env:AISSTREAM_API_KEY = "your_key_here"
#   .\start.ps1
#
# Opens the tracker and dashboard in two separate PowerShell windows.
# Close either window or press Ctrl+C in it to stop that process.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Shadow Fleet Tracker" -ForegroundColor Green
Write-Host "formerlab.eu" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# Check we're in the right directory
# ---------------------------------------------------------------------------
if (-not (Test-Path "shadow_tracker.py")) {
    Write-Host "Error: run this script from the shadow-fleet-tracker directory." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Find Python 3.11+
# ---------------------------------------------------------------------------
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(sys.version_info >= (3,11))" 2>$null
        if ($ver -eq "True") { $python = $cmd; break }
    } catch {}
}

if (-not $python) {
    Write-Host "Python 3.11+ not found." -ForegroundColor Red
    Write-Host "  Install from https://www.python.org/downloads/"
    Write-Host "  Make sure to tick 'Add Python to PATH' during install."
    Read-Host "Press Enter to exit"
    exit 1
}

$pyver = & $python --version
Write-Host "  Using $pyver"

# ---------------------------------------------------------------------------
# Virtual environment — create if none exists, activate if found
# ---------------------------------------------------------------------------
if (Test-Path ".venv\Scripts\Activate.ps1") {
    Write-Host "  Activating .venv"
    & .venv\Scripts\Activate.ps1
    $python = "python"
} elseif (Test-Path "venv\Scripts\Activate.ps1") {
    Write-Host "  Activating venv"
    & venv\Scripts\Activate.ps1
    $python = "python"
} else {
    Write-Host "  No virtual environment found — creating .venv ..."
    & $python -m venv .venv
    & .venv\Scripts\Activate.ps1
    $python = "python"
    Write-Host "  .venv created and activated" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Install / verify dependencies
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Checking dependencies..."
& $python -m pip install -q -r requirements.txt
Write-Host "  Dependencies OK" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Running preflight checks..."
& $python check.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Fix the issues above, then run .\start.ps1 again." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# ---------------------------------------------------------------------------
# Launch in separate windows
# ---------------------------------------------------------------------------
Write-Host "Starting tracker and dashboard in separate windows..."
Write-Host ""

$workdir = (Get-Location).Path

# Tracker window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$workdir'; Write-Host 'Shadow Fleet Tracker' -ForegroundColor Green; & $python shadow_tracker.py"
)

Start-Sleep -Seconds 2

# Dashboard window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$workdir'; Write-Host 'Shadow Fleet Dashboard' -ForegroundColor Green; & $python -m uvicorn webserver:app --host 0.0.0.0 --port 8000"
)

Start-Sleep -Seconds 2

Write-Host "  Tracker:   running in separate window" -ForegroundColor Green
Write-Host "  Dashboard: running in separate window" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard: http://localhost:8000" -ForegroundColor Green
Write-Host "  Map:       http://localhost:8000/map" -ForegroundColor Green
Write-Host ""

# Open browser
try {
    Start-Process "http://localhost:8000/map"
} catch {}

Write-Host "Opening browser..." -ForegroundColor DarkGray
Write-Host ""
Write-Host "Close the tracker and dashboard windows to stop the system." -ForegroundColor DarkGray
Read-Host "Press Enter to close this launcher window"