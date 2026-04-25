# CHANGELOG

All notable changes to the Lancy fork are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Lancy v0.2.32] — 2026-04-25 · rlei-odes

### Added — Document Upload API

New endpoint for pushing documents into a KB over HTTP without requiring shared filesystem access. Designed as the ingestion path for remote deployments (e.g. DGX Spark) and as a webhook target for DMS automation pipelines.

- `POST /api/v1/kb/{id}/documents` — accepts multipart file + JSON metadata, ingests the document into the target KB via the existing ingestion pipeline, then discards the temp file
- `document_id` (required) enables versioning: re-uploading the same `document_id` deletes existing chunks before inserting new ones
- `source_file` defaults to the uploaded filename so citations show the real name rather than the temp path
- Full DMS metadata schema supported: `title`, `author`, `document_class`, `document_type`, `document_created_at`, `document_released_at`, `source_url`, `tags` — all fields are optional and stored verbatim on every chunk
- KB analytics sidecar (`kb_stats_{kb_id}.json`) is now updated after upload ingestion, keeping the Analytics tab in sync with incrementally uploaded documents
- `VectorStore.delete_chunks_by_document_id()` abstract method added; implemented for both ChromaDB and pgvector backends

### Added — Spark deployment scripts

Scripts for deploying the backend on a DGX Spark (or any Ubuntu/ARM machine):

- `scripts/spark-install.sh` — one-time setup: system packages, venv, pip install
- `scripts/start-backend.sh` — start backend-only in the background (no frontend, no Ollama check); prints LAN IP on start
- `scripts/stop-backend.sh` — stop via PID file, fall back to port kill

### Added — API documentation

- `docs/API_Endpoints.md` — full endpoint reference covering all KB, RAG, file serving, and OpenAI-compatible endpoints with request/response schemas

### Fixed

- Sidebar chunk count showed 0 / "not yet indexed" after incremental reindex — `update_stats` was using the delta count instead of the actual vector store total
- `source_file` in uploaded document chunks was set to the temp filename; now defaults to the original uploaded filename

---

## [Lancy v0.2.31] — 2026-04-22 · rlei-odes

### Added — Retrieval Explorer

Interactive explorer panel for inspecting what the retrieval pipeline actually returns before the LLM sees it. See `DESIGN_DOC_Retrieval_Explorer.md` for the full design record.

- `POST /api/v1/rag/retrieve` backend endpoint — runs the full retrieval pipeline (BM25, semantic, RRF, HyDE, query expansion, reranking) against a query without invoking the LLM; returns ranked chunks with scores and metadata
- Retrieval Explorer panel in the frontend — accessible from the sidebar; shows the probe results as a ranked chunk list with score, source file, page, and chunk text
- Results update live on query submit; panel state is independent of chat sessions

### Changed — Multilingual prompt improvements

- **System prompt** — replaced the hardcoded German-only prompt with a universal English prompt that instructs the LLM to detect the user's language and respond accordingly; supports cross-lingual retrieval (query in one language, documents in another)
- **Query expansion** — removed the forced English-only output; now generates queries in both the original query language and English for broader retrieval coverage across multilingual corpora
- **Query reformulation and HyDE** — added explicit language constraints so standalone query rewriting and hypothetical document generation stay in the user's language rather than defaulting to English

### Added — Chunk Browser (Retrieval Explorer v2)

Second tab on the `/explorer` page for browsing the raw vector store contents without running a query. See `DESIGN_DOC_Retrieval_Explorer.md` section 8 for the full design record.

- `POST /api/v1/rag/chunks` backend endpoint — server-side paginated fetch over indexed chunks; accepts a list of `{key, op, value}` filter conditions (ANDed); `limit+1` trick drives `has_more` without a total count query
- `ChunkBrowser` frontend component — file dropdown (populated from `store-info` on tab switch), add-filter rows with metadata key suggestions, TanStack Table v8 with fixed baseline columns (`#`, File, Title, Index, Type) plus dynamic columns derived from the result metadata; click-to-expand rows show full chunk text in a scrollable monospace pane; load-more pagination
- Tab switcher added to the Explorer page (Retrieval Probe / Chunk Browser)
- `get_chunks_by_filter` in both vector store backends updated to accept neutral `{field: value}` filter dicts (translated to ChromaDB `$eq`/`$and` internally) and `limit`/`offset` for true server-side pagination — previously the method loaded the full collection
- `ContextWindowRetriever` fixed to use the neutral filter format; it was passing ChromaDB-native `$and`/`$eq` syntax directly, which would have silently failed against the pgvector backend



