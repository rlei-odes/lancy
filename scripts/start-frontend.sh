#!/bin/bash
# Lancy — Start frontend only (for split deployment: backend on Spark, frontend local)
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
FRONTEND="$REPO/frontend"

source "$REPO/scripts/lib/log-rotate.sh"

mkdir -p "$LOG_DIR"

# --- Already running? ---
if [ -f "$LOG_DIR/frontend.pid" ] && kill -0 "$(cat $LOG_DIR/frontend.pid)" 2>/dev/null; then
    echo "Frontend is already running (PID $(cat $LOG_DIR/frontend.pid))."
    echo "  Stop it first with: scripts/stop-frontend.sh"
    exit 1
fi

# --- Mode: DEV arg → dev server, default → production build+start ---
MODE="${1:-prod}"

# --- Show which backend URL is in use ---
BACKEND_URL=$(grep -E '^BACKEND_URL=' "$FRONTEND/.env" 2>/dev/null | cut -d= -f2-)
echo "Starting frontend on port 3000 (mode: $MODE)..."
echo "  Backend URL: ${BACKEND_URL:-http://localhost:8080 (default)}"

# --- Frontend ---
cd "$FRONTEND"
if [ package-lock.json -nt node_modules/.package-lock.json ] 2>/dev/null || [ ! -d node_modules ]; then
    echo "  Running npm install..."
    npm install -q
fi
# Roll the previous run aside instead of truncating it — a crash you restart
# out of is still readable in frontend.log.1.
rotate_log "$LOG_DIR/frontend.log"
if [ "$MODE" = "DEV" ]; then
    node_modules/.bin/next dev > >(log_writer "$LOG_DIR/frontend.log") 2>&1 &
else
    echo "  Building for production..."
    node_modules/.bin/next build > >(log_writer "$LOG_DIR/frontend.log") 2>&1
    node_modules/.bin/next start > >(log_writer "$LOG_DIR/frontend.log") 2>&1 &
fi
echo $! > "$LOG_DIR/frontend.pid"
echo "  Frontend PID: $(cat $LOG_DIR/frontend.pid)"
echo "  Log:          $LOG_DIR/frontend.log"
echo "  Stop:         scripts/stop-frontend.sh"
echo ""
echo "Frontend: http://localhost:3000"
