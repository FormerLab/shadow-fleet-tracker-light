#!/usr/bin/env python3
"""
gur_scrape.py — Scrape war-sanctions.gur.gov.ua ship catalogue

Crawls /en/transport/ships/{id}, extracts IMO, MMSI, name, and flag from each
page, and writes two output files:

  gur_mapping.json       IMO → GUR-ID          (used by shadow_tracker.py for deep-links)
  gur_vessels_full.json  GUR-ID → {imo, mmsi, name, flag}  (full catalogue for watchlist expansion)

Run once to build the initial dataset, re-run occasionally as the catalogue grows.
Use --start to resume from a specific ID without re-fetching already-scraped pages.

Usage:
    python gur_scrape.py                  # full crawl 1–ID_END
    python gur_scrape.py --start 1400     # resume / extend
    python gur_scrape.py --probe 1517     # probe a single page (debug)
    python gur_scrape.py --diff           # show GUR vessels not in Vessels1.db
"""

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL        = "https://war-sanctions.gur.gov.ua/en/transport/ships/{id}"
ID_START        = 1
ID_END          = 1600
SLEEP_SEC       = 1.5
OUTPUT_MAPPING  = "gur_mapping.json"        # IMO → GUR-ID (tracker deep-links)
OUTPUT_FULL     = "gur_vessels_full.json"   # GUR-ID → {imo, mmsi, name, flag}
VESSELS_DB      = "Vessels1.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://war-sanctions.gur.gov.ua/en/transport/ships",
}

# ---------------------------------------------------------------------------
# Extraction patterns
# ---------------------------------------------------------------------------

IMO_PATTERNS = [
    re.compile(r'IMO[:\s#]*(\d{7})', re.IGNORECASE),
    re.compile(r'"imo"[:\s"]*(\d{7})', re.IGNORECASE),
    re.compile(r'>IMO<.*?>(\d{7})<', re.IGNORECASE | re.DOTALL),
    re.compile(r'imo_number["\s:]+(\d{7})', re.IGNORECASE),
]

# MMSI is a 9-digit number. On GUR pages the value sits in a
# <span class="js_visibility_target"> immediately after the "MMSI" label div.
MMSI_PATTERNS = [
    re.compile(r'MMSI.{0,200}?js_visibility_target[^>]*>(\d{9})<', re.IGNORECASE | re.DOTALL),
    re.compile(r'MMSI[:\s#]*(\d{9})', re.IGNORECASE),
    re.compile(r'"mmsi"[:\s"]*(\d{9})', re.IGNORECASE),
]

NAME_PATTERNS = [
    re.compile(r'<h1[^>]*>\s*([A-Z0-9 \-\.]+)\s*</h1>', re.IGNORECASE),
    re.compile(r'"vessel_name"[:\s"]*([^"]{3,40})"', re.IGNORECASE),
    re.compile(r'Vessel name[^>]*>\s*</[^>]+>\s*([A-Z0-9 \-\.]{3,40})', re.IGNORECASE | re.DOTALL),
]