### Added — KB Analytics (Retrieval Explorer v3)

Third tab on the `/explorer` page showing health and indexing statistics for any Knowledge Base without querying the vector store at runtime. See `DESIGN_DOC_KB_Analytics.md` for the full design record.

- Stats sidecar `db/kb_stats_{kb_id}.json` written at the end of every ingestion run — zero cost on page load; survives server restarts
- Full recompute on reset runs; incremental merge on partial runs (new hash entries inserted, bucket counts added, `chunks_per_document_distribution` recomputed, history entry appended)
- `chunk_chars` metadata field stamped on every chunk in `load_chunks()` (shared prerequisite with the planned chunk-length filter)
- `GET /api/v1/kb/{kb_id}/stats` endpoint — reads sidecar, 404 if KB has not been indexed yet
- `KbAnalytics` frontend component — KB selector (defaults to active KB), four panels:
  - **Summary strip** — total chunks, total documents, avg / P50 (median) / P95 chunk size
  - **Chunk size distribution** — bar chart in 200-char buckets (0–200 … 2000+)
  - **Chunks per document distribution** — 101-bar histogram (1–100 individual bars + 100+), scaled to any KB size
  - **Ingestion history** — stacked bar chart grouped by calendar day; incremental runs and reset runs shown as separate colour segments
- Recharts installed; CSS variables resolved via `getComputedStyle` so charts respond to light/dark mode
- All four UI languages updated (en, de, fr, it)

### Fixed

- `vs_path` in the default KB registry corrected to point within the project directory; previously pointed to a stale absolute path from the predecessor project


---

## [Lancy v0.2.30] — 2026-04-19 · rlei-odes

### Added — Multimodal Image Retrieval Pipeline

Dual-collection RAG pipeline for indexing and retrieving images from PDFs and standalone image files. See `DESIGN_DOC_IMAGE_RETRIEVAL.md` for the full design record.

- Two independent KB-level toggles: `image_indexing_enabled` (extract and embed images at ingest) and `image_retrieval_enabled` (query `vs_image` and inject into LLM context). Both default to `false` — zero overhead when disabled.
- Image chunks embedded with `Qwen3VL-Embedding-2B` into a separate ChromaDB collection at `<vs_path>_images`. Text pipeline unchanged.
- Session-level `image_retriever_top_k` (1–4, default 1) shown in the sidebar only when the active KB has `image_retrieval_enabled` on.
- `image_embedding_model` configurable per KB; shared by both indexing and retrieval.
- `write_images` parameter threaded through `_collect_candidate_files` and `load_chunks` — standalone image files enter the dedup pre-pass and are loaded as base64 chunks when enabled.
- `build_vector_store` generalised: text-only filter removed so image chunks are embedded correctly; caller routes text and image chunks to the appropriate store.
- Retrieval parallelised with `asyncio.gather` across all retrievers (replaces sequential loop); image retriever bypasses LLM reranking.
- KB deletion cleans up both `vs_text` and `vs_image` directories.
- All four UI languages updated (en, de, fr, it).

### Fixed — `stop.sh` Leaves Frontend Workers Running

Next.js spawns child worker processes not covered by the PID file. After `./stop.sh` these lingered, holding port 3000 so the next `./start.sh` would open on port 3001. Added `pkill -P <frontend_pid>` to kill child processes before the main process.

---

## [Lancy v0.2.29] — 2026-04-18 · rlei-odes

### Fixed — Answer Disappears After Streaming

`mistral-nemo:12b` emits literal newlines inside JSON string values, which is invalid JSON. `partial_json_loads` returned `{}`, the `"answer"` key was missing, and the DB record was saved as empty — causing the streamed answer to vanish when the frontend refreshed from the DB.

