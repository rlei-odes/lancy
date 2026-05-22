#!/bin/bash
# Lancy — Start backend + frontend (both services). Pass DEV as first arg for frontend dev mode.
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
VENV="$REPO/.venv"
MODE="${1:-prod}"

mkdir -p "$LOG_DIR"

# --- Venv check ---
if [ ! -f "$VENV/bin/python" ]; then
    echo "ERROR: No virtual environment found at $VENV"
    echo "   Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
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
    echo "     source .venv/bin/activate"
    echo "     pip install -r requirements.txt"
    exit 1
fi

# --- Ollama check ---
OLLAMA_HOST="${OLLAMA_HOST:-localhost:11434}"
if curl -sf "http://$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
    echo "Ollama is running at $OLLAMA_HOST"

    # Check if the configured LLM model is pulled
    LLM_MODEL=$("$VENV/bin/python" -c "import json; d=json.load(open('$REPO/backend/src/lancy/db/rag_config.json')); print(d.get('llm_model',''))" 2>/dev/null)
    if [ -n "$LLM_MODEL" ]; then
        if curl -sf "http://$OLLAMA_HOST/api/tags" | grep -q "\"$LLM_MODEL\""; then
            echo "  Model '$LLM_MODEL' is available"
        else
            echo "⚠️  Model '$LLM_MODEL' is not pulled yet"
            echo "   Run: ollama pull $LLM_MODEL"
            echo "   The backend will start but LLM calls will fail until the model is available."
            echo ""
        fi
    fi
else
    # Check if ollama binary exists at all
    if ! command -v ollama > /dev/null 2>&1 && [ ! -f /usr/local/bin/ollama ]; then
        echo "⚠️  Warning: Ollama is not installed (binary not found)."
        echo "   Install it with: curl -fsSL https://ollama.com/install.sh | sh"
        echo "   Then start it with: ollama serve"
    else
        echo "⚠️  Warning: Ollama does not appear to be running at $OLLAMA_HOST"
        echo "   Start it with: ollama serve"
    fi
    echo "   The backend will start but LLM calls will fail until Ollama is available."
    echo ""
fi

# --- Requirements ---
echo "Checking Python requirements..."
if ! (cd "$REPO" && "$VENV/bin/pip" install -r requirements.txt -q 2>&1); then
    echo "ERROR: Failed to install requirements. Check requirements.txt."
    exit 1
fi

# --- Backend ---
echo "Starting backend (Ollama / mistral-nemo:12b) on port 8080..."
PYTHONPATH="$REPO/backend/src" \
BACKEND=ollama \
LOG_FILE="$LOG_DIR/backend.log" \
HF_HUB_OFFLINE=1 \
  "$VENV/bin/python" -m lancy.main \
  > /dev/null 2>&1 &
echo $! > "$LOG_DIR/backend.pid"
echo "  Backend PID: $(cat $LOG_DIR/backend.pid)"

# --- Frontend ---
echo "Starting frontend on port 3000 (mode: $MODE)..."
cd "$REPO/frontend"
if [ package-lock.json -nt node_modules/.package-lock.json ] 2>/dev/null || [ ! -d node_modules ]; then
    echo "  Running npm install..."
    npm install -q
fi
> "$LOG_DIR/frontend.log"
if [ "$MODE" = "DEV" ]; then
    FIFO="$LOG_DIR/frontend.fifo"
    rm -f "$FIFO" && mkfifo "$FIFO"
    awk '{ print strftime("[%Y-%m-%d %H:%M:%S]"), $0; fflush() }' < "$FIFO" >> "$LOG_DIR/frontend.log" &
    node_modules/.bin/next dev > "$FIFO" 2>&1 &
    echo $! > "$LOG_DIR/frontend.pid"
    rm -f "$FIFO"
else
    echo "  Building for production..."
    node_modules/.bin/next build >> "$LOG_DIR/frontend.log" 2>&1
    node_modules/.bin/next start > >(awk '{ print strftime("[%Y-%m-%d %H:%M:%S]"), $0; fflush() }' >> "$LOG_DIR/frontend.log") 2>&1 &
    echo $! > "$LOG_DIR/frontend.pid"
fi
echo "  Frontend PID: $(cat $LOG_DIR/frontend.pid)"

echo ""
echo "Lancy is running:"
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8080"
echo ""
echo "Logs: $LOG_DIR/"
echo "Stop: ./stop.sh"
