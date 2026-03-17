#!/usr/bin/env python3
"""
check.py — Shadow Fleet Tracker preflight checks

Run before starting the tracker to verify the environment is ready.
Called automatically by start.sh and start.bat, or run manually:

    python check.py
"""

import importlib
import os
import sys
import sqlite3
import urllib.request
import urllib.error

REQUIRED_PYTHON = (3, 11)

REQUIRED_PACKAGES = [
    ("websockets",  "websockets"),
    ("folium",      "folium"),
    ("geopy",       "geopy"),
    ("branca",      "branca"),
    ("aiohttp",     "aiohttp"),
    ("fastapi",     "fastapi"),
    ("uvicorn",     "uvicorn"),
]

VESSELS_DB   = "Vessels1.db"
CABLES_KML   = "filtered_cables.kml"
AISSTREAM_WS = "https://stream.aisstream.io"   # reachability check via HTTPS

OK   = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"

# Windows doesn't render ANSI in cmd by default
if sys.platform == "win32":
    OK   = "[OK]"
    FAIL = "[FAIL]"
    WARN = "[WARN]"

errors   = []
warnings = []


def check(label: str, passed: bool, detail: str = "", fatal: bool = True):
    if passed:
        print(f"  {OK}  {label}")
    else:
        symbol = FAIL if fatal else WARN
        print(f"  {symbol}  {label}" + (f" — {detail}" if detail else ""))
        if fatal:
            errors.append(label)
        else:
            warnings.append(label)


# ---------------------------------------------------------------------------
# Python version
# ---------------------------------------------------------------------------
print("\nPython")
ver = sys.version_info
check(
    f"Python {ver.major}.{ver.minor} (need {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+)",
    ver >= REQUIRED_PYTHON,
    f"found {ver.major}.{ver.minor} — install Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ from python.org",
)

# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
print("\nPackages")
for import_name, pip_name in REQUIRED_PACKAGES:
    try:
        importlib.import_module(import_name)
        check(import_name, True)
    except ImportError:
        check(import_name, False, f"run: pip install {pip_name}")

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
print("\nData files")
check(VESSELS_DB,  os.path.exists(VESSELS_DB),  "watchlist database missing — check repo is complete")
check(CABLES_KML,  os.path.exists(CABLES_KML),  "cable geometry missing — check repo is complete")

if os.path.exists(VESSELS_DB):
    try:
        with sqlite3.connect(VESSELS_DB) as c:
            count = c.execute("SELECT COUNT(*) FROM vessels").fetchone()[0]
        check(f"Vessels1.db — {count} vessels", count > 0, "database appears empty")
    except Exception as e:
        check("Vessels1.db readable", False, str(e))

gur = os.path.exists("gur_mapping.json")
check(
    "gur_mapping.json (War&Sanctions deep-links)",
    gur,
    "optional — run: python gur_scrape.py",
    fatal=False,
)

# ---------------------------------------------------------------------------
# Environment / API keys
# ---------------------------------------------------------------------------
print("\nAPI keys")
aisstream_key = os.getenv("AISSTREAM_API_KEY", "")
check(
    "AISSTREAM_API_KEY",
    bool(aisstream_key),
    "required — get a free key at aisstream.io, then set the env var",
)

opensanctions_key = os.getenv("OPENSANCTIONS_API_KEY", "")
check(
    "OPENSANCTIONS_API_KEY (sanctions badges)",
    bool(opensanctions_key),
    "optional — register at opensanctions.org/api for sanctions data in popups",
    fatal=False,
)

# ---------------------------------------------------------------------------
# Network reachability
# ---------------------------------------------------------------------------
print("\nNetwork")
try:
    urllib.request.urlopen(AISSTREAM_WS, timeout=5)
    check("aisstream.io reachable", True)
except urllib.error.HTTPError:
    # Any HTTP response means the host is reachable
    check("aisstream.io reachable", True)
except Exception as e:
    check("aisstream.io reachable", False, f"{e}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
if errors:
    print(f"  {FAIL}  {len(errors)} issue(s) must be fixed before starting:")
    for e in errors:
        print(f"       • {e}")
    print()
    sys.exit(1)
elif warnings:
    print(f"  {WARN}  {len(warnings)} optional item(s) not configured (tracker will still run):")
    for w in warnings:
        print(f"       • {w}")
    print(f"\n  {OK}  Ready to start.\n")
    sys.exit(0)
else:
    print(f"  {OK}  All checks passed. Ready to start.\n")
    sys.exit(0)
