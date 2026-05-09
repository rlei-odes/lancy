# BACKLOG

Planned improvements and feature work for the Lancy project.
Items are grouped by theme and roughly prioritised within each section.

---

## Known Bugs

### Sources Panel Sometimes Empty

The sources panel occasionally shows no sources even when the answer clearly references document content. Root cause is a structural tension in the prompt: the LLM is told to use filenames in the answer text but exact UUIDs in `used_sources_id` — a cognitive split that smaller models (mistral-nemo) handle inconsistently.

**Investigation steps:**
1. Log the full parsed `json_answer` dict and `relevant_source_ids` in `_answer_post_processing` for queries where sources go missing — to determine whether the LLM returns `[]`, wrong IDs, or filenames in that field.
2. Check whether failures correlate with response length (partial-JSON chunking) or specific models.

**Likely fix:** derive the sources list from filename matches in the rendered answer content rather than relying solely on `used_sources_id`. The LLM reliably uses filenames in the text (that instruction is followed); building a reverse map from filename → source ID would make citation extraction robust regardless of UUID compliance. `used_sources_id` would become corroboration rather than the sole gatekeeper.

Note: the existing `_UUID_RE` inline fallback is effectively dead code given the current prompt explicitly says "No UUIDs in the text."

### Low Source Citation Count

Observed that answers sometimes cite only 2 chunks even when retrieval is configured with a higher `k`. Possible causes: the reranker is collapsing similar chunks into fewer sources, the `used_sources_id` field in the LLM JSON response is under-populated (model not citing all chunks it used), or the source deduplication step in `_answer_post_processing` is too aggressive. Needs investigation with logging enabled on retrieved chunk count vs. cited chunk count. It might be good and even intended behaviour — when no fit / similarity is observed above the threshold, no bad fitting chunks should be served.

### LLM error: The size of tensor a (19) must match the size of tensor b (17) at non-singleton dimension 1

Unknown origin. Possibly connected to Query Expansion. Could be related to the LLM expanding the query in other languages. Observed: Korean, Thai. Possibly an LLM internal bug.

---

## Authentication & Access Control

### Active Directory / SSO Integration

**Goal:** optional AD/SSO login as an alternative to the shared-password model, for organisations that manage users centrally.

**Scope:**
- LDAP / Active Directory bind for authentication
- Map AD groups to roles (e.g. domain users → user role, IT group → admin role)
- Feature should be opt-in and deactivatable — shared-password mode remains the default for simpler deployments
- Builds naturally on top of the existing admin/user role separation

**Why:** relevant for enterprise rollout where user management via AD is already in place and individual password distribution is impractical.

---

## Ingestion

### Image Retrieval — pgvector Support for `vs_image`

**Goal:** extend the dual-collection pipeline to support pgvector as the image vector store, mirroring the existing text store backend selection.

**Why:** ChromaDB is local-only and single-process; production deployments targeting pgvector currently have no image store option. The image pipeline was implemented ChromaDB-first to unblock feature work; this closes the gap.

**Scope:**
- `make_vector_store` call in `main.py` for the image store passes `table_name=f"rag_{kb_id}_images"` — pgvector already uses this for namespacing, so no schema change is needed
- `_run_ingestion` in `main.py`: the image store instantiation mirrors the text store path; ensure the pgvector branch is exercised and the async/sync split is correct (same pattern as the existing text pgvector path)
- `delete_kb` in `kb_router.py`: for pgvector, deletion of `vs_image` should drop the images table — verify the cleanup path covers both stores
- Manual test: pgvector KB with image toggles on — index a PDF, query, confirm images surface

### Image Retrieval — Source Citations with Image Preview

Test Learning: this seems to be partially working already.

**Goal:** display retrieved images as source citations in the chat, alongside existing text chunk citations.

**Why:** currently images are injected into LLM context but the source panel only shows text chunks. Users have no way to see which images the answer drew on or verify them.