- `_escape_literal_newlines()` added to `utils/json.py` — walks the JSON character-by-character and escapes bare `\n`/`\r` inside string values before parsing
- `parse_llm_json_stream` now catches all exceptions from `partial_json_loads`, not only `ValueError`
- `_answer_post_processing` in `main.py` falls back to the raw accumulated text when the `"answer"` key cannot be extracted, preventing silent data loss

### Fixed — CORS Errors from Footer and SendBar

`footer.tsx` and `send-bar.tsx` used `process.env.NEXT_PUBLIC_SERVER_URL || "http://localhost:8080"` as API base, causing direct browser requests instead of routing through the Next.js proxy. Changed to `typeof window !== "undefined" ? "" : (process.env.SERVER_URL ?? "")`, matching the pattern used by all other components.

### Fixed — Duplicate React Key in Suggestions

`Suggestions` component keyed suggestion cards by `suggestion.text`, which caused a React warning and potential rendering issues when two suggestions shared the same text (e.g. both using `"Summarise:"`). Changed to index-based keys.

### Improved — Reindex Success Toast

- Skip counts split into `files_skipped_store` (already in vector store) and `files_skipped_batch` (duplicate content within the same run); toast shows them separately when both are non-zero
- Completion timestamp appended to the toast (e.g. "· 14:32")

### Added — Re-index Confirmation Dialog

The Re-index ↺ button (reset=True) now shows an inline confirmation dialog before proceeding. When the store is non-empty, the dialog displays the current chunk and file count ("This will permanently delete N chunks from M files…"). All four UI languages supported.

---

## [Lancy v0.2.29] — 2026-04-16 · rlei-odes

### Fixed — Markdown and Plain-Text Ingestion

`MarkdownChunker._pdf2markdown` was missing `**kwargs`, causing all `.md` and `.txt` files to fail with `unexpected keyword argument 'do_ocr'` and produce 0 chunks. Added `**kwargs` to absorb PDF-specific parameters passed by the parent `make_chunks`.

### Fixed — Reindex Proxy Timeout

`POST /reindex` previously held the HTTP connection open until ingestion completed. The Next.js Flatpak proxy would time out on large KBs, returning a 500 to the frontend while the job continued silently in the backend.

- `POST /reindex` now returns `{"started": true}` immediately; the job runs as a FastAPI background task
- `last_result` added to `IndexStatus` and populated by `rebuild_callback` on completion
- Frontend polling loop extended with `prevFinishedAt` detection (same pattern as the indexing progress modal) to show the success toast and refresh the KB registry when the job finishes

---

## [Lancy v0.2.28] — 2026-04-14 · rlei-odes

### Added — Ingestion Deduplication

Content-hash based deduplication prevents redundant parsing and embedding when the same
file is encountered more than once, either across runs or within a single batch.

- `file_hash()` — SHA-256 fingerprint of raw file bytes; content-based, filename-agnostic
- `_collect_candidate_files()` — shared helper for file discovery (extension, size, EVALUATION filter), used by both the dedup pre-pass and `load_chunks`
- Pre-pass in `_run_ingestion`: hashes all candidate files in an executor before parsing; applies cross-run dedup (skip if hash already in store) and within-batch dedup (skip duplicate content in the same run); only files that pass both checks reach `load_chunks`
- `load_chunks` gains `include_files` and `file_hashes` params; stamps every chunk with `chunk.metadata["file_hash"]` for future lookups; falls back to on-the-fly hashing when called standalone (notebook compatible)
- `build_vector_store` removes the all-or-nothing `current_count > 0` guard (which silently skipped new files on incremental runs); groups chunks by `file_hash`, skips groups already in the store; accepts `existing_hashes` from caller to avoid a second full metadata scan
- `VectorStore.get_file_hashes()` — new abstract method; implemented in `ChromaDBVectorStore` (metadata-only scan via `run_in_executor`) and `PGVectorStore` (DISTINCT SQL on `chunk_metadata["file_hash"]`)
- `ReindexResult` gains `files_skipped` field; success toast in all four UI languages shows skipped count when non-zero
- Warning logged when a store is non-empty but has no `file_hash` metadata (KB indexed before this feature; first incremental run re-embeds everything, subsequent runs are incremental)
- `ARCHITECTURE.md`: new Ingestion Pipeline chapter documenting the full flow, dedup layers, key functions, and threading model

