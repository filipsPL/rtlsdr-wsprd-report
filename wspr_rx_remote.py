#!/usr/bin/env python3
"""
wspr_rx_remote.py — WSPR spots where this station's TX was received by others.
Fetches from wspr.live API, stores in SQLite, optionally generates HTML report via wspr_rx_remote_template.html.

Usage:
    python3 wspr_rx_remote.py [--callsign CALL] [--db FILE] [--hours N] [--limit N]
                               [--output FILE] [--tx-loc LOCATOR]
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from string import Template

# --- Configuration (edit or override via CLI) ---
CALLSIGN = "SP5FLS"          # TX callsign to query
DB_FILE  = "wspr_rx.db"      # SQLite output file
HOURS    = 24                # How many past hours to fetch
LIMIT    = 1000              # Max rows per fetch (0 = no limit)
TX_LOC   = "KO02MC"          # TX Maidenhead locator (for map center)
API_URL  = "https://db1.wspr.live/"
# ------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS rx_spots (
    id          INTEGER PRIMARY KEY,
    time        TEXT    NOT NULL,
    band        INTEGER,
    rx_sign     TEXT,
    rx_lat      REAL,
    rx_lon      REAL,
    rx_loc      TEXT,
    tx_sign     TEXT,
    tx_lat      REAL,
    tx_lon      REAL,
    tx_loc      TEXT,
    distance    INTEGER,
    azimuth     INTEGER,
    rx_azimuth  INTEGER,
    frequency   INTEGER,
    power       INTEGER,
    snr         INTEGER,
    drift       INTEGER,
    version     TEXT,
    code        INTEGER
)
"""

INSERT_SQL = """
INSERT OR IGNORE INTO rx_spots
    (id, time, band, rx_sign, rx_lat, rx_lon, rx_loc,
     tx_sign, tx_lat, tx_lon, tx_loc,
     distance, azimuth, rx_azimuth,
     frequency, power, snr, drift, version, code)
VALUES
    (:id, :time, :band, :rx_sign, :rx_lat, :rx_lon, :rx_loc,
     :tx_sign, :tx_lat, :tx_lon, :tx_loc,
     :distance, :azimuth, :rx_azimuth,
     :frequency, :power, :snr, :drift, :version, :code)
"""


def build_query(callsign: str, since: datetime, limit: int) -> str:
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    limit_clause = f"LIMIT {limit}" if limit > 0 else ""
    return (
        f"SELECT * FROM wspr.rx "
        f"WHERE tx_sign='{callsign}' AND time >= '{since_str}' "
        f"ORDER BY time DESC "
        f"{limit_clause} "
        f"FORMAT JSON"
    )


def fetch_spots(query: str) -> list[dict]:
    url = API_URL + "?query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "wspr_rx_remote/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"HTTP error {resp.status}", file=sys.stderr)
                sys.exit(1)
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = payload.get("data", [])
    stats = payload.get("statistics", {})
    print(
        f"Fetched {len(rows)} spots "
        f"(API elapsed {stats.get('elapsed', '?'):.3f}s, "
        f"rows_read={stats.get('rows_read', '?')})"
    )
    return rows


def store_spots(db_file: str, spots: list[dict]) -> int:
    con = sqlite3.connect(db_file)
    try:
        con.execute(CREATE_TABLE_SQL)
        cur = con.executemany(INSERT_SQL, spots)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def maidenhead_to_latlon(locator: str) -> tuple[float, float] | None:
    loc = locator.strip().upper()
    if len(loc) < 4 or len(loc) % 2 != 0:
        return None
    try:
        lon = (ord(loc[0]) - ord("A")) * 20 - 180
        lat = (ord(loc[1]) - ord("A")) * 10 - 90
        lon += (ord(loc[2]) - ord("0")) * 2
        lat += (ord(loc[3]) - ord("0")) * 1
        if len(loc) >= 6:
            lon += (ord(loc[4]) - ord("A")) * (2 / 24)
            lat += (ord(loc[5]) - ord("A")) * (1 / 24)
            lon += 1 / 24
            lat += 1 / 48
        else:
            lon += 1
            lat += 0.5
        return (lat, lon)
    except (IndexError, ValueError):
        return None


def query_rx_spots(db_file: str, since: str | None = None) -> list[dict]:
    if not os.path.exists(db_file):
        return []
    con = sqlite3.connect(db_file)
    try:
        has_table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rx_spots'"
        ).fetchone()
        if not has_table:
            return []
        if since:
            rows = con.execute(
                "SELECT * FROM rx_spots WHERE time >= ? ORDER BY time DESC", (since,)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM rx_spots ORDER BY time DESC").fetchall()
        cols = [d[0] for d in con.execute("SELECT * FROM rx_spots LIMIT 0").description]
        spots = [dict(zip(cols, r)) for r in rows]
        for s in spots:
            b = s.get("band")
            s["band_label"] = f"{b}m" if b is not None else "?"
        return spots
    finally:
        con.close()


def generate_html(db_file: str, output_path: str, tx_callsign: str, tx_loc: str):
    coords = maidenhead_to_latlon(tx_loc)
    if not coords:
        print(f"Warning: invalid TX locator '{tx_loc}', using (0, 0)", file=sys.stderr)
        tx_lat, tx_lon = 0.0, 0.0
    else:
        tx_lat, tx_lon = coords

    one_week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    spots = query_rx_spots(db_file, since=one_week_ago)

    rx_json = json.dumps(spots, default=str)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wspr_rx_remote_template.html")
    with open(template_path, "r") as f:
        template = Template(f.read())

    html = template.safe_substitute(
        TX_CALLSIGN=tx_callsign,
        TX_LOC=tx_loc,
        TX_LAT=f"{tx_lat:.4f}",
        TX_LON=f"{tx_lon:.4f}",
        GENERATED=now_str,
        RX_JSON=rx_json,
    )

    with open(output_path, "w") as f:
        f.write(html)

    print(f"Report written → {output_path} ({len(spots)} spots)")


def main():
    parser = argparse.ArgumentParser(description="Fetch WSPR RX spots into SQLite.")
    parser.add_argument("--callsign", default=CALLSIGN, help=f"TX callsign (default: {CALLSIGN})")
    parser.add_argument("--db",       default=DB_FILE,  help=f"SQLite file (default: {DB_FILE})")
    parser.add_argument("--hours",    default=HOURS,    type=int, help=f"Look-back window in hours (default: {HOURS})")
    parser.add_argument("--limit",    default=LIMIT,    type=int, help=f"Max rows to fetch, 0=unlimited (default: {LIMIT})")
    parser.add_argument("--output",   default="wspr_rx_remote.html", help="Generate HTML report to this file (default: wspr_rx_remote.html)")
    parser.add_argument("--tx-loc",   default=TX_LOC,   help=f"TX Maidenhead locator for map center (default: {TX_LOC})")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    print(f"Querying RX spots for {args.callsign} since {since.strftime('%Y-%m-%d %H:%M UTC')} ...")

    query  = build_query(args.callsign, since, args.limit)
    spots  = fetch_spots(query)

    if not spots:
        print("No spots returned.")
    else:
        inserted = store_spots(args.db, spots)
        print(f"Inserted {inserted} new rows into {args.db} (duplicates skipped).")

    generate_html(args.db, args.output, args.callsign, args.tx_loc)


if __name__ == "__main__":
    main()
