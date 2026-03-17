"""
webserver.py — Former Lab / Shadow Fleet Tracker
FastAPI dashboard: map, log, vessel analysis, per-vessel history, GPX export.
"""

import csv
import os
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from io import StringIO
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

import transshipment_module

app = FastAPI(title="Shadow Fleet Tracker")

MAP_FILE          = "vessel_tracking_map_latest.html"
LOG_FILE          = "vessel_log.txt"
LOG_DB            = "vessel_data_log.db"
STATIC_DB         = "vessel_static.db"
LOITERING_DB      = "loitering_events.db"
VESSELS_DB        = "Vessels1.db"
TRANSSHIPMENT_DB  = "transshipment.db"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
:root {
    --bg: #0d0f14; --surface: #151820; --border: #252a35;
    --accent: #00ff88; --warn: #ff4444; --warn-dim: #7a1a1a;
    --info: #4488ff; --text: #c8cdd8; --dim: #5a6070;
    --font: 'JetBrains Mono', 'Fira Mono', monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--font); font-size: 13px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
nav {
    display: flex; align-items: center; gap: 16px;
    padding: 12px 20px; border-bottom: 1px solid var(--border);
    background: var(--surface); position: sticky; top: 0; z-index: 100;
}
nav .brand { color: var(--accent); font-weight: 700; font-size: 14px; letter-spacing: 0.05em; margin-right: auto; }
nav a { color: var(--dim); font-size: 12px; }
nav a:hover { color: var(--text); text-decoration: none; }
nav a.active { color: var(--accent); }
.btn {
    display: inline-block; padding: 6px 14px;
    background: transparent; border: 1px solid var(--border);
    color: var(--text); cursor: pointer; font-family: var(--font); font-size: 12px;
    border-radius: 3px; transition: border-color .15s, color .15s;
}
.btn:hover { border-color: var(--accent); color: var(--accent); text-decoration: none; }
.btn.primary { border-color: var(--accent); color: var(--accent); }
.btn.warn { border-color: var(--warn); color: var(--warn); }
.log-container {
    height: calc(100vh - 52px); overflow-y: auto;
    padding: 16px 20px; font-size: 12px; line-height: 1.6; white-space: pre-wrap;
}
.toolbar { display: flex; gap: 10px; align-items: center; padding: 10px 20px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.toolbar .spacer { flex: 1; }
.dim { color: var(--dim); }
.table-wrap { overflow: auto; padding: 0 20px 20px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th { padding: 8px 10px; text-align: left; color: var(--dim); border-bottom: 1px solid var(--border); white-space: nowrap; }
td { padding: 7px 10px; border-bottom: 1px solid var(--border); }
tr:hover td { background: var(--surface); }
.alert-yes { color: var(--warn); font-weight: 700; }
.alert-no { color: var(--dim); }
.filters {
    display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end;
    padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--surface);
}
.filters label { color: var(--dim); font-size: 11px; display: flex; flex-direction: column; gap: 3px; }
.filters input, .filters select {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 4px 8px; font-family: var(--font); font-size: 12px; border-radius: 3px;
}
.filters input:focus, .filters select:focus { outline: none; border-color: var(--accent); }
.filters .check-label { flex-direction: row; align-items: center; gap: 6px; margin-bottom: 2px; }
.page-content { padding: 20px; max-width: 1100px; }
.section { margin-bottom: 32px; }
.section-title {
    font-size: 11px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 6px; margin-bottom: 14px;
}
.kv-grid { display: grid; grid-template-columns: 180px 1fr; gap: 4px 12px; font-size: 12px; }
.kv-grid .k { color: var(--dim); }
.kv-grid .v { color: var(--text); }
.prox-bar-wrap { display: flex; flex-direction: column; gap: 6px; }
.prox-row { display: flex; align-items: center; gap: 10px; font-size: 11px; }
.prox-bar-bg { flex: 1; height: 14px; background: var(--border); border-radius: 2px; overflow: hidden; }
.prox-bar-fill { height: 100%; border-radius: 2px; }
.loiter-list { display: flex; flex-direction: column; gap: 6px; }
.loiter-item {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 8px 12px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 4px; font-size: 12px;
}
.loiter-item.near-cable { border-color: var(--warn); }
.loiter-badge { font-size: 10px; padding: 2px 6px; border-radius: 2px; background: var(--warn); color: #000; font-weight: 700; white-space: nowrap; }
.loiter-coords { color: var(--dim); font-size: 11px; }
.vessel-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; padding: 20px; }
.vessel-card {
    padding: 12px 14px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 4px; transition: border-color .15s; display: block; color: inherit;
}
.vessel-card:hover { border-color: var(--accent); text-decoration: none; }
.vessel-card .vname { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
.vessel-card .vmeta { font-size: 11px; color: var(--dim); line-height: 1.7; }
.vessel-card.has-alert { border-color: var(--warn); }
.vessel-card .last-seen { font-size: 10px; color: var(--dim); margin-top: 6px; }
"""


def _nav(active: str = "") -> str:
    links = [
        ("map",            "/map",            "Map"),
        ("log",            "/",               "Log"),
        ("analyze",        "/analyze",        "Vessels"),
        ("timeline",       "/timeline",       "Timeline"),
        ("loiter",         "/loitering",      "Loitering"),
        ("transshipment",  "/transshipment",  "Transshipment"),
    ]
    items = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for key, href, label in links
    )
    return f'<nav><span class="brand">◈ SHADOW FLEET TRACKER</span>{items}</nav>'


def _page(title: str, body: str, active: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Shadow Fleet Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head><body>
{_nav(active)}
{body}
</body></html>""")


def _vessel_name(mmsi: str) -> str:
    for db, table, col in [
        (STATIC_DB,  "vessel_static", "name"),
        (VESSELS_DB, "vessels",       "name"),
    ]:
        if os.path.exists(db):
            with sqlite3.connect(db) as c:
                row = c.execute(f"SELECT {col} FROM {table} WHERE mmsi=?", (mmsi,)).fetchone()
                if row and row[0] and str(row[0]).strip():
                    return str(row[0]).strip()
    return mmsi


def _query_log(
    alert_only: bool = False,
    mmsi: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    sort: str = "timestamp",
    limit: int = 200,
) -> list:
    if not os.path.exists(LOG_DB):
        return []
    allowed_sorts = {"timestamp", "speed", "heading", "mmsi"}
    sort_col = sort if sort in allowed_sorts else "timestamp"
    conditions, params = [], []
    if alert_only:
        conditions.append("cable_alert = 'Yes'")
    if mmsi:
        conditions.append("mmsi = ?"); params.append(mmsi)
    if start:
        conditions.append("timestamp >= ?"); params.append(start)
    if end:
        conditions.append("timestamp <= ?"); params.append(end)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = (f"SELECT timestamp, mmsi, name, imo, destination, speed, heading, "
           f"latitude, longitude, cable_alert FROM vessel_data_log {where} "
           f"ORDER BY {sort_col} DESC LIMIT ?")
    params.append(limit)
    with sqlite3.connect(LOG_DB) as c:
        return c.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Routes — standard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = f"""
<div class="toolbar">
    <a href="/map" class="btn primary">Open Map</a>
    <a href="/log/download" class="btn">Download Log</a>
    <a href="/analyze" class="btn">Vessel DB</a>
    <a href="/timeline" class="btn">Timeline</a>
    <a href="/loitering" class="btn">Loitering</a>
    <a href="/transshipment" class="btn">Transshipment</a>
    <span class="spacer"></span>
    <span class="dim">Auto-refresh 60s · {ts}</span>
</div>
<pre id="log" class="log-container">Loading...</pre>
<script>
function load() {{
    fetch('/log').then(r => r.text())
        .then(t => document.getElementById('log').textContent = t)
        .catch(e => document.getElementById('log').textContent = 'Error: ' + e);
}}
load(); setInterval(load, 60000);
</script>"""
    return _page("Log", body, "log")


@app.get("/log", response_class=PlainTextResponse)
async def get_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8") as f:
            return f.read()
    return "No log yet."


@app.get("/log/download")
async def download_log():
    if os.path.exists(LOG_FILE):
        return FileResponse(LOG_FILE, filename="vessel_log.txt", media_type="text/plain")
    return HTMLResponse("Log not found.", status_code=404)


@app.get("/map")
async def serve_map():
    if not os.path.exists(MAP_FILE):
        return HTMLResponse("Map not rendered yet.", status_code=404)
    nav = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0f14; font-family: 'JetBrains Mono', monospace; }
