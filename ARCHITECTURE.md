# Lancy — Architecture Reference

---

## Overview

Lancy is a full-stack RAG system structured in three layers:

```
Frontend (Next.js)
  ↕ HTTP (same-origin proxy rewrite — no CORS issues)
Backend (FastAPI + asyncio)
  ↕ per-KB config
Vector Store(s) · Embedding Backend(s) · LLM Backend(s)
```

Everything is **async** — the FastAPI backend uses `asyncio` throughout with
`loop.run_in_executor` for CPU-bound operations (embedding inference, BM25 indexing).
This prevents the SSE/streaming endpoint from blocking under concurrent load.

---

## Backend

### Entry Point

`backend/src/lancy/main.py`

FastAPI application with three routers:

| Router | Prefix | Purpose |
|--------|--------|---------|
| `rag_router` | `/rag` | Query endpoint, streaming responses |
| `kb_router` | `/kb` | KB management: list, create, switch, index, cancel |
| `openai_compat_router` | `/v1` | OpenAI-compatible chat completions |

The conversational toolkit (`conversational-toolkit/`) is a separate installable package
providing the core primitives: agents, embeddings, LLMs, vector stores, chunkers, retriever.

### KB Registry

`backend/src/lancy/kb_router.py`

```
knowledge_bases.json
  └── list of KB definitions:
        id, name, data_dirs, vector_store, embedding_backend, embedding_model,
        retrieval_k, use_bm25, use_hyde, use_query_expansion, use_reranking, ...

KB_REGISTRY: dict[str, RAGSystem]   ← loaded at startup
active_kb: str                       ← hot-swappable, no restart
```

**Hot-swap:** `POST /kb/active` changes `active_kb` in memory. The next query uses the
new KB immediately. No re-init, no restart.

**Indexing:** `POST /kb/{id}/reindex` runs in a background thread via `run_in_executor`.
A `_cancel_requested` global flag enables mid-indexing cancellation. The endpoint returns
a 409 if indexing is already running.

### Ingestion Pipeline

Ingestion converts documents on disk into embedded chunks stored in the vector store.
It is triggered by `POST /api/v1/rag/reindex` or automatically on startup when the
store is empty.

#### Flow

```
POST /reindex (reset: bool)
  │
  ├── _run_ingestion(kb, reset)               main.py
  │     │
  │     ├── make_vector_store(...)             create VS instance in async context
  │     │     (ChromaDB: reused below;
  │     │      PGVector: new instance per thread — AsyncEngine is loop-bound)
  │     │
  │     ├── [if not reset] vs.get_file_hashes()
  │     │     returns set[SHA-256] already in the store
  │     │     warns if store is non-empty but has no hashes (pre-dedup KB)
  │     │
  │     ├── executor: _prepass()               blocking I/O — thread pool
  │     │     ├── _collect_candidate_files()   extension + size + EVALUATION filter
  │     │     ├── file_hash(path) × N          SHA-256 of raw bytes, one per file
  │     │     ├── cross-run dedup              skip if hash in existing_hashes
  │     │     └── within-batch dedup           skip if hash already seen this run
  │     │                                      (add to seen_hashes only on success)
  │     │
  │     ├── executor: load_chunks(             blocking I/O + CPU — thread pool
  │     │     include_files=filtered,
  │     │     file_hashes=hash_map)
  │     │     └── per file: parse → chunk → stamp chunk.metadata["file_hash"]
  │     │         (hash computed on-the-fly if not in hash_map — notebook compat)
  │     │
  │     └── executor/new-loop: build_vector_store(   CPU-bound — separate thread
  │           chunks, embedding_model,
  │           existing_hashes=existing_hashes)
  │           ├── [if reset] clear collection
  │           ├── group chunks by file_hash
  │           ├── skip groups already in store  (belt-and-suspenders)
  │           └── embed + insert remaining chunks in batches
  │
  └── returns ReindexResult(chunks_indexed, files_processed, files_skipped, reset)
```

#### Deduplication: Two Layers

| Layer | Where | What it skips |
|-------|-------|---------------|
| Cross-run | Pre-pass | Files whose hash is already in the vector store — no parsing, no embedding |
| Within-batch | Pre-pass | Files with identical content to another file in the same run — only the first occurrence is processed |

Both layers operate before `load_chunks`, so expensive PDF parsing is never wasted on
a file that would ultimately be discarded.

`reset=True` bypasses cross-run dedup (store is cleared). Within-batch dedup still
applies — two identical files in one run always produce one set of chunks.

#### Key Functions