FLAG_PATTERNS = [
    re.compile(r'Flag \(Current\)[^>]*>.*?<[^>]+>\s*([a-z][a-z ]{2,40})', re.IGNORECASE | re.DOTALL),
    re.compile(r'"flag"[:\s"]*([a-z][a-z ]{2,30})"', re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

def fetch_page(url: str) -> str | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  HTTP {e.code}: {url}")
        return None
    except Exception as e:
        print(f"  Error: {e}: {url}")
        return None


def _first_match(html: str, patterns: list) -> str | None:
    for p in patterns:
        m = p.search(html)
        if m:
            return m.group(1).strip()
    return None


def extract_fields(html: str) -> dict:
    return {
        "imo":  _first_match(html, IMO_PATTERNS),
        "mmsi": _first_match(html, MMSI_PATTERNS),
        "name": _first_match(html, NAME_PATTERNS),
        "flag": _first_match(html, FLAG_PATTERNS),
    }

# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def probe(gur_id: int) -> None:
    url  = BASE_URL.format(id=gur_id)
    print(f"Fetching {url} ...")
    html = fetch_page(url)
    if html is None:
        print("No response / 404")
        return

    print(f"Page length: {len(html)} chars")
    fields = extract_fields(html)
    for k, v in fields.items():
        print(f"  {k:6s}: {v or 'NOT FOUND'}")

    if not fields["imo"]:
        print("\nIMO context snippets:")
        for ctx in re.finditer(r'.{0,60}[Ii][Mm][Oo].{0,60}', html):
            print(f"  ...{ctx.group()}...")
    if not fields["mmsi"]:
        print("\nMMSI context snippets:")
        for ctx in re.finditer(r'.{0,60}[Mm][Mm][Ss][Ii].{0,60}', html):
            print(f"  ...{ctx.group()}...")

# ---------------------------------------------------------------------------
# Diff — show GUR vessels not in Vessels1.db
# ---------------------------------------------------------------------------

def diff() -> None:
    if not Path(OUTPUT_FULL).exists():
        print(f"{OUTPUT_FULL} not found — run a full crawl first.")
        return

    with open(OUTPUT_FULL) as f:
        full: dict = json.load(f)

    if not Path(VESSELS_DB).exists():
        print(f"{VESSELS_DB} not found.")
        return

    with sqlite3.connect(VESSELS_DB) as c:
        watched_imos = {
            str(r[0]) for r in c.execute("SELECT imo FROM vessels WHERE imo IS NOT NULL").fetchall()
        }

    missing = [
        (gur_id, v) for gur_id, v in full.items()
        if v.get("imo") and v["imo"] not in watched_imos
    ]
    missing.sort(key=lambda x: int(x[0]))

    print(f"\nGUR vessels not in watchlist: {len(missing)} of {len(full)}\n")
    print(f"{'GUR-ID':<8} {'IMO':<10} {'MMSI':<12} {'Name':<35} Flag")
    print("-" * 80)
    for gur_id, v in missing:
        print(
            f"{gur_id:<8} {v.get('imo') or '?':<10} {v.get('mmsi') or '?':<12} "
            f"{(v.get('name') or '?')[:34]:<35} {v.get('flag') or '?'}"
        )

    # Write ready-to-use SQL for bulk import
    sql_path = Path("gur_watchlist_candidates.sql")
    with open(sql_path, "w") as f:
        f.write("-- GUR vessels not currently in Vessels1.db\n")
        f.write("-- Review before importing — tier-2 candidates\n\n")
        for gur_id, v in missing:
            imo  = v.get("imo",  "").replace("'", "''")
            mmsi = v.get("mmsi", "")
            name = (v.get("name") or "").replace("'", "''")
            if mmsi:
                f.write(
                    f"INSERT OR IGNORE INTO vessels (mmsi, imo, name) "
                    f"VALUES ('{mmsi}', '{imo}', '{name}');\n"
                )
            else:
                f.write(f"-- MMSI unknown: IMO {imo}  {name}\n")

    print(f"\nSQL written to {sql_path.resolve()}")
    print("Review before running: sqlite3 Vessels1.db < gur_watchlist_candidates.sql")

# ---------------------------------------------------------------------------
# Main crawl
# ---------------------------------------------------------------------------

def crawl(start: int, end: int) -> None:
    mapping_path = Path(OUTPUT_MAPPING)
    full_path    = Path(OUTPUT_FULL)

    mapping: dict[str, int]  = {}
    full:    dict[str, dict] = {}
    id_done: set[int]        = set()

    if full_path.exists():
        with open(full_path) as f:
            full = json.load(f)
        id_done = {int(k) for k in full}
        print(f"Loaded existing full data: {len(full)} vessels, "
              f"highest id: {max(id_done) if id_done else 0}")

    if mapping_path.exists():
        with open(mapping_path) as f:
            mapping = json.load(f)

    added = skipped = 0

    try:
        for gur_id in range(start, end + 1):
            if gur_id in id_done:
                skipped += 1
                continue

            url  = BASE_URL.format(id=gur_id)
            html = fetch_page(url)

            if html is None:
                print(f"  [{gur_id}] skip (no content)")
                time.sleep(SLEEP_SEC * 0.5)
                continue

            fields = extract_fields(html)
            imo    = fields["imo"]
            mmsi   = fields["mmsi"]
            status = f"IMO {imo or '?':>7}  MMSI {mmsi or '?':>9}  {fields.get('name') or ''}"

            if imo:
                full[str(gur_id)] = fields
                if imo not in mapping or gur_id < mapping[imo]:
                    mapping[imo] = gur_id
                added += 1
                print(f"  [{gur_id}] {status} ✓")
            else:
                print(f"  [{gur_id}] IMO not found")

            if (gur_id - start + 1) % 50 == 0:
                _save_both(mapping, full, mapping_path, full_path)
                print(f"  -> checkpoint ({len(full)} total, {added} new this run)")

            time.sleep(SLEEP_SEC)

    except KeyboardInterrupt:
        print("\nInterrupted — saving progress...")

    _save_both(mapping, full, mapping_path, full_path)
    print(f"\nDone. {len(full)} vessels, {added} added this run, {skipped} skipped.")
    print(f"  {OUTPUT_MAPPING}: {len(mapping)} IMO->ID mappings")
    print(f"  {OUTPUT_FULL}: full vessel records")
    print(f"\nRun --diff to see which GUR vessels are not in your watchlist.")


def _save_both(mapping, full, mapping_path, full_path):
    for obj, path in [(mapping, mapping_path), (full, full_path)]:
        tmp = str(path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
        os.replace(tmp, str(path))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape GUR War&Sanctions ship catalogue")
    parser.add_argument("--start", type=int, default=ID_START, help=f"Start ID (default {ID_START})")
    parser.add_argument("--end",   type=int, default=ID_END,   help=f"End ID (default {ID_END})")
    parser.add_argument("--probe", type=int, default=None,     help="Probe a single ID and print debug info")
    parser.add_argument("--diff",  action="store_true",        help="Show GUR vessels not in Vessels1.db")
    args = parser.parse_args()

    if args.probe is not None:
        probe(args.probe)
    elif args.diff:
        diff()
    else:
        crawl(args.start, args.end)
