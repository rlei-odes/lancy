"""
Knowledge Base management API.

Each Knowledge Base (KB) bundles a set of source directories, an embedding
configuration, and its own ChromaDB vector store.  Multiple KBs can coexist
(e.g. different projects, or the same project with different chunk parameters).

Endpoints:
    GET    /api/v1/kb                   — list all KBs + active KB id
    POST   /api/v1/kb                   — create KB
    PUT    /api/v1/kb/{id}              — update KB name / data_dirs / embedding config
    DELETE /api/v1/kb/{id}              — delete KB + its vector store files
    POST   /api/v1/kb/{id}/activate     — switch active KB (triggers agent rebuild)
    GET    /api/v1/files/{filename}     — serve a source document file (PDF etc.)
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Literal
from urllib.parse import unquote

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from lancy.kb_pool import EmbeddingConflict

log = logging.getLogger("uvicorn")

# ─── Data models ──────────────────────────────────────────────────────────────


class KBCreate(BaseModel):
    """Fields the caller provides when creating or updating a KB."""

    name: str = Field(..., min_length=1, max_length=100)
    data_dirs: list[str] = Field(default_factory=lambda: ["data/"])
    embedding_backend: Literal["local", "ollama", "litellm", "custom"] = "local"
    embedding_model: str = Field("nomic-ai/nomic-embed-text-v1", max_length=200)
    embedding_ollama_host: str = Field("", max_length=253)  # ollama: host:port (default localhost:11434)
    embedding_custom_base_url: str = Field("", max_length=500)  # custom: OpenAI-compat base URL
    embedding_custom_api_key: str = Field("", max_length=500)  # custom: API key
    nomic_prefix: bool = True
    max_file_size_mb: int = Field(20, ge=1, le=500)
    embedding_batch_size: int = Field(50, ge=1, le=1000)
    pdf_ocr_enabled: bool = True
    max_chunk_tokens: int = Field(0, ge=0, le=8192)
    vs_type: Literal["chromadb", "pgvector"] = "chromadb"
    vs_connection_string: str = Field("", max_length=500)  # used when vs_type == "pgvector"
    image_indexing_enabled: bool = False
    image_retrieval_enabled: bool = False
    image_embedding_model: str = Field("Qwen/Qwen3-VL-Embedding-2B", max_length=200)
    image_captioning_enabled: bool = False


class KBInfo(KBCreate):
    """Full KB record stored in the registry."""

    id: str
    vs_path: str
    chunks: int = 0
    files: int = 0
    last_indexed: str | None = None


class KBRegistry(BaseModel):
    active: str
    bases: dict[str, KBInfo]


# ─── Callback types ───────────────────────────────────────────────────────────

ActivateCallback = Callable[[KBInfo, bool], Awaitable[None]]
DeactivateCallback = Callable[[str], None]
UploadCallback = Callable[[Path, KBInfo, dict], Awaitable[None]]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _slug(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "kb"
    slug, n = base, 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    return slug


# ─── Router factory ───────────────────────────────────────────────────────────


def create_kb_router(
    db_dir: Path,
    activate_callback: ActivateCallback,
    project_root: Path | None = None,
    upload_callback: UploadCallback | None = None,
    pool_status_factory: Callable[[], dict] | None = None,
    deactivate_callback: DeactivateCallback | None = None,
) -> APIRouter:
    """
    Args:
        db_dir:             Directory where knowledge_bases.json is persisted.
        activate_callback:  Async callable(KBInfo) called when the active KB changes.
                            The callback is responsible for rebuilding the RAG agent.
    """
    router = APIRouter(prefix="/api/v1")
    registry_path = db_dir / "knowledge_bases.json"
    root = project_root or Path(__file__).parents[4]  # <project-root>/

    def _load() -> KBRegistry:
        if registry_path.exists():
            try:
                return KBRegistry(**json.loads(registry_path.read_text()))
            except Exception as exc:
                log.warning(f"Could not load knowledge_bases.json: {exc}")
        # Bootstrap: create a default KB pointing at the existing vs_text store.
        default = KBInfo(
            id="default",
            name="Standard",
            data_dirs=["data/"],
            vs_path=str(db_dir / "vs_text"),
        )
        reg = KBRegistry(active="default", bases={"default": default})
        _save(reg)
        return reg

    def _save(reg: KBRegistry) -> None:
        registry_path.write_text(reg.model_dump_json(indent=2))

    # ── Public helper (used by main.py) ──────────────────────────────────────
    def get_active() -> KBInfo:
        reg = _load()
        return reg.bases[reg.active]

    router.get_active_kb = get_active  # type: ignore[attr-defined]  # attached for main.py

    def update_stats(kb_id: str, chunks: int, files: int) -> None:
        """Called by main.py after a reindex to persist chunk/file counts."""
        reg = _load()
        if kb_id in reg.bases:
            reg.bases[kb_id].chunks = chunks
            reg.bases[kb_id].files = files
            reg.bases[kb_id].last_indexed = datetime.now(timezone.utc).isoformat()
            _save(reg)

    router.update_stats = update_stats  # type: ignore[attr-defined]

    # ── Endpoints ─────────────────────────────────────────────────────────────

    @router.get("/kb", response_model=KBRegistry)
    async def list_kbs() -> KBRegistry:
        return _load()

    @router.post("/kb", response_model=KBInfo)
    async def create_kb(cfg: KBCreate) -> KBInfo:
        reg = _load()
        slug = _slug(cfg.name, set(reg.bases.keys()))
        vs_path = str(db_dir / f"vs_{slug}")
        kb = KBInfo(id=slug, vs_path=vs_path, **cfg.model_dump())
        reg.bases[slug] = kb
        _save(reg)
        log.info(f"Created KB '{kb.name}' (id={slug})")
        return kb

    @router.put("/kb/{kb_id}", response_model=KBInfo)
    async def update_kb(kb_id: str, cfg: KBCreate) -> KBInfo:
        reg = _load()
        if kb_id not in reg.bases:
            raise HTTPException(404, f"KB '{kb_id}' not found")
        existing = reg.bases[kb_id]
        updated = KBInfo(
            id=kb_id,
            vs_path=existing.vs_path,
            chunks=existing.chunks,
            files=existing.files,
            last_indexed=existing.last_indexed,
            **cfg.model_dump(),
        )
        reg.bases[kb_id] = updated
        _save(reg)
        log.info(f"Updated KB '{kb_id}'")
        return updated

    @router.delete("/kb/{kb_id}")
    async def delete_kb(kb_id: str) -> dict:
        reg = _load()
        if kb_id not in reg.bases:
            raise HTTPException(404, f"KB '{kb_id}' not found")
        if len(reg.bases) <= 1:
            raise HTTPException(400, "Cannot delete the last Knowledge Base")
        kb = reg.bases.pop(kb_id)
        if reg.active == kb_id:
            reg.active = next(iter(reg.bases))
            await activate_callback(reg.bases[reg.active])
        _save(reg)
        if kb.vs_type == "chromadb":
            for vs in [Path(kb.vs_path), Path(kb.vs_path + "_images")]:
                if vs.exists():
                    shutil.rmtree(vs)
                    log.info(f"Deleted VS at {vs}")
        elif kb.vs_type == "pgvector" and kb.vs_connection_string:
            try:
                from sqlalchemy import text
                from sqlalchemy.ext.asyncio import create_async_engine

                conn = kb.vs_connection_string.strip()
                if conn.startswith("postgresql://"):
                    conn = conn.replace("postgresql://", "postgresql+asyncpg://", 1)
                elif conn.startswith("postgres://"):
                    conn = conn.replace("postgres://", "postgresql+asyncpg://", 1)
                engine = create_async_engine(conn)
                kb_table = f"rag_{kb_id.replace('-', '_')}"
                async with engine.begin() as c:
                    await c.execute(text(f"DROP TABLE IF EXISTS {kb_table}"))
                    await c.execute(text(f"DROP TABLE IF EXISTS {kb_table}_images"))
                await engine.dispose()
                log.info(f"Dropped pgvector tables: {kb_table}, {kb_table}_images")
            except Exception as exc:
                log.warning(f"Could not drop pgvector tables for KB '{kb_id}': {exc}")
        log.info(f"Deleted KB '{kb.name}' (id={kb_id})")
        return {"deleted": kb_id}

    @router.post("/kb/{kb_id}/activate", response_model=KBInfo)
    async def activate_kb(kb_id: str, reset: bool = Query(False)) -> KBInfo:
        reg = _load()
        if kb_id not in reg.bases:
            raise HTTPException(404, f"KB '{kb_id}' not found")
        reg.active = kb_id
        _save(reg)
        kb = reg.bases[kb_id]
        try:
            await activate_callback(kb, reset)
        except EmbeddingConflict as exc:
            raise HTTPException(
                409,
                f"{exc} Use ?reset=true to clear the pool first.",
            )
        log.info(f"Activated KB '{kb.name}' (id={kb_id}, reset={reset})")
        return kb

    @router.post("/kb/{kb_id}/deactivate")
    async def deactivate_kb(kb_id: str) -> dict:
        reg = _load()
        if kb_id not in reg.bases:
            raise HTTPException(404, f"KB '{kb_id}' not found")
        if deactivate_callback is not None:
            deactivate_callback(kb_id)
        log.info(f"Deactivated KB '{kb_id}' (unloaded from pool)")
        return {"deactivated": kb_id}

    @router.get("/kb/pool")
    async def pool_status() -> dict:
        if pool_status_factory is None:
            return {"loaded": [], "loading": [], "active": None, "emb_key": None}
        return pool_status_factory()

    @router.get("/kb/{kb_id}/stats")
    async def get_kb_stats(kb_id: str):
        stats_path = db_dir / f"kb_stats_{kb_id}.json"
        if not stats_path.exists():
            raise HTTPException(404, f"No stats available for KB '{kb_id}' — re-index to generate analytics")
        try:
            return json.loads(stats_path.read_text())
        except Exception as exc:
            raise HTTPException(500, f"Could not read stats: {exc}")

    @router.get("/files/{filename:path}")
    async def serve_file(filename: str):
        """Serve a source document (PDF, XLSX, …) from any configured data directory.
        Searched across all KBs so links remain stable when switching KBs."""
        filename = unquote(filename)
        try:
            reg = json.loads(registry_path.read_text())
            all_dirs: list[Path] = []
            for kb_data in reg.get("bases", {}).values():
                for d in kb_data.get("data_dirs", []):
                    all_dirs.append(Path(d) if Path(d).is_absolute() else root / d)
        except Exception:
            all_dirs = [root / "data"]

        for data_dir in all_dirs:
            candidate = (data_dir / filename).resolve()
            # Safety: ensure the file is inside a known data directory
            try:
                candidate.relative_to(data_dir.resolve())
            except ValueError:
                continue
            if candidate.exists() and candidate.is_file():
                return FileResponse(
                    str(candidate),
                    filename=candidate.name,
                    headers={
                        "Content-Disposition": f'inline; filename="{candidate.name}"'
                    },
                )

        raise HTTPException(
            404, f"File '{filename}' not found in any configured data directory"
        )

    @router.post("/kb/{kb_id}/documents")
    async def upload_document(
        kb_id: str,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        metadata: str = Form("{}"),
    ) -> dict:
        """Upload a document into a KB and trigger incremental indexing.

        The file is saved to a temporary location, ingested, then deleted.
        `metadata` must be a JSON string containing at least `document_id`
        (a stable identifier for versioning — same document, new version, same id).
        """
        if upload_callback is None:
            raise HTTPException(503, "Upload not available — backend not fully initialized")

        reg = _load()
        if kb_id not in reg.bases:
            raise HTTPException(404, f"KB '{kb_id}' not found")
        kb = reg.bases[kb_id]

        try:
            meta: dict = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(422, "metadata must be valid JSON")

        if not meta.get("document_id"):
            raise HTTPException(
                422,
                "document_id is required — use a stable identifier for this document "
                "(e.g. DMS record ID or canonical filename) so future versions can replace existing chunks",
            )

        original_filename = file.filename or "upload"
        suffix = Path(original_filename).suffix or ".bin"
        tmp_path = Path(tempfile.mktemp(suffix=suffix))
        tmp_path.write_bytes(await file.read())

        size_mb = tmp_path.stat().st_size / 1_048_576
        if size_mb > kb.max_file_size_mb:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(
                413,
                f"File too large: {size_mb:.1f} MB exceeds this KB's limit of {kb.max_file_size_mb} MB",
            )

        meta.setdefault("source_file", original_filename)

        background_tasks.add_task(upload_callback, tmp_path, kb, meta)
        return {"started": True, "document_id": meta["document_id"], "filename": file.filename}

    return router
