# Lancy — API Endpoints Reference

---

## API Layers

Lancy has two API entry points that handle requests differently:

| Layer | Address | Auth enforced |
|---|---|---|
| **FastAPI backend** | `http://localhost:8080` | None — open, no access control |
| **Next.js frontend** | `http://localhost:3000` | Yes — middleware checks role before proxying |

All endpoints below are available at both addresses. In production, port 8080 should be firewalled; all external traffic should go through port 3000.

### Authenticating through the frontend (port 3000)

Two methods are accepted:

- **Browser session** — a signed `rag_auth` cookie issued after login, carries the role (`admin` or `user`)
- **Bearer token** — `Authorization: Bearer <token>` in the request header; always grants admin role, intended for API clients and scripts. Both `APP_PASSWORD` and `ADMIN_PASSWORD` (if set as an env var in `frontend/.env`) are accepted. The UI-configured admin password (`auth_config.json`) is not available to the Edge Runtime and cannot be used as a Bearer token.

To call any admin endpoint through the frontend proxy:

```bash
curl -H "Authorization: Bearer <APP_PASSWORD>" http://localhost:3000/api/admin/ingest-events
```

Or with parameters:

```bash
# Put the URL in quotes, otherwise the parameters get silently dropped by bash
curl -H "Authorization: Bearer <token>" "http://localhost:3000/api/admin/ingest-events?kb_id=default&days=5&limit=200"
```

Without the header (or with a user-role session), admin endpoints return `{"error":"Unauthorized"}`. Hitting the backend directly on port 8080 bypasses auth entirely — which is why that port must be firewalled in any non-local deployment.

### Access control

All `/api/admin/*` endpoints require admin. The table below is the full access matrix enforced by the middleware.

`-` = no auth required (public) · `✓` = allowed · `403` = session exists but wrong role · `401` = no session

| Endpoint | Method | anonymous | user | admin |
|---|---|---|---|---|
| `/api/v1/kb` | GET | 401 | ✓ | ✓ |
| `/api/v1/kb` | POST | 401 | 403 | ✓ |
| `/api/v1/kb/{id}` | PUT | 401 | 403 | ✓ |
| `/api/v1/kb/{id}` | DELETE | 401 | 403 | ✓ |
| `/api/v1/kb/{id}/activate` | POST | 401 | 403 | ✓ |
| `/api/v1/kb/{id}/deactivate` | POST | 401 | 403 | ✓ |
| `/api/v1/kb/{id}/documents` | POST | 401 | 403 | ✓ |
| `/api/v1/kb/pool` | GET | 401 | ✓ | ✓ |
| `/api/v1/kb/{id}/stats` | GET | 401 | ✓ | ✓ |
| `/api/v1/files/{filename}` | GET | - | - | - |
| `/api/v1/rag/config` | GET | 401 | ✓ | ✓ |
| `/api/v1/rag/config` | POST | 401 | ✓ | ✓ |
| `/api/v1/rag/store-info` | GET | 401 | ✓ | ✓ |
| `/api/v1/rag/presets/{kb_id}` | GET | 401 | ✓ | ✓ |
| `/api/v1/rag/presets/{kb_id}` | POST | 401 | ✓ | ✓ |
| `/api/v1/rag/reindex` | POST | 401 | 403 | ✓ |
| `/api/v1/rag/reindex-cancel` | POST | 401 | 403 | ✓ |
| `/api/v1/rag/reindex-status` | GET | 401 | ✓ | ✓ |
| `/api/v1/rag/query-status` | GET | 401 | ✓ | ✓ |
| `/api/v1/rag/retrieve` | POST | 401 | ✓ | ✓ |
| `/api/v1/rag/chunks` | POST | 401 | ✓ | ✓ |
| `/api/v1/rag/status` | GET | 401 | ✓ | ✓ |
| `/api/v1/rag/litellm-models` | GET | 401 | ✓ | ✓ |
| `/api/v1/rag/ollama-models` | GET | 401 | ✓ | ✓ |
| `/api/v1/branding` | GET | - | - | - |
| `/api/v1/branding` | PUT | 401 | 403 | ✓ |
| `/api/v1/branding/avatar` | DELETE | 401 | 403 | ✓ |
| `/api/admin/*` | * | 401 | 403 | ✓ |
| `/v1/chat/completions` | POST | 401 | ✓ | ✓ |
| `/v1/models` | GET | 401 | ✓ | ✓ |

