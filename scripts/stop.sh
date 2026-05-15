#!/bin/bash
# Lancy — Stop backend + frontend

LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs"

if [ -f "$LOG_DIR/backend.pid" ]; then
    kill "$(cat $LOG_DIR/backend.pid)" 2>/dev/null && echo "Backend stopped" || echo "Backend was already stopped"
    rm "$LOG_DIR/backend.pid"
fi

if [ -f "$LOG_DIR/frontend.pid" ]; then
    FRONTEND_PID="$(cat $LOG_DIR/frontend.pid)"
    # Kill child processes first (Next.js worker threads), then the main process
    pkill -P "$FRONTEND_PID" 2>/dev/null
    kill "$FRONTEND_PID" 2>/dev/null && echo "Frontend stopped" || echo "Frontend was already stopped"
    rm "$LOG_DIR/frontend.pid"
fi

fuser -k 8080/tcp 2>/dev/null
fuser -k 3000/tcp 2>/dev/null
echo "Done."
