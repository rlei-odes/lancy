#!/bin/bash
# Lancy — Stop backend (Spark / remote backend deployment)

LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs"

if [ -f "$LOG_DIR/backend.pid" ]; then
    kill "$(cat $LOG_DIR/backend.pid)" 2>/dev/null && echo "Backend stopped" || echo "Backend was already stopped"
    rm "$LOG_DIR/backend.pid"
else
    echo "No PID file found — trying port 8080..."
    fuser -k 8080/tcp 2>/dev/null && echo "Port 8080 cleared." || echo "Nothing on port 8080."
    exit 0
fi

fuser -k 8080/tcp 2>/dev/null || true
echo "Done."
