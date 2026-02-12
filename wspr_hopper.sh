#!/bin/bash
# WSPR spot collector — cycles through bands using rtlsdr_wsprd
# Logs are stored as daily TSV files with a symlink for the analyzer.
# See: https://github.com/filipsPL/rtlsdr-wsprd-report

set -euo pipefail

# --- Configuration ---
CALLSIGN="SP5FLS"
LOCATOR="KO02MC77"
DURATION=285                                      # seconds per band session
BANDS="40m 30m 20m 17m 15m 12m 10m 6m"
RTL_GAIN=39

WSPRD_PATH="/home/filips/bin/rtlsdr_wsprd"
LOG_ANALYZER="/home/filips/software/rtlsdr-wsprd-analyzer/wspr_analyzer.py"
LOG_ANALYZER_DB="/mnt/ramdisk/rrd_dbases/wspr_log_analyzer.sqlite"
LOG_ANALYZER_HTML="/mnt/ramdisk/rrd_dbases/wspr_log_analyzer.html"

LOG_DIR="/mnt/ramdisk/filips-logs/wspr_logs"                             # daily log directory
CURRENT_LINK="${LOG_DIR}/spots-current.tsv"        # symlink to today's file
LOG_RETENTION_DAYS=30                              # remove logs older than this


# --- Functions ---

# Return path to today's log file, creating it with a header if needed
today_log() {
    local logfile="${LOG_DIR}/$(date +%Y-%m-%d).tsv"
    if [[ ! -f "$logfile" ]]; then
        echo -e "date\ttime\tsnr\tdt\tfreq\tdrift\tcall\tloc\tpwr" > "$logfile"
    fi
    echo "$logfile"
}

# Update the spots-current.tsv symlink to point to today's log
update_symlink() {
    local logfile="$1"
    ln -sf "$(basename "$logfile")" "$CURRENT_LINK"
}

# Remove log files older than LOG_RETENTION_DAYS
cleanup_old_logs() {
    find "$LOG_DIR" -name "*.tsv" -type f -mtime "+${LOG_RETENTION_DAYS}" -delete

}

# --- Main ---

mkdir -p "$LOG_DIR"

while true; do
    # Housekeeping: rotate symlink and purge old logs at the start of each cycle
    logfile="$(today_log)"
    update_symlink "$logfile"
    cleanup_old_logs

    for band in $BANDS; do
        # Date may have rolled over mid-cycle — check again
        logfile="$(today_log)"
        update_symlink "$logfile"

        echo ""
        echo "-------- $(date '+%Y-%m-%d %H:%M:%S') — Band: ${band} --------"
        timeout "${DURATION}s" \
            $WSPRD_PATH -f "$band" -c "$CALLSIGN" -l "$LOCATOR" \
                           -g "$RTL_GAIN" -F "tsv:${logfile}" \
        || true   # don't exit on timeout (rc=124) or decode failure

        echo "Session on ${band} finished."
        python "$LOG_ANALYZER" "$logfile" "$LOCATOR" --db "$LOG_ANALYZER_DB" --output "$LOG_ANALYZER_HTML"
        sleep 2
    done
done