**Scope:**
- Backend: image chunks need to be returned in the `sources` field of the RAG response (currently only text chunks are included)
- Frontend: the sources panel (and/or a dedicated image sources row) renders base64 image thumbnails with a filename label; clicking expands to full size
- Design open question: inline thumbnail strip below the answer vs. collapsible section in the sources panel — decide at implementation time based on typical image count (1–4 per query)

### PDF OCR vs. Image Retrieval — Clarify and Document for Users

Two separate features both deal with "images in PDFs" but solve different problems:

- **PDF OCR** (`pdf_ocr_enabled`) — uses Docling's OCR to extract *text* from scanned pages (image-only PDFs where no selectable text layer exists). Output: text chunks in `vs_text`. No GPU required. Useful for scanned contracts, archived documents.
- **Image retrieval** (`image_indexing_enabled`) — extracts *embedded figures, charts, and diagrams* from PDFs as images and stores them as visual embeddings in `vs_image`. Requires Qwen3VL + GPU. Useful for technical documents with data visualisations.

They are complementary, not alternatives — it is reasonable to have both on. A scanned annual report benefits from OCR (to read the text pages) and image indexing (to retrieve the charts). Needs a short guidance note in the UI or documentation to help users understand when to enable each.

### Image Retrieval — Automatic Multimodal LLM Validation (optional)

When `image_retrieval_enabled` is on, the system silently injects images into the LLM context but does not validate that the configured answering LLM is actually multimodal. A non-multimodal LLM will either ignore the image data or produce an error. A startup/save-time check against the Ollama model manifest (or a known allowlist) could surface a clear warning in the UI before the user runs a query.

### External Metadata Ingestion & Document Versioning

The metadata schema (title, author, document_id, document_class, etc.) and upload API are in place. What remains is the **versioning/replacement and deletion layer**:

- `VectorStore.delete_chunks_by_document_id(document_id)` — new abstract method, implemented for ChromaDB (metadata filter delete) and pgvector (DELETE WHERE clause)
- Ingestion pipeline: when a file carries a `document_id` that already exists in the store, delete the old chunks before adding the new ones
- `ReindexResult` extended with `files_updated` count (replacements) alongside existing skip counts
- Reindex toast updated: N added / N updated / N skipped
- `GET /api/v1/kb/{id}/documents/{document_id}` — returns current metadata for that document_id or 404
- `DELETE /api/v1/kb/{id}/documents/{document_id}` — removes all chunks for that document_id; 404 if not found; for image-enabled KBs, deletes from both `vs_text` and `vs_image`
- Optional stale-version guard on upload: `if_released_after` param; returns 409 if the stored `document_released_at` is equal to or newer
- Optional hash-dedup: skip delete+reindex cycle if incoming file hash matches stored hash and no metadata fields differ

**What is explicitly out of scope for Lancy:**
- UI for selecting or mapping metadata fields from an uploaded sample JSON
- Status-based filtering at ingestion time (filter at the DMS export / webhook level)
- Automatic time-based expiry of documents based on metadata timestamps
- Direct DMS API or database connectivity

### Document Actuality — Time-Based Metadata and Retrieval Weighting

**Goal:** make document age a first-class signal in retrieval so that answers can prefer recent documents, flag outdated sources, or apply a hard cutoff by date.

**Why:** in knowledge bases that evolve over time (policy documents, technical specs, product data), an older document may be technically relevant but factually superseded. The current retrieval pipeline has no concept of document age.

**Ingest side:** `document_released_at` is already part of the metadata schema. Additionally, stamp every chunk with `ingested_at` (ISO date, set automatically) as a fallback when no explicit date is provided.

**Retrieval side:** three configurable strategies, selectable per session or preset:

1. **Prefer recent** — time-decay score penalty; recent documents rank higher all else being equal
2. **Hard cutoff** — exclude chunks older than a given date from the candidate pool entirely
3. **Soft penalty** — scale down the final score of old chunks by a configurable factor without fully excluding them

**UI:** new session parameter in the RAG Config sidebar: "Document actuality" — off / prefer recent / cutoff (with a date picker) / soft penalty (with a decay-strength slider). Source citations show the document date when available.

