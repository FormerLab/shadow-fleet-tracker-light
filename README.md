# shadow-fleet-tracker-light

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Baltic Sea shadow fleet monitoring via live AIS data. Watches 1200+ vessels from the [Ukrainian GUR War&Sanctions catalogue](https://war-sanctions.gur.gov.ua/en/transport/ships) against the [AISStream](https://aisstream.io) WebSocket feed, plots positions on a self-updating map, flags proximity to undersea cables, and detects Russia↔West transshipment patterns.

Free, open source, runs locally. No cloud, no subscription beyond a free AISStream API key.

Part of the [Former Lab](https://formerlab.eu) sovereign intelligence toolchain.

---

## Support Former Lab

Shadow Fleet Tracker Light is built and maintained by the [Former Lab](https://formerlab.eu) team — sovereign computing,  building open, privacy-first tools on old hardware with no VC backing.

If this tool is useful to you, consider supporting on Patreon. A free tier is available, with a 7-day trial on paid tiers. Always support Ukraine!

**[patreon.com/FormerLab](https://www.patreon.com/FormerLab)**

Supporters get early access to new tools, development updates, and behind-the-scenes posts on how projects like this are built.

---

## Quick start

```bash
git clone https://github.com/FormerLab/shadow-fleet-tracker-light.git
cd shadow-fleet-tracker
pip install -r requirements.txt
export AISSTREAM_API_KEY=your_key_here   # free at aisstream.io
python shadow_tracker.py &
uvicorn webserver:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` — the map is at `/map`.

Windows users: see the [Setup](#setup) section for PowerShell/cmd environment variable syntax.

Linux / macOS users can use `start.sh`. Windows users can use `start.bat` or `start.ps1` — both open tracker and dashboard in separate windows and launch the browser automatically.

---

## What it does

- Connects to the AISStream WebSocket and subscribes to a Baltic Sea bounding box
- Filters the stream against a watchlist of 1200+ vessel MMSIs sourced from the Ukrainian GUR catalogue (`Vessels1.db`)
- Plots live positions and route trails on a Folium/Leaflet map, refreshed every 3 minutes
- Alerts when a vessel comes within 10 km of a cable segment (from `filtered_cables.kml`)
- Detects loitering — vessels slow or stationary for 20+ minutes in a fixed area
- Detects transshipment patterns — vessels calling at Russian ports then Western hubs (or vice versa) within 21 days
- Logs all position data to SQLite for offline analysis
- Warm-restarts from the position log — the map is populated immediately on startup with last known positions, no blank-canvas wait
- Serves a FastAPI dashboard for log inspection, vessel analysis, GPX export, transshipment review, and interactive route replay

---

## Map

Dark CartoDB basemap with cable routes overlaid in green. Vessel markers update every 3 minutes with a live countdown to the next refresh — the page auto-reloads. Popups show MMSI, IMO, flag, speed, heading, destination, and deep links to MarineTraffic, VesselFinder, and War&Sanctions. OpenSanctions sanctions status shown if an API key is configured.

Known vessel names from `Vessels1.db` are shown immediately on first appearance without waiting for an AIS static message.

On startup the map is pre-populated from the last 24 hours of the position log. Vessels not yet seen in the current session are shown as grey markers with a "last seen Xh ago" label — they transition to live colour as new pings arrive.

---

## Files

```
shadow_tracker.py          Main process — WS consumer, map renderer, DB writes
loitering_module.py        Loitering detection and map annotation
transshipment_module.py    Port call detection and Russia↔West transshipment flagging
webserver.py               FastAPI dashboard
gur_scrape.py              One-shot scraper — builds IMO→GUR-ID mapping + full vessel catalogue
check.py                   Preflight checks — Python version, deps, API key, network
start.sh                   Launcher for Linux / macOS
start.bat                  Launcher for Windows (Command Prompt)
start.ps1                  Launcher for Windows (PowerShell)
requirements.txt           Dependencies

Vessels1.db                Watchlist — 1200+ vessels (MMSI + IMO + name where known)
vessel_data_log.db         Position log — runtime, append-only
vessel_static.db           AIS static data cache (name, destination, flag history)
transshipment.db           Port call log and transshipment events
loitering_events.db        Loitering events
filtered_cables.kml        Baltic Sea undersea cable geometry
cables.kml                 Full cable dataset (pre-filter source)
gur_mapping.json           IMO→GUR-ID mapping (produced by gur_scrape.py, optional)
gur_vessels_full.json      Full GUR catalogue — GUR-ID→{imo, mmsi, name, flag} (produced by gur_scrape.py)
```

---

## Watchlist

`Vessels1.db` is the canonical list of tracked vessels. 1200+ entries sourced from the Ukrainian GUR War&Sanctions catalogue, covering tankers and cargo vessels identified as part of the Russian shadow fleet or implicated in Baltic hybrid activity.

The list includes vessels recently seized or intercepted by Baltic authorities:

| Vessel | IMO | Event |
|---|---|---|
| EAGLE S | 9329760 | Finland seized Dec 2024 — Estlink-2 cable sabotage, spy equipment found |
| EVENTIN | 9308065 | Germany seized Jan–Mar 2025 — drifted off Rügen, 100,000t Russian crude confiscated |
| KIWALA | 9332810 | Estonia detained Apr 2025 — flagless, EU/UK sanctioned |
| JAGUAR | 9293002 | Estonia intercepted May 2025 — Russia scrambled Su-35 to escort it |
| KIRA K | 9346720 | Wagner/GRU crew confirmed aboard Dec 2025 |
| QENDIL | 9310525 | Wagner/GRU crew Sep 2025 — Ukrainian drone strike Dec 2025 |
| FITBURG | 9250397 | Finland seized 31 Dec 2025 — Helsinki-Tallinn cable sabotage |
| CAFFA | 9143611 | Sweden seized 6 Mar 2026 — stolen Ukrainian grain, false flag |
| SEA OWL I | 9321172 | Sweden seized 12 Mar 2026 — EU sanctioned, false Comoros flag |

MMSIs are corrected against current AIS data — shadow fleet vessels reflag frequently. The `update_vessels_2026_03.sql` file documents all changes with sources.

The watchlist is intentionally open. Add vessels directly to `Vessels1.db` while the tracker is running — they will be picked up within 5 minutes without a restart.

Linux / macOS:
```bash
sqlite3 Vessels1.db "INSERT OR IGNORE INTO vessels (mmsi, imo, name) VALUES ('123456789', '9999999', 'VESSEL NAME');"
```

Windows (no sqlite3 CLI needed):
```python
import sqlite3
conn = sqlite3.connect("Vessels1.db")
conn.execute("INSERT OR IGNORE INTO vessels (mmsi, imo, name) VALUES ('123456789', '9999999', 'VESSEL NAME')")
conn.commit()
conn.close()
```

---

## Data model

**`Vessels1.db` — watchlist**
```
vessels(mmsi TEXT PK, imo TEXT, name TEXT, destination TEXT)
```

**`vessel_data_log.db` — position log**
```
vessel_data_log(timestamp, mmsi, name, imo, destination, speed, heading, latitude, longitude, cable_alert)
```
Append-only. Written on every map render cycle.

**`vessel_static.db` — AIS static data cache**
```
vessel_static(mmsi TEXT PK, name TEXT, imo TEXT, destination TEXT, flag TEXT)
flag_history(id, mmsi, flag, timestamp)
```
Populated from live `ShipStaticData` AIS messages. Takes priority over `Vessels1.db` names once received. `flag_history` records every flag change with a timestamp — reflagging events are logged and displayed on the per-vessel page.

**`loitering_events.db` — loitering log** *(created at runtime)*
```
loitering_events(id, mmsi, timestamp, latitude, longitude, near_cable)
```

**`transshipment.db` — port call and transshipment log** *(created at runtime)*
```
port_calls(id, mmsi, name, port, port_type, entry_ts, exit_ts, min_speed)
transshipment_events(id, mmsi, name, direction, from_port, from_exit_ts,
                     to_port, to_entry_ts, days_between, detected_ts)
```

---

## Requirements

- Python 3.11 or newer — [python.org/downloads](https://www.python.org/downloads/)
- An AISStream API key — free at [aisstream.io](https://aisstream.io) (register, then copy your key from the dashboard)
- The dependencies in `requirements.txt`

---

## Start scripts

The easiest way to run the tracker. Each script installs dependencies, runs preflight checks, and launches both processes.

**Linux / macOS:**
```bash
chmod +x start.sh
export AISSTREAM_API_KEY=your_key_here
./start.sh
```

**Windows (PowerShell):**
```powershell
$env:AISSTREAM_API_KEY = "your_key_here"
.\start.ps1
```

**Windows (Command Prompt):**
```cmd
set AISSTREAM_API_KEY=your_key_here
start.bat
```

All three scripts run `check.py` first — a preflight that verifies Python version, dependencies, data files, API key, and network reachability, with clear error messages if anything is missing.

You can also run the preflight check on its own at any time:
```bash
python check.py
```

---

## Setup (manual)

**1. Clone and install dependencies**

```bash
git clone https://github.com/FormerLab/shadow-fleet-tracker-light.git
cd shadow-fleet-tracker-light
pip install -r requirements.txt
```

On Ubuntu 22.04+ and Debian 12+ you may get an "externally managed environment" error from pip. The start scripts handle this automatically by creating a virtual environment — just run `start.sh` and it takes care of it. If installing manually, create a venv first:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

**2. Set your AISStream API key**

Linux / macOS:
```bash
export AISSTREAM_API_KEY=your_key_here
export OPENSANCTIONS_API_KEY=your_key_here  # optional
```

Windows (Command Prompt):
```cmd
set AISSTREAM_API_KEY=your_key_here
set OPENSANCTIONS_API_KEY=your_key_here
```

Windows (PowerShell):
```powershell
$env:AISSTREAM_API_KEY = "your_key_here"
$env:OPENSANCTIONS_API_KEY = "your_key_here"
```

**3. Run the tracker and dashboard as two separate terminals**

Terminal 1 — the AIS feed consumer:
```bash
python shadow_tracker.py
```

Terminal 2 — the web dashboard:
```bash
uvicorn webserver:app --host 0.0.0.0 --port 8000
```

**4. Open the dashboard**

```
http://localhost:8000
```

The live map is at `http://localhost:8000/map` and updates every 3 minutes. On first run the map renders immediately from any position history already in the database — no blank canvas wait.

---

## API keys

| Service | Required | Free tier | Link |
|---|---|---|---|
| AISStream | Yes | Yes — unlimited for non-commercial | [aisstream.io](https://aisstream.io) |
| OpenSanctions | No | Yes — non-commercial use | [opensanctions.org/api](https://www.opensanctions.org/api/) |

Without `OPENSANCTIONS_API_KEY` the tracker runs normally — sanctions badges are simply omitted from map popups.

---

## Configuration

All tunables are at the top of `shadow_tracker.py`:

| Constant | Default | Description |
|---|---|---|
| `BALTIC_BBOX` | 52.65–66°N, 9–30°E | AISStream subscription bounding box |
| `CABLE_ALERT_KM` | 10 | Cable proximity threshold |
| `MAP_RENDER_INTERVAL` | 180 s | Map refresh cadence |
| `MMSI_RELOAD_INTERVAL` | 300 s | Watchlist reload from DB |
| `WARM_RESTART_HOURS` | 24 | Hours of position history to load on startup |
| `RECONNECT_DELAY` | 10 s | WS reconnect backoff |

Environment variables:

| Variable | Required | Description |
|---|---|---|
| `AISSTREAM_API_KEY` | Yes | AISStream WebSocket API key |
| `OPENSANCTIONS_API_KEY` | No | Enables sanctions lookup in map popups |

OpenSanctions is free for non-commercial use — register at [opensanctions.org/api](https://www.opensanctions.org/api/). Without a key the tracker runs normally; sanctions fields are omitted from popups.

---

## War&Sanctions deep-links (optional)

[war-sanctions.gur.gov.ua](https://war-sanctions.gur.gov.ua/en/transport/ships) is the Ukrainian GUR's public catalogue of shadow fleet and sanctioned vessels. Each vessel has a numbered page with port call history, maps, and sanctions detail.

`gur_scrape.py` builds a local `IMO→GUR-ID` mapping by crawling the catalogue once:

```bash
python gur_scrape.py
```

This produces `gur_mapping.json`. The tracker loads it at startup and uses direct deep-links (`/en/transport/ships/{id}`) in map popups where available, falling back to a Google site-search for vessels not yet in the catalogue.

The crawl covers ~1600 entries at 1.5 s/request (~40 min). Re-run occasionally as the catalogue grows — use `--start` to resume from a specific ID:

```bash
python gur_scrape.py --start 1580
```

To debug a single page:

```bash
python gur_scrape.py --probe 1517
```

`gur_mapping.json` is reloaded automatically every 5 minutes alongside the watchlist — no tracker restart needed after a re-crawl.

Loitering thresholds in `loitering_module.py`:

| Constant | Default | Description |
|---|---|---|
| `SPEED_THRESHOLD_KN` | 0.5 kn | Below this counts as stopped |
| `TIME_THRESHOLD_S` | 1200 s | Duration before loitering is flagged |
| `RADIUS_THRESHOLD_KM` | 0.5 km | Max drift to still count as same spot |

Transshipment settings in `transshipment_module.py`:

| Constant | Default | Description |
|---|---|---|
| `SPEED_THRESHOLD_KN` | 1.5 kn | Below this inside a port zone counts as a call |
| `MIN_PINGS_IN_ZONE` | 2 | Minimum pings before recording a port call |
| `WINDOW_DAYS` | 21 | Max days between port calls to flag as transshipment |

Port zones covered:

| Port | Type |
|---|---|
| Ust-Luga, Primorsk, St Petersburg, Vyborg | Russian export terminals |
| Skaw/Skagen, Gothenburg, Kiel, Copenhagen, Aarhus | Western transshipment hubs |

---

## Dashboard

| Route | Description |
|---|---|
| `/` | Live log viewer, auto-refreshes every 60 s |
| `/map` | Latest rendered map (iframe-friendly) |
| `/analyze` | Filterable vessel record table with CSV export |
| `/timeline` | Activity overview — one card per tracked vessel, sorted by last seen |
| `/vessel/<mmsi>` | Per-vessel history: AIS blackouts, cable proximity, flag changes, GPX export |
| `/vessel/<mmsi>/gpx` | GPX track export — opens in QGIS, OpenStreetMap, GPSBabel |
| `/loitering` | Loitering events with near-cable flag, by-vessel summary, CSV export |
| `/transshipment` | Russia↔West port call patterns — RU→WEST and WEST→RU events, CSV export |
| `/log/download` | Raw log file download |

### Per-vessel page

Each vessel page (`/vessel/<mmsi>`) shows:

- **Route replay** — interactive Leaflet map with play/pause/reset and speed control. Trail builds point by point; marker turns red on cable alert pings
- **Summary** — first/last seen, total pings, cable alert count, AIS gap count, destinations observed
- **AIS blackouts** — any gap ≥60 minutes flagged with start/end timestamps and duration
- **Cable proximity** — percentage of pings where the cable alert was active
- **Static data drift** — name changes, flag changes with dates, destination history. Reflagging is a primary shadow fleet evasion tactic
- **GPX export** — full track with timestamps, loadable into any GIS tool

### Transshipment page

Flags two patterns inferred from position data:

- **RU→WEST** — vessel called at a Russian port then a Western hub within 21 days. Potential cargo laundering into European supply chains
- **WEST→RU** — vessel called at a Western hub then a Russian port within 21 days. Potential European goods flowing east into sanctioned territory

Port calls are inferred from position pings — no external API required. A vessel must be inside a port zone at <1.5 kn for at least 2 pings to register a call. This shows a pattern, not a verdict.

---

## Cable data

`filtered_cables.kml` is derived from open-source datasets and is approximate — not suitable for precise proximity calculations. The 10 km alert threshold accounts for this margin. Contributions of improved cable geometry are welcome.

---

## Architecture notes

The tracker runs a single async loop over the WebSocket stream. State is kept in-memory (`vessel_info`, `static_cache`) and flushed to SQLite on each render cycle. The map is written atomically via `os.replace` to avoid serving a partial file.

On startup, `warm_restart()` reads the last `WARM_RESTART_HOURS` hours from `vessel_data_log.db` and pre-populates `vessel_info` before the WebSocket connects. The initial map renders immediately. Stale positions are visually distinguished (grey markers, dimmed trails) and transition to live colour as new pings arrive.

Watchlist and `gur_mapping.json` are reloaded from disk every 5 minutes — vessels can be added and the GUR mapping re-crawled while the tracker is running, without a restart.

The webserver is stateless and reads directly from the SQLite files — no shared memory with the tracker process.

---

## Status

| Component | State |
|---|---|
| AIS stream consumer | Working |
| Watchlist | 1200+ vessels (full GUR catalogue) |
| Cable proximity alert | Working |
| Loitering detection | Working |
| Transshipment detection | Working — port calls inferred from position, 21-day window |
| Map rendering | Working — 3 min cadence, live countdown, auto-reload |
| Warm restart | Working — pre-populates map from last 24h of logs on startup |
| Vessel popups | MarineTraffic + VesselFinder + War&Sanctions direct links (1337 vessels mapped) |
| Vessel popups | OpenSanctions sanctions status (optional, requires API key) |
| Flag change detection | Working — logs changes, stored in `flag_history` |
| Dashboard — log, vessels, CSV export | Working |
| Dashboard — timeline view | Working |
| Dashboard — per-vessel history + GPX | Working |
| Dashboard — route replay | Working — play/pause/reset, speed control, cable alert highlighting |
| Dashboard — loitering panel | Working |
| Dashboard — transshipment panel | Working |
