#!/bin/bash
# Lancy — Start backend only (for Spark / remote backend deployment)
# Does not start the frontend. Skips the Ollama check — vLLM handles LLM inference on this machine.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
VENV="$REPO/.venv"
PORT=8080

mkdir -p "$LOG_DIR"

# --- Venv check ---
if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: No virtual environment found at $VENV"
    echo "   Run: scripts/install-backend.sh"
    exit 1
fi

VENV_PY_VER=$("$VENV/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
SYS_PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
if [ "$VENV_PY_VER" != "$SYS_PY_VER" ]; then
    echo "WARNING: venv Python ($VENV_PY_VER) differs from system Python ($SYS_PY_VER)."
    echo "   If packages fail to import, recreate the venv:"
    echo "     rm -rf .venv && python3 -m venv .venv && pip install -r requirements.txt"
fi

# --- Requirements ---
if [ -f "$REPO/requirements.txt" ]; then
    echo "Checking requirements..."
    if ! (cd "$REPO" && "$VENV/bin/pip" install -r requirements.txt -q 2>&1); then
        echo "ERROR: Failed to install requirements. Check requirements.txt."
        exit 1
    fi
fi

# --- Already running? ---
if [ -f "$LOG_DIR/backend.pid" ] && kill -0 "$(cat "$LOG_DIR/backend.pid")" 2>/dev/null; then
    echo "Backend is already running (PID $(cat "$LOG_DIR/backend.pid"))."
    echo "  Stop it first with: scripts/stop-backend.sh"
    exit 1
fi

# --- Port availability check ---
if ss -tunlp | grep -q ":$PORT "; then
    echo "ERROR: Port $PORT is already in use by another process."
    exit 1
fi

# --- Backend ---
echo "Starting Lancy backend on port $PORT..."
PYTHONPATH="$REPO/backend/src" \
  "$VENV/bin/python" -m lancy.main \
  > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > "$LOG_DIR/backend.pid"

# Give the process a moment to either crash or stabilise
sleep 3
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "ERROR: Backend failed to start. Last log output:"
    echo "---"
    tail -20 "$LOG_DIR/backend.log"
    echo "---"
    echo "  Full log: $LOG_DIR/backend.log"
    rm -f "$LOG_DIR/backend.pid"
    exit 1
fi

echo "  Backend PID: $BACKEND_PID"
echo "  Log:         $LOG_DIR/backend.log"
echo "  Stop:        scripts/stop-backend.sh"
echo ""
echo "Backend API: http://$(hostname -I | awk '{print $1}'):$PORT"
