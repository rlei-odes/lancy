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
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

log = logging.getLogger("uvicorn")

# ─── Data models ──────────────────────────────────────────────────────────────


class KBCreate(BaseModel):
    """Fields the caller provides when creating or updating a KB."""

    name: str
    data_dirs: list[str] = Field(default_factory=lambda: ["data/"])
    embedding_backend: str = "local"  # local | ollama | litellm | custom
    embedding_model: str = "nomic-ai/nomic-embed-text-v1"
    embedding_ollama_host: str = ""  # ollama: host:port (default localhost:11434)
    embedding_custom_base_url: str = ""  # custom: OpenAI-compat base URL
    embedding_custom_api_key: str = ""  # custom: API key
    nomic_prefix: bool = True
    max_file_size_mb: int = 20
    embedding_batch_size: int = 50
    pdf_ocr_enabled: bool = True
    max_chunk_tokens: int = 0
    vs_type: str = "chromadb"  # "chromadb" | "pgvector"
    vs_connection_string: str = ""  # used when vs_type == "pgvector"
    image_indexing_enabled: bool = False
    image_retrieval_enabled: bool = False
    image_embedding_model: str = "Qwen/Qwen3-VL-Embedding-2B"


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

ActivateCallback = Callable[[KBInfo], Awaitable[None]]


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
        # Only delete local directories for chromadb; pgvector tables are managed externally
        if kb.vs_type == "chromadb":
            for vs in [Path(kb.vs_path), Path(kb.vs_path + "_images")]:
                if vs.exists():
                    shutil.rmtree(vs)
                    log.info(f"Deleted VS at {vs}")
        log.info(f"Deleted KB '{kb.name}' (id={kb_id})")
        return {"deleted": kb_id}

    @router.post("/kb/{kb_id}/activate", response_model=KBInfo)
    async def activate_kb(kb_id: str) -> KBInfo:
        reg = _load()
        if kb_id not in reg.bases:
            raise HTTPException(404, f"KB '{kb_id}' not found")
        reg.active = kb_id
        _save(reg)
        kb = reg.bases[kb_id]
        await activate_callback(kb)
        log.info(f"Activated KB '{kb.name}' (id={kb_id})")
        return kb

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

    return router
