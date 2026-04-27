import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


_CAPTIONING_PROMPT = (
    "You are captioning an image for a document retrieval system.\n\n"
    "1. Extract all visible text exactly as it appears (labels, numbers, headings, table cells, legends). "
    "If there is no visible text, write \"none\".\n"
    "2. In 2-3 sentences, describe what the image shows. Only describe what is visually present — "
    "no interpretation, no speculation, no background knowledge.\n\n"
    "Respond in this exact format:\n"
    "VISIBLE TEXT: <extracted text or \"none\">\n"
    "DESCRIPTION: <visual description>"
)


async def _caption_image_chunks(
    text_chunks: list[Chunk],
    image_chunks: list[Chunk],
    llm: LLM,
) -> None:
    """Replace <!-- image --> placeholders in text_chunks with LLM-generated captions.

    Mutates text_chunks in-place. Images without a matching placeholder are appended
    as new standalone text chunks.
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

    async def _call_llm(image_chunk: Chunk) -> str:
        user_msg = LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(type="text", text=_CAPTIONING_PROMPT),
                MessageContent(type="image", image_url=image_chunk.content),
            ],
        )
        response = await llm.generate([user_msg])
        return "".join(mc.text or "" for mc in response.content).strip()

    for k in range(n_to_replace):
        _index_status["caption_index"] = k + 1
        chunk_idx, _ = placeholders[k]
        source = image_chunks[k].metadata.get("source_file", "image")
        log.info(f"Captioning image {k + 1}/{n_images} from '{source}' …")
        try:
            caption = await _call_llm(image_chunks[k])
            text_chunks[chunk_idx].content = text_chunks[chunk_idx].content.replace(
                "<!-- image -->",
                f"<!-- image content -->\n{caption}\n<!-- end image content -->",
                1,
            )
        except Exception as exc:
            log.error(f"Captioning failed for image {k + 1} from '{source}': {exc}")

    # Images beyond available placeholders become standalone text chunks.
    for k in range(n_to_replace, n_images):
        _index_status["caption_index"] = k + 1
        source = image_chunks[k].metadata.get("source_file", "image")
        log.info(f"Captioning standalone image {k + 1}/{n_images} from '{source}' …")
        try:
            caption = await _call_llm(image_chunks[k])
            text_chunks.append(
                Chunk(
                    title=image_chunks[k].title,
                    content=f"<!-- image content -->\n{caption}\n<!-- end image content -->",
                    mime_type="text/markdown",
                    metadata=image_chunks[k].metadata.copy(),
                )
            )
        except Exception as exc:
            log.error(f"Captioning failed for standalone image {k + 1} from '{source}': {exc}")


class _IndexingCancelled(Exception):
    pass


def cancel_indexing() -> None:
    global _cancel_requested
    _cancel_requested = True


async def run_ingestion(
    kb: KBInfo, reset: bool, db_dir: Path, cfg: RagConfig | None = None
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
        def _prepass() -> tuple[list[Path], dict[Path, str], int, int]:
            candidates = _collect_candidate_files(
                data_dirs,
                max_file_size_mb=kb.max_file_size_mb,
                max_files=None,
            )
            hashes: dict[Path, str] = {}
            for f in candidates:
                hashes[f] = file_hash(f)

            filtered: list[Path] = []
            seen_hashes: set[str] = set()
            n_skipped_store = 0
            n_skipped_batch = 0

            for f in candidates:
                h = hashes[f]
                if h in existing_hashes:
                    log.info(f"Skipping {f.name!r} — already in store (hash={h[:8]}…)")
                    n_skipped_store += 1
                elif h in seen_hashes:
                    log.warning(
                        f"Skipping {f.name!r} — duplicate content in batch (hash={h[:8]}…)"
                    )
                    n_skipped_batch += 1
                else:
                    seen_hashes.add(h)
                    filtered.append(f)

            log.info(
                f"Pre-pass complete: {len(filtered)} to index, "
                f"{n_skipped_store} already in store, "
                f"{n_skipped_batch} duplicate in batch"
            )
            return filtered, hashes, n_skipped_store, n_skipped_batch

        (
            filtered_files,
            file_hashes_map,
            n_skipped_store,
            n_skipped_batch,
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
                write_images=kb.image_indexing_enabled,
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
            )
            await _caption_image_chunks(text_chunks, image_chunks, caption_llm)
            image_chunks = []  # consumed by captioning; not stored in image VS
        elif kb.image_captioning_enabled and image_chunks and cfg is None:
            log.warning(
                "Image captioning is enabled on this KB but no session config was provided — "
                "skipping captioning. Re-index via the UI to caption images."
            )

        emb = build_embedding_model(
            kb.embedding_backend,
            kb.embedding_model,
            ollama_host=kb.embedding_ollama_host or "",
            custom_base_url=kb.embedding_custom_base_url or "",
            custom_api_key=kb.embedding_custom_api_key or "",
        )
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


async def ingest_uploaded_file(
    file_path: Path,
    kb: KBInfo,
    extra_metadata: dict,
    *,
    vs_proxy: Any,
    kb_router: Any,
    db_dir: Path,
    cfg: RagConfig | None = None,
) -> None:
    """Ingest a single uploaded file into the active KB, then delete the temp file.

    Replaces existing chunks for the same document_id before inserting new ones.
    Called by the upload worker — never call directly from request handlers.
    """
    document_id: str = extra_metadata["document_id"]
    _index_status.update({
        "indexing": True,
        "phase": "loading",
        "current_file": extra_metadata.get("source_file", file_path.name),
        "file_index": 0,
        "total_files": 1,
        "chunks_so_far": 0,
        "embed_batch": 0,
        "embed_total_batches": 0,
        "kb_name": kb.name,
        "finished_at": "",
        "last_result": None,
    })

    loop = asyncio.get_event_loop()
    try:
        vs = object.__getattribute__(vs_proxy, "_obj")
        deleted = await vs.delete_chunks_by_document_id(document_id)
        if deleted:
            log.info(f"Removed {deleted} existing chunk(s) for document_id='{document_id}'")

        h = await loop.run_in_executor(None, lambda: file_hash(file_path))

        log.info(f"Chunking '{extra_metadata.get('source_file', file_path.name)}' (document_id='{document_id}')")
        chunks = await loop.run_in_executor(
            None,
            lambda: load_chunks(
                include_files=[file_path],
                file_hashes={file_path: h},
                pdf_ocr_enabled=kb.pdf_ocr_enabled,
                max_chunk_tokens=getattr(kb, "max_chunk_tokens", 0),
                write_images=kb.image_indexing_enabled,
            ),
        )

        if not chunks:
            log.warning(f"No chunks produced from uploaded file '{file_path.name}'")
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
            )
            await _caption_image_chunks(text_chunks, image_chunks, caption_llm)
            image_chunks = []
        elif kb.image_captioning_enabled and image_chunks and cfg is None:
            log.warning(
                "Image captioning is enabled on this KB but no session config was provided — "
                "skipping captioning for this upload."
            )

        emb = build_embedding_model(
            kb.embedding_backend,
            kb.embedding_model,
            ollama_host=kb.embedding_ollama_host or "",
            custom_base_url=kb.embedding_custom_base_url or "",
            custom_api_key=kb.embedding_custom_api_key or "",
        )
        _index_status["phase"] = "embedding"

        def _on_embed_progress(batch_idx: int, total_batches: int) -> None:
            _index_status.update({"embed_batch": batch_idx, "embed_total_batches": total_batches})

        def _sync_embed_insert():
            new_loop = asyncio.new_event_loop()
            try:
                vs_instance = object.__getattribute__(vs_proxy, "_obj")
                return new_loop.run_until_complete(
                    build_vector_store(
                        chunks=text_chunks,
                        embedding_model=emb,
                        db_path=Path(kb.vs_path),
                        reset=False,
                        on_embed_progress=_on_embed_progress,
                        batch_size=kb.embedding_batch_size,
                        vector_store=vs_instance,
                        existing_hashes=set(),  # replacement already handled above
                    )
                )
            finally:
                new_loop.close()

        await loop.run_in_executor(None, _sync_embed_insert)

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
    except Exception as exc:
        log.error(f"Upload ingestion failed for '{file_path.name}': {exc!r}")
        raise
    finally:
        _index_status["indexing"] = False
        file_path.unlink(missing_ok=True)


async def enqueue_upload(
    file_path: Path,
    kb: KBInfo,
    extra_metadata: dict,
    *,
    vs_proxy: Any,
    kb_router: Any,
    db_dir: Path,
    cfg: RagConfig | None = None,
) -> None:
    """Queue a file for ingestion. Returns immediately; processing is serialised."""
    _index_status["queued"] = _upload_queue.qsize() + 1
    await _upload_queue.put((file_path, kb, extra_metadata, vs_proxy, kb_router, db_dir, cfg))


async def upload_worker() -> None:
    """Drain the upload queue one file at a time. Start once at app startup."""
    while True:
        file_path, kb, extra_metadata, vs_proxy, kb_router, db_dir, cfg = await _upload_queue.get()
        _index_status["queued"] = _upload_queue.qsize()
        try:
            await ingest_uploaded_file(
                file_path, kb, extra_metadata,
                vs_proxy=vs_proxy,
                kb_router=kb_router,
                db_dir=db_dir,
                cfg=cfg,
            )
        except Exception:
            pass  # already logged inside ingest_uploaded_file
        finally:
            _upload_queue.task_done()
