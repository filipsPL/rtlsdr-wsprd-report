#!/usr/bin/env python3
"""
WSPR Log Analyzer
https://github.com/filipsPL/rtlsdr-wsprd-report
Reads WSPR TSV logs, stores in SQLite, generates static HTML dashboard.
Usage: python wspr_analyzer.py <tsv_file> <my_locator> [--db wspr.db] [--output wspr_report.html]
"""

import argparse
import sqlite3
import csv
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from string import Template

# --- Maidenhead Grid Locator Conversion ---

def maidenhead_to_latlon(locator: str) -> tuple[float, float] | None:
    """Convert Maidenhead grid locator to (lat, lon) center point."""
    loc = locator.strip().upper()
    if len(loc) < 4 or len(loc) % 2 != 0:
        return None
    try:
        lon = (ord(loc[0]) - ord('A')) * 20 - 180
        lat = (ord(loc[1]) - ord('A')) * 10 - 90
        lon += (ord(loc[2]) - ord('0')) * 2
        lat += (ord(loc[3]) - ord('0')) * 1
        if len(loc) >= 6:
            lon += (ord(loc[4]) - ord('A')) * (2 / 24)
            lat += (ord(loc[5]) - ord('A')) * (1 / 24)
            lon += (1 / 24)  # center of subsquare
            lat += (1 / 48)
        else:
            lon += 1    # center of square
            lat += 0.5
        return (lat, lon)
    except (IndexError, ValueError):
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in km."""
    R = 6371.0
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def freq_to_band(freq_mhz: float) -> str:
    """Convert frequency in MHz to amateur band in meters."""
    bands = [
        (0.1357, 0.1378, 2200),
        (0.4742, 0.4790, 630),
        (1.8, 2.0, 160),
        (3.5, 4.0, 80),
        (5.2, 5.5, 60),
        (7.0, 7.3, 40),
        (10.1, 10.15, 30),
        (14.0, 14.35, 20),
        (18.068, 18.168, 17),
        (21.0, 21.45, 15),
        (24.89, 24.99, 12),
        (28.0, 29.7, 10),
        (50.0, 54.0, 6),
        (144.0, 148.0, 2),
    ]
    for lo, hi, band in bands:
        if lo <= freq_mhz <= hi:
            return f"{band}m"
    return f"{freq_mhz:.3f}MHz"


# --- Database ---

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            snr REAL,
            dt REAL,
            freq REAL,
            drift INTEGER,
            call TEXT,
            loc TEXT,
            pwr INTEGER,
            band TEXT,
            lat REAL,
            lon REAL,
            timestamp TEXT NOT NULL,
            UNIQUE(date, time, call, freq)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON observations(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_band ON observations(band)")

    # Migrate old timestamps that lack the UTC 'Z' suffix
    migrated = conn.execute(
        "UPDATE observations SET timestamp = timestamp || 'Z' "
        "WHERE timestamp NOT LIKE '%Z'"
    ).rowcount
    if migrated:
        print(f"  Migrated {migrated} timestamps to UTC format.")

    conn.commit()
    return conn


def import_tsv(conn: sqlite3.Connection, tsv_path: str) -> int:
    """Import TSV file into database. Returns count of new rows inserted."""
    inserted = 0
    FIELDS = ['date', 'time', 'snr', 'dt', 'freq', 'drift', 'call', 'loc', 'pwr']
    with open(tsv_path, 'r') as f:
        for line_no, line in enumerate(f, 1):
            parts = line.strip().split()
            if not parts or len(parts) < len(FIELDS):
                continue
            # Skip header line
            if parts[0] == 'date':
                continue
            row = dict(zip(FIELDS, parts))
            try:
                date = row['date']
                time_str = row['time']
                freq = float(row['freq'])
                call = row['call']
                loc = row['loc']
                snr = float(row['snr'])
                dt_val = float(row['dt'])
                drift = int(row['drift'])
                pwr = int(row['pwr'])

                band = freq_to_band(freq)
                coords = maidenhead_to_latlon(loc)
                lat = coords[0] if coords else None
                lon = coords[1] if coords else None

                # Build ISO timestamp for filtering (WSPR times are UTC)
                # time is HHMM format
                ts = f"{date}T{time_str[:2]}:{time_str[2:]}:00Z"

                conn.execute("""
                    INSERT OR IGNORE INTO observations
                    (date, time, snr, dt, freq, drift, call, loc, pwr, band, lat, lon, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (date, time_str, snr, dt_val, freq, drift, call, loc, pwr, band, lat, lon, ts))
                inserted += conn.total_changes  # approximate
            except (KeyError, ValueError) as e:
                print(f"Skipping malformed row: {e}", file=sys.stderr)
                continue
    conn.commit()
    # Get actual count
    return inserted


