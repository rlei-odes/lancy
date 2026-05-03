#!/bin/bash
# Lancy — Start frontend only (for split deployment: backend on Spark, frontend local)
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
FRONTEND="$REPO/frontend"

mkdir -p "$LOG_DIR"

# --- Already running? ---
if [ -f "$LOG_DIR/frontend.pid" ] && kill -0 "$(cat $LOG_DIR/frontend.pid)" 2>/dev/null; then
    echo "Frontend is already running (PID $(cat $LOG_DIR/frontend.pid))."
    echo "  Stop it first with: scripts/stop-frontend.sh"
    exit 1
fi

# --- Show which backend URL is in use ---
BACKEND_URL=$(grep -E '^BACKEND_URL=' "$FRONTEND/.env" 2>/dev/null | cut -d= -f2-)
echo "Starting frontend on port 3000..."
echo "  Backend URL: ${BACKEND_URL:-http://localhost:8080 (default)}"

# --- Frontend ---
cd "$FRONTEND"
node_modules/.bin/next dev > "$LOG_DIR/frontend.log" 2>&1 &
echo $! > "$LOG_DIR/frontend.pid"
echo "  Frontend PID: $(cat $LOG_DIR/frontend.pid)"
echo "  Log:          $LOG_DIR/frontend.log"
echo "  Stop:         scripts/stop-frontend.sh"
echo ""
echo "Frontend: http://localhost:3000"