---

## [Lancy v0.2.27] — 2026-04-13 · rlei-odes

### Added — Project Tooling
- `CLAUDE.md` — AI assistant briefing: project overview, key files, current config, development guidelines, security principles, commit convention, documentation drift rules
- `start.sh` / `stop.sh` rewritten — self-contained, path-independent, starts both backend and frontend in the background with PID tracking and log files in `logs/`
- `start.sh`: Ollama health check on startup; warns if Ollama is unreachable or configured model is not pulled (reads model name from `rag_config.json` dynamically)

### Added — Documentation
- `README.md`: Known Issues section added
- `CHANGELOG.md`: changelog reference added to `CLAUDE.md`

### Changed — Frontend
- Temperature hint updated in all four languages (DE/EN/FR/IT) to show `default: 0.2 (recommended for RAG)`
- `DEFAULT_SESSION.llm_temperature` aligned to `0.2` to match `rag_config.json`
- `frontend/package.json`: removed `open-browser` script and auto-launch from `dev` command (was hardcoded to wrong port 3001, opened browser before server was ready)

### Removed
- `SUMMARY.md` — redundant with `README.md`; Known Issues section preserved and moved to `README.md`

---

## [Lancy v0.2.26] — 2026-04-12 · rlei-odes

### Fixed — Merge Conflict Resolution
Resolved leftover upstream merge conflict markers across four files, keeping the fork's HEAD version in each case:
- `conversational-toolkit/src/conversational_toolkit/chunking/pdf_chunker.py` — guard `write_images` flag before creating image output directory
- `conversational-toolkit/src/conversational_toolkit/llms/local_llm.py` — lazy `MessageContent` import; use `raw_content` variable consistently
- `conversational-toolkit/src/conversational_toolkit/conversation_database/controller.py` — retain keepalive SSE streaming logic (`asyncio.wait` + timeout sentinel)
- `backend/src/lancy/utils/json.py` — retain pre-compiled `_CODE_FENCE_RE` regex and fork's JSON parse logic

### Fixed — Dependencies
- `requirements.txt`: added `einops` (required by `nomic-ai/nomic-embed-text-v1` via SentenceTransformers)

### Fixed — VectorStore Abstraction
- Added `get_source_files()` abstract method to `VectorStore` base class
- Implemented in `ChromaDBVectorStore` (metadata-only fetch via `run_in_executor`) and `PGVectorStore` (async `DISTINCT` SQL query)
- Removed two `isinstance(vs, ChromaDBVectorStore)` checks from `main.py` that bypassed the abstraction and left pgvector without a file list
- Extracted `_inject_source_files()` async helper — called from `rebuild_callback` and `_startup` so both backends get the indexed file list injected into the agent system prompt
- Removed `ChromaDBVectorStore` import from `main.py` — no longer needed

### Added — Prompt File Management
- System prompt extracted from hardcoded Python string into `prompts/system_prompt.default.md` — committed, ships as the baseline
- `prompts/system_prompt.custom.md` — gitignored; written automatically when user saves a custom prompt via the UI, never pushed to the repo
- Load priority: custom file → default file; clearing the prompt in the UI deletes the custom file and resets to default
- `rag_config.json` no longer stores `system_prompt` — prompt lives exclusively in the file system

### Fixed — Repository Hygiene
- `.gitignore`: added `.venv/` entry (was listed as `venv/` only, causing IDE source control noise on fresh installs)

---

## [Lancy v0.2.25] — 2026-03-26 · Vonlanthen INSIGHT

This release represents the full Lancy production stack on top of the SDSC baseline.

### Added — Multi-KB Architecture
- KB registry (`knowledge_bases.json`) with support for N independent knowledge bases
- Hot-swap active KB at runtime via `POST /kb/active` — no restart required
- Per-KB configuration: vector store, embedding backend, embedding model, retrieval params
- Indexing control: progress tracking, cancellation (`POST /kb/{id}/cancel`), 409 guard
- KB Router (`kb_router.py`) as dedicated FastAPI router