### Ingestion Pipeline — File-by-File Processing for Crash Recovery

**Current architecture:** `run_ingestion` processes all files in two sequential sweeps — Docling parses every file first (phase 1), then all resulting chunks are embedded and written to the vector store (phase 2). A crash mid-embedding loses all Docling work for the remaining files.

**The fix:** process one file at a time — Docling → embed → store → next file. A crash then costs at most one file; everything already committed is safe and will be skipped by hash-based dedup on the next run.

The main things to untangle:

1. Embedding model initialization — still done once before the loop, not once per file.
2. `build_vector_store()` already accepts any chunk list — call it with one file's chunks at a time. No API change needed.
3. The pre-pass stays as-is — still useful to run upfront to identify which files to skip via hash dedup. It just no longer loads chunks.
4. Image chunks — initialize the image vector store once before the loop too; store each file's image chunks immediately after its text chunks.
5. Progress reporting — actually gets simpler. Instead of "loading phase / embedding phase" across the whole batch, each file has its own mini-lifecycle.

Roughly 1–2 hours of careful work once the decision to do it is made.

### Chunking Strategy Investigation — Lessons from Large-Scale Ingestion

**Trigger:** practitioner account of ingesting 20k documents / 600k pages. Key insight: not all pages need to be chunked — the pipeline must first analyze each page and chunk only relevant content. Images and tables require separate handling, not the same path as prose text.

**Questions to investigate:**
- Is our current fixed-token chunking strategy appropriate for heterogeneous documents (prose, tables, images)?
- Should we add a pre-chunking relevance/content-type analysis step that routes pages differently?
- How do we handle tables and images as distinct chunk types vs. embedding them as text descriptions?
- At what document volume does page-level chunking outperform paragraph/sentence-level chunking for retrieval quality?

**Likely outcome:** a chunking strategy ADR that maps content type → chunking method, with a smarter pre-filter before indexing.

### Minimum Chunk Quality — Filter Near-Empty Chunks at Ingestion

**Problem:** the current pipeline indexes chunks that contain almost no retrievable content. A typical example from a PDF-converted document:

```
## Water-Activated Tape

<!-- image -->
```

This is a heading with an image placeholder — no prose, no facts, nothing an embedding model can meaningfully represent.

**What to address:**
- Add a minimum content threshold at ingestion time: merge forward any chunk below N non-whitespace characters or M meaningful tokens after stripping Markdown syntax (`#`, `<!-- ... -->`, `![]()`, etc.) — the heading or image placeholder becomes a prefix of the next substantive chunk rather than a standalone entry
- A "content score" heuristic: chunks with only headings, only image tags, or only whitespace score 0 and are candidates for merging
- Merge direction and merge limits: a merging loop with a maximum merged size cap (respecting `max_chunk_tokens`) to avoid creating oversized chunks

**Why it matters:** retrieved chunks go directly into the LLM context. A useless chunk at rank 1 wastes a slot, confuses the reranker, and can cause the LLM to return "I don't have enough information" when the real answer exists just one chunk away.

---

## UI & Settings

### Align Design of Left and Right Sidebar, Main Chat Page

All three look nice now but they are not really aligned with each other. Not necessary, but is an inconsistency.

### Improve Design of Source Citation Window

Definitely does not look nice now but is hard to design as just raw markdown is shown. We can still improve this visually and align more with our design.

---

### Neighbour Chunk Expansion

**Current behaviour:** retrieval returns exactly `top_k` chunks. Each chunk is an isolated slice of the source document — the text immediately before and after it is not included, even if it would provide useful context.

**What's needed:** a toggleable option (e.g. `neighbour_chunks: int`, default 0) that, after retrieval, fetches preceding and succeeding chunks from the same source file for each result. The expanded set is deduplicated and passed to the LLM in place of the bare result set.

- Value is in increments of two: 0 = off, 2 = one neighbour each side, 4 = two each side. Maximum 6.
- **Option:** more automation — if the found chunk is very small, do an automatic expansion. Stop at a maximum number of chunks and a maximum combined character length.

