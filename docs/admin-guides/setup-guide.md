# Setup Guide

---

## Requirements

- Python ≥ 3.12
- Node.js ≥ 18
- [Ollama](https://ollama.com) for local LLM inference (optional — OpenAI, Anthropic, and LiteLLM also work)

---

## Quick Start

### One-command start

```bash
./start.sh   # starts backend (port 8080) and frontend (port 3000)
./stop.sh
```

Logs are written to `logs/backend.log` and `logs/frontend.log`. The backend log rotates automatically at 10 MB (keeps 5 backups). Override with `LOG_FILE`, `LOG_MAX_BYTES`, and `LOG_BACKUP_COUNT` env vars.

### Manual setup

```bash
# Backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd frontend
cp .env.example .env   # set API_KEY (login password) and optionally BACKEND_URL
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

## Environment Variables

### Backend

| Variable | Required for | Example |
|----------|-------------|---------|
| `BACKEND` | All | `ollama` / `openai` / `litellm` / `anthropic` |
| `OPENAI_API_KEY` | OpenAI LLM or embedding | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic LLM | `sk-ant-...` |
| `LITELLM_BASE_URL` | LiteLLM proxy | `https://your-litellm/v1` |
| `LITELLM_API_KEY` | LiteLLM proxy | `sk-...` |
| `ALLOW_ORIGINS` | CORS config | `http://localhost:3000` |

### Frontend (`.env`)

| Variable | Description |
|----------|-------------|
| `API_KEY` | Login password for the web UI |
| `BACKEND_URL` | Backend URL for server-side proxy. Default: `http://localhost:8080` |
| `SERVER_URL` | Override for browser-side API calls. **Leave empty** for same-origin proxy. |

> `SERVER_URL` must be empty on local and NAT networks. Setting it to a hostname routes API calls externally and breaks the proxy.

---

## Deployment (systemd + nginx)

### Systemd User Services

Services run under the user account (no root required):

```
~/.config/systemd/user/
  ├── insight-backend.service    # FastAPI backend
  └── insight-frontend.service   # Next.js frontend
```

```bash
cp insight-backend.service ~/.config/systemd/user/
cp insight-frontend.service ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now insight-backend insight-frontend

journalctl --user -u insight-backend -f   # live logs
```


### KB and Config Files

```
backend/src/lancy/db/
  ├── knowledge_bases.json    # KB registry (all KB definitions + active flag)
  └── rag_config.json         # Current RAG parameters (k, BM25, HyDE, etc.)
```

These files are gitignored (contain local paths). Use `knowledge_bases.json.example`
and `rag_config.json.example` as templates.

