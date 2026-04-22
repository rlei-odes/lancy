# BACKLOG

Planned improvements and feature work for the Lancy fork.
Items are grouped by theme and roughly prioritised within each section.

## Known Bugs

### Answer Disappears After Rendering

Root cause identified and fixed: `mistral-nemo:12b` emits literal newlines inside JSON string values, which is invalid JSON. `partial_json_loads` failed silently, returning `{}`, so the extracted answer was empty and the streamed content disappeared when the DB record replaced the live display.

Fixes applied (v0.2.29):
- `_escape_literal_newlines()` pre-processes the JSON before parsing to handle bare newlines in string values
- `_answer_post_processing` falls back to the raw accumulated text if the `"answer"` key cannot be extracted, preventing silent data loss
- `parse_llm_json_stream` now catches all exceptions from `partial_json_loads`, not just `ValueError`

Monitor for recurrence. A better-instruction-following model (e.g. larger Ollama models) will reduce the frequency of format violations.

### ~~Frontend Process Not Fully Stopped by `stop.sh`~~ ✓ fixed

After running `./stop.sh`, the backend stops cleanly but parts of the frontend remain reachable at the original URL. On the next `./start.sh`, the Next.js dev server detects port 3000 as still occupied and opens on a new port (e.g. 3001), causing a mismatch with any bookmarks or configured `SERVER_URL` references.

`stop.sh` kills the PID from the PID file and then runs `fuser -k 3000/tcp`, but Next.js spawns child worker processes that are not covered by either — they linger and hold the port. Fix: extend `stop.sh` to also kill child processes of the frontend PID (e.g. `pkill -P <frontend_pid>`) before the `fuser` call, or replace the PID-file approach with a process group kill (`kill -- -<pgid>`).

### Low Source Citation Count

Observed that answers sometimes cite only 2 chunks even when retrieval is configured with a higher `k`. Possible causes: the reranker is collapsing similar chunks into fewer sources, the `used_sources_id` field in the LLM JSON response is under-populated (model not citing all chunks it used), or the source deduplication step in `_answer_post_processing` is too aggressive. Needs investigation with logging enabled on retrieved chunk count vs. cited chunk count. It might be good and even intended behaviour - when no fit / similarity is observed above the threshold, no bad fitting chunks should be served.

---

## Authentication & Access Control

### Admin / User Role Separation

Currently all authenticated users share a single password and have full access to the technical interface, including RAG configuration, reindexing, model selection, system prompt editing, and preset management.

**Goal:** introduce a two-tier access model so that end users only see the chat interface, while admins retain full configuration access.

**Scope:**
- Add an admin flag to the session (e.g. a separate admin password or a role field in the auth cookie)
- Hide the RAG Config panel from non-admin sessions
- Hide or lock: reindex buttons, model selectors, system prompt editor, embedding configuration, vector store settings, preset create/delete
- **Stop indexing button** (red, in the sidebar progress bar) — admin-only; end users should see the progress indicator but not be able to cancel a running indexing job
- End users retain: chat, conversation history, language toggle, theme toggle
- No per-user accounts required at this stage — two passwords (user / admin) is sufficient for the prototype
- **Fallback admin account** — a local admin credential (env var or config file) that works independently of SSO/AD, so the system is never locked out if the directory is unreachable; required before SSO integration is attempted

**Why:** prototype rollout to a small trusted team is fine without this, but broader internal use requires that non-technical users cannot accidentally break the configuration or trigger a full reindex.

### Active Directory / SSO Integration

**Goal:** optional AD/SSO login as an alternative to the shared-password model, for organisations that manage users centrally.

**Scope:**
- LDAP / Active Directory bind for authentication
- Map AD groups to roles (e.g. domain users → user role, IT group → admin role)
- Feature should be opt-in and deactivatable — shared-password mode remains the default for simpler deployments
- Builds naturally on top of the admin/user role separation above

**Why:** relevant for enterprise rollout where user management via AD is already in place and individual password distribution is impractical.

---

## Ingestion

### Reindex Success Toast — UX Improvements

- ~~**Split skip counts**~~ ✓ done — `files_skipped_store` and `files_skipped_batch` now separate fields in `ReindexResult`; toast shows e.g. "38 already up to date, 1 duplicate in batch"
- ~~**Timestamp**~~ ✓ done — completion time appended to toast ("· 14:32")
- **Auto-dismiss vs. persistent:** left as-is for now — current behaviour is acceptable.

### ~~Image Retrieval — Multimodal Pipeline~~ ✓ done

~~Investigation complete (see `UPSTREAM_SYNC_PLAN.md`). Decision taken.~~

Implemented. See `IMAGE_RETRIEVAL_DESIGN.md` for the full design and implementation record.

**What was built:** dual-collection pipeline — text in `vs_text`, images in `vs_image` (Qwen3VL-Embedding-2B). Two independent KB-level toggles: `image_indexing_enabled` and `image_retrieval_enabled`. Session-level `image_retriever_top_k` (1–4). Retrieved images injected as base64 into LLM context. Parallel retrieval via `asyncio.gather`. ChromaDB only (pgvector follow-up below).

