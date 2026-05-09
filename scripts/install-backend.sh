#!/bin/bash
# Lancy — Backend install script
# Run once on a fresh machine to set up the backend.
set -e

REPO_URL="https://github.com/rlei-odes/lancy.git"
INSTALL_DIR="$HOME/lancy"
PYTHON="python3"

echo "==> Checking system dependencies..."
sudo apt-get update -q
sudo apt-get install -y git python3 python3-venv python3-pip curl

echo "==> Cloning repo..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Repo already present — pulling latest."
    git -C "$INSTALL_DIR" pull
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

echo "==> Creating virtual environment..."
$PYTHON -m venv .venv

echo "==> Installing Python dependencies..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "==> Pre-downloading embedding models to HuggingFace cache..."
echo "    (backend runs with HF_HUB_OFFLINE=1 — models must be cached before first use)"
.venv/bin/python - <<'EOF'
from sentence_transformers import SentenceTransformer

MODELS = [
    ("nomic-ai/nomic-embed-text-v1",          {"trust_remote_code": True}),
    ("intfloat/multilingual-e5-large",         {}),
    ("BAAI/bge-m3",                            {}),
    ("sentence-transformers/all-MiniLM-L6-v2", {}),
]

for name, kwargs in MODELS:
    try:
        print(f"  Downloading {name}...")
        SentenceTransformer(name, **kwargs)
        print(f"  OK: {name}")
    except Exception as exc:
        print(f"  WARN: {name} — {exc}")
EOF

echo ""
echo "Install complete."
echo ""
echo "Next steps:"
echo "  1. Configure your KB data path in the Lancy UI (use a path outside the repo, e.g. ~/data/)"
echo "  2. HuggingFace models (nomic-embed-text, etc.) download to ~/.cache/huggingface on first run."
echo "     Ensure at least 2 GB of free disk space."
echo "  3. Start the backend:"
echo "       cd $INSTALL_DIR && ./scripts/start-backend.sh"