Notes:
- `POST /rag/config` and `POST /rag/presets` are intentionally user-writable — users may adjust session RAG parameters and save presets from the config panel.
- The backend itself has no auth layer; it trusts the `x-user-role` header injected by the middleware. Port 8080 must be firewalled in any non-local deployment.

### Interactive API explorer

The FastAPI-generated Swagger UI is available at `http://localhost:3000/docs`. It is proxied through the Next.js frontend and protected by the same middleware:

- **Admin session** — accessing `/docs` while logged in as admin opens the full interactive explorer. All "Try it out" requests go through the frontend proxy on port 3000, so auth is enforced exactly as it would be for any other client.
- **User session** — the middleware redirects `/docs` to `/redoc`, a read-only API reference that does not allow test requests.
- **No session** — redirected to `/login`.

---

## Knowledge Base (KB)

### List KBs

```
GET /kb
```

Returns the full KB registry including the currently active KB id.

**Response:** `KBRegistry`

```json
{
  "active": "default",
  "bases": {
    "default": {
      "id": "default",
      "name": "Standard",
      "data_dirs": ["data/"],
      "embedding_backend": "local",
      "embedding_model": "nomic-ai/nomic-embed-text-v1",
      "vs_type": "chromadb",
      "vs_path": "...",
      "chunks": 1659,
      "files": 42,
      "last_indexed": "2025-04-20T10:00:00+00:00"
    }
  }
}
```

---

### Create KB

```
POST /kb
```

**Admin only.**

**Body:** `KBCreate` (JSON)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Display name (1–100 chars) |
| `data_dirs` | string[] | `["data/"]` | Source directories to index |
| `embedding_backend` | enum | `"local"` | `local` / `ollama` / `litellm` / `custom` |
| `embedding_model` | string | `"nomic-ai/nomic-embed-text-v1"` | Model name or path |
| `embedding_ollama_host` | string | `""` | Ollama host:port |
| `embedding_custom_base_url` | string | `""` | OpenAI-compat base URL |
| `embedding_custom_api_key` | string | `""` | API key for custom endpoint |
| `nomic_prefix` | bool | `true` | Add search prefix for nomic models |
| `max_file_size_mb` | int | `20` | Max file size accepted (1–500) |
| `embedding_batch_size` | int | `50` | Chunks per embedding batch (1–1000) |
| `pdf_ocr_enabled` | bool | `true` | Run OCR on scanned PDFs |
| `max_chunk_tokens` | int | `0` | Override chunker token limit (0 = default) |
| `vs_type` | enum | `"chromadb"` | `chromadb` / `pgvector` |
| `vs_connection_string` | string | `""` | PGVector connection string |
| `image_indexing_enabled` | bool | `false` | Index images alongside text |
| `image_retrieval_enabled` | bool | `false` | Include images in retrieval results |
| `image_embedding_model` | string | `"Qwen/Qwen3-VL-Embedding-2B"` | VL model for image embeddings |

**Response:** `KBInfo` (the created KB with auto-generated `id` and `vs_path`)

---

### Update KB

```
PUT /kb/{kb_id}
```

**Admin only.**

Replaces the KB config fields. Stats (`chunks`, `files`, `last_indexed`) and `vs_path` are preserved.

**Body:** `KBCreate` — same fields as Create.

**Response:** `KBInfo`

---

### Delete KB

```
DELETE /kb/{kb_id}
```

**Admin only.**

Deletes the KB definition and, for `chromadb`, removes the vector store directory from disk. PGVector tables are not dropped automatically.

Returns `400` if it is the last KB. If the deleted KB was active, the next available KB is auto-activated.

**Response:** `{"deleted": "<kb_id>"}`

---

### Activate KB

```
POST /kb/{kb_id}/activate[?reset=false]
```

**Admin only.**

Adds the KB to the in-memory pool and sets it as the active KB for new conversations.
Non-destructive — previously loaded KBs remain in the pool and continue serving in-flight streams.

All KBs in the pool must share the same `(embedding_backend, embedding_model)`. If the
target KB uses a different embedding config, the call returns `409 EmbeddingConflict`.
Pass `?reset=true` to clear the entire pool first; this is required when switching embedding configs.

| Query param | Default | Description |
|-------------|---------|-------------|
| `reset` | `false` | Clear all loaded KBs before activating. Required when embedding config differs from the pool's. |

**Response:** `KBInfo` (the activated KB)

**Errors:** `404` KB not found · `409` embedding conflict (add `?reset=true` to resolve)

---

### Deactivate KB

```
POST /kb/{kb_id}/deactivate
```

**Admin only.**

