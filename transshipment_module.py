"""
transshipment_module.py — Former Lab / Shadow Fleet Tracker

Detects port calls and flags two transshipment patterns:
  RU → WEST  vessel called at a Russian port then a Western hub within WINDOW_DAYS
  WEST → RU  vessel called at a Western hub then a Russian port within WINDOW_DAYS

Port calls are inferred from position pings — no external API required.
A call is recorded when a vessel spends time inside a port zone at low speed.
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from geopy.distance import geodesic

DB = "transshipment.db"

# ---------------------------------------------------------------------------
# Port zones: name -> (lat, lon, radius_km)
# Skaw/Skagen radius is large because vessels often anchor in the approaches
# ---------------------------------------------------------------------------

PORTS: dict[str, tuple[float, float, float]] = {
    # Russian export terminals
    "Ust-Luga":        (59.687, 28.230, 15.0),
    "Primorsk":        (60.357, 28.620, 12.0),
    "St Petersburg":   (59.900, 30.250, 20.0),
    "Vyborg":          (60.700, 28.750, 10.0),
    # Western transshipment / entry points
    "Skaw/Skagen":     (57.750, 10.650, 25.0),
    "Gothenburg":      (57.680, 11.900, 15.0),
    "Kiel":            (54.330, 10.150, 12.0),
    "Copenhagen":      (55.680, 12.600, 12.0),
    "Aarhus":          (56.150, 10.210, 10.0),
}

RUSSIAN_PORTS = {"Ust-Luga", "Primorsk", "St Petersburg", "Vyborg"}
WESTERN_HUBS  = {"Skaw/Skagen", "Gothenburg", "Kiel", "Copenhagen", "Aarhus"}

SPEED_THRESHOLD_KN = 1.5   # below this inside a zone → counts as port activity
MIN_PINGS_IN_ZONE  = 2     # minimum pings before recording a port call
WINDOW_DAYS        = 21    # max days between port calls to flag as transshipment

# In-memory state per MMSI: tracks current zone presence
# mmsi -> {port, entry_ts, pings, min_speed}
_state: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def init_db() -> None:
    with sqlite3.connect(DB) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS port_calls (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi      TEXT,
            name      TEXT,
            port      TEXT,
            port_type TEXT,
            entry_ts  TEXT,
            exit_ts   TEXT,
            min_speed REAL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS transshipment_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi         TEXT,
            name         TEXT,
            direction    TEXT,
            from_port    TEXT,
            from_exit_ts TEXT,
            to_port      TEXT,
            to_entry_ts  TEXT,
            days_between REAL,
            detected_ts  TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pc_mmsi ON port_calls(mmsi)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pc_exit ON port_calls(exit_ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ts_mmsi ON transshipment_events(mmsi)")


def _port_type(port: str) -> str:
    if port in RUSSIAN_PORTS:
        return "russian"
    if port in WESTERN_HUBS:
        return "western"
    return "other"


def _save_port_call(mmsi: str, name: str, port: str, entry_ts: str,
                    exit_ts: str, min_speed: float) -> None:
    ptype = _port_type(port)
    with sqlite3.connect(DB) as c:
        c.execute(
            """INSERT INTO port_calls (mmsi, name, port, port_type, entry_ts, exit_ts, min_speed)
               VALUES (?,?,?,?,?,?,?)""",
            (mmsi, name, port, ptype, entry_ts, exit_ts, min_speed),
        )


def _detect_transshipments(mmsi: str, name: str, completed_port: str,
                            completed_exit: str) -> None:
    """
    After a port call closes, look for a matching call on the other side
    within WINDOW_DAYS to flag a transshipment event.
    """
    completed_type = _port_type(completed_port)
    if completed_type not in ("russian", "western"):
        return

    # Determine what we're looking for as the counterpart
    if completed_type == "russian":
        partner_type = "western"
        direction_template = "{} → {}"   # other→RU already happened; this RU is "to"
    else:
        partner_type = "russian"
        direction_template = "{} → {}"

    try:
        exit_dt = datetime.strptime(completed_exit, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return

    window_start = (exit_dt - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    window_end   = (exit_dt + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB) as c:
        # Find partner calls within the time window that don't already have
        # a transshipment event recorded against them
        partners = c.execute("""
            SELECT port, entry_ts, exit_ts FROM port_calls
            WHERE mmsi = ?
              AND port_type = ?
              AND exit_ts IS NOT NULL
              AND exit_ts BETWEEN ? AND ?
              AND port != ?
        """, (mmsi, partner_type, window_start, window_end, completed_port)).fetchall()

        for p_port, p_entry, p_exit in partners:
            # Work out direction and chronological order
            try:
                p_exit_dt = datetime.strptime(p_exit, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

            if completed_type == "russian":
                # partner is western — which came first?
                if p_exit_dt <= exit_dt:
                    # WEST → RU
                    from_port, from_exit = p_port, p_exit
                    to_port,   to_entry  = completed_port, p_exit   # entry not stored separately
                    direction = "WEST→RU"
                else:
                    # RU → WEST
                    from_port, from_exit = completed_port, completed_exit
                    to_port,   to_entry  = p_port, p_entry
                    direction = "RU→WEST"
            else:
                if p_exit_dt <= exit_dt:
                    # RU → WEST
                    from_port, from_exit = p_port, p_exit
                    to_port,   to_entry  = completed_port, p_entry
                    direction = "RU→WEST"
                else:
                    # WEST → RU
                    from_port, from_exit = completed_port, completed_exit
                    to_port,   to_entry  = p_port, p_entry
                    direction = "WEST→RU"

            # Deduplicate — skip if this pair already recorded
            existing = c.execute("""
                SELECT id FROM transshipment_events
                WHERE mmsi=? AND from_port=? AND to_port=?
                  AND from_exit_ts=?
            """, (mmsi, from_port, to_port, from_exit)).fetchone()
            if existing:
                continue

            try:
                from_dt = datetime.strptime(from_exit, "%Y-%m-%d %H:%M:%S")
                to_dt   = datetime.strptime(to_entry,  "%Y-%m-%d %H:%M:%S")
                days = abs((to_dt - from_dt).total_seconds()) / 86400
            except ValueError:
                days = 0.0

            c.execute("""
                INSERT INTO transshipment_events
                    (mmsi, name, direction, from_port, from_exit_ts,
                     to_port, to_entry_ts, days_between, detected_ts)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (mmsi, name, direction, from_port, from_exit,
                  to_port, to_entry, round(days, 1), now_ts))


