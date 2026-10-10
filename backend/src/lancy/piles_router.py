"""
Piles — user-owned document sets, processed on demand into their own vector store.

A pile is a directory of raw uploads plus, once processed, a vector store built
from them. Structurally that is what a Knowledge Base already is, so processing a
pile hands a synthetic `KBInfo` to the normal ingestion pipeline and inherits its
hash deduplication, OCR, chunking and image handling unchanged.

Piles are deliberately absent from `knowledge_bases.json`: that registry belongs to
the admin and is rewritten wholesale, while piles are created and discarded per
user. Their bookkeeping lives in the `piles` / `pile_files` tables instead.

The embedding configuration, store backend and chunking parameters are copied from
the active KB at process time, so a pile always matches the embedding model the rest
of the app is running. The pair it was built with is recorded on the pile row; a
pile whose `embedding_key` no longer matches the active KB has to be reprocessed
before it can be used.

Endpoints (all scoped to the calling session — another owner's pile is a 404):
    GET    /api/v1/piles                           — list my piles
    POST   /api/v1/piles                           — create a pile
    GET    /api/v1/piles/{id}                      — pile detail + file list
    DELETE /api/v1/piles/{id}                      — delete pile, store and raw files
    POST   /api/v1/piles/{id}/files                — upload a file into the pile
    DELETE /api/v1/piles/{id}/files/{filename}     — remove a file
    POST   /api/v1/piles/{id}/process              — chunk + embed the pile
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from lancy.database import (
    add_pile_file,
    begin_pile_processing,
    create_pile,
    delete_pile,
    delete_pile_file,
    finish_pile_processing,
    get_pile,
    init_db,
    list_pile_files,
    list_piles,
    pile_usage,
    set_pile_status,
)
from lancy.feature0_baseline_rag import _CHUNKERS, _IMAGE_EXTENSIONS, make_vector_store
from lancy.kb_router import KBInfo

log = logging.getLogger("uvicorn")

_SUPPORTED_EXTS = set(_CHUNKERS) | _IMAGE_EXTENSIONS

# Identity a pile is owned by, when the request carries no session id. Matches
# SessionCookieProvider's fallback, so Mode 1 deployments — which never issue a
# session cookie — still work and scope piles the way conversation history already is.
_DEFAULT_OWNER = "admin"


async def _dispose_vs(vs_instance: object) -> None:
    """Close a vector store's asyncpg connection pool, if it has one.

    Mirrors ingestion._dispose_vs rather than importing it: ingestion imports
    rag_router, and rag_router has to be able to reach this module's pile store
    opener — the same reason kb_pool keeps its own copy.
    """
    try:
        engine = getattr(vs_instance, "engine", None)
        if engine is not None:
            await engine.dispose()
    except Exception as exc:
        log.warning(f"Pile: vector store dispose failed: {exc}")


# Bounds on what one session can accumulate. A pile costs disk for the raw files
# and again for its vectors, and processing occupies the one embedding model the
# whole app shares — so this is capped rather than left open.
MAX_PILES_PER_OWNER = 20
MAX_FILES_PER_PILE = 200
MAX_BYTES_PER_OWNER = 2 * 1024**3  # 2 GiB of raw uploads across all of an owner's piles

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


# ─── Data models ──────────────────────────────────────────────────────────────


class PileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class PileFileInfo(BaseModel):
    filename: str
    source_file: str
    uploaded_at: str
    size_bytes: int


class PileInfo(BaseModel):
    id: str
    name: str
    created_at: str
    processed_at: str | None = None
    status: str
    error: str = ""
    embedding_key: str = ""
    vs_type: str = ""
    chunk_count: int = 0
    file_count: int = 0
    pending_files: int = 0  # files currently on disk, processed or not

    # Two separate questions, because the answers differ by consumer. Batch
    # analysis reads chunk text by metadata and never embeds anything, so a
    # store built with a since-replaced embedding model still works for it.
    # Retrieval compares vectors and does not.
    has_store: bool = False  # processed at least once — chunks exist
    embedding_current: bool = False  # those vectors match the active embedding config


class PileDetail(PileInfo):
    files: list[PileFileInfo] = Field(default_factory=list)


# ─── Callback types ───────────────────────────────────────────────────────────

# Runs the ingestion pipeline over a pile's synthetic KB; returns (chunks, files).
# The bool is `reset` — rebuild the store from scratch rather than extend it.
ProcessCallback = Callable[[KBInfo, bool], Awaitable[tuple[int, int]]]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def embedding_key(kb: KBInfo) -> str:
    """The (backend, model) pair a store's vectors were produced with.

    Same spelling as KBPool's conflict message, so the two are comparable by eye
    in logs and in the UI.
    """
    return f"{kb.embedding_backend}/{kb.embedding_model}"


def pile_owner_id(request: Request) -> str:
    """Identity a pile is owned by: the per-session id the Next.js middleware injects.

    Every consumer of piles has to agree on who the caller is, or ownership checks
    diverge between endpoints — so this and `open_pile_store` share one fallback.
    """
    return request.headers.get("x-session-id") or _DEFAULT_OWNER


def pile_vs_path(db_dir: Path, pile_id: str) -> Path:
    return db_dir / f"vs_pile_{pile_id}"


def pile_kb_id(pile_id: str) -> str:
    # uuid4().hex has no dashes, so this is already a legal pgvector identifier
    # once prefixed — the table lands at rag_pile_<hex>.
    return f"pile_{pile_id}"


@asynccontextmanager
async def open_pile_store(
    db_dir: Path,
    pile_id: str,
    owner_id: str | None,
    get_active_kb: Callable[[], KBInfo],
) -> AsyncIterator[object | None]:
    """Open a pile's vector store directly, without going through the KB pool.

    Piles are never pool residents: batch analysis reads their chunks by metadata
    and needs no retrievers, no BM25 index and no embedding model.

    Yields None when the pile does not exist, belongs to someone else, or has never
    been processed — the ownership check is the lookup itself, so a caller cannot
    reach another owner's pile by guessing its id. A pgvector store is disposed on
    exit; it owns an asyncpg pool that nothing else will reclaim.
    """
    sqlite_path = db_dir / "user_config.db"
    pile = get_pile(sqlite_path, pile_id, owner_id or _DEFAULT_OWNER)
    if pile is None or not pile["vs_type"]:
        yield None
        return

    vs_type = pile["vs_type"]
    # embedding_key is "<backend>/<model>", and model names contain slashes of their
    # own ("nomic-ai/nomic-embed-text-v1") — so split once, from the left.
    _, _, model_name = pile["embedding_key"].partition("/")
    conn_str = ""
    if vs_type == "pgvector":
        try:
            conn_str = get_active_kb().vs_connection_string or ""
        except Exception as exc:
            log.warning(f"Pile {pile_id}: could not read active KB connection string: {exc}")
        if not conn_str:
            yield None
            return

    vs = make_vector_store(
        vs_type=vs_type,
        db_path=pile_vs_path(db_dir, pile_id) if vs_type == "chromadb" else None,
        embedding_model_name=model_name,
        vs_connection_string=conn_str,
        table_name=f"rag_{pile_kb_id(pile_id)}",
    )
    try:
        yield vs
    finally:
        if vs_type == "pgvector":
            await _dispose_vs(vs)


def _safe_filename(original: str, taken: set[str]) -> str:
    """A disk-safe, pile-unique name that keeps its extension.

    The extension has to survive: `_collect_candidate_files` selects by suffix, so
    a mangled one silently drops the file from every future ingestion run.
    """
    p = Path(Path(original).name)  # Path(...).name first — strips any directory part
    suffix = _SAFE_NAME.sub("", p.suffix)[:20]
    stem = _SAFE_NAME.sub("_", p.stem).strip("._")[:120] or "upload"
    candidate = f"{stem}{suffix}"
    n = 2
    while candidate in taken:
        candidate = f"{stem}_{n}{suffix}"
        n += 1
    return candidate


async def _drop_pgvector_tables(conn_str: str, table_base: str) -> None:
    """Drop a pile's pgvector tables.

    `table_base` is interpolated, not bound — DDL cannot take a parameter. It is safe
    because it is derived from a pile id that only ever comes from `uuid4().hex` and
    has just been found in the `piles` table; a caller-supplied id that is not in
    there never reaches this function.

    Mirrors the teardown in kb_router.delete_kb rather than sharing it: extracting
    a common helper would mean rewriting that endpoint, which is outside what this
    feature needs to touch.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    conn = conn_str.strip()
    if conn.startswith("postgresql://"):
        conn = conn.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif conn.startswith("postgres://"):
        conn = conn.replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(conn)
    try:
        async with engine.begin() as c:
            await c.execute(text(f"DROP TABLE IF EXISTS {table_base}"))
            await c.execute(text(f"DROP TABLE IF EXISTS {table_base}_images"))
    finally:
        await engine.dispose()


