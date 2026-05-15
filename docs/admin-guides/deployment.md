# Deployment Guide

---

## Requirements

- Python ≥ 3.12
- Node.js ≥ 18
- An LLM server — local (e.g. Ollama) or remote (OpenAI, Anthropic, LiteLLM, vLLM, or any OpenAI-compatible endpoint)

---

## Component Overview

| Component | Runs | Compute profile |
|---|---|---|
| **Frontend** | Standalone Node.js process (port 3000) | Lightweight — serves UI, proxies API calls server-side |
| **Backend** | Standalone Python process (port 8080) | Medium baseline; CPU/GPU-heavy during ingestion |
| **Text embedding model** | Embedded in backend process | CPU-bound; ~500 MB RAM |
| **Image embedding model** (optional) | Embedded in backend process | GPU-intensive; ~5 GB VRAM — very slow on CPU |
| **LLM** | External — provided by the deployer | GPU-intensive for interactive inference; also used for image captioning when enabled |
| **Utility LLM** (optional) | Same external server as LLM | Preprocessing: HyDE, query rewriting, reranking. Defaults to main LLM; set a smaller model (e.g. `qwen2.5:3b`) to reduce latency |
| **ChromaDB** | Embedded in backend process | I/O-bound; comfortable up to ~100k chunks |
| **pgvector** | External PostgreSQL instance | I/O-bound; scales to millions of chunks |
| **User settings DB** | SQLite file (`db/user_config.db`) — always local | Per-browser retrieval overrides and presets; not configurable |
| **Conversation DB** | SQLite (default) or external PostgreSQL | Chat history and session state |

The text and image embedding models run inside the backend process and cannot be split off. The LLM is always external. Image captioning reuses the configured LLM — no separate vision process needed.

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/start.sh` | Start backend + frontend together (single-machine dev) |
| `scripts/stop.sh` | Stop backend + frontend |
| `scripts/start-backend.sh` | Start backend only (split deployment) |
| `scripts/stop-backend.sh` | Stop backend only |
| `scripts/start-frontend.sh` | Start frontend only (split deployment) |
| `scripts/stop-frontend.sh` | Stop frontend only |
| `scripts/install-backend.sh` | Fresh-machine install: clone repo, create venv, install deps, pre-download embedding models |
| `scripts/upload-docs.sh` | Batch-upload a local directory to a remote KB over HTTP |

---

## Profile 1 — Single Machine

Everything on one host. Suitable for evaluation, personal use, or small teams with modest document volumes.

```
[Browser] → localhost:3000 (Next.js)
                  ↓ BACKEND_URL=http://localhost:8080
            localhost:8080 (FastAPI + embedding model)
                  ↓
            localhost:11434 (LLM server)
            localhost/embedded (ChromaDB)
```

### Start

```bash
scripts/start.sh   # starts backend (port 8080) and frontend (port 3000)
scripts/stop.sh
```

Logs are written to `logs/backend.log` and `logs/frontend.log`. The backend log rotates at 10 MB (keeps 5 backups).

### Manual setup (without the start script)

```bash
# Backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd frontend
cp .env.example .env   # set APP_PASSWORD and optionally BACKEND_URL
npm install
npm run dev
```

### First run — create a Knowledge Base

1. Log in at `http://localhost:3000`
2. Open the **RAG Parameters** panel (right side)
3. Click **+** next to the knowledge base selector
4. Enter a name and the path to your documents (e.g. `data/`)
5. Choose an embedding backend (default: local SentenceTransformer — no API key needed)
6. Click **Re-index** — progress shows file and chunk counts in real time
7. Start asking questions

---

## Profile 2 — GPU Server + Thin Access Machine

Backend and LLM on a GPU machine; frontend on a separate (lighter) machine. The backend and LLM compete for GPU memory on shared hardware. Limit the LLM's VRAM budget in its server config to prevent out-of-memory crashes during ingestion.

```
[Browser] → frontend-host:3000 (Next.js)
                  ↓ BACKEND_URL=http://gpu-server:8080
            gpu-server:8080 (FastAPI + embedding model)
                  ↓
            gpu-server:11434 (LLM server)
            gpu-server:5432  (pgvector, optional)
```

The LLM can run on a separate GPU machine — set `ollama_host` (or `custom_base_url`) in the RAG Parameters panel to point at it.