**Use case:** documents where a retrieved chunk contains a partial answer — the conclusion of a paragraph, a table row without its header, or a clause that references the sentence above.

**Warning to surface in the UI:** enabling this option can up to triple the number of tokens sent to the LLM (up to `3 × top_k` chunks after dedup). Requires a large context window.

**Implementation notes:**
- `ContextWindowRetriever` in `context_window_retriever.py` already implements this fully — it is currently unused (not wired up).
- Backend change: wrap the active retriever with `ContextWindowRetriever` when the option is enabled in the RAG query path
- Config: `window_size: int` added to `RagConfig`; surfaced in the RAG Config panel next to `top_k`

**Variant — source citation view only (no LLM cost):**
Show neighbours in the sources panel when the user expands a citation. The retrieved chunk renders normally; preceding and succeeding chunks render below/above it in a muted style. The backend returns `neighbour_content_before` / `neighbour_content_after` alongside each source chunk. Zero latency impact, always safe to enable.

---

### RAG Settings: LLM Call Calculation

Especially with Neighbour Chunk Expansion, we have many variables that control how many chunks could get sent to the helper LLM and the main model. We should have a text-based preview telling the user the maximum value for each. If no helper model is selected, it should show that all reranking etc. calls go to the main model. The stats are: number of calls, max. number of chunks, for each.

---

### Chunk Browser — Filter for Chunks by Length

**Goal:** let the user filter the chunk browser by chunk character count — e.g. "show only chunks shorter than 100 chars" to find near-empty chunks, or "show only chunks over 1500 chars" to find runaway pages.

**Why:** the analytics histogram shows the distribution; this feature lets the user drill into the actual outliers. Natural complement to the KB Analytics tab.

**Prerequisite — store `chunk_chars` as metadata at ingestion:** stamp `chunk.metadata["chunk_chars"] = len(chunk.content)` in `load_chunks()`. This is a one-liner, but **existing KBs require a re-index** to pick it up.

**Backend:** ChromaDB's `$gte`/`$lte` operators support numeric range filters natively, but `_to_chroma_where()` in `chromadb.py` currently only translates equality (`$eq`) filters — needs extending. The chunk browser endpoint accepts optional `min_chars` / `max_chars` query params.

**Frontend:** a compact range input (two number fields or a dual-handle slider) in the Chunk Browser filter bar, alongside the existing source-file typeahead. Filters apply on submit, not on every keystroke.

**pgvector:** `WHERE (metadata->>'chunk_chars')::int BETWEEN $1 AND $2`

---

### Chunk Browser — Server-side File Search for Large KBs

**Current behaviour:** the file typeahead in the Chunk Browser fetches the full file list from `GET /api/v1/rag/store-info` on tab switch and filters client-side. Works fine for KBs with up to a few hundred files; for large KBs (thousands of source files) the upfront fetch becomes expensive.

**What's needed:** a dedicated `GET /api/v1/rag/files?q=<prefix>` endpoint that searches filenames server-side and returns only matches. The typeahead would fetch on each keystroke (debounced) instead of loading all filenames upfront.

**Blocker:** ChromaDB has no native metadata prefix-search — a full scan is required. pgvector's SQL `LIKE` query would make this trivial. Worth revisiting when pgvector support matures.

---

### Preset UI — End-to-End Review and UX Improvement

The preset save/load flow has grown organically and needs a thorough walkthrough. Known issues include `image_retrieval_enabled` being saved to the wrong config layer (fixed), but other fields may have similar misrouting. The UI interaction (save, load, "Standard" reset, switching KBs) is also unintuitive in places.

**To do:** sit down with the full preset flow — create, save, load, switch KB, reload page — and verify every field round-trips correctly. Identify UX rough edges and improve them.

---

### UI Branding & Customization — Remaining Scope

Agent name and avatar are done (v0.3.1). Remaining:

- **App name** — browser tab title and any top-bar branding; currently hardcoded in the Next.js layout
- **App favicon** — browser tab icon; replacing at runtime requires serving a dynamic file from a known path