### Added — Vector Store
- pgvector backend (`PgVectorVectorStore`) as alternative to ChromaDB
- Vector store selector per KB in knowledge_bases.json (`"vector_store": "chromadb" | "pgvector"`)

### Added — Retrieval
- BM25 sparse retrieval (rank_bm25 library)
- Hybrid retrieval: BM25 + semantic fusion via Reciprocal Rank Fusion (RRF)
- HyDE (Hypothetical Document Embeddings) — improves recall on indirect queries
- Query expansion — multi-query fusion via RRF
- LLM reranking — cross-encoder quality pass on retrieval candidates
- All retrieval features configurable per session, persisted in `rag_config.json`

### Added — Embedding Backends
- LiteLLM embedding backend (`LiteLLMEmbeddings`) — any OpenAI-compatible embed endpoint
- Ollama embedding backend (`OllamaEmbeddings`)
- Custom embedding backend with configurable base URL
- Embedding backend selector per KB

### Added — LLM Backends
- Anthropic LLM backend (`AnthropicLLM`)
- LiteLLM LLM backend (`LiteLLMLLM`) — routes to any provider via proxy
- Dynamic Ollama model list fetched from local server at runtime

### Added — Chunking
- MarkItDown chunker (`markitdown_chunker.py`) — EPUB, DOCX, DOC support

### Added — OpenAI-Compatible Endpoint
- `POST /v1/chat/completions` — maps to active KB RAG query
- `GET /v1/models` — returns available KBs as model list
- Works with Open WebUI, curl, n8n, and any OpenAI-compatible client

### Added — Frontend (RAG Config Panel)
- Collapsible right-side RAG Parameters panel (`rag-config-panel.tsx`)
- Live parameter tuning: K, BM25, HyDE, Query Expansion, Reranking, temperature
- Presets: fast / balanced / quality
- Re-index button with live progress and cancel
- LLM model selector with dynamic Ollama list
- Embedding backend and model configuration

### Added — Frontend (Sidebar & Session Management)
- Config badges per conversation: KB · LLM · T= · emb: · k= · BM25 · Rerank · HyDE
- Session labels for A/B evaluation grouping
- Per-session delete
- Hover tooltip with full config snapshot

### Added — Frontend (Auth)
- Password-protected login page (`/login`)
- Session cookie authentication (`rag_auth`)
- Middleware protecting all routes
- `POST /api/auth/login` and `POST /api/auth/logout` handlers

### Added — Frontend (i18n)
- Internationalization: DE / EN / FR / IT
- Language auto-detection from browser
- All UI strings externalized to `frontend/src/lib/lang/`

### Added — Frontend (Generation Stats)
- Per-response footer: LLM model name · query duration · tokens/second

### Added — Deployment
- Systemd user service templates (`insight-backend.service`, `insight-frontend.service`)
- nginx reverse proxy configuration (`nginx.conf`)
- `.env.example` for frontend
- Multi-device proxy rewrite setup (SERVER_URL="" pattern)

### Fixed — Async Architecture
- Full `asyncio` + `run_in_executor` refactor throughout backend
- Fixes SSE/streaming blocking under concurrent load
- SentenceTransformer, ChromaDB, BM25 all non-blocking

### Fixed — Stream Sentinel
- `AttributeError` on response end in `controller.py` when stream sentinel was `None`
- Fixes frontend hanging on query completion in certain LLM backends

---

## [Upstream Baseline] — 2026-03 · SDSC

Notebook material reviewed and finalized by Paulina Koerner (SDSC):
- All feature notebooks (`feature0a` through `feature4e`) reviewed and corrected
- `feature4` utility file created
- mypy warnings resolved

Original baseline implemented by the Swiss Data Science Center (SDSC):
- 5-stage RAG pipeline: chunk → embed → store → retrieve → generate
- ChromaDB vector store
- SentenceTransformer embeddings
- Ollama and OpenAI LLM backends
- RAGAS evaluation framework integration
- Structured evidence outputs (VERIFIED / CLAIMED / MISSING / MIXED)
- BM25 + hybrid RRF retrieval (notebook implementation)
- HyDE and query expansion (notebook implementation)
- Agent and tool-use notebooks
- PrimePack AG scenario dataset with deliberate flaws

---

