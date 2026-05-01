"""
RAG session configuration and reindex API endpoints.

Session config covers retrieval, LLM, and prompt parameters — everything that
does NOT require a vector store rebuild when changed.
KB-level config (embedding model, data dirs) lives in kb_router.py.

GET  /api/v1/rag/config       — current session config
POST /api/v1/rag/config       — save session config (applied immediately)
POST /api/v1/rag/reindex      — trigger ingestion on the active KB
GET  /api/v1/rag/store-info   — chunk count + file list for the active KB
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Annotated, Any, Callable, Literal

_SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from lancy.database import (
    USER_RETRIEVAL_FIELDS,
    get_presets,
    get_user_retrieval,
    init_db,
    migrate_json_presets,
    save_presets,
    seed_presets,
    set_user_retrieval,
)

log = logging.getLogger("uvicorn")

_SEEDS_PATH = Path(__file__).parent / "seeds" / "presets.json"


# ─── Session config (no re-index needed) ──────────────────────────────────────


class RagConfig(BaseModel):
    # Retrieval
    retriever_top_k: int = Field(5, ge=1, le=50)
    rrf_k: int = Field(60, ge=1, le=200)
    bm25_enabled: bool = True
    query_expansion: int = Field(0, ge=0, le=10)
    hyde_enabled: bool = False
    reranking_enabled: bool = False
    reranking_candidate_pool: int = Field(15, ge=3, le=100)

    # LLM
    llm_backend: Literal["ollama", "litellm", "custom"] = "ollama"
    llm_model: str = Field("mistral-nemo:12b", max_length=200)
    llm_temperature: float = Field(0.3, ge=0.0, le=2.0)
    ollama_host: str = Field("", max_length=253)  # empty = localhost:11434
    utility_llm_model: str = Field(
        "", max_length=200
    )  # empty = use same as llm_model; set e.g. "qwen2.5:3b" for faster preprocessing
    num_ctx: int = Field(
        8192, ge=512, le=131072
    )  # Ollama KV-cache window; 16384+ overflows VRAM on <16GB GPUs → CPU fallback
    llm_max_tokens: int = Field(6144, ge=128, le=32768)  # max output tokens (custom/litellm backends)
    custom_base_url: str = Field(
        "", max_length=500
    )  # custom: OpenAI-compat base URL, e.g. https://api.anthropic.com/v1
    custom_api_key: str = Field("", max_length=500)  # custom: API key for custom endpoint

    # Image retrieval (session-level; requires image_retrieval_enabled on active KB)
    image_retriever_top_k: int = Field(1, ge=1, le=4)

    @model_validator(mode="after")
    def clamp_candidate_pool(self) -> "RagConfig":
        if self.reranking_enabled and self.reranking_candidate_pool < self.retriever_top_k:
            self.reranking_candidate_pool = self.retriever_top_k
        return self

    # Prompt
    system_prompt: str = Field("", max_length=20_000)  # empty = use server default
    follow_up_count: int = Field(3, ge=0, le=10)


class ReindexRequest(BaseModel):
    reset: bool = False


class StoreInfo(BaseModel):
    chunks: int
    files: int
    file_list: list[str]


class ReindexResult(BaseModel):
    chunks_indexed: int
    files_processed: int
    files_skipped: int = 0  # total skipped (store + batch), kept for backwards compat
    files_skipped_store: int = 0  # already in vector store (cross-run dedup)
    files_skipped_batch: int = 0  # duplicate content within the same run
    reset: bool


class IndexStatus(BaseModel):
    indexing: bool = False
    phase: str = "loading"  # "loading" | "embedding"
    current_file: str = ""
    file_index: int = 0
    total_files: int = 0
    chunks_so_far: int = 0
    embed_batch: int = 0
    embed_total_batches: int = 0
    kb_name: str = ""  # name of the KB being indexed
    finished_at: str = ""  # ISO timestamp set when indexing completes
    last_result: ReindexResult | None = None  # result of the last completed reindex
    queued: int = 0  # files waiting in the upload queue


# ─── Retrieval probe ──────────────────────────────────────────────────────────


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4_000)
    bm25_enabled: bool = True
    reranking_enabled: bool = False
    filters: dict[str, str] | None = None


class ChunkScores(BaseModel):
    semantic_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    pre_rerank_rank: int | None = None  # rank before LLM reranking, if active


class ChunkResult(BaseModel):
    id: str
    content: str
    metadata: dict[str, Any]
    final_rank: int
    scores: ChunkScores


class RetrieveResponse(BaseModel):
    chunks: list[ChunkResult]
    top_k: int
    total_returned: int
    reranking_skipped: bool = False


# ─── Chunk browser ───────────────────────────────────────────────────────────


class FilterCondition(BaseModel):
    key: str = Field(..., max_length=100)
    op: Literal["eq"] = "eq"
    value: str = Field(..., max_length=500)


class ChunkBrowseRequest(BaseModel):
    filters: list[FilterCondition] = Field(default_factory=list)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class ChunkBrowseItem(BaseModel):
    id: str
    content: str
    title: str
    metadata: dict[str, Any]


class ChunkBrowseResponse(BaseModel):
    chunks: list[ChunkBrowseItem]
    returned: int
    offset: int
    has_more: bool


# ─── Router factory ───────────────────────────────────────────────────────────


def create_rag_router(
    db_dir: Path,
    vector_store_factory: Callable,  # () -> VectorStore proxy
    rebuild_callback: Callable,  # async (config, reset) -> ReindexResult
    prompts_dir: Path | None = None,  # directory containing system_prompt.*.md files
    status_factory: Callable | None = None,  # () -> dict with indexing progress
    query_status_factory: Callable | None = None,  # () -> dict with query phase
    agent_rebuild_callback: Callable
    | None = None,  # (config) -> None, rebuilds agent without reindex
    cancel_callback: Callable
    | None = None,  # () -> None, requests indexing cancellation
    retrieve_callback: Callable
    | None = None,  # async (RetrieveRequest) -> RetrieveResponse
) -> APIRouter:
    """
    Args:
        db_dir:               Directory where rag_config.json is persisted.
        prompts_dir:          Directory containing system_prompt.default.md and (optionally)
                              system_prompt.custom.md. Custom file is gitignored and written
                              whenever the user saves a non-empty system prompt via the UI.
                              Deleting the custom file resets to the default prompt.
        vector_store_factory: Zero-arg callable returning the active vector store proxy.
        rebuild_callback:     Async callable(RagConfig, reset: bool) -> ReindexResult.
                              The active KB is resolved inside main.py's callback.
    """
    router = APIRouter(prefix="/api/v1/rag")
    config_path = db_dir / "rag_config.json"
    sqlite_path = db_dir / "user_config.db"
    init_db(sqlite_path)
    migrate_json_presets(sqlite_path, db_dir)
    seed_presets(sqlite_path, _SEEDS_PATH)

    def _read_custom_prompt() -> str:
        if prompts_dir is None:
            return ""
        p = prompts_dir / "system_prompt.custom.md"
        return p.read_text().strip() if p.exists() else ""

    def _write_custom_prompt(text: str) -> None:
        if prompts_dir is None:
            return
        p = prompts_dir / "system_prompt.custom.md"
        if text:
            p.write_text(text)
        elif p.exists():
            p.unlink()  # empty string = reset to default

    def _load_config(user_id: str | None = None) -> RagConfig:
        # 1. Admin baseline from rag_config.json
        cfg = RagConfig()
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                data.pop("system_prompt", None)  # always load prompt from file
                cfg = RagConfig(**data)
            except Exception as exc:
                log.warning(f"Could not load rag_config.json: {exc} — using defaults")
        cfg.system_prompt = _read_custom_prompt()
        # 2. Overlay user-scoped retrieval fields from SQLite
        if user_id:
            user_data = get_user_retrieval(sqlite_path, user_id)
            if user_data:
                overlay = {k: v for k, v in user_data.items() if k in USER_RETRIEVAL_FIELDS}
                cfg = cfg.model_copy(update=overlay)
        return cfg

    def _save_config(cfg: RagConfig, user_id: str | None, role: str) -> None:
        if role == "admin":
            # Admin writes update the shared baseline for everyone
            _write_custom_prompt(cfg.system_prompt)
            data = cfg.model_dump()
            data.pop("system_prompt", None)
            config_path.write_text(json.dumps(data, indent=2))
        elif user_id:
            # Users may only persist retrieval fields to their own SQLite row
            data = cfg.model_dump()
            retrieval = {k: data[k] for k in USER_RETRIEVAL_FIELDS if k in data}
            set_user_retrieval(sqlite_path, user_id, retrieval)

    @router.get("/config", response_model=RagConfig)
    async def get_config(request: Request) -> RagConfig:
        user_id = request.headers.get("x-session-id")
        return _load_config(user_id)

    @router.post("/config", response_model=RagConfig)
    async def save_config(request: Request, cfg: RagConfig, background_tasks: BackgroundTasks) -> RagConfig:
        user_id = request.headers.get("x-session-id")
        role = request.headers.get("x-user-role", "user")
        _save_config(cfg, user_id, role)
        log.info(
            f"Session config saved: role={role} llm={cfg.llm_backend}/{cfg.llm_model} "
            f"top_k={cfg.retriever_top_k} temp={cfg.llm_temperature}"
        )
        if agent_rebuild_callback is not None:
            background_tasks.add_task(agent_rebuild_callback, cfg)
        return cfg

    @router.get("/store-info", response_model=StoreInfo)
    async def store_info() -> StoreInfo:
        try:
            vs = vector_store_factory()
            count = await vs.count()
            try:
                records = await vs.get_chunks_by_filter({})
                files = sorted(
                    {
                        r.metadata.get("source_file", "unknown")
                        for r in records
                        if r.metadata
                    }
                )
            except Exception:
                files = []
            return StoreInfo(chunks=count, files=len(files), file_list=files)
        except Exception as exc:
            log.warning(f"store-info error: {exc}")
            return StoreInfo(chunks=0, files=0, file_list=[])

    @router.get("/presets/{kb_id}")
    async def get_presets_route(kb_id: str, request: Request) -> dict:
        user_id = request.headers.get("x-session-id")
        return get_presets(sqlite_path, kb_id, user_id)

    @router.post("/presets/{kb_id}")
    async def save_presets_route(kb_id: str, request: Request, presets: Annotated[dict, Body()]) -> dict:
        user_id = request.headers.get("x-session-id")
        role = request.headers.get("x-user-role", "user")
        save_presets(sqlite_path, kb_id, user_id, role, presets)
        return {"saved": len(presets.get("retrieval", [])) + len(presets.get("kb", []))}

    @router.post("/reindex")
    async def reindex(req: ReindexRequest, background_tasks: BackgroundTasks) -> dict:
        """Trigger ingestion/reindex on the active Knowledge Base.

        Returns immediately — the job runs in the background. Poll
        GET /reindex-status for progress; last_result carries the final counts.
        """
        if status_factory is not None and status_factory().get("indexing"):
            raise HTTPException(
                status_code=409, detail="Indexierung läuft bereits. Bitte warten."
            )
        cfg = _load_config()  # admin baseline only — reindex is admin-only
        log.info(f"Reindex requested: reset={req.reset}")
        background_tasks.add_task(rebuild_callback, cfg, req.reset)
        return {"started": True}

    @router.post("/reindex-cancel")
    async def reindex_cancel() -> dict:
        """Request cancellation of the running indexing job."""
        if cancel_callback is not None:
            cancel_callback()
        return {"ok": True}

    @router.get("/reindex-status", response_model=IndexStatus)
    async def reindex_status() -> IndexStatus:
        if status_factory is None:
            return IndexStatus()
        s = status_factory()
        return IndexStatus(**s)

    @router.get("/query-status")
    async def query_status() -> dict:
        """Current query phase: idle | retrieving | generating"""
        if query_status_factory is None:
            return {"active": False, "phase": "idle"}
        return query_status_factory()

    @router.get("/litellm-models")
    async def get_litellm_models() -> list[str]:
        """Fetch available model IDs from the configured LiteLLM proxy."""
        import os
        from openai import AsyncOpenAI

        base_url = os.getenv("LITELLM_BASE_URL", "").rstrip("/")
        api_key = os.getenv("LITELLM_API_KEY", "")
        if not base_url:
            return []
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        try:
            client = AsyncOpenAI(base_url=base_url, api_key=api_key or "dummy")
            models = await client.models.list()
            return sorted([m.id for m in models.data])
        except Exception as exc:
            log.warning(f"Could not fetch LiteLLM models: {exc}")
            return []

    _VALID_HOST_RE = re.compile(r"^[a-zA-Z0-9._\-]+(:\d{1,5})?$")

    @router.get("/ollama-models")
    async def get_ollama_models(host: str = "localhost:11434") -> list[str]:
        """Fetch available model names from an Ollama instance."""
        import httpx

        if not host:
            return []
        if not _VALID_HOST_RE.match(host):
            raise HTTPException(status_code=400, detail="Invalid host format — use hostname:port.")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"http://{host}/api/tags")
                if r.status_code == 200:
                    data = r.json()
                    return sorted([m["name"] for m in data.get("models", [])])
        except Exception as exc:
            log.warning(f"Could not fetch Ollama models from {host}: {exc}")
        return []

    @router.get("/status")
    async def server_status() -> dict:
        return {"started_at": _SERVER_STARTED_AT}

    @router.post("/retrieve", response_model=RetrieveResponse)
    async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
        """Run retrieval pipeline without LLM — returns scored chunks for the Explorer."""
        if retrieve_callback is None:
            raise HTTPException(status_code=501, detail="Retrieval probe not configured.")
        return await retrieve_callback(req)

    @router.post("/chunks", response_model=ChunkBrowseResponse)
    async def browse_chunks(req: ChunkBrowseRequest) -> ChunkBrowseResponse:
        """Browse indexed chunks by metadata filter with server-side pagination."""
        try:
            vs = vector_store_factory()
            eq_filters = {f.key: f.value for f in req.filters if f.op == "eq"} or None
            chunks = await vs.get_chunks_by_filter(eq_filters, limit=req.limit + 1, offset=req.offset)
            has_more = len(chunks) > req.limit
            page = chunks[: req.limit]
            return ChunkBrowseResponse(
                chunks=[
                    ChunkBrowseItem(id=c.id, content=c.content, title=c.title, metadata=c.metadata)
                    for c in page
                ],
                returned=len(page),
                offset=req.offset,
                has_more=has_more,
            )
        except Exception as exc:
            log.warning(f"browse-chunks error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    return router