| Function | File | Role |
|----------|------|------|
| `file_hash(path)` | `feature0_baseline_rag.py` | SHA-256 of raw file bytes |
| `_collect_candidate_files(dirs, ...)` | `feature0_baseline_rag.py` | Extension + size + EVALUATION filter, shared by pre-pass and `load_chunks` |
| `load_chunks(..., include_files, file_hashes)` | `feature0_baseline_rag.py` | Parse files, stamp `chunk.metadata["file_hash"]` |
| `build_vector_store(..., existing_hashes)` | `feature0_baseline_rag.py` | Embed and persist; skips chunks already in store |
| `VectorStore.get_file_hashes()` | `vectorstores/base.py` | Abstract: return set of hashes in the store |
| `_run_ingestion(kb, reset)` | `main.py` | Orchestrates all steps above; returns `(chunks, files, skipped)` |

#### Threading Model

The ingestion pipeline has three distinct blocking phases, each isolated to avoid
blocking the FastAPI event loop:

```
async context (_run_ingestion)
  │
  ├── await vs.get_file_hashes()      [async — ChromaDB uses run_in_executor internally]
  │
  ├── await run_in_executor(_prepass) [blocking I/O: file discovery + SHA-256 hashing]
  │
  ├── await run_in_executor(load_chunks) [blocking I/O + CPU: PDF parsing, chunking]
  │
  └── await run_in_executor(_sync_build_vs)
        └── new_loop.run_until_complete(build_vector_store(...))
              [CPU-bound: SentenceTransformer.encode() — needs its own event loop]
```

`_sync_build_vs` creates a fresh event loop because `SentenceTransformer.encode()`
and the subsequent `insert_chunks` calls must complete synchronously from the thread's
perspective. For PGVector, a new `AsyncEngine` is created inside this thread (engine
is bound to the loop that created it and cannot be shared across loops).

---

### Retrieval Pipeline

`conversational-toolkit/src/conversational_toolkit/retriever/`

```
Query
  ↓
[Optional] Query Expansion
  → LLM generates N rephrased variants
  → each variant retrieves independently

[Optional] HyDE
  → LLM generates a hypothetical answer document
  → hypothetical doc is embedded (not the original query)
  → embedded HyDE vector used for ANN search

[Parallel]
  → BM25 retrieval (sparse, term-frequency based)
  → Semantic retrieval (ANN on query/HyDE embedding)

[RRF Fusion]
  → Reciprocal Rank Fusion merges ranked lists
  → Score: Σ 1 / (k + rank_i)   (k=60 default)

[Optional] LLM Reranking
  → Cross-encoder or LLM scores each candidate
  → Final Top-K selected

→ Top-K chunks passed to generator
```

### Vector Stores

| Backend | Class | When to use |
|---------|-------|-------------|
| `chromadb` | `ChromaDBVectorStore` | Default, local, no dependencies |
| `pgvector` | `PgVectorVectorStore` | PostgreSQL available, larger scale, SQL filtering |

Both backends:
- Use HNSW index for ANN search
- Support metadata filtering (source file, page number)
- Store chunk text + embedding + metadata per document

### Embedding Backends

| Backend | Class | Notes |
|---------|-------|-------|
| `local` | `SentenceTransformerEmbeddings` | Fully offline, CPU/GPU, default |
| `ollama` | `OllamaEmbeddings` | Local Ollama server |
| `litellm` | `LiteLLMEmbeddings` | Any OpenAI-compatible embed endpoint |
| `custom` | `CustomEmbeddings` | Custom base URL + model |

**Dimension lock:** A ChromaDB collection is created with the dimension of the first
embedding. Changing embedding model after indexing requires a full re-index with `reset=True`.

### LLM Backends

| Backend | Class | Notes |
|---------|-------|-------|
| `ollama` | `OllamaLLM` | Local inference, privacy-preserving |
| `openai` | `OpenAILLM` | OpenAI API |
| `anthropic` | `AnthropicLLM` | Anthropic API |
| `litellm` | `LiteLLMLLM` | LiteLLM proxy — routes to any provider |

All LLM backends implement the same interface (`BaseLLM.stream()`), so the retrieval
pipeline and agent are backend-agnostic.

### OpenAI-Compatible Endpoint

`backend/src/lancy/openai_compat_router.py`

```
POST /v1/chat/completions
  → maps to active KB RAG query
  → returns OpenAI-format response (streaming supported)

GET /v1/models
  → returns available KBs as model list
```

Any OpenAI-compatible client (Open WebUI, curl, n8n, Cursor) can point to this endpoint
with `base_url=http://localhost:8080/v1` and `api_key=<anything>`.

### Async Architecture

Critical for production: all blocking operations use `run_in_executor`:

