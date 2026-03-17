"""
shadow_tracker.py — Former Lab / Shadow Fleet Tracker
AISStream WebSocket consumer for Baltic Sea shadow fleet monitoring.
"""

import asyncio
import websockets
import json
import sqlite3
import math
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import escape

import aiohttp
import folium
from folium import MacroElement
from branca.element import Figure
from jinja2 import Template
from geopy.distance import geodesic

import loitering_module
import transshipment_module

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AISSTREAM_API_KEY = os.getenv("AISSTREAM_API_KEY", "")
AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

# Baltic Sea bounding box — tighter than the old version (dropped Arctic noise)
BALTIC_BBOX = {
    "north": 66.0,
    "south": 52.65,
    "west":  9.0,
    "east":  30.0,
}

CABLE_ALERT_KM        = 10      # proximity threshold for cable alert
MAP_RENDER_INTERVAL   = 180     # seconds between map refreshes
RECONNECT_DELAY       = 10      # seconds before WS reconnect on error
MMSI_RELOAD_INTERVAL  = 300     # seconds between watchlist reloads from DB

OPENSANCTIONS_API_KEY = os.getenv("OPENSANCTIONS_API_KEY", "")
OPENSANCTIONS_URL     = "https://api.opensanctions.org/match/sanctions"

# In-memory OpenSanctions cache: imo -> {sanctions: [...], fetched: bool}
_sanctions_cache: dict[str, dict] = {}

VESSELS_DB     = "Vessels1.db"
LOG_DB         = "vessel_data_log.db"
STATIC_DB      = "vessel_static.db"
CABLES_KML     = "filtered_cables.kml"
MAP_OUTPUT     = "vessel_tracking_map_latest.html"
LOG_FILE       = "vessel_log.txt"
GUR_MAPPING    = "gur_mapping.json"   # IMO → GUR ship ID, produced by gur_scrape.py

GUR_BASE_URL   = "https://war-sanctions.gur.gov.ua/en/transport/ships/{id}"
GUR_SEARCH_URL = "https://www.google.com/search?q=site:war-sanctions.gur.gov.ua+{imo}"

def load_gur_mapping() -> dict[str, int]:
    """Load IMO→GUR-ID mapping from gur_mapping.json if it exists."""
    if not os.path.exists(GUR_MAPPING):
        return {}
    try:
        with open(GUR_MAPPING) as f:
            data = json.load(f)
        log(f"GUR mapping loaded: {len(data)} vessels")
        return data
    except Exception as e:
        log(f"GUR mapping load failed: {e}")
        return {}

