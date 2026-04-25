#!/bin/bash
# Lancy — Start backend only (for Spark / remote backend deployment)
# Does not start the frontend. Skips the Ollama check — vLLM handles LLM inference on this machine.
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
VENV="$REPO/.venv"

mkdir -p "$LOG_DIR"

# --- Venv check ---
if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: No virtual environment found at $VENV"
    echo "   Run: scripts/spark-install.sh"
    exit 1
fi
if ! "$VENV/bin/python" -c "import uvicorn" 2>/dev/null; then
    VENV_PY_VER=$("$VENV/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
    SYS_PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
    echo "ERROR: The virtual environment appears to be broken (uvicorn not importable)."
    if [ "$VENV_PY_VER" != "$SYS_PY_VER" ]; then
        echo "   Cause: venv was created with Python $VENV_PY_VER but system Python is now $SYS_PY_VER."
    fi
    echo "   Fix: recreate the venv:"
    echo "     rm -rf .venv"
    echo "     python3 -m venv .venv"
    echo "     pip install -r requirements.txt"
    exit 1
fi

# --- Already running? ---
if [ -f "$LOG_DIR/backend.pid" ] && kill -0 "$(cat $LOG_DIR/backend.pid)" 2>/dev/null; then
    echo "Backend is already running (PID $(cat $LOG_DIR/backend.pid))."
    echo "  Stop it first with: scripts/stop-backend.sh"
    exit 1
fi

# --- Backend ---
echo "Starting Lancy backend on port 8080..."
PYTHONPATH="$REPO/backend/src" \
  "$VENV/bin/python" -m lancy.main \
  > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$LOG_DIR/backend.pid"
echo "  Backend PID: $(cat $LOG_DIR/backend.pid)"
echo "  Log:         $LOG_DIR/backend.log"
echo "  Stop:        scripts/stop-backend.sh"
echo ""
echo "Backend API: http://$(hostname -I | awk '{print $1}'):8080"
