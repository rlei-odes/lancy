# Technical Design: File-by-File Ingestion for Crash Recovery and Scalability

_Status: ready to implement_

---

## Problem Statement

`run_ingestion` (the folder-scan path) processes all files in two sequential sweeps:

1. **Phase 1 — Docling**: `load_chunks()` is called once with the full list of filtered files. All files are parsed and chunked before a single byte hits the vector store.
2. **Phase 2 — Embed + store**: all resulting chunks are embedded and written to the vector store in one pass.

A crash anywhere in phase 1 or 2 loses all work done so far in that run. On the next run, the pre-pass correctly skips files whose hash is already in the store — but nothing has been committed yet, so every file is retried from scratch.

For medium-sized KBs (tens of files) this is tolerable. For thousands of files it is not: Docling parsing of a large corpus can take hours, and peak RAM holds chunks for every file simultaneously before a single write occurs.

The upload path (`ingest_uploaded_file`) does not have this problem — it already processes one file at a time and commits immediately.

---

## Key Insight: The Vector Store IS the Progress Tracker

No separate progress file or state database is needed. The existing dedup mechanism already does the job:

- On every `run_ingestion` call, `get_file_hashes()` pulls all file hashes already committed to the store (lines 413–419 in `ingestion.py`).
- The pre-pass (lines 451–465) skips any file whose hash is already present.

So: if each file's chunks are committed to the store **immediately after embedding**, a crash mid-run leaves all previously committed files in the store with their hashes recorded. The next run's pre-pass skips them automatically and resumes from the first uncommitted file.

The mechanism is already correct. The only change needed is **when** the commit happens — per-file rather than at the end of the full run.

---

## Proposed Architecture

### What stays the same

- **Pre-pass** (collect candidates, hash, dedup filter): unchanged. It is cheap, correct, and handles both cross-run dedup (against store hashes) and within-batch dedup (duplicate content in the same folder).
- **Embedding model init**: initialized once before the loop, not per file.
- **Image vector store init**: opened once before the loop.
- **Cancel / progress status dict**: unchanged in structure; progress reporting actually simplifies (each file has its own mini-lifecycle instead of "loading phase / embedding phase" across the whole batch).

### What changes

Replace the monolithic load → caption → embed → store with a per-file loop:

```
emb = build_embedding_model(...)       # once
image_vs = make_vector_store(...)      # once (if image indexing enabled)

for i, file in enumerate(filtered_files):
    _on_progress(file.name, i, total, chunks_so_far)

    chunks = load_chunks([file], ...)  # one file — via subprocess pool
    if not chunks:
        continue

    text_chunks, image_chunks = split(chunks)

    if captioning_enabled:
        await _caption_image_chunks(text_chunks, image_chunks, caption_llm)
        text_chunks = quality_filter(text_chunks)
        image_chunks = []

    await build_vector_store(chunks=text_chunks, ...)   # commits file hash to store
    if image_indexing_enabled and image_chunks:
        await build_vector_store(chunks=image_chunks, vector_store=image_vs, ...)

    chunks_so_far += len(text_chunks)
```

After each `build_vector_store` call, the file's hash is in the store. A crash after that commit costs zero re-work for that file on the next run.

### Subprocess pool

`ingest_uploaded_file` already runs Docling in a `ProcessPoolExecutor` (spawn context, 1 worker) to isolate CUDA and glibc heap corruption. The same pool should be reused in the per-file loop — `_chunking_pool_fn` / `_get_chunking_pool()` are already defined and work correctly for single-file chunking.

---

## Memory Impact

Current: peak RAM = chunks for all N files simultaneously.
Proposed: peak RAM = chunks for one file at a time.

For a 500-file KB with an average of 50 chunks per file at ~2 KB each, the difference is ~50 MB vs ~50 KB of chunk data in flight at any moment (before embedding vectors are added). The real saving is larger when image chunks are involved.

---

## Decisions

1. **Captioning LLM init**: eager — initialized once before the loop if `image_captioning_enabled` is set on the KB, regardless of whether any file actually produces image chunks. Fails fast if misconfigured rather than discovering the problem mid-run.

2. **Partial-file failure handling**: log and continue, consistent with the upload path. If Docling crashes on file 400 of 1000, the subprocess pool resets, the error is logged, and the run continues with file 401. Files that fail leave no hash in the store and will be retried on the next run.

5. **Move / delete after ingestion**: deferred to a later sprint. The per-file commit architecture enables it cleanly — once a file's hash is in the store it is safe to act on — but it needs its own KB-level toggle and UI work.

6. **Cancellation**: `_cancel_requested` is checked between files, not mid-Docling. This is safer — a file is either fully committed or not touched, so no partial state can end up in the store. **Note**: if a large file (e.g. 50 MB PDF) is currently being chunked when the user cancels, the cancellation will not take effect until that file finishes. This could mean a wait of several minutes. Accepted tradeoff for data integrity.

---

## Open for Further Discussion

4. **KB stats (`write_kb_stats`)**: currently called once at the end of `run_ingestion` with all chunks. Per-file, it either needs to be called incrementally (accumulating a running total) or deferred to the end with a collected summary. The current signature may need adjustment either way.

---

## Progress Reporting — Full Analysis

### Current architecture

The backend maintains a single global `_index_status` dict (ingestion.py lines 161–176) shared by both ingestion paths. It is served by `GET /api/v1/rag/reindex-status` (no auth restriction — all authenticated users can poll it).

The frontend `IndexingStatus` component (`sidebar/indexing-status.tsx`) polls this endpoint every **5s** while indexing and **15s** when idle. It renders unconditionally in the sidebar for all authenticated users (history.tsx line 201 — no role guard).

