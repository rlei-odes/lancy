# Technical Design: Multimodal Image Retrieval

_Status: design approved, implemented in v0.2.28_

---

## Spec: Ingestion Deduplication

**Branch:** `feature/ingestion-deduplication`

---

### Background

A draft PR was submitted to the upstream SDSC repo (SwissDataScienceCenter/sme-kt-zh-collaboration-rag#3) with an initial implementation. It was not merged. The reviewer's comments identified four concrete design problems that must be addressed in this implementation. This spec incorporates those learnings.

---

### Problem Statement

The current `build_vector_store()` has an all-or-nothing guard: if the collection already contains any chunks, the entire embedding pass is skipped. If the collection is empty, all files are parsed and embedded without any check for duplicates within the batch.

This produces two failure modes:
1. The same file under two different filenames is embedded twice in a single run, creating duplicate chunks that dilute retrieval.
2. After a `reset=False` reindex that finds an existing collection, any new files added since the last index run are silently ignored.

---

### Goals

- Skip parsing and embedding of files whose content is already in the vector store (cross-run deduplication).
- Skip parsing and embedding of files whose content has already been seen earlier in the same ingestion batch (within-run deduplication).
- Stamp every chunk with its source file's SHA-256 hash so that future runs can identify it regardless of filename.
- Keep the expensive file-parsing step (`load_chunks`) free of embedding-level concerns: hashing happens before parsing, not inside it.
- Surface skipped files clearly in the log.
- `reset=True` clears the store and bypasses cross-run deduplication; within-batch deduplication still applies.

---

### Learnings from the Draft PR (SwissDataScienceCenter/sme-kt-zh-collaboration-rag#3)

The upstream reviewer (Thibaut-Loiseau) left six specific comments. The problems and their resolutions:

**1. `get_existing_hashes` took a `db_path` — wrong abstraction layer**

The draft passed a filesystem path to `get_existing_hashes`, then instantiated `ChromaDBVectorStore` inside the function. This hardcodes the ChromaDB backend and breaks if the vector store is PGVector or anything else.

Fix: add `get_file_hashes() -> set[str]` as an abstract method on the `VectorStore` base class. Each backend implements it. Call sites pass a `VectorStore` instance, not a path.

**2. `get_existing_hashes` accessed `vs.collection` directly**

The draft reached into `.collection`, a ChromaDB-specific property, from what was supposed to be a backend-agnostic utility function. Acceptable in a notebook; not in production code.

Fix: the logic for querying metadata lives inside the `ChromaDBVectorStore.get_file_hashes()` implementation, not in any shared utility.

**3. `seen_hashes.add(hash_value)` was called before the `try/except` block**

If processing the first occurrence of a duplicate file raises an exception, the hash was already registered as seen. The second occurrence would then be silently skipped — neither copy would end up in the store.

Fix: only call `seen_hashes.add(hash_value)` after the file has been successfully parsed and its chunks collected.

**4. The `"unknown"` fallback in `build_vector_store` was unsafe**

The draft used `chunk.metadata.get("file_hash", "unknown")` when grouping chunks. If more than one chunk lacked a `file_hash` key, they would all fall into a single `"unknown"` bucket and only the first group would be ingested.

Fix: `file_hash` must always be present in chunk metadata — it is stamped by `load_chunks` before any chunk is returned. If it is somehow missing at the `build_vector_store` stage, raise an error rather than silently grouping under a sentinel.

**5. Don't filter inside `load_chunks`**

The draft added `existing_hashes` filtering logic to `load_chunks`. The reviewer's position: `load_chunks` reads files; it should not need to know about what is already in the store.

Resolution for this spec: pre-compute existing hashes from the vector store in `_run_ingestion` (in `main.py`), build the set of files to skip before calling `load_chunks`, and pass only the list of non-skipped files in. `load_chunks` itself remains unaware of the store. The within-batch deduplication (seen_hashes) lives in the pre-pass, not inside load_chunks.

**6. Do not commit test fixture files for duplicate detection**

The draft committed `data/EVALUATION_duplicate_file.pdf`. Testing for duplicates should reuse an already-present file — point the KB at a directory where the same file appears twice under different names (or copy one), test, then clean up.

---

### Design

**New abstract method on `VectorStore`:**

```python
# conversational-toolkit/src/conversational_toolkit/vectorstores/base.py
@abstractmethod
async def get_file_hashes(self) -> set[str]:
    """Return the set of file_hash values stored in this collection's metadata."""
    ...
```

**`ChromaDBVectorStore.get_file_hashes()` implementation:**

Follow the same pattern as `get_source_files()` — wrap the synchronous ChromaDB call in `run_in_executor`:

```python
async def get_file_hashes(self) -> set[str]:
    import asyncio
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: self.collection.get(include=["metadatas"])
    )
    return {m["file_hash"] for m in (result.get("metadatas") or []) if m and "file_hash" in m}
```

**`PGVectorStore.get_file_hashes()` implementation:**

The metadata is stored in the `chunk_metadata` JSON column. The pattern is identical to the existing `get_source_files()` method — same SQLAlchemy accessor, different key:

```python
async def get_file_hashes(self) -> set[str]:
    await self._ensure_initialized()
    async with self.SessionLocal() as session:
        result = await session.execute(
            select(self.table.c.chunk_metadata["file_hash"].astext)
            .distinct()
            .where(self.table.c.chunk_metadata["file_hash"].astext.isnot(None))
        )
        return {row[0] for row in result if row[0]}
```

**New helper `file_hash()` in `feature0_baseline_rag.py`:**

```python
def file_hash(path: Path) -> str:
    """SHA-256 fingerprint of a file's raw bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

**`load_chunks` signature change:**

Add two new optional parameters:

```python
def load_chunks(
    data_dirs: list[Path] | None = None,
    ...
    include_files: list[Path] | None = None,       # if set, skip data_dirs iteration
    file_hashes: dict[Path, str] | None = None,    # precomputed hashes for stamping
) -> list[Chunk]:
```

When `include_files` is provided, the function iterates that list instead of scanning `data_dirs`. This allows the pre-pass to pass only files that survived deduplication. `file_hashes` is used to stamp `chunk.metadata["file_hash"]` for each file; if a file's hash is not in the dict, compute it on the fly as a fallback (notebook compatibility).

**Extract `_collect_candidate_files()` helper from `load_chunks`:**

The pre-pass needs to know which files exist and pass filtering (extension, size, "EVALUATION" exclusion) before it can hash them. This logic currently lives inside `load_chunks`. Rather than duplicating it, extract it into a shared helper:

```python
def _collect_candidate_files(
    data_dirs: list[Path],
    max_file_size_mb: float,
    max_files: int | None = None,
) -> list[Path]:
    """Return the filtered list of ingestable files from the given directories."""
    ...
```

`load_chunks` calls this helper when `include_files` is not provided. The pre-pass calls it directly.

**Pre-pass in `_run_ingestion` (main.py), before `load_chunks`:**

File hashing reads entire files from disk — blocking I/O. Per the CLAUDE.md constraint, all blocking operations must use `run_in_executor`. The pre-pass runs in the executor, producing two outputs: the filtered file list and the precomputed hash map.

Steps (all inside a single `run_in_executor` call, before the existing `load_chunks` executor call):

1. Call `_collect_candidate_files(data_dirs, ...)` to get candidate files.
2. For each file, compute `file_hash(path)` → build `file_hashes: dict[Path, str]`.
3. Apply cross-run dedup: filter out files whose hash is in `existing_hashes`, log and count each skip.
4. Apply within-batch dedup: from the remainder, build `seen_hashes`, skip files whose hash is already seen (only add to `seen_hashes` after the file passes), log and count each skip.
5. Return `(filtered_files, file_hashes, n_skipped_store, n_skipped_batch)`.

`existing_hashes` is obtained by `await vector_store.get_file_hashes()` before the executor call. To do this without blocking the event loop, the VS must be queryable from the async context:

- **ChromaDB**: `ChromaDBVectorStore.get_file_hashes()` wraps in `run_in_executor` internally (consistent with `get_source_files()`), so awaiting it from the async context is correct. `make_vector_store` can be called synchronously in the async context; the same instance is passed to `_sync_build_vs`.
- **PGVector**: `AsyncEngine` is bound to the event loop it was created in and cannot be reused in `_sync_build_vs`'s separate loop. Create two separate `PGVectorStore` instances: one in the async context for the hash query, one inside `_sync_build_vs` for embedding (as today).

**`_run_ingestion` return type:**

Change from `tuple[int, int]` to `tuple[int, int, int]` — adding `files_skipped`. Update the call site at line 598:
```python
chunks_n, files_n, skipped_n = await _run_ingestion(kb, reset)
```
And the `ReindexResult` construction:
```python
return ReindexResult(chunks_indexed=chunks_n, files_processed=files_n, files_skipped=skipped_n, reset=reset)
```
`update_stats` (line 599) is unchanged — it receives only `chunks_n` and `files_n` (files actually indexed, not skipped).

**In `build_vector_store`:**

- Remove the `current_count > 0` early-exit guard (it was the root cause of new files being silently ignored on incremental runs).
- Accept an optional `existing_hashes: set[str] | None = None` parameter. When provided (passed from the pre-pass), use it directly — do not call `get_file_hashes()` again. When `None` (notebook/standalone call), call `get_file_hashes()` internally. This prevents loading all chunk metadata twice per reindex run.
- Group incoming chunks by `file_hash`. Raise `ValueError` if any chunk is missing `file_hash` in its metadata.
- For each group, check whether the hash is in `existing_hashes`. If yes, log and skip. If no, embed and insert.

**`ReindexResult` and UI:**

Add `files_skipped: int = 0` to `ReindexResult` in `rag_router.py`. Populate it from the pre-pass counts. Update the `statusIndexed` string in all four language files (`en.ts`, `de.ts`, `fr.ts`, `it.ts`) to surface the skipped count when non-zero. Example:

```
# when skipped > 0
"Indexed {{chunks}} chunks from {{new}} files ({{skipped}} already up to date)."
# when skipped == 0
"Indexed {{chunks}} chunks from {{files}} files."
```

The frontend `reindex()` function in `rag-config-panel.tsx` already reads `data.files_processed` — it will also read `data.files_skipped` and pick the appropriate string.

**Logging:**

- Each cross-run skip: `INFO "Skipping {filename!r} — already in store (hash={hash[:8]}…)"`
- Each within-batch skip: `WARNING "Skipping {filename!r} — duplicate content in current batch (hash={hash[:8]}…)"`
- Summary at end: `INFO "Ingestion complete: {new} new files embedded, {skipped_store} skipped (already in store), {skipped_batch} skipped (duplicate in batch)"`

---

### Scaling Considerations

These are known limitations at 10k+ documents. None block this feature's correctness at current scale; they are noted here to avoid designing into a corner.

**`collection.get(include=["metadatas"])` does not scale (ChromaDB).**
`get_file_hashes()` fetches metadata for every chunk in the collection — no pagination, no filtering, no distinct. At 200k chunks this loads hundreds of MB of Python dicts into memory and takes seconds. The existing `get_source_files()` has the same problem. The long-term fix is a separate per-file hash registry (e.g., a small SQLite or JSON file alongside the vector store) that stores one record per file rather than deriving state from chunk metadata. That is a follow-up task.

**Hashing all files on every run.**
`file_hash()` reads the entire file to compute SHA-256. At 10k × 5MB average that is 50 GB of disk reads per run, even when 9,990 files have not changed. The fix is a mtime + size fast-path: if both are unchanged since the file was last indexed, skip the SHA-256 read entirely. This requires the per-file registry above and is a follow-up.

**Old collections have no `file_hash` metadata.**
KBs indexed before this feature was deployed will have no `file_hash` in their chunk metadata. `get_file_hashes()` will return an empty set, and the first incremental reindex will re-embed everything. This is correct behaviour (not a bug) but may surprise users. Log a warning when the store is non-empty but `get_file_hashes()` returns an empty set.

**BM25 in-memory corpus (pre-existing, not introduced by dedup).**
`BM25Retriever` calls `get_chunks_by_filter()` with no filter on the first query after a reindex, loading all chunks including full text content into memory, then tokenising them to build the index. At 200k chunks this takes many seconds and several GB of RAM. It happens on the first user query, not during reindex. Dedup helps indirectly by preventing duplicate chunks from inflating the corpus, but does not solve the underlying problem.

---

### What Does Not Change

- `reset=True` wipes the collection and skips cross-run deduplication (nothing in the store to compare against). Within-batch deduplication still applies — two files with identical content in the same batch still produce only one set of chunks.
- The BM25 index is rebuilt from scratch on every reindex (lazy init in `BM25Retriever`), so no explicit invalidation is needed.
- No new dependencies.

---

### Out of Scope for This Feature

- Removal of chunks belonging to a file that has been deleted from disk (orphan cleanup).
- A force-reingest flag for individual files.
- UI display of per-file dedup status (only counts, not per-file breakdown).

These are deferred to a follow-up once the core deduplication is stable.

---
