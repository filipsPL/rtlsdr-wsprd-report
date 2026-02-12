# WSPR Monitor

A set of *simple* tools for unattended Weak Signal Propagation Reporter [WSPR](https://en.wikipedia.org/wiki/WSPR_(amateur_radio_software)) spot collection and visualization using an RTL-SDR receiver and [rtlsdr_wsprd](https://github.com/filipsPL/rtlsdr-wsprd/) (modified). The report is single, self-containing html file, so no telegraf, grafana, influxdb etc. ;-) 

## Components

**`wspr_hopper.sh`** — Band-hopping collector. Cycles `rtlsdr_wsprd` ([fork](https://github.com/filipsPL/rtlsdr-wsprd/)) through configured bands, writing spots to daily TSV log files (`wspr_logs/YYYY-mm-dd.tsv`). A symlink `spots-current.tsv` always points to today's file. Logs older than 30 days are automatically removed.

**`wspr_analyzer.py`** — Report generator. Reads WSPR TSV logs, stores observations in a SQLite database (with deduplication), and produces a self-contained static HTML dashboard (~80-200 kb, depending on the number of observations) covering the last 7 days.


| screenshot 1                               | screenshot 2                                              |
| ------------------------------------------ | --------------------------------------------------------- |
| ![screenshot1](obrazki/obrazek-README.png) | ![screenshot visualization](obrazki/obrazek-README-1.png) |


The dashboard includes:

- Interactive map (Leaflet) showing paths from your QTH to each spotted station, color-coded by band
- Time window selector (1h / 6h / 12h / 24h / 7d)
- Summary stats: spot count, unique stations, bands active, max distance
- Hour vs Band activity heatmap
- Rose histogram showing the signal direction distribution
- Sortable observation table with calculated distance

## Requirements

- Python 3.10+, no external packages
- [rtlsdr_wsprd](https://github.com/filipsPL/rtlsdr-wsprd/) - please note 💡 this is the modified version that saves log to file (which is needed to be processed by the python script)
- RTL-SDR dongle for spot collection
- A web browser to view the report (uses Leaflet and CartoDB tiles via CDN)

## Usage

### Collecting spots

Edit the configuration section at the top of `wspr_hopper.sh` (callsign, locator, bands, gain), then run:

```bash
chmod +x wspr_hopper.sh
./wspr_hopper.sh
```
It will loop indefinitely, spending ~5 minutes on each band. Spots are appended to `wspr_logs/YYYY-mm-dd.tsv`. It has incorporated `wspr_analyzer.py` call every time the band is changed.

### Generating the report

(when used without `wspr_hopper.sh` or if you want your reports more frequently than every `wspr_hopper.sh` loop)

```bash
python3 wspr_analyzer.py samples/spots.tsv KO02 --db samples/wspr.db --output samples/wspr_report.html
```

| Argument   | Description                                         |
| ---------- | --------------------------------------------------- |
| `tsv_file` | Path to WSPR TSV log (positional, required)         |
| `locator`  | Your Maidenhead grid locator (positional, required) |
| `--db`     | SQLite database path (default: `wspr.db`)           |
| `--output` | Output HTML file (default: `wspr_report.html`)      |

Re-importing the same file is safe — duplicate observations are skipped.

To ingest all accumulated logs at once:

```bash
for f in wspr_logs/2026-*.tsv; do
    python3 wspr_analyzer.py "$f" KO02
done
```