Both require backend config storage plumbing that is already in place (`branding.json` / `GET|PUT /api/v1/branding`). The frontend side needs to apply the fetched values to `<title>` and `<link rel="icon">`.

**Note:** PWA manifest / OG images require a build-time step, not feasible at runtime.

---

### Industrial Cyber-Noir Theme ("Cyber-Purple")

**Goal:** add a third app theme — alongside light and dark — matching the lancy.tech visual identity: deep purple-tinted backgrounds, violet/cyan accents, glassmorphic card surfaces, a grid-dot body background, and optional CRT scanline overlay.

**How the current theming system works:** `useTheme.tsx` (custom Context, no next-themes), `globals.css` (HSL CSS variables in `:root` and `.dark`), Tailwind resolves via CSS variables.

**Implementation layers:**

| Layer | What it involves | Effort |
|---|---|---|
| Color palette | `.cyber-purple` CSS variable block in `globals.css` | ~1–2h |
| Theme enum + provider | Add `CYBER_PURPLE`; apply `cyber-purple` class to body | ~30 min |
| Toggle UI | 3-state cycle in settings | ~30 min |
| Grid dot background | CSS `background-image` radial-gradient scoped to `.cyber-purple` | ~30 min |
| Glassmorphic cards | `backdrop-blur` + semi-transparent `--card` CSS variable (alpha only in this theme) | ~1h |
| CRT scanline overlay | Full-viewport pseudo-element, `pointer-events: none` | ~30 min |

**Total for a solid first pass** (colors + grid + glassmorphism + theme switch, no glitch text): roughly a half-day.

---

## Admin Tooling

### User Feedback — Thumbs Up / Down

**Goal:** let users rate individual answers with a thumbs up or down. Capture the active RAG configuration at the time of rating so quality can be correlated with retrieval settings. The thumbs up / down UI is already implemented.

**Scope:**
- Rating stored in the conversation database alongside the conversation and message ID, with a full snapshot of the `RagConfig` active at that moment
- Admin view: a simple feedback log showing recent ratings, the question, the answer excerpt, and the config snapshot — sortable by rating and date
- Optional aggregation: rating counts per model, per KB, per preset

---

### Session Logs

**Goal:** capture valuable statistics about each user query run.

**Scope:**
- Used KB
- Used model (main and helper model)
- Duration in seconds (as displayed to the user)
- Calculated tokens per second (as displayed to the user)
- Considered chunks during the process (i.e. total including candidates, neighbour expansion)
- Chunks sent to reranking
- Chunks sent to the main model
- The above three stats, but in character count

**Technical solution:** evaluate how to implement this requirement, and what else would already be available to collect in the code.

---

### Customisable Retrieval Prompts

The query expansion, HyDE, and reranking prompts are currently hardcoded in Python. Unlike the system prompt (answer tone/format), these affect retrieval quality and could benefit from domain-specific tuning.

**Candidates:**
- **Query expansion** (`utils/retriever.py`) — could guide rephrasing toward domain vocabulary
- **HyDE** (`utils/retriever.py`) — hypothetical document generation; domain context improves embedding match quality
- **LLM reranking** (`retriever/reranking_retriever.py`) — could define what "relevant" means for the specific corpus

**Scope:** same file-based pattern as the system prompt — `prompts/query_expansion.default.md`, `prompts/hyde.default.md`, `prompts/reranking.default.md`, each with a gitignored `.custom.md` override. Admin-only UI exposure makes sense given the technical nature.

**System prompt UX — default vs. custom toggle:**
The system prompt field should have an explicit Default / Custom toggle. When the user switches to Custom for the first time, pre-fill the editor with the server default so they have a starting point. After that, the custom text is kept separate — switching back to Default restores the server default without destroying the custom draft.

**Domain context prompt (corpus glossary):**
Investigate adding a second, lightweight prompt field — a "corpus context" block — where the user can provide domain-specific instructions: special terminology, common abbreviations, ID patterns, or notes on document provenance. Distinct from the system prompt (answer format/tone) and from retrieval prompts (query rewriting). Appended to LLM context only when non-empty.