### Install the backend on the remote machine

Clone the repo first — this is the recommended approach as it lets you pull updates later with `git pull`:

```bash
git clone https://github.com/rlei-odes/lancy.git ~/lancy
bash ~/lancy/scripts/install-backend.sh
```

The install script handles: system packages, Python venv creation, pip install, and pre-downloading embedding models to the HuggingFace cache.

If you don't have git yet, you can bootstrap with the one-liner (it clones the repo as part of the install):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rlei-odes/lancy/main/scripts/install-backend.sh)
```

### Configure and start

**`frontend/.env` on the frontend machine:**
```env
BACKEND_URL=http://<backend-machine-ip>:8080
APP_PASSWORD=...
```

**Backend environment on the GPU machine** — set this before starting the backend:
```env
ALLOW_ORIGINS=http://frontend-host:3000
```

`ALLOW_ORIGINS` controls which hosts the backend accepts cross-origin requests from (CORS). Set it to the URL of your frontend machine. Without it, the backend only allows `localhost` — requests from any other host will be blocked. Multiple origins can be comma-separated: `http://host-a:3000,http://host-b:3000`.

To set it, either export it in your shell before running `scripts/start-backend.sh`, or add it to the backend machine's environment (e.g. in the systemd service file under `[Service]` as an additional `Environment=` line).

```bash
# On the backend machine:
scripts/start-backend.sh

# On the frontend machine:
scripts/start-frontend.sh
```

On first start, `rag_config.json` is not present on a fresh clone — the backend defaults to Ollama. Open the **RAG Parameters** panel to configure the LLM backend (provider, host/URL, model). Settings are saved automatically.

### Keeping up to date

```bash
cd ~/lancy && git pull
scripts/stop-backend.sh && scripts/start-backend.sh
```

### Ingesting documents on a remote backend

There are two ways to get documents into a KB:

**Option A — Folder path (backend has direct filesystem access)**

When the backend machine can read the documents directly (local disk, NFS mount, etc.), set the KB's document path to that folder in the **RAG Parameters panel** and click **Re-index**. This is the simplest approach for Profile 2 when both the documents and the backend are on the same machine.

**Option B — HTTP upload (no shared filesystem)**

Documents can be pushed to the backend over HTTP from any machine. Useful when the documents live on a different host than the backend.

Batch upload a local directory:

```bash
scripts/upload-docs.sh http://<backend-host>:8080 <kb-id> /path/to/docs/
```

Single file:

```bash
curl -X POST http://<backend-host>:8080/api/v1/kb/<kb-id>/documents \
  -F "file=@/path/to/document.pdf" \
  -F 'metadata={"document_id": "my-doc", "source_file": "document.pdf"}'
```

Monitor ingestion progress:

```bash
curl http://<backend-host>:8080/api/v1/rag/reindex-status
```

Supported formats: `.pdf` `.md` `.txt` `.png` `.jpg` `.jpeg` `.gif` `.tiff` `.bmp` `.webp`

---

## Profile 3 — Production Split (Recommended)

Three dedicated tiers. Suitable for teams, higher load, or managed infrastructure.

```
[Browser] → nginx/Caddy (HTTPS, port 443)
                  ↓ proxy_pass :3000
            frontend-host:3000 (Next.js)
                  ↓ BACKEND_URL=http://backend-host:8080
            backend-host:8080 (FastAPI + embedding model)   ← GPU recommended
                  ↓
            llm-host:11434 (LLM server)                     ← GPU required
            db-host:5432   (PostgreSQL + pgvector)
```

**Recommended hardware split:**

- **LLM host** — GPU with sufficient VRAM for your chosen model (e.g. 24 GB for a 13B model at full precision). CPU inference is possible but slow for interactive use.
- **Backend host** — GPU recommended if image captioning is enabled (~5 GB VRAM). CPU-only is fine for text-only KBs.
- **Frontend host** — any lightweight Node.js host; no GPU needed.
- **Database host** — standard PostgreSQL instance; managed cloud instances (RDS, Supabase, etc.) work.

**Minimal config checklist:**

