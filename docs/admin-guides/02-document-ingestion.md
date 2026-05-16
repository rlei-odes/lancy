# Document Ingestion Guide
How to create Knowledge Bases, configure ingestion settings, and get documents into Lancy.
---

## Overview

Ingestion is the process of reading your source documents, splitting them into chunks, embedding each chunk into a vector representation, and writing those embeddings into a vector store. Once indexed, chunks are retrieved during queries using a combination of semantic search and BM25 keyword matching.

Lancy organises documents into **Knowledge Bases (KBs)**. Each KB has its own embedding model, vector store, and ingestion settings — they are fully independent and can be switched at runtime without restarting the backend, provided they share the same embedding model.

---

## Creating a Knowledge Base

### Via the UI

1. Log in as admin and open the **RAG Parameters** panel (right side of the interface)
2. Click **+** next to the knowledge base selector
3. Enter a name, a source directory path (e.g. `data/`), and choose your embedding backend
4. Save — the KB is registered but not yet indexed
5. Click **Re-index** to run the first ingestion

### Via the API

```bash
curl -X POST http://localhost:8080/api/v1/kb \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My KB",
    "data_dirs": ["/absolute/path/to/docs"],
    "embedding_backend": "local",
    "embedding_model": "nomic-ai/nomic-embed-text-v1"
  }'
```

The response includes the generated `id` — keep it for subsequent API calls.

Note: If you are using the API to ingest files, the source directory path is irrelevant.
---

## KB Settings Reference

These are set at KB creation and stored in `knowledge_bases.json`. Most can be edited in the **RAG Parameters** panel. Changing the embedding model on an existing KB requires a full re-index with `reset=true` — the vector store dimension is fixed at creation time.

### Embedding

| Setting | Default | Notes |
|---|---|---|
| `embedding_backend` | `local` | `local` / `ollama` / `litellm` / `custom` |
| `embedding_model` | `nomic-ai/nomic-embed-text-v1` | Must match installed weights for `local`; must be available on the configured server for others |
| `embedding_ollama_host` | _(localhost:11434)_ | Only used when `embedding_backend=ollama` |
| `embedding_custom_base_url` | — | OpenAI-compatible base URL; used when `embedding_backend=custom` |
| `embedding_custom_api_key` | — | API key for the custom embedding endpoint |
| `nomic_prefix` | `true` | Prepend task prefix to queries and chunks — required for Nomic models, harmless for others |
| `embedding_batch_size` | `50` | Chunks per embedding call. Higher = faster but more VRAM. Reduce if you see OOM errors during ingestion. |

### Chunking

| Setting | Default | Notes |
|---|---|---|
| `max_chunk_tokens` | `0` | Token ceiling per chunk. `0` = auto-size (each chunker picks a sensible default, typically 512–1024 tokens). Override only if retrieval quality suffers. |
| `max_file_size_mb` | `20` | Files exceeding this are skipped with a warning. Raise for large PDFs; lower to prevent accidental ingestion of binaries. |
| `pdf_ocr_enabled` | `true` | Enable OCR for scanned PDFs. Adds significant processing time per page. Disable for native-text PDFs to speed up ingestion. |

### Image support

| Setting | Default | Notes |
|---|---|---|
| `image_indexing_enabled` | `false` | Extract and embed images found in documents. Requires a GPU for reasonable performance. |
| `image_retrieval_enabled` | `false` | Allow image similarity search in queries. Only meaningful if `image_indexing_enabled` is on. |
| `image_embedding_model` | `Qwen/Qwen3-VL-Embedding-2B` | Vision embedding model. Must be pre-downloaded before the backend starts. |
| `image_captioning_enabled` | `false` | Generate LLM captions for each image during ingestion. Captions are indexed alongside the image embedding, improving text-based retrieval of visual content. Uses the session LLM. |

### Vector store

| Setting | Default | Notes |
|---|---|---|
| `vs_type` | `chromadb` | `chromadb` (embedded, no external service) or `pgvector` (external PostgreSQL) |
| `vs_connection_string` | — | PostgreSQL connection string; required when `vs_type=pgvector`. E.g. `postgresql://user:pass@host:5432/lancy` |

---

## Supported File Formats

| Format | Extensions | Notes |
|---|---|---|
| PDF | `.pdf` | Full layout extraction via docling. OCR available for scanned pages (`pdf_ocr_enabled`). Image extraction available (`image_indexing_enabled`). |
| Excel | `.xlsx` `.xls` | Each sheet becomes separate chunks; row structure preserved. |
| Word | `.docx` | Processed via MarkItDown for layout preservation. |
| Text / Markdown | `.txt` `.md` | Markdown-aware splitting that respects heading hierarchy. |
| Images | `.png` `.jpg` `.jpeg` `.gif` `.tiff` `.bmp` `.webp` | Only ingested when `image_indexing_enabled=true`. Direct upload via UI or API only — the batch script skips image files. |