The current phase model for `run_ingestion`:
- `"loading"` — Docling parsing all files in one pass → progress: `file_index / total_files`
- `"captioning"` — LLM captioning all images → progress: `caption_index / caption_total`
- `"embedding"` — embedding all chunks → progress: `embed_batch / embed_total_batches`

This three-phase model maps cleanly to the current monolithic flow. The frontend displays phase labels as `"1/3 Loading"`, `"2/3 Captioning"`, `"3/3 Embedding"`.

### What per-file processing breaks

The three-phase model collapses entirely. With per-file processing each file has its own mini-lifecycle — chunking → captioning → embedding — and these phases cycle N times. A single global "phase" field is no longer meaningful at run scope.

Concretely:
- `embed_batch / embed_total_batches` currently counts batches across all chunks in the run. Per-file it would reset on every file. The percentage it drives (`status.embed_batch / status.embed_total_batches`) would bounce 0→100% for each file.
- The `"1/3 Loading"` label implies all files are in one loading phase. Per-file there is no such phase.
- The progress bar in "loading" currently uses `file_index / total_files` — this part is actually fine and becomes the primary signal.

### Proposed redesign — folder scan path

Replace the run-scoped phase with two orthogonal fields:

```
file_index: int          # current file number (primary progress, unchanged)
total_files: int         # total files to process (unchanged)
file_phase: str          # "chunking" | "captioning" | "embedding" — sub-phase within current file
embed_batch: int         # embedding batch within current file (secondary)
embed_total_batches: int # embedding total batches within current file (secondary)
```

Drop the top-level `phase` field from `run_ingestion`'s usage (keep it for the upload path which is still single-file). Or unify: `phase` = the file-level sub-phase.

Frontend display becomes:
- Progress bar: `file_index / total_files` always (clean, monotonically increasing)
- Label: `"File 3/47 · Chunking"`, `"File 3/47 · Captioning"`, `"File 3/47 · Embedding"`
- Sub-label (only during embedding): `"Batch 2/5"`
- The `"X/Y phases"` prefix is removed — it only made sense for the monolithic three-phase model

### Upload path latency issue — separate problem

For `ingest_uploaded_file` (single-file upload via API) the phase model is fine — it was always single-file. The problem is different: Docling runs in a subprocess with no progress callbacks. For a non-trivial PDF, chunking takes 10–60+ seconds during which the status shows `phase: "loading"` with no sub-progress. If a poll fires at second 0 (queued) and the next at second 5 (file is already past loading and into embedding), the loading phase is never seen.

Options considered:
1. **Poll faster during indexing**: reduce from 5s to 2s. Simple but increases backend load, and still won't show intra-Docling progress.
2. **Add a chunking start timestamp to `_index_status`**: frontend can display elapsed time — `"Chunking · 23s"` — so the user sees the phase is active and how long it has been running. No backend change to Docling needed.
3. **Accept it**: the upload path processes files one at a time through the queue anyway. A queued count is already shown. The "missing" loading phase is cosmetic.

**Decision: option 3 — accept it for now.** Enough other things to address. The queued count already signals activity, and the loading phase gap is cosmetic. Can be revisited later.

### Role investigation findings

| Action | Current enforcement | Correct? |
|---|---|---|
| Trigger reindex (`POST /reindex`) | Admin only — middleware ✓ | Yes |
| Cancel reindex (`POST /reindex-cancel`) | **Not guarded** — see below | **Bug** |
| Poll status (`GET /reindex-status`) | All authenticated users | Reasonable — see below |
| See indexing banner in sidebar | All authenticated users | Reasonable — see below |
| Stop button in banner | All authenticated users | **Wrong — should be admin only** |

**Cancel endpoint path mismatch (bug):** The middleware guards `POST /api/v1/rag/reindex/cancel` (line 30 in middleware.ts), but the frontend component calls `POST /api/v1/rag/reindex-cancel` (different path — hyphen vs slash). The cancel endpoint is currently unguarded for regular users.

**Should regular users see indexing status?** Yes — knowing the KB is being updated explains slower responses and changing results. Showing progress is informative and harmless. Hiding it entirely from users would require the component to be role-gated, which adds complexity for no real security benefit.

**Should regular users see the Stop button?** No. Cancelling a reindex is an admin action (same as triggering one). The component currently renders the Stop button for all roles. It should be hidden for `role === "user"`.

### Required fixes (independent of per-file refactor)

These can and should be fixed now, ahead of the larger refactor:

1. Align the cancel endpoint path — either change the middleware guard from `/reindex/cancel` to `/reindex-cancel`, or change the component's `fetch` call. Pick one path and make both sides match.
2. Gate the Stop button on `role === "admin"` in `indexing-status.tsx`. The component already has access to `useRole()` via its parent; it needs to be passed the role or access the hook itself.

---

## Affected Code

| Location | Change |
|---|---|
| `backend/src/lancy/ingestion.py` — `run_ingestion()` | Core refactor: replace monolithic load+embed with per-file loop |
| `backend/src/lancy/ingestion.py` — `_index_status` | Replace run-scoped `phase` with per-file `file_phase`; add `chunking_started_at` |
| `backend/src/lancy/ingestion.py` — `write_kb_stats` call site | Collect stats incrementally or defer to end |
| `frontend/src/components/sections/sidebar/indexing-status.tsx` | Redesign phase labels; gate Stop button on admin role; show elapsed chunking time |
| `frontend/src/middleware.ts` | Fix cancel endpoint path to match component |
| No change needed | `ingest_uploaded_file`, pre-pass, `build_vector_store`, `load_chunks`, vector store dedup |
