# Deployment Guide
Main guide for deploying all Lancy components to your environment.
---

## What you're deploying

Lancy has three moving parts that you bring up independently:

- **Backend** — a FastAPI application written in Python. It runs inside a virtual environment (`.venv/`) and is started via the provided shell scripts or, for persistent deployments, as a systemd user service. It owns the retrieval pipeline, embedding models, and vector store.
- **Frontend** — a Next.js application that runs as a standalone Node.js process. It serves the web UI and proxies all API calls to the backend server-side, so that the backend never needs to be publicly reachable itself.
- **LLM** — always external, i.e. not included here. You bring your own: a local [Ollama](https://ollama.com) instance is the default, but any OpenAI-compatible endpoint works (self-hosted vLLM, LiteLLM proxy, OpenAI, Anthropic, etc.).

On a single machine, `scripts/start.sh` handles all of this. For split deployments, each piece can be installed and managed independently.

---

## Requirements

- Python ≥ 3.12
- Node.js ≥ 18
- An LLM server or API endpoint to use

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

## Installation

### Backend

Run the install script — it handles system packages, Python venv creation, pip install, and pre-downloading the embedding models to the HuggingFace cache:

```bash
git clone https://github.com/rlei-odes/lancy.git ~/lancy
bash ~/lancy/scripts/install-backend.sh
```

To pin to a specific release instead of `main`, check out the tag after cloning:

```bash
git clone https://github.com/rlei-odes/lancy.git ~/lancy
cd ~/lancy && git checkout v0.2.31
bash scripts/install-backend.sh
```

Available releases are listed on the [GitHub releases page](https://github.com/rlei-odes/lancy/releases).

If you don't have git yet, the script can bootstrap itself:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rlei-odes/lancy/main/scripts/install-backend.sh)
```

**Manual alternative** (if you prefer not to use the install script):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Frontend

The frontend has no separate install step — `scripts/start-frontend.sh` runs `npm install` automatically if `node_modules` is missing or outdated. The only thing you need to do manually is create the `.env` file:

```bash
cd frontend
cp .env.example .env   # set APP_PASSWORD; set BACKEND_URL only for split deployments
```

Once that's done, pick the deployment profile below that matches your setup and follow its start instructions. Depending on which deployment profile you use, the .env file has to be edited accordingly.

---

## Deployment Profiles

The stack is modular by design — you can run everything on a single powerful laptop or split it across dedicated machines as your needs grow. The three profiles below cover the common configurations, from a quick local trial to a production-grade split deployment. Read through them before you start, since the profile you choose determines which scripts and environment variables you'll need.

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

Run the installation steps from the [Installation](#installation) section above on the backend machine before proceeding.

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

To update to a specific release rather than the latest commit:

```bash
cd ~/lancy && git fetch --tags && git checkout v0.2.31
scripts/stop-backend.sh && scripts/start-backend.sh
```

For getting documents into a KB on a remote backend, see [02-document-ingestion.md](02-document-ingestion.md) — it covers both the folder-scan and HTTP upload methods.

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

## First Run — Create a Knowledge Base

Once the stack is up (whichever profile you chose), the steps to load your first documents are the same:

1. Log in at the frontend URL (e.g. `http://localhost:3000`)
2. Open the **RAG Parameters** panel (right side)
3. Click **+** next to the knowledge base selector
4. Enter a name and the path to your documents (e.g. `data/`)
5. Choose an embedding backend (default: local SentenceTransformer — no API key needed)
6. Click **Re-index** — progress shows file and chunk counts in real time
7. Start asking questions

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

Configured per KB in the **RAG Parameters panel**: select `pgvector` as the vector store type and enter the connection string. Prerequisite: create a database and enable pgvector for it. The backend creates the table and index automatically on first use.

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

The shell scripts in `scripts/` are designed for interactive use — they background the process and track the PID themselves, which doesn't fit the systemd model. For persistent services, run the processes directly as shown below.

### Backend

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

### Frontend

The frontend service runs `next dev` directly. For production deployments, replace `next dev` with `next start` — but run `npm run build` in the `frontend/` directory first and after every update.

Create `~/.config/systemd/user/lancy-frontend.service`:

```ini
[Unit]
Description=Lancy frontend
After=network.target

[Service]
WorkingDirectory=%h/lancy/frontend
ExecStart=%h/lancy/frontend/node_modules/.bin/next dev
Restart=on-failure
StandardOutput=append:%h/lancy/logs/frontend.log
StandardError=append:%h/lancy/logs/frontend.log

[Install]
WantedBy=default.target
```

The service reads `frontend/.env` automatically (Next.js loads it on startup). Make sure the file exists before enabling the service.

### Registering the services

```bash
systemctl --user daemon-reload
systemctl --user enable --now lancy-backend lancy-frontend
```

Tail the logs to verify both started correctly:

```bash
journalctl --user -u lancy-backend -f
journalctl --user -u lancy-frontend -f
```

Enable lingering so the services start at boot without an active user session:

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