**Pending manual tests** (blocked by hardware — requires GPU and multimodal LLM):
- PDF with embedded images, both toggles on, multimodal LLM configured
- Indexing on, retrieval off — images indexed, not retrieved

### Image Retrieval — pgvector Support for `vs_image`

**Goal:** extend the dual-collection pipeline to support pgvector as the image vector store, mirroring the existing text store backend selection.

**Why:** ChromaDB is local-only and single-process; production deployments targeting pgvector currently have no image store option. The image pipeline was implemented ChromaDB-first to unblock feature work; this closes the gap.

**Scope:**
- `make_vector_store` call in `main.py` for the image store passes `table_name=f"rag_{kb_id}_images"` — pgvector already uses this for namespacing, so no schema change is needed
- `_run_ingestion` in `main.py`: the image store instantiation mirrors the text store path; ensure the pgvector branch is exercised and the async/sync split is correct (same pattern as the existing text pgvector path)
- `delete_kb` in `kb_router.py`: for pgvector, deletion of `vs_image` should drop the images table — verify the cleanup path covers both stores
- Manual test: pgvector KB with image toggles on — index a PDF, query, confirm images surface

### Image Retrieval — Source Citations with Image Preview

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

### Image Retrieval — Optional Caption Pipeline (low priority)

`IMAGE_MODE=caption` alternative: at ingest, a lightweight VL model (e.g. `minicpm-v` via Ollama) generates a text description of each image; caption stored as a text chunk in `vs_text`. Works with any text-only LLM, no GPU required for retrieval. Useful when the corpus has important image content but large hardware is not available.

### Image Retrieval — Automatic Multimodal LLM Validation (optional)

When `image_retrieval_enabled` is on, the system silently injects images into the LLM context but does not validate that the configured answering LLM is actually multimodal. A non-multimodal LLM will either ignore the image data or produce an error. A startup/save-time check against the Ollama model manifest (or a known allowlist) could surface a clear warning in the UI before the user runs a query.



### File Upload API + Incremental Indexing

**Goal:** allow external tools (n8n, Make, custom scripts, DMS webhooks) to push a document directly into a KB via API, without requiring filesystem access or a full reindex.

**Scope:**
- `POST /api/v1/kb/{id}/documents` — accepts a file upload plus optional metadata (document_id, version, source system); writes it to the KB's data directory and queues it for indexing
- Requires incremental indexing to be solved first: currently `build_vector_store()` either resets fully or skips entirely — per-document content-hash deduplication must be in place for single-file ingest to be meaningful
- File upload endpoint should return a job ID that can be polled via the existing reindex-status endpoint
- Natural webhook target for DMS release/approval events — see Metadata & Versioning below

**Why:** closes the loop for automation use cases — the `/v1/chat/completions` endpoint already allows querying the RAG from external tools; this adds the ability to feed documents in from the same tools. Combined with metadata and versioning support, enables a fully automated DMS → RAG pipeline.

### External Metadata Ingestion & Document Versioning

**Context:** enterprise DMS solutions (SharePoint, Alfresco, OpenText, etc.) attach structured metadata to files — author, department, document type, validity date, status, project ID. This metadata is useful for retrieval attribution and filtering, but is not captured by the current file-based ingestion pipeline. A related problem is document replacement: when a new version or approved revision of a document is available, the old chunks in the vector store should be removed.

**Scope boundary — what Lancy does and does not own:**

DMS metadata models are complex and vary significantly between systems. A single DMS may distinguish between versions (any change) and revisions (approved changes), use different field names for status, and apply custom approval workflows. Building a UI for field mapping, version/revision logic interpretation, and status filtering inside Lancy would turn it into a DMS integration platform — that is out of scope.

**The responsibility split:**
- **Integration layer** (customer script, n8n, webhook): reads DMS metadata, applies any required filtering (e.g. only status=Released), maps DMS field names to Lancy's fixed schema, and delivers the file + metadata to Lancy
- **Lancy**: accepts the file and the pre-mapped metadata, stamps it onto chunks, and handles replacement using the `document_id` it is given

Lancy defines the schema; the integration layer handles the transformation. This keeps the ingestion code simple and avoids building a configurable ETL engine inside the RAG system.

**Lancy metadata schema:**

Sidecar file: `document.pdf.meta.json` alongside the file, or a JSON body field in the File Upload API request.

```json
{
  "document_id": "stable-uid-from-dms",
  "title": "Human-readable document title",
  "author": "Name or department",
  "document_class": "Technical",
  "document_type": "Specification",
  "document_created_at": "2024-01-15",
  "document_released_at": "2025-03-01",
  "source_url": "https://dms.example.com/documents/12345",
  "tags": ["project-x", "team-z"]
}
```

Only `document_id` is required for versioning. All other fields are optional and stored as chunk metadata. `document_released_at` serves as the version timestamp for stale-version detection. `document_class` is a category above `document_type` (e.g. Technical / Commercial / Legal / Internal) and is intended as a future retrieval filter — letting users scope answers to a specific document class.

