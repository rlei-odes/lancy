import asyncio
import base64
import concurrent.futures
import hashlib
import io
import logging
import multiprocessing
import os
import signal
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"

from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.llms.base import LLM, LLMMessage, MessageContent, Roles
from lancy.feature0_baseline_rag import (
    _ROOT,
    VS_PATH,
    _collect_candidate_files,
    build_embedding_model,
    build_llm,
    build_vector_store,
    file_hash,
    load_chunks,
    make_vector_store,
)
from lancy.kb_router import KBInfo
from lancy.kb_stats import write_kb_stats
from lancy.rag_router import RagConfig, ReindexResult

log = logging.getLogger("uvicorn")


def _available_ram_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1_048_576  # kB → GB
    except Exception:
        pass
    return None


def _clear_cuda_cache() -> None:
    """Free GPU memory held by docling's pipeline models between pipeline stages."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            free, total = torch.cuda.mem_get_info()
            log.debug(f"CUDA cache cleared: {free / 1e9:.1f} GB free / {total / 1e9:.1f} GB total")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Subprocess pool for docling PDF chunking
#
# docling's native OCR/layout libs can corrupt glibc's heap on certain PDFs,
# triggering abort() (SIGABRT) and killing the entire uvicorn process. Running
# load_chunks in a subprocess means only that subprocess dies on a crash;
# the main backend stays alive and the next upload continues.
#
# "spawn" context is used because CUDA is not fork-safe: a forked child inherits
# the parent's CUDA context in an undefined state. "spawn" starts a fresh Python
# interpreter so docling initialises its own CUDA pipeline cleanly.
# ---------------------------------------------------------------------------


def _chunking_pool_fn(
    file_path_str: str,
    h: str,
    pdf_ocr_enabled: bool,
    max_chunk_tokens: int,
    write_images: bool,
):
    """Top-level function executed in the chunking subprocess."""
    import logging as _log
    _log.basicConfig(level=_log.INFO, format="%(asctime)s [chunk-worker] %(levelname)s %(message)s")
    from pathlib import Path as _Path
    from lancy.feature0_baseline_rag import load_chunks as _load_chunks
    fp = _Path(file_path_str)
    return _load_chunks(
        include_files=[fp],
        file_hashes={fp: h},
        pdf_ocr_enabled=pdf_ocr_enabled,
        max_chunk_tokens=max_chunk_tokens,
        write_images=write_images,
    )


_chunking_pool: concurrent.futures.ProcessPoolExecutor | None = None


def _get_chunking_pool() -> concurrent.futures.ProcessPoolExecutor:
    global _chunking_pool
    if _chunking_pool is None:
        _chunking_pool = concurrent.futures.ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
        )
    return _chunking_pool


def _reset_chunking_pool() -> None:
    global _chunking_pool
    if _chunking_pool is not None:
        # Kill running subprocesses immediately so they don't linger holding GPU memory.
        # shutdown(wait=False, cancel_futures=True) only cancels *pending* futures, not
        # already-running ones — the subprocess keeps going unless we SIGKILL it.
        try:
            for pid in list(_chunking_pool._processes.keys()):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except Exception:
            pass
        try:
            _chunking_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _chunking_pool = None


# ---------------------------------------------------------------------------
# Embedding model cache — keep one loaded instance per (backend, model_name)
# so it isn't reloaded from GPU on every document. Cleared on KB switch.
# ---------------------------------------------------------------------------
_emb_cache: dict[tuple, Any] = {}
_emb_cache_key: tuple = ()


def _get_cached_embedding_model(kb) -> Any:
    global _emb_cache, _emb_cache_key
    key = (kb.embedding_backend, kb.embedding_model, kb.embedding_ollama_host,
           kb.embedding_custom_base_url)
    if key != _emb_cache_key or not _emb_cache:
        _emb_cache.clear()
        _emb_cache[key] = build_embedding_model(
            kb.embedding_backend,
            kb.embedding_model,
            ollama_host=kb.embedding_ollama_host or "",
            custom_base_url=kb.embedding_custom_base_url or "",
            custom_api_key=kb.embedding_custom_api_key or "",
        )
        _emb_cache_key = key
    return _emb_cache[key]


# ---------------------------------------------------------------------------
# Global index status (polled by frontend via GET /api/v1/rag/reindex-status)
# ---------------------------------------------------------------------------
_index_status: dict = {
    "indexing": False,
    "phase": "loading",
    "current_file": "",
    "file_index": 0,
    "total_files": 0,
    "chunks_so_far": 0,
    "embed_batch": 0,
    "embed_total_batches": 0,
    "caption_index": 0,
    "caption_total": 0,
    "kb_name": "",
    "finished_at": "",
    "last_result": None,
    "queued": 0,
}
_cancel_requested: bool = False

# ---------------------------------------------------------------------------
# Upload queue — serialises single-file uploads so they never run concurrently
# ---------------------------------------------------------------------------
_upload_queue: asyncio.Queue = asyncio.Queue()


def _load_captioning_prompt() -> str:
    for name in ("image_captioning_prompt.custom.md", "image_captioning_prompt.default.md"):
        p = _PROMPTS_DIR / name
        if p.exists():
            content = p.read_text().strip()
            if content:
                return content
    raise FileNotFoundError("No image captioning prompt found in prompts/")


# Images smaller than this (in total pixels) are skipped without an LLM call.
# Calibrated on real document sets: 68k px² (repeated logo) → exclude,
# 145k px² (useful figure) → keep. 100k sits cleanly between both.
_MIN_CAPTION_IMAGE_AREA = 100_000


def _img_info(b64: str) -> tuple[str, int, int]:
    """Decode base64 image → (sha256_hex, width, height). Dimensions are 0 on error."""
    raw = base64.b64decode(b64)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with Image.open(io.BytesIO(raw)) as img:
            w, h = img.size
    except Exception:
        w, h = 0, 0
    return digest, w, h


async def _caption_image_chunks(
    text_chunks: list[Chunk],
    image_chunks: list[Chunk],
    llm: LLM,
) -> None:
    """Replace <!-- image --> placeholders in text_chunks with LLM-generated captions.

    Mutates text_chunks in-place. Images without a matching placeholder are appended
    as new standalone text chunks.

    Optimisations applied before any LLM call:
    - Size filter: images below _MIN_CAPTION_IMAGE_AREA pixels are skipped.
    - Dedup: identical images (by SHA-256 of raw bytes) are captioned once and
      the result is reused for every subsequent occurrence within the same file.
    """
    # Collect placeholder positions (chunk_idx, nth occurrence) in document order.
    placeholders: list[tuple[int, int]] = []
    for i, chunk in enumerate(text_chunks):
        for j in range(chunk.content.count("<!-- image -->")):
            placeholders.append((i, j))

    n_images = len(image_chunks)
    n_to_replace = min(len(placeholders), n_images)

    if len(placeholders) != n_images:
        log.warning(
            f"Image caption: {len(placeholders)} placeholder(s) in text but "
            f"{n_images} image chunk(s) — captioning {n_to_replace}."
        )

    _index_status["caption_total"] = n_images
    prompt = _load_captioning_prompt()

    async def _call_llm(image_chunk: Chunk) -> str:
        user_msg = LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(type="text", text=prompt),
                MessageContent(type="image", image_url=image_chunk.content),
            ],
        )
        response = await llm.generate([user_msg])
        return "".join(mc.text or "" for mc in response.content).strip()

    def _useful(caption: str) -> bool:
        if "SKIP_RESULT" in caption:
            return False
        # Require at least some visible text — descriptions of purely visual
        # images (logos, illustrations) almost never contain extractable text.
        visible = ""
        for line in caption.splitlines():
            if line.upper().startswith("VISIBLE TEXT:"):
                visible = line.split(":", 1)[-1].strip()
                break
        return visible.lower() not in ("", "none")

    # Pre-caption pass: one LLM call per unique, non-tiny image.
    # caption_cache maps SHA-256 hex → caption text, or None if skipped/failed.
    caption_cache: dict[str, str | None] = {}
    # Store per-image (digest, w, h) so the apply loops don't re-decode.
    img_infos: list[tuple[str, int, int]] = []

    for k, img_chunk in enumerate(image_chunks):
        _index_status["caption_index"] = k + 1
        source = img_chunk.metadata.get("source_file", "image")
        digest, w, h = _img_info(img_chunk.content)
        img_infos.append((digest, w, h))
        area = w * h
        log.info(f"Image {k + 1}/{n_images}: {w}×{h} px from '{source}'")

        if digest in caption_cache:
            log.info(f"Image {k + 1}/{n_images}: reusing caption (identical content, hash={digest[:8]}…)")
            continue

        if area > 0 and area < _MIN_CAPTION_IMAGE_AREA:
            log.info(f"Skipping image {k + 1} — too small ({w}×{h} px, area={area})")
            caption_cache[digest] = None
            continue

        try:
            caption = await _call_llm(img_chunk)
            if _useful(caption):
                caption_cache[digest] = caption
            else:
                log.info(f"Skipping image {k + 1} — no retrieval value (SKIP_RESULT or no visible text)")
                caption_cache[digest] = None
        except Exception as exc:
            log.error(f"Captioning failed for image {k + 1} from '{source}': {exc}")
            caption_cache[digest] = None

    # Apply captions to placeholder positions in text chunks.
    for k in range(n_to_replace):
        chunk_idx, _ = placeholders[k]
        digest = img_infos[k][0]
        caption = caption_cache.get(digest)
        if caption:
            text_chunks[chunk_idx].content = text_chunks[chunk_idx].content.replace(
                "<!-- image -->",
                f"<!-- image content -->\n{caption}\n<!-- end image content -->",
                1,
            )
        else:
            text_chunks[chunk_idx].content = text_chunks[chunk_idx].content.replace(
                "<!-- image -->", "", 1
            )

    # Images beyond available placeholders become standalone text chunks.
    for k in range(n_to_replace, n_images):
        digest = img_infos[k][0]
        caption = caption_cache.get(digest)
        if caption:
            text_chunks.append(
                Chunk(
                    title=image_chunks[k].title,
                    content=f"<!-- image content -->\n{caption}\n<!-- end image content -->",
                    mime_type="text/markdown",
                    metadata=image_chunks[k].metadata.copy(),
                )
            )


class _IndexingCancelled(Exception):
    pass


def cancel_indexing() -> None:
    global _cancel_requested
    _cancel_requested = True


async def run_ingestion(
    kb: KBInfo, reset: bool, db_dir: Path, cfg: RagConfig | None = None, db_engine: Any = None
) -> tuple[int, int, int, int]:
    """Chunk + embed all files in kb.data_dirs.

    Returns (chunks_indexed, files_processed, files_skipped_store, files_skipped_batch).
    files_skipped_store: already in vector store (cross-run dedup).
    files_skipped_batch: duplicate content within the same run.
    Originally returned a single files_skipped int; now split for finer reporting — see
    duplicate content within the same batch).
    """
    global _cancel_requested
    _cancel_requested = False
    _index_status.update(
        {
            "indexing": True,
            "phase": "loading",
            "current_file": "",
            "file_index": 0,
            "total_files": 0,
            "chunks_so_far": 0,
            "embed_batch": 0,
            "embed_total_batches": 0,
            "caption_index": 0,
            "caption_total": 0,
            "captioning_enabled": kb.image_captioning_enabled,
            "kb_name": kb.name,
            "finished_at": "",
            "last_result": None,
        }
    )

    def _on_progress(
        current_file: str, file_index: int, total_files: int, chunks_so_far: int
    ) -> None:
        if _cancel_requested:
            raise _IndexingCancelled()
        _index_status.update(
            {
                "current_file": current_file,
                "file_index": file_index,
                "total_files": total_files,
                "chunks_so_far": chunks_so_far,
            }
        )

    loop = asyncio.get_event_loop()
    try:
        data_dirs = [
            Path(d) if Path(d).is_absolute() else _ROOT / d for d in kb.data_dirs
        ]

        vs_type = getattr(kb, "vs_type", "chromadb") or "chromadb"
        vs_conn = getattr(kb, "vs_connection_string", "") or ""
        vs_path = Path(kb.vs_path) if vs_type == "chromadb" else None

        # Instantiate the vector store in the async context so get_file_hashes()
        # can be awaited here. For ChromaDB this instance is reused in the build
        # thread below. For PGVector the AsyncEngine is loop-bound, so a second
        # instance is created inside _sync_build_vs.
        vs_for_query = make_vector_store(
            vs_type=vs_type,
            db_path=vs_path,
            embedding_model_name=kb.embedding_model,
            vs_connection_string=vs_conn,
            table_name=f"rag_{kb.id.replace('-', '_')}",
        )

        # Fetch hashes already in the store. On reset we skip this — the store
        # will be cleared anyway, and cross-run dedup does not apply.
        existing_hashes: set[str] = set()
        if not reset:
            existing_hashes = await vs_for_query.get_file_hashes()
            if existing_hashes:
                log.info(
                    f"Dedup: {len(existing_hashes)} file hash(es) already in store"
                )
            else:
                current_count = await vs_for_query.count()
                if current_count > 0:
                    # Store has chunks but none have file_hash — indexed before
                    # deduplication was introduced. First incremental run will
                    # re-embed everything; subsequent runs will be incremental.
                    log.warning(
                        f"Vector store has {current_count} chunks but no file_hash metadata. "
                        "This KB was indexed before deduplication support was added. "
                        "All files will be re-embedded on this run."
                    )

        # Pre-pass: collect candidates, hash them, apply dedup filters.
        # File hashing reads entire file bytes — blocking I/O, run in executor.
        def _prepass() -> tuple[list[Path], dict[Path, str], int, int, list[Path], list[Path]]:
            candidates = _collect_candidate_files(
                data_dirs,
                max_file_size_mb=kb.max_file_size_mb,
                max_files=None,
            )
            hashes: dict[Path, str] = {}
            for f in candidates:
                hashes[f] = file_hash(f)

            filtered: list[Path] = []
            skipped_store: list[Path] = []
            skipped_batch: list[Path] = []
            seen_hashes: set[str] = set()
            n_skipped_store = 0
            n_skipped_batch = 0

            for f in candidates:
                h = hashes[f]
                if h in existing_hashes:
                    log.info(f"Skipping {f.name!r} — already in store (hash={h[:8]}…)")
                    n_skipped_store += 1
                    skipped_store.append(f)
                elif h in seen_hashes:
                    log.warning(
                        f"Skipping {f.name!r} — duplicate content in batch (hash={h[:8]}…)"
                    )
                    n_skipped_batch += 1
                    skipped_batch.append(f)
                else:
                    seen_hashes.add(h)
                    filtered.append(f)

            log.info(
                f"Pre-pass complete: {len(filtered)} to index, "
                f"{n_skipped_store} already in store, "
                f"{n_skipped_batch} duplicate in batch"
            )
            return filtered, hashes, n_skipped_store, n_skipped_batch, skipped_store, skipped_batch

        (
            filtered_files,
            file_hashes_map,
            n_skipped_store,
            n_skipped_batch,
            skipped_store_files,
            skipped_batch_files,
        ) = await loop.run_in_executor(None, _prepass)

        if not filtered_files:
            log.info(
                f"All files already indexed for KB '{kb.name}' — nothing to embed."
            )
            return 0, 0, n_skipped_store, n_skipped_batch

        # Run blocking load_chunks in thread pool so event loop stays responsive.
        chunks = await loop.run_in_executor(
            None,
            lambda: load_chunks(
                include_files=filtered_files,
                file_hashes=file_hashes_map,
                on_progress=_on_progress,
                pdf_ocr_enabled=kb.pdf_ocr_enabled,
                max_chunk_tokens=getattr(kb, "max_chunk_tokens", 0),
                write_images=kb.image_indexing_enabled or kb.image_captioning_enabled,
            ),
        )
        if not chunks:
            log.warning(
                f"No chunks produced for KB '{kb.name}' — vector store unchanged."
            )
            return 0, 0, n_skipped_store, n_skipped_batch

        text_chunks = [c for c in chunks if c.mime_type.startswith("text")]
        image_chunks = [c for c in chunks if c.mime_type.startswith("image")]

        if kb.image_captioning_enabled and image_chunks and cfg is not None:
            _index_status["phase"] = "captioning"
            caption_llm = build_llm(
                backend=cfg.llm_backend,
                model_name=cfg.llm_model or None,
                temperature=0.1,
                ollama_host=cfg.ollama_host or None,
                custom_base_url=cfg.custom_base_url or "",
                custom_api_key=cfg.custom_api_key or "",
                max_tokens=512,
            )
            await _caption_image_chunks(text_chunks, image_chunks, caption_llm)
            image_chunks = []  # consumed by captioning; not stored in image VS
        elif kb.image_captioning_enabled and image_chunks and cfg is None:
            log.warning(
                "Image captioning is enabled on this KB but no session config was provided — "
                "skipping captioning. Re-index via the UI to caption images."
            )

        try:
            emb = build_embedding_model(
                kb.embedding_backend,
                kb.embedding_model,
                ollama_host=kb.embedding_ollama_host or "",
                custom_base_url=kb.embedding_custom_base_url or "",
                custom_api_key=kb.embedding_custom_api_key or "",
            )
        except OSError as exc:
            raise RuntimeError(
                f"Embedding model '{kb.embedding_model}' is not in the local cache. "
                "Pre-download it on the server before indexing (HF_HUB_OFFLINE is enabled)."
            ) from exc
        _index_status["phase"] = "embedding"

        def _on_embed_progress(batch_idx: int, total_batches: int) -> None:
            if _cancel_requested:
                raise _IndexingCancelled()
            _index_status.update(
                {"embed_batch": batch_idx, "embed_total_batches": total_batches}
            )

        # build_vector_store is async but calls blocking SentenceTransformer.encode().
        # Run it in a thread with its own event loop to keep the main loop responsive.
        # For PGVector, AsyncEngine is loop-bound: create a fresh instance inside
        # the thread's own loop rather than reusing vs_for_query.
        def _sync_build_vs():
            new_loop = asyncio.new_event_loop()
            try:
                if vs_type == "pgvector":
                    vs_instance = make_vector_store(
                        vs_type=vs_type,
                        db_path=vs_path,
                        embedding_model_name=kb.embedding_model,
                        vs_connection_string=vs_conn,
                        table_name=f"rag_{kb.id.replace('-', '_')}",
                    )
                else:
                    vs_instance = vs_for_query  # ChromaDB: safe to reuse across threads
                return new_loop.run_until_complete(
                    build_vector_store(
                        chunks=text_chunks,
                        embedding_model=emb,
                        db_path=vs_path or VS_PATH,
                        reset=reset,
                        on_embed_progress=_on_embed_progress,
                        batch_size=kb.embedding_batch_size,
                        vector_store=vs_instance,
                        existing_hashes=existing_hashes,
                        use_task_prefix=kb.nomic_prefix,
                    )
                )
            finally:
                new_loop.close()

        await loop.run_in_executor(None, _sync_build_vs)

        # Image store — only when indexing toggle is on and images were extracted.
        if kb.image_indexing_enabled and image_chunks:
            log.info(f"Indexing {len(image_chunks)} image chunk(s) into vs_image …")
            existing_image_hashes: set[str] = set()
            if not reset:
                image_vs_for_query = make_vector_store(
                    vs_type=vs_type,
                    db_path=Path(kb.vs_path + "_images")
                    if vs_type == "chromadb"
                    else None,
                    embedding_model_name=kb.image_embedding_model,
                    vs_connection_string=vs_conn,
                    table_name=f"rag_{kb.id.replace('-', '_')}_images",
                )
                existing_image_hashes = await image_vs_for_query.get_file_hashes()

            image_emb = build_embedding_model("qwen3vl", kb.image_embedding_model)

            def _sync_build_image_vs():
                new_loop = asyncio.new_event_loop()
                try:
                    image_vs = make_vector_store(
                        vs_type=vs_type,
                        db_path=Path(kb.vs_path + "_images")
                        if vs_type == "chromadb"
                        else None,
                        embedding_model_name=kb.image_embedding_model,
                        vs_connection_string=vs_conn,
                        table_name=f"rag_{kb.id.replace('-', '_')}_images",
                    )
                    return new_loop.run_until_complete(
                        build_vector_store(
                            chunks=image_chunks,
                            embedding_model=image_emb,
                            vector_store=image_vs,
                            reset=reset,
                            existing_hashes=existing_image_hashes,
                            use_task_prefix=False,
                        )
                    )
                finally:
                    new_loop.close()

            await loop.run_in_executor(None, _sync_build_image_vs)
            log.info(
                f"Image indexing complete: {len(image_chunks)} chunk(s) processed."
            )

        n_files = len(Counter(c.metadata.get("source_file", "?") for c in chunks))
        log.info(
            f"Ingestion complete: {n_files} new files embedded, "
            f"{n_skipped_store} skipped (already in store), "
            f"{n_skipped_batch} skipped (duplicate in batch)"
        )

        if db_engine is not None:
            chunks_per_file: dict[str, int] = {}
            for c in chunks:
                src = c.metadata.get("source_file", "")
                chunks_per_file[src] = chunks_per_file.get(src, 0) + 1
            for f in filtered_files:
                status = "ok" if f.name in chunks_per_file else "no_chunks"
                size_mb = round(f.stat().st_size / (1024 * 1024), 3)
                await _write_ingest_event(
                    db_engine, kb.id, f.name, f.name, status,
                    chunks=chunks_per_file.get(f.name, 0), file_size_mb=size_mb,
                )
            for f in skipped_store_files:
                size_mb = round(f.stat().st_size / (1024 * 1024), 3)
                await _write_ingest_event(
                    db_engine, kb.id, f.name, f.name, "skipped", file_size_mb=size_mb,
                )
            for f in skipped_batch_files:
                size_mb = round(f.stat().st_size / (1024 * 1024), 3)
                await _write_ingest_event(
                    db_engine, kb.id, f.name, f.name, "skipped_duplicate", file_size_mb=size_mb,
                )
        if text_chunks:
            try:
                write_kb_stats(
                    db_dir=db_dir,
                    kb_id=kb.id,
                    chunks=text_chunks,
                    was_reset=reset,
                    files_added=n_files,
                    files_skipped_store=n_skipped_store,
                    files_skipped_batch=n_skipped_batch,
                )
            except Exception as exc:
                log.warning(f"Failed to write KB stats for '{kb.id}': {exc}")
        return len(chunks), n_files, n_skipped_store, n_skipped_batch
    except _IndexingCancelled:
        log.info("Indexing cancelled by user request.")
        return 0, 0, 0, 0
    finally:
        _cancel_requested = False
        _index_status["indexing"] = False


async def _write_ingest_event(
    db_engine: Any,
    kb_id: str,
    document_id: str,
    filename: str,
    status: str,
    *,
    chunks: int | None = None,
    file_size_mb: float | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    try:
        from sqlalchemy import text as _sa_text
        ts = datetime.now(timezone.utc).isoformat()
        async with db_engine.begin() as conn:
            await conn.execute(
                _sa_text(
                    "INSERT INTO ingest_events "
                    "(ts, kb_id, document_id, filename, status, chunks, file_size_mb, duration_ms, error) "
                    "VALUES (:ts, :kb_id, :document_id, :filename, :status, :chunks, :file_size_mb, :duration_ms, :error)"
                ),
                {
                    "ts": ts, "kb_id": kb_id, "document_id": document_id,
                    "filename": filename, "status": status,
                    "chunks": chunks, "file_size_mb": file_size_mb,
                    "duration_ms": duration_ms, "error": error,
                },
            )
    except Exception as exc:
        log.warning(f"Failed to write ingest event: {exc}")


async def ingest_uploaded_file(
    file_path: Path,
    kb: KBInfo,
    extra_metadata: dict,
    *,
    vs: Any,
    kb_router: Any,
    db_dir: Path,
    db_engine: Any = None,
    cfg: RagConfig | None = None,
) -> None:
    """Ingest a single uploaded file into the active KB, then delete the temp file.

    Replaces existing chunks for the same document_id before inserting new ones.
    Called by the upload worker — never call directly from request handlers.
    """
    document_id: str = extra_metadata["document_id"]
    source_name = extra_metadata.get("source_file", file_path.name)
    _ingest_start = datetime.now(timezone.utc)
    _size_mb: float | None = None
    _event_written = False
    _index_status.update({
        "indexing": True,
        "phase": "loading",
        "current_file": source_name,
        "file_index": 0,
        "total_files": 1,
        "chunks_so_far": 0,
        "embed_batch": 0,
        "embed_total_batches": 0,
        "captioning_enabled": kb.image_captioning_enabled,
        "kb_name": kb.name,
        "finished_at": "",
        "last_result": None,
    })

    loop = asyncio.get_event_loop()
    try:
        deleted = await vs.delete_chunks_by_document_id(document_id)
        if deleted:
            log.info(f"Removed {deleted} existing chunk(s) for document_id='{document_id}'")

        h = await loop.run_in_executor(None, lambda: file_hash(file_path))

        size_mb = file_path.stat().st_size / 1_048_576
        _size_mb = size_mb
        ram_gb = _available_ram_gb()
        ram_info = f", {ram_gb:.1f} GB RAM free" if ram_gb is not None else ""
        if size_mb > 50:
            log.warning(
                f"Large file upload: '{source_name}' is {size_mb:.1f} MB{ram_info} "
                f"(document_id='{document_id}') — high OOM risk during chunking"
            )
        else:
            log.info(f"Chunking '{source_name}': {size_mb:.1f} MB{ram_info} (document_id='{document_id}')")
        pool = _get_chunking_pool()
        future = pool.submit(
            _chunking_pool_fn,
            str(file_path), h,
            kb.pdf_ocr_enabled,
            getattr(kb, "max_chunk_tokens", 0),
            kb.image_indexing_enabled or kb.image_captioning_enabled,
        )
        # Scale timeout with file size: large documents need proportionally more time.
        # Floor of 600s; ~60s per MB above that (19 MB → ~1140s, 50 MB → 3000s).
        chunk_timeout = max(600, int(size_mb * 60))
        try:
            chunks = await loop.run_in_executor(None, lambda: future.result(timeout=chunk_timeout))
        except concurrent.futures.TimeoutError:
            log.error(
                f"Chunking subprocess timed out after {chunk_timeout}s for '{source_name}' — "
                "killing worker pool. Backend is safe; upload skipped."
            )
            _reset_chunking_pool()
            if db_engine is not None:
                _ms = int((datetime.now(timezone.utc) - _ingest_start).total_seconds() * 1000)
                await _write_ingest_event(
                    db_engine, kb.id, document_id, source_name, "timeout",
                    file_size_mb=_size_mb, duration_ms=_ms,
                    error=f"Chunking timed out after {chunk_timeout}s",
                )
                _event_written = True
            raise RuntimeError(f"Chunking timed out for '{source_name}'")
        except concurrent.futures.BrokenExecutor:
            _reset_chunking_pool()
            if db_engine is not None:
                _ms = int((datetime.now(timezone.utc) - _ingest_start).total_seconds() * 1000)
                await _write_ingest_event(
                    db_engine, kb.id, document_id, source_name, "crashed",
                    file_size_mb=_size_mb, duration_ms=_ms,
                    error="Chunking subprocess crashed (glibc heap corruption)",
                )
                _event_written = True
            raise RuntimeError(
                f"Chunking subprocess crashed for '{source_name}' — "
                "glibc heap corruption in docling native code. "
                "Backend is safe; upload skipped."
            )
        _clear_cuda_cache()  # free CUDA memory in main process before embedding

        if not chunks:
            log.warning(f"No chunks produced from uploaded file '{file_path.name}'")
            if db_engine is not None:
                _ms = int((datetime.now(timezone.utc) - _ingest_start).total_seconds() * 1000)
                await _write_ingest_event(
                    db_engine, kb.id, document_id, source_name, "no_chunks",
                    chunks=0, file_size_mb=_size_mb, duration_ms=_ms,
                )
            return

        for chunk in chunks:
            chunk.metadata.update(extra_metadata)

        text_chunks = [c for c in chunks if c.mime_type.startswith("text")]
        image_chunks = [c for c in chunks if c.mime_type.startswith("image")]

        if kb.image_captioning_enabled and image_chunks and cfg is not None:
            _index_status["phase"] = "captioning"
            caption_llm = build_llm(
                backend=cfg.llm_backend,
                model_name=cfg.llm_model or None,
                temperature=0.1,
                ollama_host=cfg.ollama_host or None,
                custom_base_url=cfg.custom_base_url or "",
                custom_api_key=cfg.custom_api_key or "",
                max_tokens=512,
            )
            await _caption_image_chunks(text_chunks, image_chunks, caption_llm)
            image_chunks = []
        elif kb.image_captioning_enabled and image_chunks and cfg is None:
            log.warning(
                "Image captioning is enabled on this KB but no session config was provided — "
                "skipping captioning for this upload."
            )

        emb = _get_cached_embedding_model(kb)
        _index_status["phase"] = "embedding"

        def _on_embed_progress(batch_idx: int, total_batches: int) -> None:
            _index_status.update({"embed_batch": batch_idx, "embed_total_batches": total_batches})

        def _sync_embed_insert():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(
                    build_vector_store(
                        chunks=text_chunks,
                        embedding_model=emb,
                        db_path=Path(kb.vs_path),
                        reset=False,
                        on_embed_progress=_on_embed_progress,
                        batch_size=kb.embedding_batch_size,
                        vector_store=vs,
                        existing_hashes=set(),  # replacement already handled above
                        use_task_prefix=kb.nomic_prefix,
                    )
                )
            finally:
                new_loop.close()

        await loop.run_in_executor(None, _sync_embed_insert)
        _clear_cuda_cache()  # release GPU memory held by embedding between documents

        if kb.image_indexing_enabled and image_chunks:
            vs_type = getattr(kb, "vs_type", "chromadb") or "chromadb"
            vs_conn = getattr(kb, "vs_connection_string", "") or ""
            image_vs_path = Path(kb.vs_path + "_images") if vs_type == "chromadb" else None
            image_vs_for_delete = make_vector_store(
                vs_type=vs_type,
                db_path=image_vs_path,
                embedding_model_name=kb.image_embedding_model,
                vs_connection_string=vs_conn,
                table_name=f"rag_{kb.id.replace('-', '_')}_images",
            )
            deleted_images = await image_vs_for_delete.delete_chunks_by_document_id(document_id)
            if deleted_images:
                log.info(f"Removed {deleted_images} existing image chunk(s) for document_id='{document_id}'")
            image_emb = build_embedding_model("qwen3vl", kb.image_embedding_model)

            def _sync_embed_images():
                new_loop = asyncio.new_event_loop()
                try:
                    image_vs = make_vector_store(
                        vs_type=vs_type,
                        db_path=image_vs_path,
                        embedding_model_name=kb.image_embedding_model,
                        vs_connection_string=vs_conn,
                        table_name=f"rag_{kb.id.replace('-', '_')}_images",
                    )
                    return new_loop.run_until_complete(
                        build_vector_store(
                            chunks=image_chunks,
                            embedding_model=image_emb,
                            vector_store=image_vs,
                            reset=False,
                            existing_hashes=set(),
                            use_task_prefix=False,
                        )
                    )
                finally:
                    new_loop.close()

            await loop.run_in_executor(None, _sync_embed_images)
            log.info(f"Image upload ingest complete: {len(image_chunks)} image chunk(s).")

        if text_chunks:
            try:
                write_kb_stats(
                    db_dir=db_dir,
                    kb_id=kb.id,
                    chunks=text_chunks,
                    was_reset=False,
                    files_added=1,
                    files_skipped_store=0,
                    files_skipped_batch=0,
                )
            except Exception as exc:
                log.warning(f"Failed to write KB stats for '{kb.id}': {exc}")

        try:
            total_count = await vs.count()
            total_files = len(await vs.get_source_files())
        except Exception:
            total_count = len(chunks)
            total_files = 1
        kb_router.update_stats(kb.id, total_count, total_files)

        result = ReindexResult(
            chunks_indexed=len(chunks),
            files_processed=1,
            files_skipped=0,
            files_skipped_store=0,
            files_skipped_batch=0,
            reset=False,
        )
        _index_status["last_result"] = result.model_dump()
        _index_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        log.info(
            f"Upload ingest complete: {len(chunks)} chunks from '{file_path.name}' "
            f"(document_id='{document_id}')"
        )
        if db_engine is not None:
            _ms = int((datetime.now(timezone.utc) - _ingest_start).total_seconds() * 1000)
            await _write_ingest_event(
                db_engine, kb.id, document_id, source_name, "success",
                chunks=len(chunks), file_size_mb=_size_mb, duration_ms=_ms,
            )
            _event_written = True
    except Exception as exc:
        log.error(f"Upload ingestion failed for '{file_path.name}': {exc!r}")
        if db_engine is not None and not _event_written:
            _ms = int((datetime.now(timezone.utc) - _ingest_start).total_seconds() * 1000)
            await _write_ingest_event(
                db_engine, kb.id, document_id, source_name, "crashed",
                file_size_mb=_size_mb, duration_ms=_ms,
                error=str(exc)[:500],
            )
        raise
    finally:
        _index_status["indexing"] = False
        file_path.unlink(missing_ok=True)


async def enqueue_upload(
    file_path: Path,
    kb: KBInfo,
    extra_metadata: dict,
    *,
    vs: Any,
    kb_router: Any,
    db_dir: Path,
    db_engine: Any = None,
    cfg: RagConfig | None = None,
) -> None:
    """Queue a file for ingestion. Returns immediately; processing is serialised."""
    _index_status["queued"] = _upload_queue.qsize() + 1
    await _upload_queue.put((file_path, kb, extra_metadata, vs, kb_router, db_dir, db_engine, cfg))


async def upload_worker() -> None:
    """Drain the upload queue one file at a time. Start once at app startup."""
    while True:
        file_path, kb, extra_metadata, vs, kb_router, db_dir, db_engine, cfg = await _upload_queue.get()
        _index_status["queued"] = _upload_queue.qsize()
        try:
            await ingest_uploaded_file(
                file_path, kb, extra_metadata,
                vs=vs,
                kb_router=kb_router,
                db_dir=db_dir,
                db_engine=db_engine,
                cfg=cfg,
            )
        except Exception:
            pass  # already logged inside ingest_uploaded_file
        finally:
            _upload_queue.task_done()