_gur_mapping: dict[str, int] = {}   # populated in run() after load


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} - {message}"
    print(line)
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 10 * 1024 * 1024:
        os.rename(LOG_FILE, LOG_FILE + ".bak")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_dbs() -> None:
    with _connect(VESSELS_DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS vessels (
            mmsi TEXT PRIMARY KEY, imo TEXT, name TEXT, destination TEXT
        )""")

    with _connect(LOG_DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS vessel_data_log (
            timestamp   TEXT,
            mmsi        TEXT,
            name        TEXT,
            imo         TEXT,
            destination TEXT,
            speed       REAL,
            heading     REAL,
            latitude    REAL,
            longitude   REAL,
            cable_alert TEXT
        )""")

    with _connect(STATIC_DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS vessel_static (
            mmsi        TEXT PRIMARY KEY,
            name        TEXT,
            imo         TEXT,
            destination TEXT,
            flag        TEXT
        )""")
        # Migrate existing DBs that lack the flag column
        cols = [r[1] for r in c.execute("PRAGMA table_info(vessel_static)").fetchall()]
        if "flag" not in cols:
            c.execute("ALTER TABLE vessel_static ADD COLUMN flag TEXT")

        c.execute("""CREATE TABLE IF NOT EXISTS flag_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi      TEXT,
            flag      TEXT,
            timestamp TEXT
        )""")

    loitering_module.init_db()
    transshipment_module.init_db()


def load_watchlist() -> dict[str, str | None]:
    """Returns {mmsi: name_or_None} for all watched vessels."""
    with _connect(VESSELS_DB) as c:
        rows = c.execute("SELECT mmsi, name FROM vessels").fetchall()
    return {r[0]: (r[1].strip() if r[1] else None) for r in rows}


def save_static(mmsi: str, name: str, imo: str, destination: str, flag: str = "") -> None:
    with _connect(STATIC_DB) as c:
        c.execute(
            "INSERT OR REPLACE INTO vessel_static (mmsi, name, imo, destination, flag) VALUES (?,?,?,?,?)",
            (mmsi, name, imo, destination, flag),
        )


def load_static(mmsi: str) -> dict:
    with _connect(STATIC_DB) as c:
        row = c.execute(
            "SELECT name, imo, destination, flag FROM vessel_static WHERE mmsi=?", (mmsi,)
        ).fetchone()
    return {"name": row[0], "imo": row[1], "destination": row[2], "flag": row[3]} if row else {}


def record_flag_change(mmsi: str, new_flag: str, ts: str) -> None:
    """Append a flag change event only when the flag differs from the last recorded one."""
    with _connect(STATIC_DB) as c:
        last = c.execute(
            "SELECT flag FROM flag_history WHERE mmsi=? ORDER BY id DESC LIMIT 1", (mmsi,)
        ).fetchone()
        if last is None or last[0] != new_flag:
            c.execute(
                "INSERT INTO flag_history (mmsi, flag, timestamp) VALUES (?,?,?)",
                (mmsi, new_flag, ts),
            )
            if last is not None:
                log(f"FLAG CHANGE — {mmsi}: {last[0]} → {new_flag}")


def flush_log(vessels: list[dict]) -> None:
    with _connect(LOG_DB) as c:
        c.executemany(
            """INSERT INTO vessel_data_log
               (timestamp, mmsi, name, imo, destination, speed, heading, latitude, longitude, cable_alert)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    v["timestamp"], v["mmsi"], v["name"], v["imo"],
                    v["destination"], v["speed"], v["heading"],
                    v["latitude"], v["longitude"], v["cable_alert"],
                )
                for v in vessels
            ],
        )


# ---------------------------------------------------------------------------
# KML / cable helpers
# ---------------------------------------------------------------------------

def load_cables(path: str) -> list[dict]:
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    cables = []
    for pm in root.findall(".//kml:Placemark", ns):
        name_el = pm.find("kml:name", ns)
        coords_el = pm.find(".//kml:coordinates", ns)
        if coords_el is None:
            continue
        coords_text = coords_el.text or ""
        coords = [
            (float(c.split(",")[1]), float(c.split(",")[0]))
            for c in coords_text.strip().split()
        ]
        cables.append({"name": name_el.text if name_el is not None else "", "coords": coords})
    return cables


def dist_to_segment(lat, lon, lat1, lon1, lat2, lon2) -> float:
    """Minimum geodesic distance (km) from point to a line segment."""
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    denom = dlat ** 2 + dlon ** 2
    if denom == 0:
        return geodesic((lat, lon), (lat1, lon1)).km
    u = ((lat - lat1) * dlat + (lon - lon1) * dlon) / denom
    u = max(0.0, min(1.0, u))
    return geodesic((lat, lon), (lat1 + u * dlat, lon1 + u * dlon)).km


def near_cable(lat: float, lon: float, cables: list[dict], threshold_km: float) -> bool:
    for cable in cables:
        coords = cable["coords"]
        for i in range(len(coords) - 1):
            if dist_to_segment(lat, lon, *coords[i], *coords[i + 1]) <= threshold_km:
                return True
    return False


