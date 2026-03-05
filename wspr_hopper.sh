#!/bin/bash
# WSPR spot collector — cycles through bands using rtlsdr_wsprd
# Logs are stored as daily TSV files with a symlink for the analyzer.
# See: https://github.com/filipsPL/rtlsdr-wsprd-report

set -euo pipefail

# --- Configuration ---
CALLSIGN="SP5FLS"
LOCATOR="KO02MC77"
WSPR_CYCLES=2                                     # number of full WSPR cycles (120s each) per band session
MARGIN=7                                          # seconds to subtract from WSPR_CYCLES*120 to ensure we stop before the next cycle starts
BANDS_DAY="40m 30m 20m 17m 15m 12m 10m 6m"       # 06-18 UTC
BANDS_NIGHT="160m 80m 60m 40m 30m"                # 18-06 UTC
DAY_START=6                                       # UTC hour when day begins
DAY_END=18                                        # UTC hour when night begins
RTL_GAIN=39

WSPRD_PATH="/home/filips/bin/rtlsdr_wsprd"
LOG_ANALYZER="/home/filips/software/rtlsdr-wsprd-report/wspr_analyzer.py"
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

# Return the appropriate band list for the current UTC hour
current_bands() {
    local hour
    hour=$(date -u +%-H)
    if (( hour >= DAY_START && hour < DAY_END )); then
        echo "$BANDS_DAY"
    else
        echo "$BANDS_NIGHT"
    fi
}

# Calculate seconds from now until the next aligned band-start:
#   even UTC minute - MARGIN seconds, at least WSPR_CYCLES*120 seconds away.
# WSPR cycle = 120 s; aligned phase = 120 - MARGIN (e.g. 113 for MARGIN=7).
calc_duration() {
    local now cycle min_duration target secs
    now=$(date -u +%s)
    cycle=120
    min_duration=$(( WSPR_CYCLES * cycle ))
    target=$(( cycle - MARGIN ))
    secs=$(( (target - now % cycle + cycle) % cycle ))
    while (( secs < min_duration )); do
        secs=$(( secs + cycle ))
    done
    echo "$secs"
}

# --- Signal handling ---

_cleanup() {
    echo ""
    echo "WSPR hopper stopped."
    exit 0
}
trap _cleanup INT TERM

# --- Main ---

mkdir -p "$LOG_DIR"

while true; do
    # Housekeeping: rotate symlink and purge old logs at the start of each cycle
    logfile="$(today_log)"
    update_symlink "$logfile"
    cleanup_old_logs

    bands=$(current_bands)
    for band in $bands; do
        # Date may have rolled over mid-cycle — check again
        logfile="$(today_log)"
        update_symlink "$logfile"

        dur=$(calc_duration)
        echo ""
        echo "-------- $(date '+%Y-%m-%d %H:%M:%S') — Band: ${band} (${dur}s) --------"
        timeout "${dur}s" \
            $WSPRD_PATH -f "$band" -c "$CALLSIGN" -l "$LOCATOR" \
                           -g "$RTL_GAIN" -F "tsv:${logfile}" \
        || true   # don't exit on timeout (rc=124) or decode failure

        echo "Session on ${band} finished."
        python "$LOG_ANALYZER" "$CURRENT_LINK" "$LOCATOR" --db "$LOG_ANALYZER_DB" --output "$LOG_ANALYZER_HTML"
        sleep 2
    done
done

