#!/bin/bash
# Lancy — Stop backend (Spark / remote backend deployment)
#
# Graceful shutdown is not cosmetic here. The backend's lifespan handler tears
# down the docling chunking ProcessPoolExecutor, whose "spawn" children each hold
# their own CUDA context. SIGKILLing the parent skips that handler (and Python's
# atexit), leaving those children reparented to init and pinning GPU memory until
# the machine is rebooted. So: SIGTERM, wait, and only then escalate.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
VENV_PY="$REPO/.venv/bin/python"
GRACE=20   # seconds to allow for graceful shutdown before SIGKILL

# Matches the ProcessPoolExecutor("spawn") children and the multiprocessing
# resource tracker, scoped to this repo's interpreter so unrelated Python
# processes on the host are never touched.
WORKER_PAT="^${VENV_PY} -c from multiprocessing"

wait_for_exit() {
    local pid=$1 timeout=$2 waited=0
    while kill -0 "$pid" 2>/dev/null; do
        [ "$waited" -ge "$timeout" ] && return 1
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

# --- Stop the main process ---
if [ -f "$LOG_DIR/backend.pid" ]; then
    PID="$(cat "$LOG_DIR/backend.pid")"
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping backend (PID $PID), allowing ${GRACE}s for graceful shutdown..."
        kill -TERM "$PID" 2>/dev/null
        if wait_for_exit "$PID" "$GRACE"; then
            echo "Backend stopped cleanly."
        else
            echo "WARNING: backend still alive after ${GRACE}s — sending SIGKILL."
            echo "         Chunking workers may have been orphaned; sweeping below."
            kill -KILL "$PID" 2>/dev/null
            wait_for_exit "$PID" 5 || echo "ERROR: PID $PID will not terminate."
        fi
    else
        echo "Backend was already stopped (stale PID file)."
    fi
    rm -f "$LOG_DIR/backend.pid"
else
    echo "No PID file found — checking for stray processes."
fi

# --- Sweep orphaned chunking workers ---
# Runs unconditionally: orphans outlive the parent that spawned them, so they are
# still present on a run where the PID file is already gone.
ORPHANS="$(pgrep -f "$WORKER_PAT" 2>/dev/null || true)"
if [ -n "$ORPHANS" ]; then
    echo "Found orphaned chunking workers: $(echo "$ORPHANS" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill -TERM $ORPHANS 2>/dev/null
    sleep 3
    STILL="$(pgrep -f "$WORKER_PAT" 2>/dev/null || true)"
    if [ -n "$STILL" ]; then
        # shellcheck disable=SC2086
        kill -KILL $STILL 2>/dev/null
        echo "Force-killed: $(echo "$STILL" | tr '\n' ' ')"
    fi
    echo "Orphaned workers cleared."
fi

# --- Port check ---
# Reported, not killed: a blind `fuser -k` here sends SIGKILL and was what
# defeated the graceful shutdown above in the first place.
if ss -tunlp 2>/dev/null | grep -q ":8080 "; then
    echo "WARNING: something still holds port 8080 —"
    ss -tunlp 2>/dev/null | grep ":8080 "
fi

echo "Done."