Unloads a KB from the in-memory pool. In-flight streams hold a reference to the `LoadedKB`
and complete safely. Has no effect if the KB is not currently loaded.

**Response:** `{"deactivated": "<kb_id>"}`

---

### Pool Status

```
GET /kb/pool
```

Returns the current state of the KB pool.

**Response:**

```json
{
  "loaded": ["default", "project-x"],
  "loading": [],
  "active": "default",
  "emb_key": {"backend": "local", "model": "nomic-ai/nomic-embed-text-v1"}
}
```

| Field | Description |
|-------|-------------|
| `loaded` | KB ids currently in the pool |
| `loading` | KB ids being loaded right now (model init in progress) |
| `active` | The active KB id (used for new conversations without an explicit KB) |
| `emb_key` | Shared embedding config the pool is locked to; `null` if pool is empty |

---

### KB Analytics

```
GET /kb/{kb_id}/stats
```

Returns pre-computed analytics written by the indexer after a full re-index.
Returns `404` if no stats file exists for this KB yet.

**Response:** JSON analytics object (see KB Analytics design doc for schema)

---

### Upload Document

```
POST /kb/{kb_id}/documents
```

**Admin only.**

Uploads a single document into a KB and triggers incremental indexing as a background task.
The file is written to a temporary path, ingested, then deleted — it is never stored permanently.

**Content-Type:** `multipart/form-data`

| Part | Type | Description |
|------|------|-------------|
| `file` | binary | The document file (PDF, DOCX, XLSX, MD, …) |
| `metadata` | JSON string | Must contain `document_id` plus any optional fields |

**Metadata fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `document_id` | **yes** | Stable identifier for this document. Re-uploading with the same `document_id` deletes existing chunks before inserting new ones (versioning). Use a DMS record ID, canonical filename, or any stable key — not the content hash. |
| `source_file` | no | Display name shown in source citations. Defaults to the uploaded filename. |
| `title` | no | Human-readable document title. |
| `author` | no | Author name or responsible department. |
| `document_class` | no | Top-level category: e.g. `Technical`, `Commercial`, `Legal`, `Internal`. Intended as a future retrieval filter. |
| `document_type` | no | Sub-category below `document_class`: e.g. `Specification`, `Report`, `Plan`. |
| `document_created_at` | no | Original creation date (`YYYY-MM-DD`). |
| `document_released_at` | no | Release / approval date (`YYYY-MM-DD`). Used as the version timestamp for stale-version detection. |
| `source_url` | no | Deep link back to the document in the originating DMS or file system. When present, source citations link to this URL instead of opening the local content popup. |
| `tags` | no | List of free-form tags, e.g. `["project-x", "team-z"]`. Stored as JSON on each chunk. |
| Any other field | no | Merged into every chunk's metadata verbatim. |

**Example:**

```bash
# Through the frontend proxy (authenticated):
curl -X POST http://localhost:3000/api/v1/kb/default/documents \
  -H "Authorization: Bearer <APP_PASSWORD>" \
  -F "file=@/path/to/document.pdf" \
  -F 'metadata={"document_id": "doc-42", "title": "Q1 Report", "department": "Finance"}'

# Direct backend (no auth required, firewall in production):
curl -X POST http://localhost:8080/api/v1/kb/default/documents \
  -F "file=@/path/to/document.pdf" \
  -F 'metadata={"document_id": "doc-42", "title": "Q1 Report", "department": "Finance"}'
```

**Response:**

```json
{
  "started": true,
  "document_id": "doc-42",
  "filename": "document.pdf"
}
```

The ingestion runs in the background. Poll the reindex status endpoint to track progress:

```bash
curl http://localhost:8080/api/v1/rag/reindex-status
```

Returns `503` if the backend is not fully initialised, `409` if indexing is already running,
`422` if `document_id` is missing or `metadata` is invalid JSON.

---

## File Serving

### Serve Source Document

```
GET /files/{filename}
```

Serves a source document (PDF, XLSX, etc.) from any configured data directory.
Searched across all KBs so citation links remain stable when switching KBs.

Path traversal is blocked — the resolved path must remain inside a known data directory.

**Response:** The file as an inline attachment.

---

## RAG

### RAG Config

```
GET  /rag/config
POST /rag/config
```

Read or write the current RAG session configuration (LLM backend, model, retrieval parameters, system prompt).

Write semantics differ by role:

- **Admin** — writes update the shared baseline in `rag_config.json` and the system prompt file, affecting all future sessions.
- **User** — writes are scoped to the user's session; only retrieval fields (`retriever_top_k`, `bm25_enabled`, etc.) are persisted per-user. LLM fields are ignored.