**Prompt editor UI:** the current sidebar is too narrow for comfortable prompt editing. Options: dedicated admin page (full-width), markdown editor with syntax highlighting and preview.

---

## Integrations

### Workflows Sidebar — Webhook Configuration

**Goal:** make the workflows sidebar panel useful by wiring it up to configurable webhook URLs.

**Scope:**
- Currently `WORKFLOWS = []` in `frontend/src/components/sections/sidebar/workflows.tsx` — entries are hardcoded
- Options: expose via environment variable, a `workflows.json` config file (gitignored, with a `.example`), or an admin UI panel
- n8n and Make are the most relevant targets for the planned deployment

**Why:** enables users to trigger external automations (create a ticket, save to Notion, forward to a colleague) directly from a RAG conversation without leaving the UI.

---

## Security & Privacy

### Full Offline Mode

**Goal:** the system should be fully functional with no internet connection after initial setup (model downloads). All dependencies that phone home should have their update checks suppressed by default.

**Known issue:** SentenceTransformer (and potentially other services in the stack) makes outbound requests on every model load to check for updates, even when all assets are fully cached locally. With no internet this causes multi-second retry delays and noisy error logs.

**Fix:** set a `LANCY_OFFLINE=1` environment variable in `start.sh` that maps to the relevant library-specific offline flags (e.g. `HF_HUB_OFFLINE=1` for the HuggingFace ecosystem).

```bash
# in start.sh, before the backend launch:
LANCY_OFFLINE="${LANCY_OFFLINE:-1}" \
HF_HUB_OFFLINE="${LANCY_OFFLINE:-1}" \
...
```

Document in the setup guide: after first run the system is designed to operate fully offline; how to temporarily disable for a model update (`LANCY_OFFLINE=0 ./start.sh`).

### Network Egress Audit — What Phones Home?

**Goal:** produce a complete, verified list of all external network calls made by the system under normal operation, for air-gap readiness and data privacy assessment.

**Known calls:**
- `huggingface.co` — SentenceTransformer model update check on every `build_embedding_model()` call (fixable with `HF_HUB_OFFLINE=1`)
- `ollama.com` — Ollama may check for binary or model updates; needs verification

**Unknown / to verify:** ChromaDB telemetry, Docling calls during document parsing, other Python dependencies that phone home on import.

**Method:** run the backend with a local DNS proxy or `tcpdump` to capture all outbound DNS queries and HTTPS connections during startup, ingestion, and a query.

**Output:** a documented list in `docs/admin-guides/` of all external hosts, what triggers the call, and how to suppress it.

### API Endpoint Protection — Rate Limiting and Request Queueing

The backend currently has no rate limiting or concurrency guards beyond the single-job check on `/api/v1/rag/reindex`.

**Recommended approach:**

- **Rate limiting:** add `slowapi` with per-IP limits on the hot endpoints — e.g. 10 requests/minute on `/messages/stream`, 30/minute on `/rag/retrieve`, and 5/minute on the auth endpoint.
- **LLM concurrency:** a simple asyncio semaphore on the answer path (e.g. max 3 concurrent LLM calls) prevents connection pile-up under load.
- **Auth hardening:** the passcode endpoint should reject requests after N failed attempts within a time window — a simple in-memory counter per IP.

---

## Production Installation and Architecture

### Component Map — What Runs Where

| Component | Process | Compute profile | Can run remotely? |
|---|---|---|---|
| Frontend | Next.js (Node.js) | Lightweight — serves UI, proxies API calls | Yes — any Node host |
| Backend | FastAPI (Python) | Medium baseline; heavy during indexing | Yes — needs network access to Ollama and vector store |
| nomic-embed-text | Inside backend process (SentenceTransformer) | CPU-bound; ~500 MB RAM | No — embedded in backend |
| Qwen3VL (image embeddings) | Inside backend process (transformers/PyTorch) | GPU-intensive; ~5 GB VRAM or very slow on CPU | No — embedded in backend |
| Ollama | Separate process / server | GPU-intensive for LLM inference | Yes — `ollama_host` setting |
| ChromaDB | Embedded in backend process | I/O-bound; scales to ~100k chunks comfortably | No — local filesystem |
| pgvector | External PostgreSQL + pgvector extension | I/O-bound; scales to millions of chunks | Yes — connection string |

