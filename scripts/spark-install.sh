#!/bin/bash
# Lancy — Backend install script for DGX Spark / Ubuntu ARM
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

echo ""
echo "Install complete."
echo ""
echo "Next steps:"
echo "  1. Configure your KB data path in the Lancy UI (use a path outside the repo, e.g. ~/data/)"
echo "  2. HuggingFace models (nomic-embed-text, etc.) download to ~/.cache/huggingface on first run."
echo "     Ensure at least 2 GB of free disk space."
echo "  3. Start the backend:"
echo "       cd $INSTALL_DIR && ./scripts/start-backend.sh"