`source_url` is an optional deep link back to the document in the originating DMS or file system. When present, source citations in the answer should link directly to this URL rather than opening the current local content popup. When absent, fall back to the existing behaviour (filename link → content popup). Implementation: the `source://` URL handler in the frontend Markdown renderer checks the chunk's stored metadata for `source_url` and opens it in a new tab if available; the backend already stores all metadata fields on each chunk so no ingestion-side changes are needed beyond reading the field.

**Document replacement (versioning):**

- `VectorStore.delete_chunks_by_document_id(document_id)` — new abstract method, implemented for ChromaDB (metadata filter delete) and pgvector (DELETE WHERE clause)
- Ingestion pipeline: when a file carries a `document_id` that already exists in the store, delete the old chunks before adding the new ones
- `ReindexResult` extended with `files_updated` count (replacements) alongside existing skip counts
- Reindex toast updated: N added / N updated / N skipped

**Stale version protection:**

The integration layer (webhook/script) is stateless — it cannot know whether a newer version of a document is already in the store. Two complementary mechanisms:

- `GET /api/v1/kb/{id}/documents/{document_id}` — returns the current metadata stored for that document_id (title, `document_released_at`, chunk count), or 404 if not present. The caller can compare `document_released_at` values and abort if the stored version is already newer.
- Optional version guard on the upload endpoint: accept an `if_released_after` parameter; if the stored `document_released_at` is equal to or newer, Lancy returns 409 Conflict with the stored metadata. This keeps the guard logic server-side and avoids requiring a pre-check round-trip.

**Document invalidation (explicit deletion):**

When a document is withdrawn or put out of validation in the DMS, the integration layer calls:

`DELETE /api/v1/kb/{id}/documents/{document_id}` — removes all chunks for that document_id from the vector store. Returns 404 if not found, 200 with a count of chunks removed otherwise.

This is the correct primitive for the "document no longer valid" use case. Lancy does not automatically expire documents based on timestamps — the DMS workflow owns that decision and calls the delete endpoint explicitly.

**Dual-store deletion (multimodal):**

When `image_indexing_enabled` is on for a KB, a document's chunks exist in both `vs_text` and `vs_image`. The `DELETE` endpoint must call `delete_chunks_by_document_id` on both stores. The same applies to document replacement during versioning: old chunks must be purged from both stores before new ones are inserted. Image chunks carry the same `document_id` and `file_hash` metadata as text chunks from the same source file, so the deletion predicate is identical — the endpoint just needs to know that two stores exist for the KB.

**What is explicitly out of scope for Lancy:**
- UI for selecting or mapping metadata fields from an uploaded sample JSON
- Interpretation of version vs. revision semantics (the integration layer decides what counts as a replacement)
- Status-based filtering at ingestion time (filter at the DMS export / webhook level)
- Automatic time-based expiry of documents based on metadata timestamps
- Direct DMS API or database connectivity (DMS-specific, belongs in the integration layer)

### Document Actuality — Time-Based Metadata and Retrieval Weighting

**Goal:** make document age a first-class signal in retrieval so that answers can prefer recent documents, flag outdated sources, or apply a hard cutoff by date.

**Why:** in knowledge bases that evolve over time (policy documents, technical specs, product data), an older document may be technically relevant but factually superseded. The current retrieval pipeline has no concept of document age — a 2018 spec ranks the same as the 2024 revision if their embeddings are similar.

**Ingest side:**

- `document_released_at` is already part of the planned metadata schema (see External Metadata Ingestion above) — this feature builds directly on it
- Additionally, stamp every chunk with `ingested_at` (ISO date, set automatically at indexing time) — no sidecar file needed; the ingestion pipeline writes it unconditionally
- `ingested_at` serves as a fallback when no explicit `document_released_at` is provided

**Retrieval side:**

Three configurable strategies, selectable per session or preset:

1. **Prefer recent** — apply a time-decay score penalty to older chunks before or after reranking; recent documents rank higher all else being equal. Penalty function: linear or exponential decay from the reference date (today, or a configurable anchor).
2. **Hard cutoff** — exclude chunks older than a given date from the candidate pool entirely. Useful when historical documents are indexed for reference but should never appear in answers about current state.
3. **Soft penalty** — scale down the final score of old chunks by a configurable factor without fully excluding them. A 2018 document can still surface if nothing more recent is relevant.

**UI:**

- New session parameter in the RAG Config sidebar: "Document actuality" — off / prefer recent / cutoff (with a date picker) / soft penalty (with a slider for decay strength)
- Source citations in the chat show the document date when available — lets users judge recency themselves even when no penalty is applied

**Scope note:** this feature is complementary to the versioning/replacement system above — that handles explicit document supersession; this handles implicit age-based preference for live corpora where old documents are never explicitly removed.

---

### Chunking Strategy Investigation — Lessons from Large-Scale Ingestion

**Trigger:** Practitioner account of ingesting 20k documents / 600k pages. Key insight: not all pages need to be chunked — the pipeline must first analyze each page and chunk only relevant content. Images and tables require separate handling, not the same path as prose text.