**Key constraint:** the Next.js frontend proxies all API calls server-side (`SERVER_URL` in `frontend/.env`). For a split deployment (frontend and backend on different hosts), `SERVER_URL` must point to the backend from the *frontend server's* perspective — not the browser's. Leave it empty only when both run on the same host.

---

### Deployment Profiles

**Profile 1 — Single machine (current dev setup)**

Everything on one machine. Ollama local, ChromaDB local, no GPU required (text-only).

```
[User browser] → localhost:3000 (Next.js) → localhost:8080 (FastAPI) → localhost:11434 (Ollama)
```

**Profile 2 — GPU server + thin access machine**

Backend and Ollama on a GPU server. Frontend served from the same server or a lightweight separate host.

```
[User browser] → frontend-host:3000 (Next.js)
                      ↓SERVER_URL=http://gpu-server:8080
                 gpu-server:8080 (FastAPI + nomic + Qwen3VL)
                      ↓
                 gpu-server:11434 (Ollama)
                 gpu-server:5432  (pgvector, optional)
```

Ollama can alternatively run on a different GPU server than the backend — configure via `ollama_host` in the RAG config panel.

**Profile 3 — Full production split**

Frontend on a web server / reverse proxy (nginx, Caddy), backend on a GPU machine, pgvector on a managed PostgreSQL instance.

---

### Containerisation

Docker Compose is the natural packaging target. Rough service layout:

```yaml
services:
  frontend:   # node:22-alpine, builds Next.js, exposes 3000
  backend:    # python:3.13-slim + pip install, exposes 8080
  pgvector:   # pgvector/pgvector:pg16 (optional — swap for ChromaDB-only mode)
  ollama:     # ollama/ollama (optional — or point to host Ollama via ollama_host)
```

**Challenges to solve before containerising:**

- **GPU passthrough:** Ollama and Qwen3VL both need CUDA. Requires `nvidia-container-toolkit` on the Docker host.
- **Model persistence:** Ollama model files (~7 GB for mistral-nemo) and Qwen3VL weights (~5 GB) must be volume-mounted or pre-baked into the image.
- **Data and config persistence:** `db/` and `data/` must be volume-mounted — never baked into the image.
- **HuggingFace cache:** volume-mount `~/.cache/huggingface` so it survives container restarts. For air-gapped deployments, pre-populate the cache and set `HF_HUB_OFFLINE=1`.
- **`SERVER_URL`:** in a Compose setup, the frontend container reaches the backend via the Compose service name (e.g. `http://backend:8080`). `frontend/.env` must set `SERVER_URL=http://backend:8080`.
- **The dev server problem:** the current frontend runs `next dev`. A production Compose setup should build with `next build` and serve with `next start`.

---

### Using pgvector as Database

Use HNSW indexes on vector columns. Without an index, pgvector does a sequential scan (every row compared to the query), causing CPU spikes.

```sql
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
```

The index should be created after initial bulk ingestion, not before. Add this to the KB setup documentation when pgvector becomes the primary target.

---

### nginx Reverse Proxy Configuration

No example nginx config exists in the repo yet. Needed for any real deployment: TLS termination, single public entry point on port 443, HTTP → HTTPS redirect, security headers (`Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`).

**What to build:** a minimal `nginx.conf` example committed to `docs/admin-guides/`. Should cover a single-host deployment (one domain, certbot cert, proxy to `:3000`).

---

## Research & Future Directions

### Agentic RAG — RAG as a Tool

The current architecture uses a fixed pipeline: every query always triggers retrieval first (`CustomRAG`). The notebook prototypes (feature4b/d) explored a `ToolAgent` using the ReAct pattern, where retrieval is an *optional* tool call — the model decides whether to search the vector store or answer from its own knowledge.