.nav { display: flex; align-items: center; gap: 10px; padding: 6px 12px;
       background: #151820; border-bottom: 1px solid #252a35; height: 38px; }
.nav a { color: #00ff88; text-decoration: none; font-size: 12px;
         padding: 4px 10px; border: 1px solid #252a35; border-radius: 4px; }
.nav a:hover { border-color: #00ff88; }
.nav .dim { color: #5a6070; font-size: 11px; margin-left: auto; }
#map-container { position: relative; width: 100%; height: calc(100vh - 38px); background: #0d0f14; }
iframe { position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; transition: opacity 0.5s ease; }
</style></head><body>
<div class="nav">
  <a href="/">← Home</a>
  <a href="/analyze">Vessel DB</a>
  <a href="/timeline">Timeline</a>
  <a href="/loitering">Loitering</a>
  <a href="/transshipment">Transshipment</a>
  <span class="dim">Live map — updates every 3 min</span>
</div>
<div id="map-container">
  <iframe id="map-frame" src="/map/raw" onload="this.style.opacity='1';" style="opacity:0;"></iframe>
</div>
<script>
// Poll for map updates — when the file changes, crossfade to a fresh iframe
var currentTs = null;

function checkForUpdate() {{
    fetch('/map/timestamp')
        .then(function(r) {{ return r.text(); }})
        .then(function(ts) {{
            if (currentTs === null) {{ currentTs = ts; return; }}
            if (ts !== currentTs) {{
                currentTs = ts;
                var old = document.getElementById('map-frame');
                var fresh = document.createElement('iframe');
                fresh.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;border:none;opacity:0;transition:opacity 0.5s ease;';
                fresh.src = '/map/raw?_=' + Date.now();
                fresh.onload = function() {{
                    fresh.style.opacity = '1';
                    setTimeout(function() {{
                        if (old.parentNode) old.parentNode.removeChild(old);
                        fresh.id = 'map-frame';
                    }}, 600);
                }};
                document.getElementById('map-container').appendChild(fresh);
            }}
        }})
        .catch(function() {{}});  // silently ignore network errors
}}

// Poll every 15s — catches new renders promptly without hammering the server
checkForUpdate();
setInterval(checkForUpdate, 15000);
</script>
</body></html>"""
    return HTMLResponse(nav)


@app.get("/map/raw")
async def serve_map_raw():
    if os.path.exists(MAP_FILE):
        return FileResponse(MAP_FILE, media_type="text/html")
    return HTMLResponse("Map not rendered yet.", status_code=404)


@app.get("/map/timestamp")
async def map_timestamp():
    if os.path.exists(MAP_FILE):
        return PlainTextResponse(str(int(os.path.getmtime(MAP_FILE))))
    return PlainTextResponse("0")


# ---------------------------------------------------------------------------
# /analyze
# ---------------------------------------------------------------------------

@app.get("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    alert: Optional[str] = Query(None),
    mmsi: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    sort: str = Query("timestamp"),
    limit: int = Query(200),
):
    rows = _query_log(alert_only=alert == "yes", mmsi=mmsi, start=start, end=end, sort=sort, limit=limit)
    qs = request.url.query
    export_url = f"/analyze/export?{qs}" if qs else "/analyze/export"
    alert_checked = "checked" if alert == "yes" else ""

    table_rows = ""
    for r in rows:
        ts, _mmsi, name, imo, dest, speed, heading, lat, lon, cable = r
        cls = "alert-yes" if cable == "Yes" else "alert-no"
        vessel_link = f'<a href="/vessel/{_mmsi}">{_mmsi}</a>'
        table_rows += (
            f'<tr><td>{ts}</td><td>{vessel_link}</td><td>{name or "—"}</td><td>{imo or "—"}</td>'
            f'<td>{dest or "—"}</td><td>{speed}</td><td>{heading}</td>'
            f'<td>{float(lat):.4f}</td><td>{float(lon):.4f}</td>'
            f'<td class="{cls}">{cable}</td></tr>'
        )

    body = f"""
<div class="filters">
    <form method="get" action="/analyze" style="display:contents;">
        <label>MMSI<input name="mmsi" value="{mmsi or ''}" placeholder="all"></label>
        <label>Start<input type="datetime-local" name="start" value="{start or ''}"></label>
        <label>End<input type="datetime-local" name="end" value="{end or ''}"></label>
        <label>Sort<select name="sort">
            {'\n'.join(f'<option value="{v}" {"selected" if sort==v else ""}>{v.title()}</option>'
                         for v in ["timestamp","mmsi","speed","heading"])}
        </select></label>
        <label>Limit<input type="number" name="limit" value="{limit}" style="width:70px;"></label>
        <label class="check-label">
            <input type="checkbox" name="alert" value="yes" id="alert-cb" {alert_checked}
                   onchange="this.form.submit()"> Cable alerts only
        </label>
        <button class="btn primary" type="submit">Filter</button>
        <a href="{export_url}" class="btn">Export CSV</a>
        <a href="/analyze" class="btn">Clear</a>
    </form>
</div>
<div class="table-wrap">
<p class="dim" style="padding:10px 0;">{len(rows)} records — click MMSI for vessel history</p>
<table>
<tr><th>Timestamp</th><th>MMSI</th><th>Name</th><th>IMO</th>
    <th>Destination</th><th>Speed</th><th>Heading</th>
    <th>Lat</th><th>Lon</th><th>Cable Alert</th></tr>
{table_rows}
</table></div>"""
    return _page("Vessels", body, "analyze")


@app.get("/analyze/export")
async def export_csv(
    alert: Optional[str] = Query(None),
    mmsi: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    sort: str = Query("timestamp"),
    limit: int = Query(200),
):
    rows = _query_log(alert_only=alert == "yes", mmsi=mmsi, start=start, end=end, sort=sort, limit=limit)
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp","mmsi","name","imo","destination","speed","heading","latitude","longitude","cable_alert"])
    w.writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vessel_export.csv"},
    )


# ---------------------------------------------------------------------------
# /timeline — vessel activity overview cards
# ---------------------------------------------------------------------------

@app.get("/timeline", response_class=HTMLResponse)
async def timeline():
    if not os.path.exists(LOG_DB):
        return _page("Timeline", "<div class='page-content'><p class='dim'>No data yet.</p></div>", "timeline")

    with sqlite3.connect(LOG_DB) as c:
        rows = c.execute("""
            SELECT mmsi,
                   MIN(timestamp) AS first_seen,
                   MAX(timestamp) AS last_seen,
                   COUNT(*)       AS pings,
                   SUM(CASE WHEN cable_alert='Yes' THEN 1 ELSE 0 END) AS alerts,
                   MAX(name)      AS name
            FROM vessel_data_log
            GROUP BY mmsi
            ORDER BY last_seen DESC
        """).fetchall()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    cards = ""
    for r in rows:
        mmsi, first, last, pings, alerts, name = r
        display_name = (name or "").strip() or _vessel_name(mmsi)
        alert_class = "has-alert" if alerts else ""
        alert_badge = (
            f'<span style="color:var(--warn);font-weight:700;"> ⚠ {alerts} cable alert{"s" if alerts > 1 else ""}</span>'
            if alerts else ""
        )
        cards += f"""
        <a href="/vessel/{mmsi}" class="vessel-card {alert_class}">
            <div class="vname">{display_name}{alert_badge}</div>
            <div class="vmeta">MMSI {mmsi}<br>{pings} pings &middot; from {first[:10]}</div>
            <div class="last-seen">last seen {last}</div>
        </a>"""

    body = f"""
<div class="toolbar">
    <span class="dim">{len(rows)} vessels tracked &middot; {now_str} UTC</span>
    <span class="spacer"></span>
    <a href="/analyze" class="btn">Full Table</a>
</div>
<div class="vessel-grid">{cards}</div>"""
    return _page("Timeline", body, "timeline")


# ---------------------------------------------------------------------------
# /vessel/<mmsi> — per-vessel history
# ---------------------------------------------------------------------------

@app.get("/vessel/{mmsi}", response_class=HTMLResponse)
async def vessel_page(mmsi: str):
    if not os.path.exists(LOG_DB):
        return _page("Vessel", "<div class='page-content'><p class='dim'>No data.</p></div>")

    with sqlite3.connect(LOG_DB) as c:
        rows = c.execute("""
            SELECT timestamp, name, imo, destination, speed, heading,
                   latitude, longitude, cable_alert
            FROM vessel_data_log
            WHERE mmsi = ?
            ORDER BY timestamp ASC
        """, (mmsi,)).fetchall()

    if not rows:
        return _page("Vessel", f"<div class='page-content'><p class='dim'>No records for MMSI {mmsi}.</p></div>")

    name     = next((str(r[1]).strip() for r in rows if r[1] and str(r[1]).strip() not in ("", "N/A")), None) or _vessel_name(mmsi)
    imo      = next((str(r[2]).strip() for r in rows if r[2] and str(r[2]).strip() not in ("", "N/A")), "—")
    dest_vals = list(dict.fromkeys(str(r[3]).strip() for r in rows if r[3] and str(r[3]).strip() not in ("", "N/A")))
    first_ts = rows[0][0]
    last_ts  = rows[-1][0]
    alerts   = sum(1 for r in rows if r[8] == "Yes")
    pings    = len(rows)

    mt_link = f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{mmsi}"
    vf_link = f"https://www.vesselfinder.com/vessels/details/{imo}" if imo != "—" else None
    ext = f'<a href="{mt_link}" target="_blank" class="btn">MarineTraffic ↗</a>'
    if vf_link:
        ext += f' <a href="{vf_link}" target="_blank" class="btn">VesselFinder ↗</a>'
    ext += f' <a href="/vessel/{mmsi}/gpx" class="btn">Export GPX</a>'
    if alerts:
        ext += f' <a href="/analyze?mmsi={mmsi}&alert=yes" class="btn warn">⚠ {alerts} cable alert{"s" if alerts > 1 else ""}</a>'

    # AIS gap detection (>= 60 min)
    GAP_MIN = 60
    gaps = []
    for i in range(1, len(rows)):
        try:
            t0 = datetime.strptime(rows[i-1][0], "%Y-%m-%d %H:%M:%S")
            t1 = datetime.strptime(rows[i][0],   "%Y-%m-%d %H:%M:%S")
            gap = (t1 - t0).total_seconds() / 60
            if gap >= GAP_MIN:
                gaps.append((rows[i-1][0], rows[i][0], round(gap)))
        except Exception:
            pass

    if gaps:
        gap_html = "".join(
            f'<div class="loiter-item" style="border-color:var(--info);">'
            f'<span style="color:var(--info);font-weight:700;">AIS BLACKOUT</span>'
            f'<span>{g[0]}</span><span class="dim">→</span><span>{g[1]}</span>'
            f'<span class="loiter-coords">{g[2]} min</span></div>'
            for g in gaps
        )
    else:
        gap_html = '<p class="dim">No significant gaps (threshold: 60 min).</p>'

    # Cable proximity
    cable_ts = [r[0] for r in rows if r[8] == "Yes"]
    cable_pct = round(len(cable_ts) / pings * 100) if pings else 0
    if cable_ts:
        cable_html = f"""
<p style="font-size:12px;margin-bottom:10px;">
    Active for <span style="color:var(--warn);font-weight:700;">{len(cable_ts)} of {pings} pings ({cable_pct}%)</span>
</p>
<div class="prox-bar-wrap">
  <div class="prox-row">
    <span style="width:90px;">Alert rate</span>
    <div class="prox-bar-bg">
      <div class="prox-bar-fill" style="width:{cable_pct}%;background:var(--warn);"></div>
    </div>
    <span>{cable_pct}%</span>
  </div>
</div>
<p class="dim" style="margin-top:8px;font-size:11px;">First: {cable_ts[0]} &middot; Last: {cable_ts[-1]}</p>"""
    else:
        cable_html = '<p class="dim">No cable alerts recorded.</p>'

    # Static data drift
    name_hist = list(dict.fromkeys(str(r[1]).strip() for r in rows if r[1] and str(r[1]).strip() not in ("", "N/A")))
    dest_hist = list(dict.fromkeys(str(r[3]).strip() for r in rows if r[3] and str(r[3]).strip() not in ("", "N/A")))

    # Flag history from vessel_static.db
    flag_hist = []
    if os.path.exists(STATIC_DB):
        with sqlite3.connect(STATIC_DB) as c:
            frows = c.execute(
                "SELECT flag, timestamp FROM flag_history WHERE mmsi=? ORDER BY id ASC", (mmsi,)
            ).fetchall()
            flag_hist = [(r[0], r[1]) for r in frows]

    static_items = ""
    if len(name_hist) > 1:
        static_items += (
            f'<div class="loiter-item" style="border-color:var(--warn);">'
            f'<span style="color:var(--warn);">NAME CHANGE</span>'
            f'<span>{" → ".join(name_hist)}</span></div>'
        )
    if len(flag_hist) > 1:
        flag_str = " → ".join(
            f'{f} <span class="dim">({t[:10]})</span>' for f, t in flag_hist
        )
        static_items += (
            f'<div class="loiter-item" style="border-color:var(--warn);">'
            f'<span style="color:var(--warn);">FLAG CHANGE</span>'
            f'<span>{flag_str}</span></div>'
        )
    elif flag_hist:
        static_items += (
            f'<div class="loiter-item"><span class="dim">FLAG</span>'
            f'<span>{flag_hist[-1][0]}</span></div>'
        )
    for d in dest_hist:
        static_items += f'<div class="loiter-item"><span class="dim">DEST</span><span>{d}</span></div>'
    if not static_items:
        static_items = '<p class="dim">No name, flag or destination changes recorded.</p>'

    # Route replay data — all positions with timestamps, skip nulls
    replay_points = [
        {"ts": r[0], "lat": r[6], "lon": r[7], "spd": r[4], "hdg": r[5], "cable": r[8] == "Yes"}
        for r in rows if r[6] is not None and r[7] is not None
    ]
    import json as _json
    replay_json = _json.dumps(replay_points)

    body = f"""
<div class="toolbar">
    <span style="font-weight:700;font-size:14px;">{name}</span>
    <span class="dim">MMSI {mmsi} &middot; IMO {imo}</span>
    <span class="spacer"></span>
    {ext}
</div>
<div class="page-content">

<div class="section">
<div class="section-title">Route Replay</div>
<div id="replay-map" style="height:380px;border-radius:6px;border:1px solid var(--border);margin-bottom:12px;"></div>
<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px;">
    <button id="rp-play"  class="btn" onclick="rpPlay()">▶ Play</button>
    <button id="rp-pause" class="btn" onclick="rpPause()" disabled>⏸ Pause</button>
    <button id="rp-reset" class="btn" onclick="rpReset()">⏮ Reset</button>
    <label style="color:var(--dim);font-size:12px;">Speed
        <select id="rp-speed" onchange="rpSetSpeed(this.value)" style="background:var(--surface);color:var(--text);border:1px solid var(--border);padding:2px 6px;margin-left:4px;">
            <option value="50">0.5×</option>
            <option value="25" selected>1×</option>
            <option value="10">2.5×</option>
            <option value="4">6×</option>
            <option value="1">Max</option>
        </select>
    </label>
    <span id="rp-status" style="color:var(--dim);font-size:12px;"></span>
</div>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const RP_POINTS = {replay_json};
let rpMap, rpMarker, rpTrail, rpIdx = 0, rpTimer = null, rpSpeed = 25;

function rpInit() {{
    if (RP_POINTS.length === 0) return;
    const start = RP_POINTS[0];
    rpMap = L.map('replay-map', {{zoomControl:true}}).setView([start.lat, start.lon], 7);
    L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
        attribution:'&copy; CartoDB', maxZoom:18
    }}).addTo(rpMap);
    rpTrail   = L.polyline([], {{color:'#4488ff', weight:2, opacity:0.7}}).addTo(rpMap);
    rpMarker  = L.circleMarker([start.lat, start.lon], {{radius:7, color:'#ff8800', fillColor:'#ff8800', fillOpacity:1}}).addTo(rpMap);
    rpStatus();
}}

function rpStatus() {{
    const el = document.getElementById('rp-status');
    if (!RP_POINTS.length) {{ el.textContent = 'No position data.'; return; }}
    const p = RP_POINTS[Math.min(rpIdx, RP_POINTS.length-1)];
    el.textContent = p.ts + '  ' + p.spd + ' kn';
}}

function rpStep() {{
    if (rpIdx >= RP_POINTS.length) {{ rpPause(); return; }}
    const p = RP_POINTS[rpIdx];
    const ll = [p.lat, p.lon];
    rpMarker.setLatLng(ll);
    rpMarker.setStyle({{color: p.cable ? '#ff4444' : '#ff8800', fillColor: p.cable ? '#ff4444' : '#ff8800'}});
    rpTrail.addLatLng(ll);
    rpIdx++;
    rpStatus();
}}

function rpPlay() {{
    if (rpIdx >= RP_POINTS.length) rpReset();
    document.getElementById('rp-play').disabled  = true;
    document.getElementById('rp-pause').disabled = false;
    rpTimer = setInterval(rpStep, rpSpeed);
}}

function rpPause() {{
    clearInterval(rpTimer); rpTimer = null;
    document.getElementById('rp-play').disabled  = false;
    document.getElementById('rp-pause').disabled = true;
}}

function rpReset() {{
    rpPause();
    rpIdx = 0;
    rpTrail.setLatLngs([]);
    if (RP_POINTS.length) {{
        const p = RP_POINTS[0];
        rpMarker.setLatLng([p.lat, p.lon]);
        rpMap.setView([p.lat, p.lon], 7);
    }}
    rpStatus();
}}

function rpSetSpeed(v) {{ rpSpeed = parseInt(v); if (rpTimer) {{ rpPause(); rpPlay(); }} }}

window.addEventListener('load', rpInit);
</script>
</div>

<div class="section">
<div class="section-title">Summary</div>
<div class="kv-grid">
    <span class="k">First seen</span><span class="v">{first_ts}</span>
    <span class="k">Last seen</span><span class="v">{last_ts}</span>
    <span class="k">Total pings</span><span class="v">{pings}</span>
    <span class="k">Cable alerts</span>
    <span class="v" style="color:{"var(--warn)" if alerts else "var(--dim)"};">{alerts}</span>
    <span class="k">AIS gaps &ge;60 min</span><span class="v">{len(gaps)}</span>
    <span class="k">Destinations seen</span><span class="v">{", ".join(dest_vals) or "—"}</span>
</div>
</div>

<div class="section">
<div class="section-title">AIS Blackouts</div>
<div class="loiter-list">{gap_html}</div>
</div>

<div class="section">
<div class="section-title">Cable Proximity</div>
{cable_html}
</div>

<div class="section">
<div class="section-title">Static Data History (name / destination changes)</div>
<div class="loiter-list">{static_items}</div>
</div>

</div>"""
    return _page(f"{name} — Vessel", body)


# ---------------------------------------------------------------------------
# /vessel/<mmsi>/gpx
# ---------------------------------------------------------------------------

@app.get("/vessel/{mmsi}/gpx")
async def vessel_gpx(mmsi: str):
    if not os.path.exists(LOG_DB):
        return HTMLResponse("No data.", status_code=404)
    with sqlite3.connect(LOG_DB) as c:
        rows = c.execute("""
            SELECT timestamp, latitude, longitude, speed, name
            FROM vessel_data_log
            WHERE mmsi = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY timestamp ASC
        """, (mmsi,)).fetchall()
    if not rows:
        return HTMLResponse("No position data.", status_code=404)

    name = next((str(r[4]).strip() for r in rows if r[4] and str(r[4]).strip() not in ("", "N/A")), mmsi)
    root = ET.Element("gpx", {
        "version": "1.1", "creator": "shadow-fleet-tracker",
        "xmlns": "http://www.topografix.com/GPX/1/1",
    })
    trk = ET.SubElement(root, "trk")
    ET.SubElement(trk, "name").text = f"{name} ({mmsi})"
    seg = ET.SubElement(trk, "trkseg")
    for ts, lat, lon, speed, _ in rows:
        pt = ET.SubElement(seg, "trkpt", {"lat": str(lat), "lon": str(lon)})
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            ET.SubElement(pt, "time").text = dt.isoformat()
        except Exception:
            pass
        if speed is not None:
            ET.SubElement(pt, "desc").text = f"{speed} kn"

    gpx_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    filename = f"vessel_{mmsi}_{rows[0][0][:10]}.gpx"
    return Response(
        content=gpx_str, media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# /loitering
# ---------------------------------------------------------------------------

@app.get("/loitering", response_class=HTMLResponse)
async def loitering_page():
    if not os.path.exists(LOITERING_DB):
        return _page("Loitering", """
<div class='page-content'>
<p class='dim' style='margin-top:20px;'>
No loitering events yet. Detected when a vessel stays below 0.5 kn
within a 0.5 km radius for more than 20 minutes.
</p></div>""", "loiter")

    with sqlite3.connect(LOITERING_DB) as c:
        rows = c.execute("""
            SELECT id, mmsi, timestamp, latitude, longitude, near_cable
            FROM loitering_events ORDER BY timestamp DESC
        """).fetchall()

    if not rows:
        return _page("Loitering", "<div class='page-content'><p class='dim'>No events yet.</p></div>", "loiter")

    near_count = sum(1 for r in rows if r[5])
    by_mmsi: dict = {}
    for r in rows:
        by_mmsi.setdefault(r[1], []).append(r)

    summary_rows = ""
    for mmsi, events in sorted(by_mmsi.items(), key=lambda x: -len(x[1])):
        nc = sum(1 for e in events if e[5])
        name = _vessel_name(mmsi)
        summary_rows += (
            f'<tr><td><a href="/vessel/{mmsi}">{mmsi}</a></td><td>{name}</td>'
            f'<td>{len(events)}</td><td class="{"alert-yes" if nc else "alert-no"}">{nc}</td>'
            f'<td>{events[-1][2]}</td></tr>'
        )

    event_items = ""
    for r in rows[:100]:
        eid, mmsi, ts, lat, lon, nc = r
        name = _vessel_name(mmsi)
        nc_badge = '<span class="loiter-badge">NEAR CABLE</span>' if nc else ""
        maps_url = f"https://www.google.com/maps?q={lat},{lon}"
        event_items += (
            f'<div class="loiter-item {"near-cable" if nc else ""}">'
            f'{nc_badge}'
            f'<span><a href="/vessel/{mmsi}">{name}</a></span>'
            f'<span class="dim">{ts}</span>'
            f'<span class="loiter-coords"><a href="{maps_url}" target="_blank">{float(lat):.4f}, {float(lon):.4f} ↗</a></span>'
            f'</div>'
        )

    body = f"""
<div class="toolbar">
    <span class="dim">{len(rows)} events &middot; {near_count} near cable &middot; {len(by_mmsi)} vessels</span>
    <span class="spacer"></span>
    <a href="/loitering/export" class="btn">Export CSV</a>
</div>
<div class="page-content">
<div class="section">
<div class="section-title">By Vessel</div>
<div class="table-wrap" style="padding:0;">
<table>
<tr><th>MMSI</th><th>Name</th><th>Events</th><th>Near Cable</th><th>Last Event</th></tr>
{summary_rows}
</table></div>
</div>
<div class="section">
<div class="section-title">All Events (newest first)</div>
<div class="loiter-list">{event_items}</div>
</div>
</div>"""
    return _page("Loitering", body, "loiter")


@app.get("/loitering/export")
async def loitering_export():
    if not os.path.exists(LOITERING_DB):
        return HTMLResponse("No data.", status_code=404)
    with sqlite3.connect(LOITERING_DB) as c:
        rows = c.execute(
            "SELECT id, mmsi, timestamp, latitude, longitude, near_cable FROM loitering_events ORDER BY timestamp DESC"
        ).fetchall()
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["id","mmsi","timestamp","latitude","longitude","near_cable"])
    w.writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=loitering_events.csv"},
    )


# ---------------------------------------------------------------------------
# /transshipment — Russia↔West port call pattern detection
# ---------------------------------------------------------------------------

@app.get("/transshipment", response_class=HTMLResponse)
async def transshipment_page(
    direction: Optional[str] = Query(None),
):
    stats = transshipment_module.get_stats()
    events = transshipment_module.get_recent_events(limit=200, direction=direction or None)
    calls  = transshipment_module.get_recent_port_calls(limit=50)

    no_data = stats["port_calls"] == 0

    dir_filter = direction or ""
    ru_sel   = 'style="color:var(--warn);font-weight:700;"' if direction == "RU→WEST" else ""
    west_sel = 'style="color:var(--info);font-weight:700;"' if direction == "WEST→RU" else ""

    # --- Event rows ---
    event_rows = ""
    for e in events:
        d = e["direction"]
        color = "var(--warn)" if d == "RU→WEST" else "var(--info)"
        arrow = "🡒"
        event_rows += f"""<tr>
            <td><span style="color:{color};font-weight:700;">{d}</span></td>
            <td><a href="/vessel/{e['mmsi']}">{e['name'] or e['mmsi']}</a></td>
            <td>{e['from_port']}</td>
            <td>{e['from_exit_ts']}</td>
            <td>{arrow}</td>
            <td>{e['to_port']}</td>
            <td>{e['to_entry_ts']}</td>
            <td>{e['days_between']}d</td>
        </tr>"""

    if not event_rows:
        event_rows = f'<tr><td colspan="8" class="dim" style="padding:20px;">{"No events yet — detection runs live as vessels are tracked." if no_data else "No events match this filter."}</td></tr>'

    # --- Port call rows ---
    call_rows = ""
    for c in calls:
        ptype_color = "var(--warn)" if c["port_type"] == "russian" else "var(--info)"
        call_rows += f"""<tr>
            <td><a href="/vessel/{c['mmsi']}">{c['name'] or c['mmsi']}</a></td>
            <td><span style="color:{ptype_color};">{c['port']}</span></td>
            <td class="dim">{c['port_type']}</td>
            <td>{c['entry_ts']}</td>
            <td>{c['exit_ts']}</td>
            <td>{c['min_speed']} kn</td>
        </tr>"""

    if not call_rows:
        call_rows = '<tr><td colspan="6" class="dim" style="padding:20px;">No port calls recorded yet.</td></tr>'

    # --- Explanation box ---
    explanation = """
<div style="padding:12px 16px;background:var(--surface);border:1px solid var(--border);
            border-radius:4px;font-size:11px;color:var(--dim);line-height:1.7;margin-bottom:20px;">
    <span style="color:var(--text);font-weight:700;">How this works</span><br>
    Port calls are inferred from position pings — a vessel must be inside a port zone
    at &lt;1.5 kn for at least 2 pings to register a call.
    A transshipment event is flagged when the same vessel calls at a
    <span style="color:var(--warn);">Russian port</span> and a
    <span style="color:var(--info);">Western hub</span> within 21 days, in either direction.<br><br>
    <span style="color:var(--warn);">RU→WEST</span> — Russian port then Gothenburg/Skaw/etc.
    Potential cargo laundering into European supply chains.<br>
    <span style="color:var(--info);">WEST→RU</span> — Western hub then Russian port.
    Potential European goods flowing east into sanctioned territory.<br><br>
    Port proximity is approximate. AIS blackouts between calls will not be detected.
    This shows a pattern, not a verdict.
</div>"""

    body = f"""
<div class="toolbar">
    <span class="dim">
        {stats['ru_west']} RU→WEST &nbsp;·&nbsp;
        {stats['west_ru']} WEST→RU &nbsp;·&nbsp;
        {stats['vessels']} vessels &nbsp;·&nbsp;
        {stats['port_calls']} port calls logged
    </span>
    <span class="spacer"></span>
    <a href="/transshipment?direction=RU%E2%86%92WEST" class="btn" {ru_sel}>RU→WEST</a>
    <a href="/transshipment?direction=WEST%E2%86%92RU" class="btn" {west_sel}>WEST→RU</a>
    <a href="/transshipment" class="btn">All</a>
    <a href="/transshipment/export" class="btn">Export CSV</a>
</div>
<div class="page-content">

{explanation}

<div class="section">
<div class="section-title">Transshipment Events</div>
<div class="table-wrap" style="padding:0;margin-bottom:24px;">
<table>
<tr>
    <th>Direction</th><th>Vessel</th>
    <th>From Port</th><th>Departed</th><th></th>
    <th>To Port</th><th>Arrived</th><th>Days</th>
</tr>
{event_rows}
</table></div>
</div>

<div class="section">
<div class="section-title">Recent Port Calls (last 50)</div>
<div class="table-wrap" style="padding:0;">
<table>
<tr><th>Vessel</th><th>Port</th><th>Type</th><th>Entry</th><th>Exit</th><th>Min Speed</th></tr>
{call_rows}
</table></div>
</div>

</div>"""

    return _page("Transshipment", body, "transshipment")


@app.get("/transshipment/export")
async def transshipment_export(direction: Optional[str] = Query(None)):
    events = transshipment_module.get_recent_events(limit=10000, direction=direction or None)
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["direction","mmsi","name","from_port","from_exit_ts",
                "to_port","to_entry_ts","days_between","detected_ts"])
    for e in events:
        w.writerow([e["direction"], e["mmsi"], e["name"], e["from_port"],
                    e["from_exit_ts"], e["to_port"], e["to_entry_ts"],
                    e["days_between"], e["detected_ts"]])
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transshipment_events.csv"},
    )