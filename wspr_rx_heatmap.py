#!/usr/bin/env python3
"""
WSPR RX Heatmap — all-time world heatmap of received stations.
https://github.com/filipsPL/rtlsdr-wsprd-report
Reads all reception data from SQLite and generates a world heatmap HTML report.
Usage: python wspr_rx_heatmap.py [--db wspr.db] [--output wspr_rx_heatmap.html]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from string import Template


def band_sort_key(b: str) -> int:
    try:
        return int(b.replace("m", "").replace("MHz", ""))
    except ValueError:
        return 99999


def main():
    parser = argparse.ArgumentParser(description="WSPR Global Analysis")
    parser.add_argument("--db", default="wspr.db", help="SQLite database path (default: wspr.db)")
    parser.add_argument("--output", default="wspr_rx_heatmap.html", help="Output HTML file (default: wspr_rx_heatmap.html)")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Error: database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    total_spots = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    if total_spots == 0:
        print("No observations found in database.", file=sys.stderr)
        sys.exit(1)

    date_range_row = conn.execute("SELECT MIN(date), MAX(date) FROM observations").fetchone()
    date_range = f"{date_range_row[0]} – {date_range_row[1]}"

    unique_calls = conn.execute("SELECT COUNT(DISTINCT call) FROM observations").fetchone()[0]
    unique_locs = conn.execute("SELECT COUNT(DISTINCT loc) FROM observations").fetchone()[0]

    # All bands
    bands_rows = conn.execute("SELECT DISTINCT band FROM observations WHERE band IS NOT NULL").fetchall()
    all_bands = sorted([r[0] for r in bands_rows], key=band_sort_key)

    # --- Heatmap: band × hour ---
    heatmap_rows = conn.execute(
        """
        SELECT CAST(substr(time, 1, 2) AS INTEGER) AS hour, band, COUNT(*) AS count
        FROM observations
        WHERE band IS NOT NULL AND time IS NOT NULL AND length(time) >= 2
        GROUP BY hour, band
        ORDER BY hour, band
        """
    ).fetchall()

    heatmap_data = [{"hour": r["hour"], "band": r["band"], "count": r["count"]} for r in heatmap_rows]

    # --- Heatmap points for Leaflet: [lat, lon, intensity] ---
    # Aggregate by locator grid square to reduce payload; intensity = spot count
    loc_rows = conn.execute(
        """
        SELECT lat, lon, COUNT(*) as count
        FROM observations
        WHERE lat IS NOT NULL AND lon IS NOT NULL
        GROUP BY lat, lon
        """
    ).fetchall()

    max_loc_count = max((r["count"] for r in loc_rows), default=1)
    heat_points = [
        [round(r["lat"], 4), round(r["lon"], 4), round(r["count"] / max_loc_count, 4)]
        for r in loc_rows
    ]

    conn.close()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wspr_rx_heatmap_template.html")
    with open(template_path, "r") as f:
        template = Template(f.read())

    html = template.safe_substitute(
        GENERATED=now_str,
        TOTAL_SPOTS=f"{total_spots:,}",
        UNIQUE_CALLS=f"{unique_calls:,}",
        UNIQUE_LOCS=f"{unique_locs:,}",
        NUM_BANDS=len(all_bands),
        DATE_RANGE=date_range,
        HEATMAP_JSON=json.dumps(heatmap_data),
        HEAT_POINTS_JSON=json.dumps(heat_points),
    )

    with open(args.output, "w") as f:
        f.write(html)

    print(f"Total spots:     {total_spots:,}")
    print(f"Unique stations: {unique_calls:,}")
    print(f"Unique locators: {unique_locs:,}")
    print(f"Bands:           {', '.join(all_bands)}")
    print(f"Date range:      {date_range}")
    print(f"Report written → {args.output}")


if __name__ == "__main__":
    main()