**What exists in the codebase:**
- `ToolAgent` is already implemented in `conversational-toolkit/src/conversational_toolkit/agents/`
- The `Tool` base class lives in `conversational_toolkit.tools.base`

**Three levels of agentic capability:**

1. **Agentic Mode toggle** — switch the backend from `CustomRAG` to `ToolAgent`, making retrieval optional. Implement as a toggle in the RAG config panel.
2. **Extended tool registration** — register additional tools (e.g. calculators, external lookups) with the production agent by subclassing `Tool` and registering at startup.
3. **Multi-agent / subagent pattern** — wrap the RAG pipeline as a tool for a coordinator agent. Enables routing across multiple KBs by domain.

**Recommended first step:** implement the Agentic Mode toggle (level 1) — highest-value change with the smallest footprint, and the existing `ToolAgent` makes it straightforward.

---

### Graph-RAG — Possible Next Evolution

Standard RAG retrieves isolated chunks. Graph-RAG builds a knowledge graph over the corpus and retrieves by traversing entity relationships — better for multi-hop questions and documents with dense cross-references.

**Candidates to evaluate:**
- [LightRAG](https://github.com/HKUDS/LightRAG) — lightweight graph-RAG framework; builds a KG from documents, hybrid graph + vector retrieval
- [RAG-Anything](https://github.com/HKUDS/RAG-Anything) — multimodal extension of LightRAG; handles text, tables, images, and figures natively

**Questions to answer before committing:**
- Does graph-RAG meaningfully improve answer quality on our target document types (technical specs, policy docs)?
- What is the build cost — time and memory — for a 20k-document corpus?
- Can it coexist with the current ChromaDB / BM25 hybrid, or does it replace the retrieval layer entirely?
- How does it interact with our per-KB isolation model?

---

## Maintenance / Chores

### Dependency Updates

#### Frontend

No urgent updates. The following are worth revisiting when there is a concrete reason:

- **Tailwind CSS v3 → v4** — v4 replaces `tailwind.config.js` with a CSS-first config. Real migration effort, not a version bump. Only worth doing if a v4-specific feature is needed.
- **React 18 → 19** — React 19 is stable. Low urgency.
- **`@types/node: ^20` → `^22`** — trivial bump.

Minor/patch updates within existing major versions (`^` ranges in `package.json`) are picked up automatically by `npm update` and can be run periodically without concern.

#### Python

The pinned versions in `requirements.txt` are the higher-priority concern — they don't auto-update. Packages to watch:

- **`docling`** — updates frequently, has had breaking changes between minor versions
- **`chromadb`** — actively developed, API surface has shifted across releases
- **`ollama`** — Python client tracks new Ollama features; worth updating alongside Ollama server upgrades

Run `pip list --outdated` periodically and update these selectively. Test retrieval and ingestion after any chromadb or docling bump.

### Refactor: Reduce Size of main.py

Done: Extract ingestion pipeline from `main.py` into `ingestion.py`. Done: KBPool and DispatchingAgent extracted to `kb_pool.py`.

`main.py` is still large. More potential extractions need to be identified — the `build_server()` function in particular has grown.

**Not in scope:** wrapping `build_server` shared state in a class — larger refactor, more risk, deferred.

### Performance: Source Display Lag After Streaming

After the LLM finishes streaming, there is a noticeable delay before sources appear in the UI. Two contributing factors:

1. **Large final chunk**: sources (full chunk text) are sent only in the last stream chunk, after the LLM finishes. For many or large sources this chunk is heavy.
2. **Redundant GET call**: `onEnd` in `useMessaging.tsx` fires `conversationService.get(activeConversationId)` immediately after the stream closes. This re-fetches every message in the conversation and does a sequential `await source_db.get_sources_by_message_id()` per message. The sources already arrive in the final stream chunk, so this GET call is redundant for display purposes.

**Possible directions:**
- Drop `setThread` from the `onEnd` GET result; use it only for sidebar/title refresh
- Move source fetching out of `get_conversation_by_id` into a separate on-demand endpoint
- Send sources as a lightweight reference (id + filename only) in the stream, fetch full content lazily on click