def query_observations(conn: sqlite3.Connection, since: str | None = None) -> list[dict]:
    """Query observations, optionally filtered by timestamp >= since."""
    if since:
        rows = conn.execute(
            "SELECT * FROM observations WHERE timestamp >= ? ORDER BY timestamp DESC", (since,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM observations ORDER BY timestamp DESC").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM observations LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


# --- HTML Generation ---

def generate_html(conn: sqlite3.Connection, my_locator: str, output_path: str):
    my_coords = maidenhead_to_latlon(my_locator)
    if not my_coords:
        print(f"Error: invalid locator '{my_locator}'", file=sys.stderr)
        sys.exit(1)
    my_lat, my_lon = my_coords

    # Load observations from the last 7 days only
    one_week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_obs = query_observations(conn, since=one_week_ago)

    # Precompute distances
    for obs in all_obs:
        if obs['lat'] is not None and obs['lon'] is not None:
            obs['distance_km'] = round(haversine_km(my_lat, my_lon, obs['lat'], obs['lon']), 1)
        else:
            obs['distance_km'] = None

    # Build heatmap data: hour (0-23) x band → count
    heatmap = defaultdict(lambda: defaultdict(int))
    all_bands = set()
    for obs in all_obs:
        hour = int(obs['time'][:2]) if obs['time'] and len(obs['time']) >= 2 else 0
        band = obs['band'] or '?'
        heatmap[hour][band] += 1
        all_bands.add(band)

    # Sort bands by wavelength (numerically)
    def band_sort_key(b):
        try:
            return int(b.replace('m', '').replace('MHz', '9999'))
        except ValueError:
            return 99999
    sorted_bands = sorted(all_bands, key=band_sort_key)

    # Build heatmap JSON
    heatmap_data = []
    for hour in range(24):
        for band in sorted_bands:
            count = heatmap[hour][band]
            if count > 0:
                heatmap_data.append({"hour": hour, "band": band, "count": count})

    # Convert observations to JSON-safe format
    import json
    obs_json = json.dumps(all_obs, default=str)
    heatmap_json = json.dumps(heatmap_data)
    bands_json = json.dumps(sorted_bands)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Load external HTML template
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wspr_template.html')
    with open(template_path, 'r') as f:
        template = Template(f.read())

    html = template.safe_substitute(
        LOCATOR=my_locator,
        MY_LAT=f"{my_lat:.2f}",
        MY_LON=f"{my_lon:.2f}",
        GENERATED=now_str,
        OBS_JSON=obs_json,
        HEATMAP_JSON=heatmap_json,
        BANDS_JSON=bands_json,
    )

    with open(output_path, 'w') as f:
        f.write(html)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description='WSPR Log Analyzer')
    parser.add_argument('tsv_file', help='Input WSPR TSV log file')
    parser.add_argument('locator', help='Your Maidenhead grid locator (e.g. JN47)')
    parser.add_argument('--db', default='wspr.db', help='SQLite database path (default: wspr.db)')
    parser.add_argument('--output', default='wspr_report.html', help='Output HTML file (default: wspr_report.html)')
    args = parser.parse_args()

    if not os.path.exists(args.tsv_file):
        print(f"Error: file not found: {args.tsv_file}", file=sys.stderr)
        sys.exit(1)

    conn = init_db(args.db)
    print(f"Importing {args.tsv_file}...")
    before = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    import_tsv(conn, args.tsv_file)
    after = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    print(f"  {after - before} new observations added (total: {after})")

    print(f"Generating report → {args.output}")
    generate_html(conn, args.locator, args.output)
    print("Done.")
    conn.close()


if __name__ == '__main__':
    main()