The GET response includes all fields; use the Swagger UI schema or `GET /rag/config` for the full field list.

---

### RAG Presets

```
GET  /rag/presets/{kb_id}
POST /rag/presets/{kb_id}
```

Read or save named retrieval and KB configuration presets for a specific KB.

Write semantics mirror the config endpoint: admins write shared presets, users write their own. `POST` body is a `{"retrieval": [...], "kb": [...]}` dict; returns `{"saved": <count>}`.

---

### Reindex

```
POST /rag/reindex
```

**Admin only.**

Triggers a full or incremental re-index of the active KB.

**Body:** `{"reset": false}`

- `reset: false` — incremental: skips files whose content hash is already in the store
- `reset: true` — full: clears the store before indexing

Returns `409` if indexing is already running.

---

### Cancel Reindex

```
POST /rag/reindex/cancel
```

**Admin only.**

Requests cancellation of an in-progress re-index. The indexer checks the flag between files.

---

### Retrieve (Retrieval Explorer)

```
POST /rag/retrieve
```

Dry-run of the retrieval pipeline — returns ranked chunks with per-chunk scores without calling the LLM. Used by the Retrieval Explorer UI tab.

**Body:**

```json
{
  "query": "string",
  "bm25_enabled": true,
  "reranking_enabled": false,
  "filters": {"source_file": "report.pdf"}
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | The retrieval query (1–4000 chars) |
| `bm25_enabled` | bool | `true` | Include BM25 sparse retrieval |
| `reranking_enabled` | bool | `false` | Apply LLM reranking after RRF fusion |
| `filters` | object | `null` | Metadata filters, e.g. `{"source_file": "x.pdf"}` |

**Response:**

```json
{
  "chunks": [
    {
      "id": "abc123",
      "content": "...",
      "metadata": {"source_file": "report.pdf", "page": 3},
      "final_rank": 1,
      "scores": {
        "semantic_score": 0.87,
        "bm25_score": 12.4,
        "rrf_score": 0.032,
        "pre_rerank_rank": 2
      }
    }
  ],
  "top_k": 5,
  "total_returned": 5,
  "reranking_skipped": false
}
```

`pre_rerank_rank` is only populated when `reranking_enabled` is `true`.

---

### Reindex Status

```
GET /rag/reindex-status
```

Returns the current indexing state.

**Response:**

```json
{
  "indexing": false,
  "phase": "embedding",
  "current_file": "report.pdf",
  "file_index": 3,
  "total_files": 10,
  "chunks_so_far": 120,
  "embed_batch": 2,
  "embed_total_batches": 5,
  "kb_name": "Standard",
  "finished_at": "2025-04-20T10:01:23+00:00",
  "last_result": {
    "chunks_indexed": 450,
    "files_processed": 10,
    "files_skipped": 2,
    "files_skipped_store": 1,
    "files_skipped_batch": 1,
    "reset": false
  }
}
```

### Chunk Browser

```
POST /rag/chunks
```

Returns a paginated list of raw chunks from the vector store, optionally filtered by metadata. Used by the Chunk Browser UI tab. Accepts a `ChunkBrowseRequest` body (see Swagger for schema).

---

### Utility Endpoints

These are read-only, take no significant parameters, and are self-explanatory in the Swagger UI:

| Endpoint | Description |
|---|---|
| `GET /rag/status` | Server readiness and active KB info |
| `GET /rag/query-status` | Number of in-flight RAG queries |
| `GET /rag/store-info` | Chunk count and file list for the active vector store |
| `GET /rag/litellm-models` | Available models from the configured LiteLLM proxy |
| `GET /rag/ollama-models[?host=]` | Available models from an Ollama instance (`host` defaults to `localhost:11434`) |

---

## Admin

All endpoints in this section require admin role.

### Ingest Events

```
GET /admin/ingest-events
```

Paginated log of every document ingested across all KBs. Useful for auditing what was indexed, when, and whether it succeeded.

| Query param | Type | Description |
|---|---|---|
| `kb_id` | string | Filter to a specific KB |
| `status` | string | Filter by status: `ok`, `error`, `skipped` |
| `days` | int | Limit to events from the last N days |
| `limit` | int | Page size (default 200) |
| `offset` | int | Pagination offset (default 0) |

**Response:** `IngestEventPage`

```json
{
  "events": [
    {
      "id": 1,
      "ts": "2025-04-20T10:01:23+00:00",
      "kb_id": "default",
      "document_id": "doc-42",
      "filename": "report.pdf",
      "status": "ok",
      "chunks": 34,
      "file_size_mb": 1.2,
      "duration_ms": 4200,
      "error": null
    }
  ],
  "total": 142,
  "limit": 200,
  "offset": 0
}
```

---

### Admin Config

```
GET /admin/config
PUT /admin/config
```

Read or update the admin configuration. Currently controls automatic conversation history cleanup.

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_cleanup_enabled` | bool | `true` | Enable automatic deletion of old conversations on startup |
| `auto_cleanup_months` | int | `12` | Delete conversations older than this many months (1–99) |
| `auto_cleanup_last_run` | string \| null | `null` | ISO timestamp of the last cleanup run (read-only; ignored on PUT) |

