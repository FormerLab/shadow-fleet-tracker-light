"""
loitering_module.py — Former Lab / Shadow Fleet Tracker
Loitering detection and map annotation.
"""

import sqlite3
from datetime import datetime, timezone

import folium
from folium.plugins import HeatMap

LOITERING_DB = "loitering_events.db"

SPEED_THRESHOLD_KN  = 0.5   # knots — below this counts as stopped
TIME_THRESHOLD_S    = 1200  # 20 minutes before flagging as loitering
RADIUS_THRESHOLD_KM = 0.5   # max drift radius to still count as same spot

# In-memory state: mmsi -> {start: datetime, positions: [(lat, lon)]}
_memory: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def init_db() -> None:
    with sqlite3.connect(LOITERING_DB) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS loitering_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi       TEXT,
            timestamp  TEXT,
            latitude   REAL,
            longitude  REAL,
            near_cable INTEGER
        )""")


def _save_event(mmsi: str, ts: str, lat: float, lon: float, near_cable: bool) -> None:
    with sqlite3.connect(LOITERING_DB) as c:
        c.execute(
            "INSERT INTO loitering_events (mmsi, timestamp, latitude, longitude, near_cable) VALUES (?,?,?,?,?)",
            (mmsi, ts, lat, lon, int(near_cable)),
        )


def _load_events() -> list[tuple]:
    try:
        with sqlite3.connect(LOITERING_DB) as c:
            return c.execute(
                "SELECT mmsi, timestamp, latitude, longitude, near_cable FROM loitering_events"
            ).fetchall()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Detection — called per position update
# ---------------------------------------------------------------------------

def update(mmsi: str, speed, lat: float, lon: float, near_cable: bool) -> None:
    """
    Feed a position update. Emits a loitering event if the vessel has been
    slow / stationary at the same location for longer than TIME_THRESHOLD_S.
    """
    now = datetime.now(timezone.utc)

    if mmsi not in _memory:
        _memory[mmsi] = {"start": now, "positions": [(lat, lon)], "near_cable": near_cable}
        return

    rec = _memory[mmsi]
    rec["positions"].append((lat, lon))
    if near_cable:
        rec["near_cable"] = True

    speed_val = float(speed) if speed is not None else 0.0

    if speed_val >= SPEED_THRESHOLD_KN:
        # Moving — reset
        _memory[mmsi] = {"start": now, "positions": [(lat, lon)], "near_cable": near_cable}
        return

    duration = (now - rec["start"]).total_seconds()
    if duration < TIME_THRESHOLD_S:
        return

    # Check the vessel hasn't drifted too far (still in same area)
    positions = rec["positions"]
    center_lat = sum(p[0] for p in positions) / len(positions)
    center_lon = sum(p[1] for p in positions) / len(positions)

    from geopy.distance import geodesic
    max_drift = max(geodesic((p[0], p[1]), (center_lat, center_lon)).km for p in positions)
    if max_drift > RADIUS_THRESHOLD_KM:
        # Drifting too far — not stationary loitering, reset
        _memory[mmsi] = {"start": now, "positions": [(lat, lon)], "near_cable": near_cable}
        return

    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    _save_event(mmsi, ts, center_lat, center_lon, rec["near_cable"])
    # Reset after logging
    _memory[mmsi] = {"start": now, "positions": [(lat, lon)], "near_cable": near_cable}


# ---------------------------------------------------------------------------
# Map annotation — called during map render
# ---------------------------------------------------------------------------

def add_to_map(m: folium.Map) -> None:
    """Add loitering markers and heatmap to a folium Map object."""
    events = _load_events()
    if not events:
        return

    heat_data = []
    for mmsi, ts, lat, lon, nc in events:
        color = "red" if nc else "gray"
        folium.Marker(
            [lat, lon],
            popup=f"<b>Loitering</b><br>MMSI: {mmsi}<br>Time: {ts}<br>Near cable: {bool(nc)}",
            icon=folium.Icon(color=color, icon="exclamation-sign"),
        ).add_to(m)
        heat_data.append([lat, lon])

    HeatMap(heat_data, radius=20, blur=15, min_opacity=0.3).add_to(m)


# ---------------------------------------------------------------------------
# Export helpers (optional, CLI use)
# ---------------------------------------------------------------------------

def export_csv(path: str = "loitering_events.csv") -> None:
    import csv
    events = _load_events()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mmsi", "timestamp", "latitude", "longitude", "near_cable"])
        w.writerows(events)


def export_geojson(path: str = "loitering_events.geojson") -> None:
    import json
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"mmsi": mmsi, "timestamp": ts, "near_cable": bool(nc)},
        }
        for mmsi, ts, lat, lon, nc in _load_events()
    ]
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
