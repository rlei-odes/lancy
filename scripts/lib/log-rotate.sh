#!/bin/bash
# Lancy — shared log rotation for the start scripts.
#
# The backend's Python logger rotates logs/backend.log by itself (see
# backend/src/lancy/logging_config.py). Everything else we launch — the Next.js
# server, and the raw stdout/stderr of the backend process — reaches disk through
# a plain shell redirect, which neither rotates nor survives a restart. That is
# what this file is for.
#
# Usage:
#     source "$REPO/scripts/lib/log-rotate.sh"
#     rotate_log "$LOG_DIR/frontend.log"
#     next dev > >(log_writer "$LOG_DIR/frontend.log") 2>&1 &
#
# Defaults match the backend's and honour the same env vars, so one setting
# covers every log the project writes.

LOG_MAX_BYTES="${LOG_MAX_BYTES:-10485760}"   # 10 MB
LOG_BACKUP_COUNT="${LOG_BACKUP_COUNT:-5}"

# Absolute path to this file, resolved once at source time. log_writer hands it
# to awk, which calls back into it from a working directory of its own (the
# start scripts cd into frontend/ before launching), so a relative path would
# not survive the trip.
_LOG_ROTATE_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

# rotate_log <file> [backups]
#
# Cascade <file> → <file>.1 → … → <file>.<backups>, dropping the oldest.
# Called at startup in place of truncating the file, so the run that just
# crashed is still readable after the restart, and called by log_writer when a
# file outgrows its size cap.
rotate_log() {
    local file="$1"
    local backups="${2:-$LOG_BACKUP_COUNT}"
    local i

    # Nothing worth keeping — leave it be rather than shifting a cascade of
    # empty backups over real history.
    if [ ! -s "$file" ]; then
        return 0
    fi

    rm -f "$file.$backups"
    for (( i = backups - 1; i >= 1; i-- )); do
        if [ -f "$file.$i" ]; then
            mv -f "$file.$i" "$file.$((i + 1))"
        fi
    done
    mv -f "$file" "$file.1"
}

# log_writer <file> [maxBytes] [backups]
#
# Reads stdin, timestamps each line, appends it to <file>, and rotates as soon
# as the file passes maxBytes — so an unattended run stays bounded at roughly
# maxBytes × (backups + 1) instead of growing until the disk fills.
#
# Run it as the write end of a service's output:
#     cmd > >(log_writer "$LOG") 2>&1 &
#
# It tracks the size it has written rather than stat-ing the file per line, so
# it picks up whatever the file already holds on entry. LC_ALL=C makes awk's
# length() count bytes instead of characters, keeping the cap honest for
# non-ASCII output.
log_writer() {
    local file="$1"
    local max="${2:-$LOG_MAX_BYTES}"
    local backups="${3:-$LOG_BACKUP_COUNT}"
    local start_size=0

    if [ -f "$file" ]; then
        start_size="$(wc -c < "$file" | tr -d '[:space:]')"
    fi

    LC_ALL=C awk \
        -v LOG="$file" \
        -v MAX="$max" \
        -v BACKUPS="$backups" \
        -v ROTATOR="$_LOG_ROTATE_SH" \
        -v SIZE="$start_size" '
        {
            line = strftime("[%Y-%m-%d %H:%M:%S] ") $0
            print line >> LOG
            fflush(LOG)
            SIZE += length(line) + 1
            if (SIZE >= MAX) {
                # Close before handing the path to the rotator; the next print
                # reopens it, by then a fresh empty file.
                close(LOG)
                system("bash \"" ROTATOR "\" \"" LOG "\" " BACKUPS)
                SIZE = 0
            }
        }
    '
}

# Executed rather than sourced: rotate the file named on the command line.
# This is how log_writer's awk rotates mid-stream.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    rotate_log "$@"
fi
