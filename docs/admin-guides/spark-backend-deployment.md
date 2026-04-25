# Backend Deployment on DGX Spark

Deployment guide for running the Lancy backend on a DGX Spark (or any Ubuntu/ARM server),
with the frontend remaining on a separate local machine.

## Architecture

```
[User browser] → local-machine:3000 (Next.js, dev or prod)
                      ↓ SERVER_URL=http://192.168.1.141:8080
                 spark:8080 (FastAPI backend — retrieval, ingestion, embeddings)
                      ↓
                 spark:8000 (vLLM — LLM inference, OpenAI-compatible)
```

The frontend proxies all API calls server-side. The browser never talks directly to the backend.

---

## Battle Plan

### Step 1 — File upload endpoint (done)

`POST /api/v1/kb/{id}/documents` is implemented. It accepts a multipart file upload
plus JSON metadata, ingests the document into the KB, then discards the temp file.
No shared filesystem access required — documents are pushed over HTTP from any machine.

### Step 2 — Install dependencies and set up the repo on the Spark

Run the install script on the Spark:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rlei-odes/lancy/main/scripts/spark-install.sh)
```

Or clone the repo first and run it locally:

```bash
git clone https://github.com/rlei-odes/lancy.git ~/lancy
bash ~/lancy/scripts/spark-install.sh
```

The script handles: system packages, Python venv creation, repo clone, pip install.

### Step 3 — Configure the frontend to point at the Spark

In `frontend/.env` on the local machine:

```
SERVER_URL=http://192.168.1.141:8080
```

Leave `NEXT_PUBLIC_API_BASE` empty. Restart the frontend after changing.

### Step 4 — Start the backend on the Spark

```bash
./start-backend.sh
```

See `scripts/start-backend.sh` below. Mirrors the existing `start.sh` but omits the
frontend and skips the Ollama check (vLLM handles LLM inference on the Spark).

### Step 5 — Ingest documents via the upload endpoint

With the backend running on the Spark, push documents from the local machine:

```bash
curl -X POST http://192.168.1.141:8080/api/v1/kb/default/documents \
  -F "file=@/path/to/document.pdf" \
  -F 'metadata={"document_id": "my-doc-001", "title": "My Document"}'
```

Poll ingestion progress:

```bash
curl http://192.168.1.141:8080/api/v1/rag/reindex-status
```

See `docs/API_Endpoints.md` for the full metadata field reference.

### Step 6 — (Optional) Set up a systemd user service for auto-start

Install the service so the backend starts automatically on boot and can be managed with
`systemctl --user start|stop|restart lancy-backend`.

See the systemd section below.

### Staying up to date

```bash
cd ~/lancy
git pull
systemctl --user restart lancy-backend   # or ./start-backend.sh if not using systemd
```

---

## Scripts

The scripts live at `scripts/` in the repo root and are executable.

| Script | Purpose |
|--------|---------|
| [`scripts/spark-install.sh`](../../scripts/spark-install.sh) | One-time setup: system packages, venv, pip install |
| [`scripts/start-backend.sh`](../../scripts/start-backend.sh) | Start the backend in the background, log to `logs/backend.log` |
| [`scripts/stop-backend.sh`](../../scripts/stop-backend.sh) | Stop the backend via PID file, fall back to port kill |

---

## Systemd user service (optional)

Create `~/.config/systemd/user/lancy-backend.service` on the Spark:

```ini
[Unit]
Description=Lancy backend
After=network.target

[Service]
WorkingDirectory=%h/lancy
ExecStart=%h/lancy/.venv/bin/python -m lancy.main
Environment=PYTHONPATH=%h/lancy/backend/src
Restart=on-failure
StandardOutput=append:%h/lancy/logs/backend.log
StandardError=append:%h/lancy/logs/backend.log

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable lancy-backend
systemctl --user start lancy-backend
```

Requires lingering to be enabled so the service survives logout:

```bash
loginctl enable-linger $USER
```

---

## Notes

- **Data directory:** keep documents outside the repo (e.g. `~/data/`) so they are never accidentally committed. Configure the path in the KB settings after first start.
- **HuggingFace cache:** nomic-embed-text and other HuggingFace models download to `~/.cache/huggingface` on first run. Ensure at least 2 GB of free disk space.
- **ARM compatibility:** all current dependencies (FastAPI, ChromaDB, sentence-transformers, rank-bm25) support ARM. Verify on first install that `pip install -r requirements.txt` completes without errors.