**Questions to investigate:**
- Is our current fixed-token chunking strategy appropriate for heterogeneous documents (prose, tables, images)?
- Should we add a pre-chunking relevance/content-type analysis step that routes pages differently?
- How do we handle tables and images as distinct chunk types vs. embedding them as text descriptions?
- At what document volume does page-level chunking outperform paragraph/sentence-level chunking for retrieval quality?

**Likely outcome:** a chunking strategy ADR that maps content type → chunking method, with a smarter pre-filter before indexing.


---

## UI & Settings

### Retrieval Explorer — top_k / candidate pool validation

**Issue:** the RAG Config sidebar allows setting `top_k` higher than `reranking_candidate_pool` (e.g. top_k=4, pool=3). This is logically invalid — you cannot return 4 reranked results from a pool of 3 candidates. Currently this causes no visible error but produces confusing results.

**Options to evaluate:**
- Frontend: clamp `reranking_candidate_pool` minimum to `top_k` in the sidebar (or vice versa — warn when pool < top_k)
- Backend: enforce at runtime in `retrieve_callback` / the RAG query path and return a clear validation error
- Both: frontend prevents the bad state; backend guards anyway

**Why it matters:** affects both normal RAG queries and the Retrieval Explorer probe. A user changing top_k without adjusting the pool would silently get degraded results.

---

### Preset Scope — Query Presets vs. Ingestion Presets

**Current behaviour:** a preset saves the full combined state — both session/query parameters (`RagConfig`: top-k, BM25, reranking, LLM model, temperature, etc.) and KB-level embedding/ingestion parameters (embedding backend, model, batch size, chunk tokens, OCR, image toggles). Presets are already per-KB: stored in `rag_presets_{kb_id}.json`, so a preset saved for KB "A" does not appear for KB "B".

**The problem:** bundling query and ingestion settings into one preset is convenient but dangerous. Loading a preset that changes the embedding model silently makes the index stale — the user must re-index before results are meaningful, and there is no warning. This is an admin-level action masquerading as a casual user action.

**Open questions to resolve:**

1. **Should presets influence ingestion/embedding settings at all?** One argument: yes, because the embedding model is part of the "configuration profile" for a KB and admins want to save/restore complete profiles. Counter-argument: embedding settings should only be changed deliberately and never as a side-effect of loading a query preset.

2. **Should presets be split into two types?**
   - *Query presets* (user-facing): top-k, BM25 on/off, reranking, temperature, follow-up count, image retriever top-k. Safe to swap at any time — no re-index required. Could be exposed to end users, not just admins.
   - *Ingestion presets* (admin-only): embedding backend, model, batch size, chunk tokens, OCR, image toggles. Loading one should show a warning: "This will require a full re-index to take effect."

3. **Are all parameters truly per-KB?** Session parameters (`RagConfig`) are currently global — one active config shared across all KBs. KB-level parameters are per-KB. If a user switches KBs, the session config (LLM model, top-k, etc.) does not change. This may be surprising: a preset saved for KB "A" that includes LLM model selection will apply that model when loaded, even though the LLM is not KB-specific. Worth making explicit in the UI which parameters belong to the KB and which are global session settings.

**Suggested direction:** split presets into query and ingestion categories; tie ingestion preset changes to a re-index warning; expose query presets to end users once role separation is in place. Decide in conjunction with the Admin / User Role Separation item above.

---

## Admin Tooling

### Retrieval Debugger — Chunk Inspector & Retrieval Probe

**Goal:** an admin-only UI panel that exposes the internals of the retrieval pipeline, allowing configuration tuning and quality assessment without going through the LLM.

**Why:** currently the only way to assess retrieval quality is to run a full RAG query and judge the answer. That conflates LLM quality with retrieval quality. A dedicated retrieval view lets you tune k, compare ranking methods, and inspect what was actually indexed — independently of the LLM.

**Two modes:**

*Chunk browser*
- Query the vector store directly by filename, source metadata, or keyword
- Display raw chunks: content, source file, chunk index, file hash, any other stored metadata
- Useful for verifying what was indexed from a given document and spotting bad chunking

*Retrieval probe*
- Enter a natural language question; run the full retrieval pipeline but stop before the LLM
- Display the top-k results as a ranked list with scores broken out by method:
  - BM25 score
  - Semantic (vector) score
  - RRF combined rank
- Allow adjusting k in the UI and seeing immediately how the result set changes
- Useful for: setting k, diagnosing why a relevant chunk isn't surfacing, comparing the effect of toggling BM25 on/off

**Backend:**

Add `POST /api/v1/rag/retrieve` — takes a query string and retrieval parameters (k, bm25 on/off, reranking on/off), runs the retrieval pipeline, and returns the chunks with per-method scores. No LLM call. The retrieval step is already decoupled from the LLM in the existing code, so this is mostly exposure work.

**Frontend:**

The admin view is a dedicated full-width page layout, not a panel crammed into the existing sidebar. Layout: large main content area on the left for the chunk browser / retrieval probe, with the existing RAG config sidebar sitting beside it on the right — same sidebar, different screen context with more room to breathe.

Two sub-views in the main area, selectable by toggle or tabs: chunk browser and retrieval probe.