| Host | What to set |
|---|---|
| Frontend | `BACKEND_URL=http://backend-host:8080`, `APP_PASSWORD` |
| Backend | `ALLOW_ORIGINS=https://your-domain`, LLM credentials or host, `DATABASE_URL` (if using Postgres) |
| KB definitions | `vs_connection_string=postgresql://...` (if using pgvector; set in RAG Parameters panel) |
| Reverse proxy | TLS termination, proxy to frontend port 3000 |

---

## Configuration Reference

### Backend Environment Variables

| Variable | Required for | Example |
|---|---|---|
| `ALLOW_ORIGINS` | CORS on split deployments | `http://frontend-host:3000` |
| `SECRET_KEY` | JWT signing (session cookies) | any long random string |
| `DATABASE_URL` | PostgreSQL conversation DB | `postgresql://user:pass@host/db` |
| `LITELLM_BASE_URL` | LiteLLM proxy backend | `https://your-litellm/v1` |
| `LITELLM_API_KEY` | LiteLLM proxy backend | `sk-...` |

### Frontend `.env`

| Variable | Description |
|---|---|
| `APP_PASSWORD` | Login password for the web UI |
| `BACKEND_URL` | Backend URL for the server-side proxy. Default: `http://localhost:8080` |

### Backend → LLM

Most LLM settings are configured at runtime from the **RAG Parameters panel** — no env var or restart needed:

| Setting | Where |
|---|---|
| Provider (`ollama` / `custom` / `litellm`) | RAG Parameters panel — `llm_backend` |
| Model name | RAG Parameters panel — `llm_model` |
| LLM server host/URL | RAG Parameters panel — `ollama_host` or `custom_base_url` |
| API key | RAG Parameters panel — `custom_api_key` |
| Utility/preprocessing model | RAG Parameters panel — `utility_llm_model` |
| LiteLLM proxy URL + key | Env vars: `LITELLM_BASE_URL`, `LITELLM_API_KEY` |

The `custom` backend accepts any OpenAI-compatible endpoint — use it for OpenAI, Anthropic, vLLM, or hosted APIs.

### Backend → Vector Store (pgvector)

Configured per KB in the **RAG Parameters panel**: select `pgvector` as the vector store type and enter the connection string. The backend creates the table and index automatically on first use.

```
postgresql://user:password@<db-host>:5432/lancy
```

### Backend → Conversation DB (PostgreSQL)

Env var only:

```env
DATABASE_URL=postgresql://user:password@<db-host>:5432/lancy
```

Defaults to a local SQLite file if unset.

---

## Auto-start (systemd)

To run the backend as a persistent service that survives logout and starts on boot:

Create `~/.config/systemd/user/lancy-backend.service`:

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

Register and start the service:

```bash
systemctl --user daemon-reload          # make systemd aware of the new file
systemctl --user enable --now lancy-backend  # enable at boot + start immediately
```

Tail the logs to verify it started correctly:

```bash
journalctl --user -u lancy-backend -f
```

Enable lingering so the service starts at boot without an active user session:

```bash
loginctl enable-linger $USER
```

---

## Firewall / Network

Lancy is not designed to be exposed to the public internet. Run it on a private or internal network and restrict access accordingly:

| Service | Allow access from |
|---|---|
| Frontend (3000) | Internal network users (via reverse proxy on 443 with TLS) |
| Backend (8080) | Frontend host only |
| LLM server (11434) | Backend host only |
| PostgreSQL (5432) | Backend host only |

Backend, LLM server, and database ports should only be reachable by admins for maintenance — not by end users or from outside the internal network.

---

## Persistent Data

These paths must survive restarts and deployments:

| Path | Contents |
|---|---|
| `backend/src/lancy/db/knowledge_bases.json` | KB registry — names, paths, embedding config, connection strings |
| `backend/src/lancy/db/rag_config.json` | Active RAG parameters (k, BM25 weight, HyDE, etc.) |
| `backend/src/lancy/db/conversations.db` | Conversation history and messages (SQLite; replaced by PostgreSQL if `DATABASE_URL` is set) |
| `backend/src/lancy/db/user_config.db` | Per-browser retrieval overrides and presets (always SQLite) |
| `backend/src/lancy/db/vs_text/` | ChromaDB vector store files (if using ChromaDB) |
| `data/` | Source documents |

The JSON config files are gitignored — use the `.example` templates to bootstrap them on a new host. For container deployments, volume-mount the entire `backend/src/lancy/db/` directory and `data/` — do not bake them into the image.