# ─── Router factory ───────────────────────────────────────────────────────────


def create_piles_router(
    db_dir: Path,
    piles_dir: Path,
    get_active_kb: Callable[[], KBInfo],
    process_callback: ProcessCallback,
    status_factory: Callable[[], dict] | None = None,
) -> APIRouter:
    """
    Args:
        db_dir:           Directory holding user_config.db and the pile vector stores.
        piles_dir:        Directory holding raw uploads, one subdirectory per pile.
        get_active_kb:    Zero-arg callable returning the active KBInfo — the template
                          a pile's embedding, store and chunking config is copied from.
        process_callback: Async callable(KBInfo) -> (chunks, files) running ingestion.
        status_factory:   Returns the global index status. Processing a pile uses the
                          same embedding model and chunking pool as an admin reindex,
                          so the two must not overlap.
    """
    router = APIRouter(prefix="/api/v1")
    sqlite_path = db_dir / "user_config.db"
    db_dir.mkdir(parents=True, exist_ok=True)
    piles_dir.mkdir(parents=True, exist_ok=True)
    # Idempotent, and it keeps this router usable without depending on the order
    # routers happen to be constructed in.
    init_db(sqlite_path)

    _owner = pile_owner_id

    def _pile_dir(pile_id: str) -> Path:
        return piles_dir / pile_id

    def _vs_path(pile_id: str) -> Path:
        return pile_vs_path(db_dir, pile_id)

    def _kb_id(pile_id: str) -> str:
        return pile_kb_id(pile_id)

    def _require_pile(pile_id: str, owner_id: str) -> dict:
        pile = get_pile(sqlite_path, pile_id, owner_id)
        if pile is None:
            # 404 rather than 403 for someone else's pile: a 403 would confirm it exists.
            raise HTTPException(404, "Pile not found")
        return pile

    def _to_info(pile: dict, active_key: str) -> dict:
        return {
            **{
                k: pile[k]
                for k in (
                    "id",
                    "name",
                    "created_at",
                    "processed_at",
                    "status",
                    "error",
                    "embedding_key",
                    "vs_type",
                    "chunk_count",
                    "file_count",
                )
            },
            "pending_files": len(list_pile_files(sqlite_path, pile["id"])),
            # "stale" counts: its store holds chunks for the files that were present
            # at the last run, which is exactly what batch analysis reads.
            "has_store": pile["status"] in ("ready", "stale"),
            "embedding_current": bool(pile["embedding_key"]) and pile["embedding_key"] == active_key,
        }

    def _active_key() -> str:
        try:
            return embedding_key(get_active_kb())
        except Exception as exc:
            log.warning(f"Piles: could not read active KB embedding config: {exc}")
            return ""

    def _pile_kb(pile: dict) -> KBInfo:
        """The synthetic KBInfo the ingestion pipeline processes this pile through.

        Everything about how documents are turned into vectors is inherited from the
        active KB, so a pile is always compatible with the running embedding model
        and follows the same chunking policy as the admin corpus.
        """
        active = get_active_kb()
        pile_id = pile["id"]
        return KBInfo(
            id=_kb_id(pile_id),
            # Surfaces in the shared index-status badge while processing runs. The
            # pile's own name is left out on purpose — that badge is visible to
            # everyone, and it is not the place to publish one user's labels.
            name="User pile",
            vs_path=str(_vs_path(pile_id)),
            data_dirs=[str(_pile_dir(pile_id))],
            embedding_backend=active.embedding_backend,
            embedding_model=active.embedding_model,
            embedding_ollama_host=active.embedding_ollama_host,
            embedding_custom_base_url=active.embedding_custom_base_url,
            embedding_custom_api_key=active.embedding_custom_api_key,
            nomic_prefix=active.nomic_prefix,
            max_file_size_mb=active.max_file_size_mb,
            embedding_batch_size=active.embedding_batch_size,
            pdf_ocr_enabled=active.pdf_ocr_enabled,
            max_chunk_tokens=active.max_chunk_tokens,
            vs_type=active.vs_type,
            vs_connection_string=active.vs_connection_string,
            # Batch analysis reads text chunks only, so the image stores are not
            # built for piles. Captioning still applies: it enriches text chunks.
            image_indexing_enabled=False,
            image_retrieval_enabled=False,
            image_captioning_enabled=active.image_captioning_enabled,
        )

    # ── Endpoints ─────────────────────────────────────────────────────────────

    @router.get("/piles", response_model=list[PileInfo])
    async def list_my_piles(request: Request) -> list[dict]:
        active_key = _active_key()
        return [_to_info(p, active_key) for p in list_piles(sqlite_path, _owner(request))]

    @router.post("/piles", response_model=PileInfo)
    async def create_my_pile(request: Request, cfg: PileCreate) -> dict:
        owner_id = _owner(request)
        n_piles, _ = pile_usage(sqlite_path, owner_id)
        if n_piles >= MAX_PILES_PER_OWNER:
            raise HTTPException(409, f"Pile limit reached ({MAX_PILES_PER_OWNER}). Delete one first.")
        pile_id = uuid.uuid4().hex
        try:
            create_pile(sqlite_path, pile_id, owner_id, cfg.name.strip())
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"You already have a pile named '{cfg.name}'")
        _pile_dir(pile_id).mkdir(parents=True, exist_ok=True)
        log.info(f"Pile created (id={pile_id})")
        return _to_info(_require_pile(pile_id, owner_id), _active_key())

    @router.get("/piles/{pile_id}", response_model=PileDetail)
    async def get_my_pile(pile_id: str, request: Request) -> dict:
        pile = _require_pile(pile_id, _owner(request))
        return {
            **_to_info(pile, _active_key()),
            "files": list_pile_files(sqlite_path, pile_id),
        }

    @router.delete("/piles/{pile_id}")
    async def delete_my_pile(pile_id: str, request: Request) -> dict:
        pile = _require_pile(pile_id, _owner(request))
        if pile["status"] == "processing":
            raise HTTPException(409, "Pile is being processed — wait for it to finish")

        # Bookkeeping first: once the rows are gone the pile is unreachable, so a
        # failure in the disk teardown below cannot leave a half-deleted pile the
        # user can still see and act on. It can leave orphaned bytes, which is why
        # each step is logged.
        delete_pile(sqlite_path, pile_id, pile["owner_id"])

        if pile["vs_type"] == "pgvector":
            conn_str = get_active_kb().vs_connection_string or ""
            if not conn_str:
                log.warning(
                    f"Pile {pile_id}: pgvector store left in place — no connection string available on the active KB"
                )
            else:
                try:
                    await _drop_pgvector_tables(conn_str, f"rag_{_kb_id(pile_id)}")
                except Exception as exc:
                    log.warning(f"Pile {pile_id}: could not drop pgvector tables: {exc}")
        elif pile["vs_type"] == "chromadb":
            for vs in (_vs_path(pile_id), Path(str(_vs_path(pile_id)) + "_images")):
                if vs.exists():
                    shutil.rmtree(vs, ignore_errors=True)

        shutil.rmtree(_pile_dir(pile_id), ignore_errors=True)
        # run_ingestion writes analytics per KB id; a pile's copy goes with it.
        (db_dir / f"kb_stats_{_kb_id(pile_id)}.json").unlink(missing_ok=True)
        log.info(f"Pile deleted (id={pile_id})")
        return {"deleted": pile_id}

    @router.post("/piles/{pile_id}/files", response_model=PileFileInfo)
    async def upload_to_pile(pile_id: str, request: Request, file: UploadFile = File(...)) -> dict:
        owner_id = _owner(request)
        pile = _require_pile(pile_id, owner_id)
        if pile["status"] == "processing":
            raise HTTPException(409, "Pile is being processed — wait for it to finish")

        existing = list_pile_files(sqlite_path, pile_id)
        if len(existing) >= MAX_FILES_PER_PILE:
            raise HTTPException(409, f"This pile already holds the maximum of {MAX_FILES_PER_PILE} files")

        original = file.filename or "upload"
        suffix = Path(original).suffix.lower()
        if suffix not in _SUPPORTED_EXTS:
            raise HTTPException(
                422,
                f"Unsupported file type '{suffix or original}'. Supported extensions: {sorted(_SUPPORTED_EXTS)}",
            )

        limit_mb = get_active_kb().max_file_size_mb
        _, used_bytes = pile_usage(sqlite_path, owner_id)
        remaining = MAX_BYTES_PER_OWNER - used_bytes

        filename = _safe_filename(original, {f["filename"] for f in existing})
        pile_path = _pile_dir(pile_id)
        pile_path.mkdir(parents=True, exist_ok=True)

        # Streamed to a scratch name and renamed on success, with both limits checked
        # as the bytes arrive. max_file_size_mb reaches 500, so reading the upload into
        # memory to measure it is what would take the process down; and a rename means
        # a rejected or interrupted upload never appears in the pile directory, where
        # the next ingestion run would pick it up.
        scratch = pile_path / f".incoming-{uuid.uuid4().hex}"
        written = 0
        try:
            with scratch.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit_mb * 1_048_576:
                        raise HTTPException(413, f"File too large: exceeds the {limit_mb} MB limit")
                    if written > remaining:
                        raise HTTPException(
                            413,
                            f"Storage limit reached ({MAX_BYTES_PER_OWNER // 1024**3} GiB). "
                            "Delete some files or a pile first.",
                        )
                    out.write(chunk)
            scratch.replace(pile_path / filename)
        except BaseException:
            scratch.unlink(missing_ok=True)
            raise

        add_pile_file(sqlite_path, pile_id, filename, original, written)
        log.info(f"Pile {pile_id}: added '{filename}' ({written / 1_048_576:.1f} MB)")
        row = next(f for f in list_pile_files(sqlite_path, pile_id) if f["filename"] == filename)
        return row

    @router.delete("/piles/{pile_id}/files/{filename:path}")
    async def delete_pile_upload(pile_id: str, filename: str, request: Request) -> dict:
        pile = _require_pile(pile_id, _owner(request))
        if pile["status"] == "processing":
            raise HTTPException(409, "Pile is being processed — wait for it to finish")

        # The stored name is already sanitised, so re-sanitising here is what keeps a
        # crafted path from escaping the pile directory.
        safe = _safe_filename(filename, set())
        if not delete_pile_file(sqlite_path, pile_id, safe):
            raise HTTPException(404, "File not found in this pile")
        (_pile_dir(pile_id) / safe).unlink(missing_ok=True)
        log.info(f"Pile {pile_id}: removed '{safe}'")
        return {"deleted": safe}

    @router.post("/piles/{pile_id}/process")
    async def process_my_pile(pile_id: str, request: Request, background_tasks: BackgroundTasks) -> dict:
        pile = _require_pile(pile_id, _owner(request))
        if pile["status"] == "processing":
            raise HTTPException(409, "Pile is already being processed")
        if not list_pile_files(sqlite_path, pile_id):
            raise HTTPException(400, "Pile is empty — upload a document first")
        if status_factory is not None and status_factory().get("indexing"):
            raise HTTPException(409, "Indexing is already running. Please try again shortly.")

        kb = _pile_kb(pile)
        key = embedding_key(kb)
        # A pile built with a different embedding model has a store whose vectors are
        # not comparable to the current ones (and may differ in dimension), so it is
        # rebuilt from scratch rather than extended.
        reset = bool(pile["embedding_key"]) and pile["embedding_key"] != key

        async def _run() -> None:
            begin_pile_processing(sqlite_path, pile_id, kb.vs_type)
            try:
                chunks, files = await process_callback(kb, reset)
                finish_pile_processing(
                    sqlite_path,
                    pile_id,
                    chunk_count=chunks,
                    file_count=files,
                    embedding_key=key,
                )
                log.info(f"Pile {pile_id} processed: {chunks} chunk(s) from {files} file(s)")
            except Exception as exc:
                # Class name only — the message can carry a connection string or a
                # local path, and this value is rendered in the UI.
                log.exception(f"Pile {pile_id} processing failed")
                set_pile_status(sqlite_path, pile_id, "failed", type(exc).__name__)

        background_tasks.add_task(_run)
        return {"started": True, "reset": reset}

    return router