*Retrieval probe results list:*

Each result is a card with a large rank number on the left (bold, prominent), then the chunk content and scores. The list shows **k + y** results total, where k is the current configured cutoff and y is a configurable lookahead (e.g. +5). The first k cards render normally; the remaining y cards are visually dimmed — lower opacity, maybe a subtle "outside k" label — so you can see exactly what the RAG would discard. This makes the effect of changing k immediately legible: raise k by 2 and two grayed cards become active.

**Scope note:** the UI cards are non-trivial but not complex — a ranked list with expandable text. Generic vector store UIs (ChromaDB-UI etc.) don't know about the BM25/RRF pipeline, so building this in-app is the only way to get the full picture.



### Knowledge Base Analytics Page

**Goal:** a dedicated stats/analytics view in the admin UI that gives a quick health overview of the indexed knowledge base — corpus composition, ingestion history, and retrieval activity.

**Why:** once a KB grows beyond a few dozen documents, it becomes hard to reason about what is in it, how it is chunked, and how actively it is being used. Dashboards answer these questions without needing to query the vector store manually.

**Proposed diagrams and panels:**

- **Chunk size distribution** — histogram bucketing chunks by character count (e.g. 0–200, 200–500, 500–1000, 1000+). Reveals over-chunking, under-chunking, and runaway pages (OCR noise, headers, footers).
- **Chunks per document** — scatter or bar chart: documents on x-axis ordered by chunk count, chunk count on y-axis. Identifies outliers — a single document producing 10× the average chunk count may indicate a problem.
- **Ingestion timeline** — bar chart of chunks (or documents) indexed per day or month. Shows when the KB was last populated and whether ingestion is a one-time event or ongoing.
- **Retrieval hit frequency** (optional) — which chunks appear most often in retrieval results; which documents are never retrieved. Useful for corpus hygiene: documents that are never retrieved may be poorly chunked, off-topic, or redundant.

**Placement:** a third tab in the Retrieval Explorer admin view ("Analytics"), alongside the existing chunk browser and retrieval probe tabs. Shares the same full-width layout.

**Implementation approach:** replace the removed `matplotlib` dependency with a backend `/api/v1/rag/stats` endpoint that returns raw JSON (chunk size buckets, per-document counts, ingestion timeline). The frontend renders this with **Recharts** (already a common choice in Next.js/Tailwind stacks) — gives hover effects, responsive layout, and a design that fits the existing UI. Static matplotlib images would not.

**Performance note:** chunk size and count statistics require a full metadata scan of the vector store. For large KBs (tens of thousands of chunks) this should be pre-computed and cached — not run on every page load. A background job triggered at the end of each indexing run is the natural hook.

---

### User Feedback — Thumbs Up / Down

**Goal:** let users rate individual answers with a thumbs up or down. Capture the active RAG configuration at the time of rating so quality can be correlated with retrieval settings.

**Why:** RAG quality is hard to judge in aggregate without structured feedback. Recording which configuration settings (k, BM25, reranking, model, temperature) were active when a good or bad answer was given lets admins identify which presets actually work on the real corpus.

**Scope:**
- Thumbs up / down button visible per answer in the chat (logged-in users only)
- Rating stored in the conversation database alongside the conversation and message ID, with a full snapshot of the `RagConfig` active at that moment
- Admin view: a simple feedback log showing recent ratings, the question, the answer excerpt, and the config snapshot — sortable by rating and date
- Optional aggregation: rating counts per model, per KB, per preset — useful for spotting that a specific LLM or k value consistently gets low ratings

**Privacy note:** feedback entries are stored server-side in the existing DB; no external service involved. On public or multi-user deployments, the feedback log should be admin-only.

---

### Customisable Retrieval Prompts

The query expansion, HyDE, and reranking prompts are currently hardcoded in Python. Unlike the system prompt (answer tone/format), these affect retrieval quality and could benefit from domain-specific tuning.

**Candidates:**
- **Query expansion** (`utils/retriever.py`) — could guide rephrasing toward domain vocabulary (e.g. industry terminology, local acronyms)
- **HyDE** (`utils/retriever.py`) — hypothetical document generation; domain context improves embedding match quality
- **LLM reranking** (`retriever/reranking_retriever.py`) — could define what "relevant" means for the specific corpus (e.g. prioritise verified sources over marketing material)

**Scope:** same file-based pattern as the system prompt — `prompts/query_expansion.default.md`, `prompts/hyde.default.md`, `prompts/reranking.default.md`, each with a gitignored `.custom.md` override. Admin-only UI exposure makes sense given the technical nature.

**Why:** hardcoded prompts cannot be tuned without touching source code; domain-specific guidance measurably improves retrieval recall and precision.

