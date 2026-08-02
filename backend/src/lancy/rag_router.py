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

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Annotated, Any, Callable, Literal

_SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from lancy.database import (
    USER_RETRIEVAL_FIELDS,
    get_presets,
    get_default_preset,
    get_user_retrieval,
    init_db,
    migrate_json_presets,
    save_presets,
    seed_presets,
    set_user_retrieval,
)
from lancy.feature0_baseline_rag import build_llm

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
    llm_temperature: float = Field(0.2, ge=0.0, le=2.0)
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
    utility_custom_base_url: str = Field(
        "", max_length=500
    )  # custom: base URL for utility LLM; empty = same as custom_base_url
    utility_custom_api_key: str = Field(
        "", max_length=500
    )  # custom: API key for utility endpoint; empty = same as custom_api_key

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


# ─── Metadata facets (chat pre-filter discovery) ─────────────────────────────


class MetadataFacetsResponse(BaseModel):
    key: str
    distinct_count: int
    threshold: int
    values: list[str] | None  # None when distinct_count exceeds threshold


# ─── Document stats (expand-context budget estimation) ────────────────────────


class DocumentStatsRequest(BaseModel):
    source_files: list[str] = Field(..., min_length=1, max_length=100)
    id_field: Literal["source_file", "document_id"] = "source_file"
    """Metadata field the entries in `source_files` are matched against.

    Defaults to source_file so the expand-context popover is unaffected; the batch
    analysis UI passes document_id when the caller drives the batch by DMS ids.
    """


class DocumentStat(BaseModel):
    source_file: str  # the identifier that was requested, per id_field
    chunk_count: int
    char_count: int


class DocumentStatsResponse(BaseModel):
    stats: list[DocumentStat]


# ─── Batch document analysis ──────────────────────────────────────────────────


class AnalyzeDocumentRequest(BaseModel):
    """Analyze a single document end-to-end with a caller-supplied prompt + JSON schema.

    Exactly one of `document_id` or `source_file` must be set. All chunks matching that
    identifier are fetched, wrapped, and handed to the main LLM together with the
    caller's questions. Response is validated against `response_schema` (OpenAI-compatible
    backends enforce this at decode time; Ollama's `json` mode returns valid JSON but
    without schema constraint — the schema still guides the model via the prompt).

    `kb_id` is optional — omit it to use the currently active KB.
    """

    document_id: str | None = Field(default=None, max_length=500)
    source_file: str | None = Field(default=None, max_length=500)
    prompt: str = Field(..., min_length=1, max_length=20_000)
    response_schema: dict[str, Any] = Field(..., description="JSON Schema for the LLM output.")
    kb_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def one_identifier(self) -> "AnalyzeDocumentRequest":
        if bool(self.document_id) == bool(self.source_file):
            raise ValueError("Provide exactly one of document_id or source_file.")
        return self


class AnalyzeDocumentResponse(BaseModel):
    document_id: str | None
    source_file: str | None
    chunk_count: int
    char_count: int
    result: dict[str, Any] | None
    skipped: str | None = None  # "over_budget" | "no_chunks" | None
    budget_chars: int | None = None


