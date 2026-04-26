#!/bin/bash
# Lancy — Stop frontend only

LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs"

if [ -f "$LOG_DIR/frontend.pid" ]; then
    FRONTEND_PID="$(cat $LOG_DIR/frontend.pid)"
    pkill -P "$FRONTEND_PID" 2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null && echo "Frontend stopped" || echo "Frontend was already stopped"
    rm "$LOG_DIR/frontend.pid"
else
    echo "No PID file found — trying port 3000..."
    fuser -k 3000/tcp 2>/dev/null && echo "Port 3000 cleared." || echo "Nothing on port 3000."
    exit 0
fi

fuser -k 3000/tcp 2>/dev/null || true
echo "Done."