**System prompt UX — default vs. custom toggle:**
The system prompt field should have an explicit Default / Custom toggle. When the user switches to Custom for the first time, pre-fill the editor with the server default so they have a starting point rather than a blank slate. After that, the custom text is kept separate — stored in the session and persisted on save — and the default is never overwritten. Switching back to Default should restore the server default without destroying the custom draft (keep it in state so toggling back doesn't lose their work).

**Domain context prompt (corpus glossary):**
Investigate adding a second, lightweight prompt field — a "corpus context" block — where the user can provide domain-specific instructions: special terminology, common abbreviations, ID patterns found in the documents, or notes on document provenance. This is distinct from the system prompt (which governs answer format and tone) and from retrieval prompts (which govern query rewriting). It would be appended to the context sent to the LLM only when non-empty, so it has zero effect on default deployments. Useful for specialised corpora where the LLM would otherwise misinterpret jargon.

**Prompt editor UI — consider a dedicated admin section:**
The current sidebar panel is too narrow for comfortable prompt editing. Options to consider:
- A dedicated **Prompt Settings** page in the admin section (separate route), giving full-width layout.
- A **markdown editor with syntax highlighting and preview** (e.g. `@uiw/react-md-editor` or CodeMirror with a markdown mode) — prompts are markdown, so rendering the preview inline helps the user see what the LLM will receive.
- The retrieval prompts (expansion, HyDE, reranking) are more technical than the system prompt; grouping them under a collapsible "Advanced" section within the same page keeps the UI approachable for non-technical admins.


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

**Known issue:** SentenceTransformer (and potentially other services in the stack) makes outbound requests on every model load to check for updates, even when all assets are fully cached locally. With no internet this causes multi-second retry delays and noisy error logs before falling back to the cache. It also constitutes unnecessary telemetry to third-party services.

**Fix:** set an `Lancy_OFFLINE=1` environment variable in `start.sh` that maps to the relevant library-specific offline flags (e.g. `HF_HUB_OFFLINE=1` for the HuggingFace ecosystem). As other services are identified, their suppression flags are added under the same umbrella variable.

```bash
# in start.sh, before the backend launch:
Lancy_OFFLINE="${Lancy_OFFLINE:-1}" \
HF_HUB_OFFLINE="${Lancy_OFFLINE:-1}" \
PYTHONPATH="$REPO/backend/src" \
BACKEND=ollama \
  "$VENV/bin/python" -m lancy.main ...
```

Also document in `local_setup.md` that after first run the system is designed to operate fully offline, what `Lancy_OFFLINE=1` suppresses, and how to temporarily disable it if a model update is actually wanted (`Lancy_OFFLINE=0 ./start.sh`).

### Network Egress Audit — What Phones Home?

**Goal:** produce a complete, verified list of all external network calls made by the system under normal operation, so that the deployment can be assessed for air-gap readiness and data privacy.

**Known calls (from observation):**
- `huggingface.co` — SentenceTransformer model update check on every `build_embedding_model()` call (fixable with `HF_HUB_OFFLINE=1`, see above)
- `ollama.com` — Ollama may check for binary or model updates; needs verification

**Unknown / to verify:**
- ChromaDB — any telemetry or update checks?
- Docling — any calls during document parsing?
- Any other Python dependencies that phone home on import or first use?

**Method:** run the backend with network access but with a local DNS proxy or `tcpdump` to capture all outbound DNS queries and HTTPS connections during startup, ingestion, and a query. Catalogue every external host contacted, the reason, and whether it can be suppressed.

**Output:** a documented list in `local_setup.md` (or a dedicated `SECURITY.md`) of all external hosts, what triggers the call, and how to suppress it for air-gapped or privacy-sensitive deployments.

### API Endpoint Protection — Rate Limiting and Request Queueing

The backend currently has no rate limiting or concurrency guards beyond the single-job check on `/api/v1/rag/reindex`. With multiple simultaneous users this matters: the `/api/v1/messages/stream` endpoint triggers a full retrieval + LLM call per request, and `/api/v1/rag/retrieve` runs embedding inference — both are expensive and unbounded. The login/passcode endpoints have no brute-force protection either.

**Recommended approach:**

- **Rate limiting:** add `slowapi` (the FastAPI equivalent of Flask-Limiter) with per-IP limits on the hot endpoints — e.g. 10 requests/minute on `/messages/stream`, 30/minute on `/rag/retrieve`, and 5/minute on the auth endpoint. Limits are set as decorators on the route functions and require no architectural change.
- **Reindex queueing:** the existing in-progress guard (409 if already indexing) is sufficient for now; a proper job queue is only needed if concurrent reindex requests from different KBs become a requirement.
- **LLM concurrency:** if Ollama is running on limited hardware (single GPU), concurrent LLM calls queue internally in Ollama — but FastAPI will still accept all requests and hold open connections. A simple asyncio semaphore on the answer path (e.g. max 3 concurrent LLM calls) prevents connection pile-up under load.
- **Auth hardening:** the passcode endpoint should reject requests after N failed attempts within a time window — a simple in-memory counter per IP is enough for a non-public deployment.

**Why now:** input sanitization (max lengths, enum guards) is in place, which closes the "bad data" surface. Rate limiting closes the "too much data" surface. Together they cover the realistic abuse scenarios for a small multi-user internal deployment.

---

## Production Installation and Architecture

### Component Map — What Runs Where

Understanding what each process does helps decide how to split them across hardware:

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

Everything on one machine. Ollama local, ChromaDB local, no GPU required (text-only). Simple but not scalable.

```
[User browser] → localhost:3000 (Next.js) → localhost:8080 (FastAPI) → localhost:11434 (Ollama)
```

**Profile 2 — GPU server + thin access machine**

Backend and Ollama on a GPU server (on-prem or cloud). Frontend served from the same server or a lightweight separate host. Best fit for the planned deployment: GPU server does the heavy lifting; users access via browser.

```
[User browser] → frontend-host:3000 (Next.js)
                      ↓SERVER_URL=http://gpu-server:8080
                 gpu-server:8080 (FastAPI + nomic + Qwen3VL)
                      ↓
                 gpu-server:11434 (Ollama)
                 gpu-server:5432  (pgvector, optional)
```

Ollama can alternatively run on a *different* GPU server than the backend — configure via `ollama_host` in the RAG config panel. Useful if the company already has a dedicated Ollama instance.

**Profile 3 — Full production split**

Frontend on a web server / reverse proxy (nginx, Caddy), backend on a GPU machine, pgvector on a managed PostgreSQL instance. Ollama on the same GPU machine as the backend or on a dedicated inference server.

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

- **GPU passthrough:** Ollama and Qwen3VL both need CUDA. Requires `nvidia-container-toolkit` on the Docker host and `deploy.resources.reservations.devices` in Compose. Must be tested on the target hardware before claiming it works.
- **Model persistence:** Ollama model files (~7 GB for mistral-nemo) and Qwen3VL weights (~5 GB) must be volume-mounted or pre-baked into the image. Pre-baking makes images large but removes the first-run download. Volume mounts are more flexible but require setup on the host.
- **Data and config persistence:** `db/` (ChromaDB collections, `rag_config.json`, `knowledge_bases.json`) and `data/` (document corpus) must be volume-mounted — never baked into the image.
- **HuggingFace cache:** SentenceTransformer and Qwen3VL download model weights to `~/.cache/huggingface`. This cache should be volume-mounted so it survives container restarts. For air-gapped deployments, pre-populate the cache and set `HF_HUB_OFFLINE=1`.
- **`SERVER_URL`:** in a Compose setup, the frontend container reaches the backend via the Compose service name (e.g. `http://backend:8080`), not localhost. The `frontend/.env` (or an env override in Compose) must set `SERVER_URL=http://backend:8080`.
- **The dev server problem:** the current frontend runs `next dev`, which is not suitable for production containers. A production Compose setup should build with `next build` and serve with `next start`. This also removes the port-leaking issue from `stop.sh` entirely.

---

### Using pgvector as Database

Use HNSW indexes on vector columns. Without an index, pgvector does a sequential scan (every row compared to the query), causing CPU spikes. With HNSW, queries are fast and lightweight regardless of collection size.

```sql
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
```

The index should be created after initial bulk ingestion, not before — building it on an empty or growing table wastes time. Add this to the KB setup documentation when pgvector becomes the primary target.

---

### nginx Reverse Proxy Configuration

No example nginx config exists in the repo yet. The systemd services get both processes running, but for any real deployment nginx sits in front and handles several things that Next.js and FastAPI should not do themselves:

- **TLS termination** — serve over HTTPS with a cert from certbot or a manual cert; without this, passwords and session cookies travel in plaintext
- **Single public entry point** — expose only port 443 externally; nginx proxies to Next.js on `:3000`, and Next.js proxies backend calls server-side to FastAPI on `:8080` — the backend never needs to be publicly reachable
- **HTTP → HTTPS redirect** — any request on port 80 gets redirected permanently
- **Security headers** — `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options` added in one place rather than in application code

**What to build:** a minimal `nginx.conf` example committed to `docs/admin-guides/`. Should cover a single-host deployment (one domain, certbot cert, proxy to `:3000`). The frontend's existing proxy rewrite handles the backend — nginx only needs to know about port 3000.


---

### Investigate API Completeness and Check if More Consequent API Design is Necessary

Finding on a question about API status:
No, /api/rag/query (or /api/v1/rag/query) does not exist in the current codebase.

I found a reference to it in the ARCHITECTURE.md file, but based on the actual implementation in main.py and the router files (rag_router.py, kb_router.py), that seems to be a documentation artifact or a typo for /api/v1/messages.

The current query-related endpoints are:

/api/v1/messages: The main endpoint used by the frontend for chat queries.
/api/v1/rag/query-status: Used by the frontend to poll for the current phase of a running query (e.g., "retrieving" vs "generating").

---

## Research & Future Directions

### Agentic RAG — RAG as a Tool

The current architecture uses a fixed pipeline: every query always triggers retrieval first (`CustomRAG`). The notebook prototypes (feature4b/d) explored a `ToolAgent` using the ReAct pattern, where retrieval is an *optional* tool call — the model decides whether to search the vector store or answer from its own knowledge. This is the natural next evolution of the system.

**What exists in the codebase:**
- `ToolAgent` is already implemented in `conversational-toolkit/src/conversational_toolkit/agents/`
- The `Tool` base class lives in `conversational_toolkit.tools.base` — any new tool subclasses it and implements `async call(args) -> dict`
- The infrastructure is there; it is just not wired into `main.py`

**How `RetrieveRelevantChunks` was prototyped (from the now-deleted `feature4_tool_agents.py`):**
```python
class RetrieveRelevantChunks(Tool):
    def __init__(self, name, description, parameters, retriever):
        ...
    async def call(self, args):
        chunks = await self.retriever.retrieve(args["query"])
        return {"result": chunks_to_text(chunks)}  # formatted as ## Chunk {title}: ``` {content} ```
```
Wiring: instantiate with the active KB's retriever, register with `ToolAgent` at startup in `main.py`.

**Three levels of agentic capability identified in the notebooks:**

1. **Agentic Mode toggle** — switch the backend from `CustomRAG` to `ToolAgent`, making retrieval optional. The model answers general questions without a vector store lookup, saving tokens and latency. Implement as a toggle in the RAG config panel.

2. **Extended tool registration** — register additional tools (e.g. calculators, external lookups) with the production agent by subclassing `Tool` and registering at startup.

3. **Multi-agent / subagent pattern** — wrap the RAG pipeline as a tool for a coordinator agent. Enables routing across multiple KBs (e.g. one subagent per domain). Lower priority; useful only once multi-KB usage becomes a real need.

**Recommended first step:** implement the Agentic Mode toggle (level 1) — it is the highest-value change with the smallest footprint, and the existing `ToolAgent` class makes it straightforward.

---

### RAG Quality Evaluation with Ragas

`ragas` is already present in `conversational-toolkit/pyproject.toml` and was used in the notebook prototypes to compute Faithfulness and Answer Relevancy metrics. It was deliberately left in the codebase rather than removed, but there is currently no production integration and it is uncertain whether one will ever be built.

**Why not in real-time:** Ragas requires additional LLM calls per answer, making it unsuitable for the request-response cycle.

**Potential future use:** a background batch job that periodically samples recent answers and scores them (Faithfulness, Context Recall, Answer Relevancy), writing results to a log or dashboard. Would provide an ongoing quality signal without impacting latency.

A ground-truth query set (`EVALUATION_QUERIES`) covering the PrimePack AG demo corpus is already defined in `backend/src/lancy/feature1_evaluation.py` — it can serve as the basis for any Ragas evaluation run.

**Status:** dependency retained, no implementation planned. Revisit if systematic quality monitoring becomes a priority.

---

### Graph-RAG — Possible Next Evolution

Standard RAG retrieves isolated chunks. Graph-RAG builds a knowledge graph over the corpus and retrieves by traversing entity relationships — better for multi-hop questions and documents with dense cross-references.

**Candidates to evaluate:**
- [LightRAG](https://github.com/HKUDS/LightRAG) — lightweight graph-RAG framework; builds a KG from documents, hybrid graph + vector retrieval
- [RAG-Anything](https://github.com/HKUDS/RAG-Anything) — multimodal extension of LightRAG; handles text, tables, images, and figures natively in the same graph

**Questions to answer before committing:**
- Does graph-RAG meaningfully improve answer quality on our target document types (technical specs, policy docs)?
- What is the build cost — time and memory — for a 20k-document corpus?
- Can it coexist with the current ChromaDB / BM25 hybrid, or does it replace the retrieval layer entirely?
- How does it interact with our per-KB isolation model?

**Suggested first step:** run LightRAG on the PrimePack demo dataset, compare retrieval quality on a set of multi-hop test questions against the current hybrid pipeline.


---

## Documentation

### Add API Landscape and Description in Architecture

Current API landscape summary:
POST /api/v1/messages: Full RAG Chat (Retrieval + LLM).
GET /api/v1/rag/config: Session parameters.
POST /api/v1/rag/reindex: Ingestion/Indexing.
GET /api/v1/kb: Knowledge Base management.
POST /api/v1/rag/retrieve (New): Standalone Retrieval for the Explorer.
This new endpoint will essentially be a "dry run" of the retrieval logic that the main chat uses, but with the added ability to return those detailed scores (BM25, Semantic, etc.) that are usually discarded before the LLM sees them.

### Interactive API Documentation with Example Payloads

FastAPI auto-generates an OpenAPI schema and exposes a Swagger UI at `/docs` and ReDoc at `/redoc` — both are already live, but they show only field types and defaults, not realistic example payloads. Enriching the Pydantic models with `json_schema_extra` examples would make the Swagger UI directly useful for manual testing and integration work: a developer could open `/docs`, pick an endpoint, click "Try it out", and fire a real request against the running backend with a pre-filled example body.

**Scope:**

- Add `model_config = ConfigDict(json_schema_extra={"examples": [...]})` to the key request models: `RagConfig`, `KBCreate`, `RetrieveRequest`, `MessageInput`, and `ChatCompletionRequest` — one realistic example payload per model is enough
- The auto-generated response schemas are already complete; adding a few `response_description` strings to the route decorators improves readability
- Consider locking `/docs` and `/redoc` behind the existing auth check for non-development deployments — currently anyone who knows the URL can browse and call the API without a passcode