---

### Clear Conversation History

```
POST /admin/clear
```

Immediately deletes all conversations older than a given threshold. This is irreversible.

**Body:**

```json
{ "older_than_months": 6 }
```

**Response:**

```json
{
  "deleted_conversations": 84,
  "deleted_messages": 1203,
  "deleted_reactions": 47,
  "deleted_sources": 3102
}
```

---

### Statistics

Three read-only endpoints backed by the conversation database. All accept a `?days=` query param (default 180).

| Endpoint | Description |
|---|---|
| `GET /admin/stats/usage` | Daily conversation and message counts |
| `GET /admin/stats/db` | Row counts and disk sizes for the conversation DB and vector store |
| `GET /admin/stats/performance` | Per-model token/s and response time stats |

---

### LLM Debug Mode

Captures raw LLM prompts and responses to a log file for debugging. Off by default. In-memory flag — resets on restart.

| Endpoint | Description |
|---|---|
| `POST /admin/llm-debug/enable` | Turn on debug logging |
| `POST /admin/llm-debug/disable` | Turn off debug logging |
| `GET /admin/llm-debug/status` | Returns `{"enabled": bool}` |
| `GET /admin/llm-debug/log[?lines=100]` | Returns the last N lines of the debug log |

---

## Branding

### Get Branding

```
GET /api/v1/branding
```

Public — no auth required. Returns the current agent name and avatar URL. Called by all clients on load to personalise the UI.

**Response:**

```json
{
  "agent_name": "Lancy",
  "agent_avatar_url": "/uploads/avatar.png"
}
```

`agent_avatar_url` is `null` if no custom avatar has been uploaded.

---

### Update Branding

```
PUT /api/v1/branding
```

**Admin only.** `multipart/form-data`.

| Part | Type | Description |
|---|---|---|
| `agent_name` | string (form field) | New display name; omit or send empty to leave unchanged |
| `avatar` | file (optional) | PNG, JPEG, WebP, or SVG; max 2 MB. Replaces any existing avatar. |

**Response:** updated `BrandingConfig`

---

### Delete Avatar

```
DELETE /api/v1/branding/avatar
```

**Admin only.** Removes the uploaded avatar file and resets `agent_avatar_url` to `null`. Has no effect if no avatar is set.

**Response:** updated `BrandingConfig`

---

## OpenAI-Compatible

### Chat Completions

```
POST /v1/chat/completions
```

The primary entrypoint for programmatic RAG queries. This is what Open WebUI, LibreChat, and any OpenAI-compatible client use to query the knowledge base.

Supports both streaming (`stream: true`) and non-streaming responses. The `model` field is accepted but ignored — the active KB is always used.

**Request:**

```json
{
  "model": "rag-assistant",
  "messages": [
    {"role": "user", "content": "What does the Q1 report say about margins?"}
  ],
  "stream": false
}
```

Multi-turn conversations are supported: include prior `user`/`assistant` turns in `messages`. System messages are silently ignored — the agent uses its own configured system prompt.

**Quick curl example (through the frontend proxy with auth):**

```bash
curl -s -X POST "http://localhost:3000/v1/chat/completions" \
  -H "Authorization: Bearer <APP_PASSWORD>" \
  -H "Content-Type: application/json" \
  -d '{"model":"rag-assistant","messages":[{"role":"user","content":"What are the key findings?"}]}'
```

**Streaming note:** `stream: true` returns SSE in OpenAI chunk format, but the LLM response is fully generated before forwarding — it is not token-by-token streamed. This is transparent to clients.

**Sources:** appended to the response content as a markdown block (`---\n**Quellen:**\n- …`). There is no separate structured sources field in this endpoint; use the native `/rag/retrieve` endpoint if you need structured chunk metadata.

---

### List Models

```
GET /v1/models
```

Returns the available KBs as an OpenAI model list, allowing clients to switch KBs by selecting a model.