Files with unsupported extensions are logged as warnings and skipped. Warnings appear in `logs/backend.log` and are also recorded in the ingest event history (see [Troubleshooting](#troubleshooting)).

---

## Ingestion Methods

### Method 1 — Folder scan (UI or API)

The simplest approach when documents are on the same machine as the backend or accessible to it (local disk, NFS mount, etc.). Set `data_dirs` to one or more absolute paths, then trigger indexing from the UI:

- **Incremental indexing** — processes only new or changed files. Files already present in the vector store are skipped (deduplication via SHA-256 hash). Use this for routine updates.
- **Re-index** — clears the entire vector store first, then re-embeds everything from scratch. Use this after changing the embedding model, or to recover from a corrupted store.

You can also trigger these actions via the API:

```bash
# Incremental — only re-embeds files not already in the vector store
curl -X POST http://localhost:8080/api/v1/rag/reindex \
  -H "Content-Type: application/json" \
  -d '{"reset": false}'

# Full reset — clears the vector store before re-embedding everything
curl -X POST http://localhost:8080/api/v1/rag/reindex \
  -H "Content-Type: application/json" \
  -d '{"reset": true}'
```

### Method 2 — HTTP upload (no shared filesystem)

Use this when documents live on a different host than the backend (e.g. Profile 2 or 3 split deployments).

**Batch upload — using the provided script:**

```bash
scripts/upload-docs.sh http://<backend-host>:8080 <kb-id> /path/to/docs/
```

`upload-docs.sh` is a working reference implementation rather than a polished tool. It recursively finds all supported files in the given directory, uploads them one at a time via the document upload API, and polls the reindex-status endpoint after each file before sending the next. It waits up to 40 minutes per file and tolerates up to 10 minutes of backend unavailability before giving up. For production use, you may want to adapt it — for example, to parallelise uploads, handle authentication headers, or integrate with a document management system. The script is intentionally simple enough to read and modify.

**Single file:**

```bash
curl -X POST http://<backend-host>:8080/api/v1/kb/<kb-id>/documents \
  -F "file=@/path/to/document.pdf" \
  -F 'metadata={"document_id": "my-doc", "source_file": "document.pdf"}'
```

`document_id` is required and must be unique within the KB — the backend will reject duplicates. Use the filename stem as a safe default. The `source_file` field is stored as metadata and surfaced in retrieval results so users can trace a chunk back to its origin.

For the full ingestion API reference including request/response schemas, see [03-API-endpoints.md](03-API-endpoints.md).

---

## Monitoring Ingestion

Poll the status endpoint to track progress:

```bash
curl http://localhost:8080/api/v1/rag/reindex-status
```

Key fields in the response:

| Field | Meaning |
|---|---|
| `indexing` | `true` while a job is running |
| `phase` | Current step: `loading` → `chunking` → `embedding` → `captioning` |
| `current_file` | Filename being processed right now |
| `file_index` / `total_files` | Progress through the file list |
| `chunks_so_far` | Cumulative chunks produced in this run |
| `embed_batch` / `embed_total_batches` | Embedding progress within the current file |
| `last_result.files_skipped_store` | Files skipped because they were already in the vector store (dedup) |
| `last_result.files_skipped_batch` | Files skipped because duplicate content was detected within this run |
| `queued` | Files in the upload queue waiting to start |

The UI's ingestion panel reads this endpoint live and displays the same information.

To cancel a running job:

```bash
curl -X POST http://localhost:8080/api/v1/rag/reindex-cancel
```

Cancellation is cooperative — the backend finishes the current chunk batch before stopping.

---

## Troubleshooting

**Check the backend log** for per-file warnings and errors:

```bash
tail -f logs/backend.log
```

**Check ingest history** — the backend records the outcome of every ingestion run:

```bash
curl http://localhost:8080/api/admin/ingest-events
```

**Common issues:**

| Symptom | Likely cause |
|---|---|
| File skipped with no error | Exceeds `max_file_size_mb`, or unsupported format |
| Embedding OOM during ingestion | Reduce `embedding_batch_size` (e.g. to 10–20) |
| OCR very slow | Expected for scanned PDFs — set `pdf_ocr_enabled=false` if pages have native text |
| Re-index doesn't pick up changed files | File content unchanged (same hash) — modify the file or use `reset=true` |
| `reset=true` required after changing model | Embedding dimensions are fixed at collection creation; changing the model requires clearing the store |