_ANALYZE_TIMEOUT_S = 110  # Next.js proxy is 120s; stay under it so the script sees a clean error
_BUDGET_FRACTION = 0.6    # match expand-context — leaves headroom for prompt + output


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
    vs_by_kb_factory: Callable | None = None,  # (kb_id: str) -> VectorStore | None
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

    def _read_analyze_prompt() -> str:
        if prompts_dir is None:
            return ""
        for name in ("batch_analyze.custom.md", "batch_analyze.default.md"):
            p = prompts_dir / name
            if p.exists():
                content = p.read_text().strip()
                if content:
                    return content
        return ""

    def _load_config(user_id: str | None = None, role: str = "user") -> RagConfig:
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
        # 2. Overlay user-scoped retrieval fields from SQLite (admin writes go
        # straight to rag_config.json, never to this table — so admin reads
        # must skip the overlay too, or they'd always hit the "first visit"
        # seed fallback below and clobber the baseline they just saved).
        if role != "admin" and user_id:
            user_data = get_user_retrieval(sqlite_path, user_id)
            if user_data:
                overlay = {k: v for k, v in user_data.items() if k in USER_RETRIEVAL_FIELDS}
                cfg = cfg.model_copy(update=overlay)
            else:
                # First visit — seed the user's session from the Default preset
                default_preset = get_default_preset(sqlite_path)
                if default_preset:
                    overlay = {k: v for k, v in default_preset.items() if k in USER_RETRIEVAL_FIELDS}
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
        role = request.headers.get("x-user-role", "user")
        return _load_config(user_id, role)

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
    async def store_info(kb_id: str | None = None) -> StoreInfo:
        try:
            vs = (vs_by_kb_factory(kb_id) if kb_id and vs_by_kb_factory else None) or vector_store_factory()
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

    @router.get("/metadata-facets", response_model=MetadataFacetsResponse)
    async def metadata_facets(
        key: str,
        kb_id: str | None = None,
        threshold: int = 120,
    ) -> MetadataFacetsResponse:
        """Distinct values for a metadata key in the active (or specified) KB.

        Used by the admin config UI and chat pre-filter widgets. When the number of
        distinct values exceeds `threshold`, `values` is omitted — the caller falls
        back to a free-text input.
        """
        if threshold < 1:
            raise HTTPException(status_code=400, detail="threshold must be >= 1")
        if kb_id and vs_by_kb_factory:
            vs = vs_by_kb_factory(kb_id)
            if vs is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"KB '{kb_id}' is not loaded — activate it before requesting facets.",
                )
        else:
            vs = vector_store_factory()
            if vs is None:
                raise HTTPException(status_code=404, detail="No active KB.")
        try:
            values = await vs.get_metadata_values(key)
        except Exception as exc:
            log.warning(f"metadata-facets error for key={key!r}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))
        return MetadataFacetsResponse(
            key=key,
            distinct_count=len(values),
            threshold=threshold,
            values=values if len(values) <= threshold else None,
        )

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

    @router.post("/document-stats", response_model=DocumentStatsResponse)
    async def document_stats(req: DocumentStatsRequest) -> DocumentStatsResponse:
        """Return chunk_count + char_count per requested identifier.

        Used by the expand-context popover to estimate LLM context budget usage
        before the user commits, and by the batch analysis UI to flag over-budget
        documents before any LLM call is spent. Character count is used as a cheap,
        model-agnostic proxy for tokens (see the popover's ~/3.5 approximation).
        """
        try:
            vs = vector_store_factory()
            if vs is None:
                raise HTTPException(status_code=404, detail="No active KB.")
            chunks = await vs.get_chunks_by_filter(filters={req.id_field: req.source_files})
            by_file: dict[str, tuple[int, int]] = {}
            for c in chunks:
                sf = c.metadata.get(req.id_field, "")
                cur = by_file.get(sf, (0, 0))
                by_file[sf] = (cur[0] + 1, cur[1] + len(c.content or ""))
            return DocumentStatsResponse(
                stats=[
                    DocumentStat(source_file=sf, chunk_count=by_file.get(sf, (0, 0))[0], char_count=by_file.get(sf, (0, 0))[1])
                    for sf in req.source_files
                ]
            )
        except HTTPException:
            raise
        except Exception as exc:
            log.warning(f"document-stats error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/analyze-prompt-template")
    async def analyze_prompt_template() -> dict:
        """Return the raw batch-analysis system prompt template.

        The batch analysis UI renders this with {user_prompt}/{schema} substituted so
        the user sees the complete prompt before running. Served from the backend
        rather than duplicated in the frontend so a custom template shows up as-is.
        """
        tpl = _read_analyze_prompt()
        if not tpl:
            raise HTTPException(status_code=404, detail="batch_analyze prompt template not found")
        return {"template": tpl}

    @router.post("/analyze-document", response_model=AnalyzeDocumentResponse)
    async def analyze_document(req: AnalyzeDocumentRequest, http: Request) -> AnalyzeDocumentResponse:
        """Analyze one document end-to-end: fetch all its chunks, wrap them, invoke the
        main LLM with the caller's prompt and JSON schema, return the parsed result.

        Sibling to expand-context but non-streaming, script-friendly, and per-document.
        Chunks are grouped and ordered like expand-context. Over-budget documents are
        skipped (not truncated) — the response reports `skipped="over_budget"` so the
        batch script can log a row and continue.
        """
        vs = (vs_by_kb_factory(req.kb_id) if req.kb_id and vs_by_kb_factory else None) or vector_store_factory()
        if vs is None:
            raise HTTPException(
                status_code=404,
                detail=f"KB {req.kb_id!r} not found." if req.kb_id else "No active KB.",
            )

        filter_field = "document_id" if req.document_id else "source_file"
        filter_value = req.document_id or req.source_file
        try:
            chunks = await vs.get_chunks_by_filter(filters={filter_field: filter_value})
        except Exception as exc:
            log.warning(f"analyze-document chunk fetch failed ({filter_field}={filter_value!r}): {exc}")
            raise HTTPException(status_code=500, detail=f"Chunk fetch failed: {exc}")

        char_count = sum(len(c.content or "") for c in chunks)
        if not chunks:
            return AnalyzeDocumentResponse(
                document_id=req.document_id, source_file=req.source_file,
                chunk_count=0, char_count=0, result=None, skipped="no_chunks",
            )

        role = http.headers.get("x-user-role", "user")
        user_id = http.headers.get("x-session-id")
        cfg = _load_config(user_id=user_id, role=role)
        budget_chars = int(cfg.num_ctx * _BUDGET_FRACTION * 3.5)  # ~3.5 chars per token
        if char_count > budget_chars:
            return AnalyzeDocumentResponse(
                document_id=req.document_id, source_file=req.source_file,
                chunk_count=len(chunks), char_count=char_count,
                result=None, skipped="over_budget", budget_chars=budget_chars,
            )

        system_prompt_tpl = _read_analyze_prompt()
        if not system_prompt_tpl:
            raise HTTPException(status_code=500, detail="batch_analyze prompt template not found")
        system_prompt = system_prompt_tpl.replace(
            "{schema}", json.dumps(req.response_schema, indent=2)
        ).replace("{user_prompt}", req.prompt)

        # Wrap chunks as <document><chunk id="…">…</chunk>…</document>. Single-document
        # variant of expand-context's <sources><source>…</source></sources>.
        parts = [f'<document filename="{req.source_file or req.document_id}">']
        for c in chunks:
            if "text" not in (c.mime_type or ""):
                continue  # skip image chunks — this endpoint is for text analysis
            parts.append(f'<chunk id="{c.id}">{c.content}</chunk>')
        parts.append("</document>")
        user_content = "\n".join(parts)

        # OpenAI-compatible backends get schema-constrained decoding; Ollama's `json` mode
        # only enforces valid JSON (schema shape is guided by the prompt).
        if cfg.llm_backend == "ollama":
            response_format: Any = "json"
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "AnalysisResult", "schema": req.response_schema, "strict": True},
            }

        try:
            llm = build_llm(
                backend=cfg.llm_backend,
                model_name=cfg.llm_model or None,
                temperature=cfg.llm_temperature,
                response_format=response_format,
                ollama_host=cfg.ollama_host.strip() or None,
                num_ctx=cfg.num_ctx,
                max_tokens=cfg.llm_max_tokens if cfg.llm_backend != "ollama" else None,
                custom_base_url=getattr(cfg, "custom_base_url", ""),
                custom_api_key=getattr(cfg, "custom_api_key", ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"LLM build failed: {exc}")

        messages = [
            LLMMessage(role=Roles.SYSTEM, content=[MessageContent(type="text", text=system_prompt)]),
            LLMMessage(role=Roles.USER, content=[MessageContent(type="text", text=user_content)]),
        ]

        try:
            response = await asyncio.wait_for(llm.generate(messages), timeout=_ANALYZE_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning(f"analyze-document timeout ({_ANALYZE_TIMEOUT_S}s) for {filter_field}={filter_value!r}")
            raise HTTPException(status_code=504, detail=f"LLM timed out after {_ANALYZE_TIMEOUT_S}s")
        except Exception as exc:
            log.warning(f"analyze-document LLM call failed for {filter_field}={filter_value!r}: {exc}")
            raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

        raw = "".join(mc.text or "" for mc in response.content if mc.type == "text").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning(f"analyze-document JSON parse failed: {exc}; raw={raw[:500]!r}")
            raise HTTPException(status_code=502, detail=f"LLM returned invalid JSON: {exc}")

        return AnalyzeDocumentResponse(
            document_id=req.document_id, source_file=req.source_file,
            chunk_count=len(chunks), char_count=char_count, result=parsed,
        )

    return router
