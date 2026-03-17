#!/usr/bin/env bash
# start.sh — Shadow Fleet Tracker launcher (Linux / macOS)
#
# Usage:
#   chmod +x start.sh
#   export AISSTREAM_API_KEY=your_key_here
#   ./start.sh
#
# Both processes log to terminal. Stop with Ctrl+C.

set -e

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
DIM="\033[2m"
RESET="\033[0m"

echo ""
echo -e "${GREEN}Shadow Fleet Tracker${RESET}"
echo -e "${DIM}formerlab.eu${RESET}"
echo ""

# ---------------------------------------------------------------------------
# Check we're in the right directory
# ---------------------------------------------------------------------------
if [ ! -f "shadow_tracker.py" ]; then
    echo -e "${RED}Error: run this script from the shadow-fleet-tracker directory.${RESET}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
        if [ "$VER" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}Python 3.11+ not found.${RESET}"
    echo "  Linux:  sudo apt install python3 / sudo dnf install python3"
    echo "  macOS:  brew install python  (https://brew.sh)"
    echo "  All:    https://www.python.org/downloads/"
    exit 1
fi

echo -e "  Using $($PYTHON --version)"

# ---------------------------------------------------------------------------
# Virtual environment — create if none exists, activate if found
# ---------------------------------------------------------------------------
if [ -d ".venv" ]; then
    echo -e "  Activating .venv"
    source .venv/bin/activate
    PYTHON="python"
elif [ -d "venv" ]; then
    echo -e "  Activating venv"
    source venv/bin/activate
    PYTHON="python"
else
    echo -e "  No virtual environment found — creating .venv …"
    $PYTHON -m venv .venv
    source .venv/bin/activate
    PYTHON="python"
    echo -e "  ${GREEN}.venv created and activated${RESET}"
fi

# ---------------------------------------------------------------------------
# Install / verify dependencies
# ---------------------------------------------------------------------------
echo ""
echo "Checking dependencies…"
$PYTHON -m pip install -q -r requirements.txt
echo -e "  ${GREEN}Dependencies OK${RESET}"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
echo ""
echo "Running preflight checks…"
if ! $PYTHON check.py; then
    echo ""
    echo -e "${RED}Fix the issues above, then run ./start.sh again.${RESET}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
echo "Starting tracker and dashboard…"
echo -e "${DIM}Both processes share this terminal. Press Ctrl+C to stop both.${RESET}"
echo ""

# Trap Ctrl+C to kill both background jobs cleanly
trap 'echo ""; echo "Stopping…"; kill $TRACKER_PID $WEB_PID 2>/dev/null; exit 0' INT TERM

$PYTHON shadow_tracker.py &
TRACKER_PID=$!

# Give the tracker a moment to render the initial map before the webserver starts
sleep 2

$PYTHON -m uvicorn webserver:app --host 0.0.0.0 --port 8000 &
WEB_PID=$!

echo -e "  ${GREEN}Tracker PID:   $TRACKER_PID${RESET}"
echo -e "  ${GREEN}Dashboard PID: $WEB_PID${RESET}"
echo ""
echo -e "  ${GREEN}Dashboard: http://localhost:8000${RESET}"
echo -e "  ${GREEN}Map:       http://localhost:8000/map${RESET}"
echo ""

# Wait for either process to exit
wait $TRACKER_PID $WEB_PID