```python
# Embedding inference (CPU-bound)
loop = asyncio.get_event_loop()
embeddings = await loop.run_in_executor(None, model.encode, texts)

# ChromaDB operations (I/O + Python GIL)
results = await loop.run_in_executor(None, collection.query, ...)
```

Without this, the SSE streaming endpoint blocks under load — the frontend shows a frozen
response until the full generation completes.

---

## Frontend

### Tech Stack

- Next.js 15 (Pages Router)
- TypeScript
- Tailwind CSS
- shadcn/ui components

### API Routing

The frontend uses Next.js **API route rewrites** to proxy backend calls:

```
Browser → GET /api/rag/query
  → Next.js API route
  → fetches http://BACKEND_URL/rag/query (server-side)
  → streams response back to browser
```

`SERVER_URL` in `.env` must be empty (or unset) for this to work correctly.
If set to a hostname, API calls loop out externally and break on NAT/local networks.

### RAG Config Panel

`frontend/src/components/sections/rag-config-panel.tsx`

Right-side collapsible panel exposing all RAG parameters:
- Knowledge base selector (with hot-swap)
- LLM model and temperature
- Embedding backend and model
- Retrieval: k, BM25 on/off, HyDE on/off, Query Expansion on/off, Reranking on/off
- Presets (fast / balanced / quality)
- Re-index / cancel indexing controls with live progress

All settings are persisted in `rag_config.json` and snapshotted per conversation.

### Session Labels & Badges

Each conversation stores a `rag_config_snapshot` at creation time. The sidebar renders
badges showing the full config: `KB · LLM · T= · emb: · k= · BM25 · Rerank · HyDE`.

Session labels group conversations for A/B evaluation runs: label two sessions,
run the same questions with different configs, compare outputs.

### Auth

Password-based login via session cookie:
- `POST /api/auth/login` validates password against `API_KEY` env var, sets `rag_auth` cookie
- `middleware.ts` checks cookie on every request, redirects to `/login` if missing
- Stateless — no database, no user accounts

---

## Deployment

### Systemd User Services

Services run under the user account (no root required):

```
~/.config/systemd/user/
  ├── insight-backend.service    # FastAPI backend
  └── insight-frontend.service   # Next.js frontend
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now insight-backend insight-frontend
journalctl --user -u insight-backend -f   # live logs
```

### nginx Reverse Proxy

`nginx.conf` provides:
- TLS termination (via certbot or manual cert)
- Proxy to Next.js frontend (`:3000`)
- Frontend proxies backend internally — nginx only needs to reach port 3000

### KB and Config Files

```
backend/src/lancy/db/
  ├── knowledge_bases.json    # KB registry (all KB definitions + active flag)
  └── rag_config.json         # Current RAG parameters (k, BM25, HyDE, etc.)
```

These files are gitignored (contain local paths). Use `knowledge_bases.json.example`
and `rag_config.json.example` as templates.

---

## Configuration Reference

### knowledge_bases.json structure

```json
{
  "active": "my-kb",
  "knowledge_bases": [
    {
      "id": "my-kb",
      "name": "My Knowledge Base",
      "data_dirs": ["data/"],
      "vector_store": "chromadb",
      "embedding_backend": "local",
      "embedding_model": "nomic-ai/nomic-embed-text-v1",
      "persist_directory": "db/my-kb-chroma"
    }
  ]
}
```

### rag_config.json structure

```json
{
  "retrieval_k": 5,
  "use_bm25": true,
  "use_hyde": false,
  "use_query_expansion": false,
  "use_reranking": false,
  "llm_model": "mistral-nemo:12b",
  "llm_temperature": 0.1
}
```

---

## Sequence Diagram — Query Flow

```
User            Frontend        Backend         VectorStore     LLM
 │                │               │                 │             │
 │──── query ────►│               │                 │             │
 │                │──── POST ────►│                 │             │
 │                │         [Query Expansion?]       │             │
 │                │               │──── embed ─────►│             │
 │                │               │◄─── vectors ────│             │
 │                │               │                 │             │
 │                │         [HyDE?] generate hypothetical          │
 │                │               │────────────────────────────►  │
 │                │               │◄─── hypothetical doc ──────── │
 │                │               │──── embed hypo ─►│            │
 │                │               │                  │            │
 │                │         [BM25 + Semantic parallel]│            │
 │                │               │──── ANN query ──►│            │
 │                │               │◄─── top-K chunks─│            │
 │                │               │                  │            │
 │                │         [RRF fusion → Rerank?]    │            │
 │                │               │                  │            │
 │                │         Build prompt (system + chunks + query) │
 │                │               │────────────────────────────►  │
 │                │         stream tokens                          │
 │                │◄── SSE ───────│◄─── token ──────────────────  │
 │◄─ token ───────│               │                 │             │
```