# ---------------------------------------------------------------------------
# Main update — called per position ping from shadow_tracker
# ---------------------------------------------------------------------------

def update(mmsi: str, speed: float | None, lat: float, lon: float,
           ts: str, name: str) -> None:
    """
    Feed one position ping. Manages in-memory port zone state and closes
    port calls when vessels exit zones.
    """
    spd = float(speed) if speed is not None else 999.0

    # Which port zone is the vessel currently inside? (first match)
    current_port: str | None = None
    for port, (plat, plon, radius) in PORTS.items():
        if geodesic((lat, lon), (plat, plon)).km <= radius:
            current_port = port
            break

    rec = _state.get(mmsi)

    if current_port and spd <= SPEED_THRESHOLD_KN:
        # Inside a zone at low speed
        if rec and rec["port"] == current_port:
            # Continuing same call
            rec["pings"] += 1
            rec["last_ts"] = ts
            rec["min_speed"] = min(rec["min_speed"], spd)
        else:
            # New zone entry (or switched zones — close old one first)
            if rec and rec["pings"] >= MIN_PINGS_IN_ZONE:
                _save_port_call(mmsi, name, rec["port"],
                                rec["entry_ts"], rec["last_ts"], rec["min_speed"])
                _detect_transshipments(mmsi, name, rec["port"], rec["last_ts"])
            _state[mmsi] = {
                "port":      current_port,
                "entry_ts":  ts,
                "last_ts":   ts,
                "pings":     1,
                "min_speed": spd,
            }
    else:
        # Outside all zones (or moving too fast)
        if rec and rec["pings"] >= MIN_PINGS_IN_ZONE:
            _save_port_call(mmsi, name, rec["port"],
                            rec["entry_ts"], rec["last_ts"], rec["min_speed"])
            _detect_transshipments(mmsi, name, rec["port"], rec["last_ts"])
        if mmsi in _state:
            del _state[mmsi]


# ---------------------------------------------------------------------------
# Query helpers for webserver
# ---------------------------------------------------------------------------

def get_recent_events(limit: int = 200,
                      direction: str | None = None) -> list[dict]:
    if not __import__("os").path.exists(DB):
        return []
    conditions, params = [], []
    if direction:
        conditions.append("direction = ?")
        params.append(direction)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    with sqlite3.connect(DB) as c:
        rows = c.execute(f"""
            SELECT mmsi, name, direction, from_port, from_exit_ts,
                   to_port, to_entry_ts, days_between, detected_ts
            FROM transshipment_events {where}
            ORDER BY detected_ts DESC LIMIT ?
        """, params).fetchall()
    return [
        dict(zip(
            ["mmsi","name","direction","from_port","from_exit_ts",
             "to_port","to_entry_ts","days_between","detected_ts"], r
        ))
        for r in rows
    ]


def get_recent_port_calls(limit: int = 100) -> list[dict]:
    if not __import__("os").path.exists(DB):
        return []
    with sqlite3.connect(DB) as c:
        rows = c.execute("""
            SELECT mmsi, name, port, port_type, entry_ts, exit_ts, min_speed
            FROM port_calls
            WHERE exit_ts IS NOT NULL
            ORDER BY exit_ts DESC LIMIT ?
        """, (limit,)).fetchall()
    return [
        dict(zip(
            ["mmsi","name","port","port_type","entry_ts","exit_ts","min_speed"], r
        ))
        for r in rows
    ]


def get_stats() -> dict:
    if not __import__("os").path.exists(DB):
        return {"ru_west": 0, "west_ru": 0, "vessels": 0, "port_calls": 0}
    with sqlite3.connect(DB) as c:
        ru_west  = c.execute("SELECT COUNT(*) FROM transshipment_events WHERE direction='RU→WEST'").fetchone()[0]
        west_ru  = c.execute("SELECT COUNT(*) FROM transshipment_events WHERE direction='WEST→RU'").fetchone()[0]
        vessels  = c.execute("SELECT COUNT(DISTINCT mmsi) FROM transshipment_events").fetchone()[0]
        calls    = c.execute("SELECT COUNT(*) FROM port_calls WHERE exit_ts IS NOT NULL").fetchone()[0]
    return {"ru_west": ru_west, "west_ru": west_ru, "vessels": vessels, "port_calls": calls}