async def fetch_sanctions(imo: str) -> list[dict]:
    """
    Query OpenSanctions /match/sanctions for a vessel by IMO.
    Returns a list of sanction dicts: [{programs: [...], authorities: [...]}]
    Result is cached in _sanctions_cache keyed by IMO.
    No-ops silently if OPENSANCTIONS_API_KEY is not set.
    """
    if not OPENSANCTIONS_API_KEY or not imo or imo == "N/A":
        return []
    if imo in _sanctions_cache:
        return _sanctions_cache[imo].get("sanctions", [])

    payload = {
        "queries": {
            imo: {
                "schema": "Vessel",
                "properties": {"imoNumber": [imo]},
            }
        }
    }
    headers = {
        "Authorization": f"ApiKey {OPENSANCTIONS_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENSANCTIONS_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    _sanctions_cache[imo] = {"sanctions": []}
                    return []
                data = await resp.json()
                results = data.get("responses", {}).get(imo, {}).get("results", [])
                sanctions = []
                for r in results:
                    if r.get("score", 0) < 0.8:
                        continue
                    props = r.get("properties", {})
                    programs    = props.get("program", [])
                    authorities = props.get("authority", [])
                    sanctions.append({"programs": programs, "authorities": authorities})
                _sanctions_cache[imo] = {"sanctions": sanctions}
                return sanctions
    except Exception as e:
        log(f"[OpenSanctions] {imo}: {e}")
        _sanctions_cache[imo] = {"sanctions": []}
        return []


# ---------------------------------------------------------------------------
# Map rendering
# ---------------------------------------------------------------------------

def build_map(vessel_info: dict, cables: list[dict]) -> folium.Map:
    m = folium.Map(location=[58.5, 20.0], zoom_start=6, tiles="CartoDB dark_matter")

    # Cables
    for cable in cables:
        folium.PolyLine(
            locations=cable["coords"], color="#00ff88", weight=2, opacity=0.6,
            tooltip=cable["name"],
        ).add_to(m)

    # Vessels
    for v in vessel_info.values():
        route = v.get("route", [])
        if not route:
            continue

        alert = v["cable_alert"] == "Yes"
        stale = v.get("stale", False)
        mmsi  = escape(v['mmsi'])
        imo   = escape(v['imo'])
        name  = escape(v['name'])

        mt_link  = f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{v['mmsi']}"
        vf_link  = f"https://www.vesselfinder.com/vessels/details/{v['imo']}" if v['imo'] != 'N/A' else None
        if v['imo'] != 'N/A':
            gur_id = _gur_mapping.get(v['imo'])
            gur_link = (
                GUR_BASE_URL.format(id=gur_id)
                if gur_id
                else GUR_SEARCH_URL.format(imo=v['imo'])
            )
        else:
            gur_link = None

        ext_links = f'<a href="{mt_link}" target="_blank" style="color:#00aaff;">MarineTraffic</a>'
        if vf_link:
            ext_links += f' · <a href="{vf_link}" target="_blank" style="color:#00aaff;">VesselFinder</a>'
        if gur_link:
            ext_links += f' · <a href="{gur_link}" target="_blank" style="color:#ffaa00;">War&amp;Sanctions</a>'

        # Stale indicator — data from a previous session
        stale_html = ""
        if stale:
            try:
                last_dt = datetime.strptime(v['timestamp'], "%Y-%m-%d %H:%M:%S")
                age_h   = (datetime.now(timezone.utc).replace(tzinfo=None) - last_dt).total_seconds() / 3600
                age_str = f"{int(age_h)}h ago" if age_h >= 1 else f"{int(age_h*60)}m ago"
                stale_html = f"<span style='color:#888;font-size:11px;'>⏱ last seen {age_str}</span><br>"
            except Exception:
                stale_html = "<span style='color:#888;font-size:11px;'>⏱ pre-restart data</span><br>"

        # Sanctions block — from cache (populated async during render cycle)
        sanctions = _sanctions_cache.get(v['imo'], {}).get("sanctions", [])
        if sanctions:
            programs = ", ".join(
                p for s in sanctions for p in s.get("programs", [])
            ) or "sanctioned"
            sanctions_html = f"<span style='color:#ff4444;font-weight:bold;'>⚑ SANCTIONED</span> <span style='color:#888;font-size:11px;'>{escape(programs)}</span><br>"
        else:
            sanctions_html = ""

        # Flag from static cache
        flag = v.get("flag", "")
        flag_html = f"<span style='color:#888;'>Flag &nbsp;</span> {escape(flag)}<br>" if flag and flag != "N/A" else ""

        popup_html = f"""
        <div style='font-family:monospace;font-size:12px;min-width:220px;'>
        <b style='font-size:13px;'>{name if name != 'N/A' else '⚠ Name unknown'}</b><br>
        {stale_html}{sanctions_html}<span style='color:#888;'>MMSI</span> {mmsi}<br>
        <span style='color:#888;'>IMO &nbsp;</span> {imo}<br>
        {flag_html}<span style='color:#888;'>Dest &nbsp;</span> {escape(v['destination'])}<br>
        <span style='color:#888;'>Speed</span> {escape(str(v['speed']))} kn &nbsp;
        <span style='color:#888;'>Hdg</span> {escape(str(v['heading']))}°<br>
        <span style='color:#888;'>Updated</span> {escape(v['timestamp'])}<br>
        <hr style='border:none;border-top:1px solid #333;margin:5px 0;'>
        {ext_links}<br>
        {"<span style='color:#ff4444;font-weight:bold;'>⚠ CABLE ALERT</span>" if alert else ""}
        </div>"""
        popup = folium.Popup(popup_html, max_width=320)

        # Route trail — dimmed for stale data
        trail_color = "#2a3a55" if stale else "#4488ff"
        if len(route) > 1:
            folium.PolyLine(route, color=trail_color, weight=1.5, opacity=0.5).add_to(m)
            for pt in route[:-1]:
                folium.CircleMarker(
                    pt, radius=2, color=trail_color, fill=True, fill_opacity=0.3
                ).add_to(m)

        # Current position marker — grey for stale, normal colour for live
        if stale:
            marker_color = "gray"
        elif alert:
            marker_color = "red"
        else:
            marker_color = "orange"
        folium.Marker(
            route[-1],
            popup=popup,
            tooltip=f"{v['name'] or v['mmsi']}{'  (stale)' if stale else ''}",
            icon=folium.Icon(color=marker_color, icon="ship", prefix="fa"),
        ).add_to(m)

    # Live countdown overlay — counts down to next map refresh
    # Note: reload is handled by the wrapper page (webserver.py /map)
    # The iframe JS only drives the countdown display, no reload here.
    render_ts_iso  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    render_ts_disp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = f"""{{% macro html(this, kwargs) %}}
    <div id="sft-bar" style="position:fixed;bottom:8px;left:8px;background:rgba(0,0,0,0.72);
                color:#ccc;padding:6px 12px;font-family:monospace;font-size:12px;
                border-radius:4px;z-index:9999;display:flex;gap:14px;align-items:center;">
        <span>&#9672; Shadow Fleet Tracker</span>
        <span style="color:#888;">rendered {render_ts_disp}</span>
        <span>next update <span id="sft-cd" style="color:#00ff88;font-weight:bold;">-</span></span>
    </div>
    <script>
    (function(){{
        var rendered = new Date("{render_ts_iso}").getTime();
        var interval = {MAP_RENDER_INTERVAL} * 1000;
        var next = rendered + interval;
        function tick(){{
            var now = Date.now();
            var rem = Math.max(0, Math.round((next - now) / 1000));
            var m = Math.floor(rem / 60);
            var s = rem % 60;
            document.getElementById("sft-cd").textContent =
                (rem <= 0) ? "—" : (m > 0 ? m + "m " : "") + s + "s";
        }}
        tick();
        setInterval(tick, 1000);
    }})();
    </script>
    {{% endmacro %}}"""
    macro = MacroElement()
    macro._template = Template(html)
    m.get_root().add_child(macro)

    return m


def save_map(m: folium.Map) -> None:
    tmp = MAP_OUTPUT + ".tmp"
    m.save(tmp)
    os.replace(tmp, MAP_OUTPUT)


# ---------------------------------------------------------------------------
# Warm restart — pre-populate vessel_info from DB on startup
# ---------------------------------------------------------------------------

WARM_RESTART_HOURS = 24   # how many hours of history to load back in

def warm_restart() -> dict[str, dict]:
    """
    Read the last WARM_RESTART_HOURS hours of position data from vessel_data_log.db
    and reconstruct vessel_info so the map renders immediately on startup rather
    than starting blank.

    Returns a vessel_info dict ready to pass straight to build_map().
    Positions from a previous session are marked stale=True so the popup
    shows how old the data is and markers are visually dimmed.
    """
    if not os.path.exists(LOG_DB):
        return {}

    from datetime import timedelta
    cutoff_str = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=WARM_RESTART_HOURS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    vessel_info: dict[str, dict] = {}

    try:
        with _connect(LOG_DB) as c:
            rows = c.execute("""
                SELECT timestamp, mmsi, name, imo, destination,
                       speed, heading, latitude, longitude, cable_alert
                FROM vessel_data_log
                WHERE timestamp >= ?
                ORDER BY mmsi, timestamp ASC
            """, (cutoff_str,)).fetchall()
    except Exception as e:
        log(f"[warm_restart] DB read failed: {e}")
        return {}

    for row in rows:
        ts, mmsi, name, imo, dest, speed, heading, lat, lon, cable = row
        if lat is None or lon is None:
            continue

        prev  = vessel_info.get(mmsi, {})
        route = prev.get("route", [])
        route.append([lat, lon])

        vessel_info[mmsi] = {
            "mmsi":        mmsi,
            "name":        name or "N/A",
            "imo":         imo  or "N/A",
            "destination": dest or "N/A",
            "flag":        "",
            "speed":       speed,
            "heading":     heading,
            "latitude":    lat,
            "longitude":   lon,
            "timestamp":   ts,
            "route":       route,
            "cable_alert": cable or "No",
            "stale":       True,
        }

    if vessel_info:
        log(f"Warm restart: loaded {len(vessel_info)} vessels from last {WARM_RESTART_HOURS}h of logs")

    return vessel_info


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run() -> None:
    global _gur_mapping

    cables = load_cables(CABLES_KML)
    log(f"Loaded {len(cables)} cable segments from {CABLES_KML}")

    watchlist: dict[str, str | None] = load_watchlist()
    log(f"Watchlist: {len(watchlist)} vessels")

    _gur_mapping = load_gur_mapping()

    # Runtime state — pre-populated from DB so the map isn't blank on restart
    vessel_info: dict[str, dict] = warm_restart()
    static_cache: dict[str, dict] = {}  # mmsi -> {name, imo, destination}

    # Pre-seed static_cache with names already in Vessels1.db so they show
    # immediately in popups without waiting for a ShipStaticData AIS message.
    for mmsi, name in watchlist.items():
        if name:
            static_cache.setdefault(mmsi, {})["name"] = name
    # Also seed from warm-restart data so names are available immediately
    for mmsi, v in vessel_info.items():
        if v.get("name") and v["name"] != "N/A":
            static_cache.setdefault(mmsi, {})["name"] = v["name"]
        if v.get("imo") and v["imo"] != "N/A":
            static_cache.setdefault(mmsi, {})["imo"] = v["imo"]

    # Render an initial map from warm-restart data so the user sees something
    # immediately rather than a blank page while waiting for live pings.
    if vessel_info:
        log("Rendering warm-restart map…")
        m = build_map(vessel_info, cables)
        loitering_module.add_to_map(m)
        save_map(m)

    last_render   = 0.0
    last_reload   = 0.0

    while True:
        try:
            log("Connecting to AISStream…")
            async with websockets.connect(AISSTREAM_URL, ping_interval=30) as ws:
                subscribe_msg = {
                    "APIKey": AISSTREAM_API_KEY,
                    "BoundingBoxes": [
                        [
                            [BALTIC_BBOX["south"], BALTIC_BBOX["west"]],
                            [BALTIC_BBOX["north"], BALTIC_BBOX["east"]],
                        ]
                    ],
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
                }
                await ws.send(json.dumps(subscribe_msg))
                log("Subscribed to Baltic Sea bbox.")

                async for raw in ws:
                    now = datetime.now(timezone.utc)
                    ts  = now.strftime("%Y-%m-%d %H:%M:%S")
                    t   = now.timestamp()

                    # Reload watchlist and GUR mapping periodically
                    if t - last_reload > MMSI_RELOAD_INTERVAL:
                        new_wl = load_watchlist()
                        if new_wl != watchlist:
                            log(f"Watchlist updated: {len(new_wl)} vessels")
                            watchlist.clear()
                            watchlist.update(new_wl)
                            # Seed any newly added vessels that have names in the DB,
                            # but don't overwrite entries already populated from AIS.
                            for mmsi, name in watchlist.items():
                                if name and mmsi not in static_cache:
                                    static_cache[mmsi] = {"name": name}
                        new_gur = load_gur_mapping()
                        if new_gur != _gur_mapping:
                            _gur_mapping.clear()
                            _gur_mapping.update(new_gur)
                        last_reload = t

                    msg = json.loads(raw)
                    mtype = msg.get("MessageType")

                    # -- Static data (all vessels in bbox, cache only watchlist) --
                    if mtype == "ShipStaticData":
                        s    = msg["Message"]["ShipStaticData"]
                        mmsi = str(s.get("UserID", ""))
                        if not mmsi or mmsi not in watchlist:
                            continue
                        name = (s.get("Name") or "N/A").strip()
                        imo  = str(s.get("ImoNumber") or s.get("IMO") or "N/A")
                        dest = (s.get("Destination") or "N/A").strip()
                        flag = (s.get("Flag") or s.get("CountryCode") or "").strip()
                        # AIS static message wins — full override
                        static_cache[mmsi] = {"name": name, "imo": imo, "destination": dest, "flag": flag}
                        save_static(mmsi, name, imo, dest, flag)
                        if flag:
                            record_flag_change(mmsi, flag, ts)

                    # -- Position report --
                    elif mtype == "PositionReport":
                        p    = msg["Message"]["PositionReport"]
                        mmsi = str(p.get("UserID", ""))
                        lat  = p.get("Latitude")
                        lon  = p.get("Longitude")

                        if not mmsi or lat is None or lon is None:
                            continue
                        if mmsi not in watchlist:
                            continue

                        speed   = p.get("Sog") or p.get("Speed")
                        heading = p.get("TrueHeading") or p.get("Heading")
                        alert   = near_cable(lat, lon, cables, CABLE_ALERT_KM)

                        static = static_cache.get(mmsi) or load_static(mmsi)

                        # static_cache may be partially populated (name from DB only,
                        # no imo/destination yet). Fall back to vessel_static.db for
                        # any fields still missing.
                        if not static.get("imo") or not static.get("destination"):
                            persisted = load_static(mmsi)
                            static = {
                                "name":        static.get("name") or persisted.get("name", "N/A"),
                                "imo":         static.get("imo")  or persisted.get("imo",  "N/A"),
                                "destination": static.get("destination") or persisted.get("destination", "N/A"),
                            }

                        prev = vessel_info.get(mmsi, {})
                        route = prev.get("route", [])
                        route.append([lat, lon])

                        cable_alert = "Yes" if alert or prev.get("cable_alert") == "Yes" else "No"

                        vessel_info[mmsi] = {
                            "mmsi":        mmsi,
                            "name":        static.get("name", "N/A"),
                            "imo":         static.get("imo", "N/A"),
                            "destination": static.get("destination", "N/A"),
                            "flag":        static.get("flag", ""),
                            "speed":       speed,
                            "heading":     heading,
                            "latitude":    lat,
                            "longitude":   lon,
                            "timestamp":   ts,
                            "route":       route,
                            "cable_alert": cable_alert,
                            "stale":       False,  # live ping — no longer stale
                        }

                        if alert:
                            log(f"CABLE ALERT — {mmsi} ({static.get('name','?')}) at {lat:.4f},{lon:.4f}")

                        loitering_module.update(mmsi, speed, lat, lon, alert)
                        transshipment_module.update(
                            mmsi, speed, lat, lon, ts,
                            static.get("name", "N/A"),
                        )

                    # -- Render cycle --
                    if t - last_render > MAP_RENDER_INTERVAL:
                        active = len(vessel_info)
                        log(f"Rendering map — {active} vessels tracked")

                        # Fetch sanctions for any visible vessel whose IMO we know
                        # and hasn't been looked up yet this session.
                        if OPENSANCTIONS_API_KEY:
                            imos_to_check = [
                                v["imo"] for v in vessel_info.values()
                                if v["imo"] != "N/A" and v["imo"] not in _sanctions_cache
                            ]
                            if imos_to_check:
                                await asyncio.gather(
                                    *[fetch_sanctions(imo) for imo in imos_to_check],
                                    return_exceptions=True,
                                )

                        m = build_map(vessel_info, cables)
                        loitering_module.add_to_map(m)
                        save_map(m)
                        flush_log(list(vessel_info.values()))
                        last_render = t

        except websockets.exceptions.ConnectionClosed as e:
            log(f"WS closed: {e}. Reconnecting in {RECONNECT_DELAY}s…")
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            log(f"[ERROR] {type(e).__name__}: {e}")
            await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    init_dbs()
    asyncio.run(run())