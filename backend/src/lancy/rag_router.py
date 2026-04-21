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
from typing import Annotated, Callable

_SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("uvicorn")


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
    llm_backend: str = Field("ollama")  # ollama | litellm | custom
    llm_model: str = Field("mistral-nemo:12b")
    llm_temperature: float = Field(0.3, ge=0.0, le=2.0)
    ollama_host: str = Field("")  # empty = localhost:11434
    utility_llm_model: str = Field(
        ""
    )  # empty = use same as llm_model; set e.g. "qwen2.5:3b" for faster preprocessing
    num_ctx: int = Field(
        8192, ge=512, le=131072
    )  # Ollama KV-cache window; 16384+ overflows VRAM on <16GB GPUs → CPU fallback
    custom_base_url: str = Field(
        ""
    )  # custom: OpenAI-compat base URL, e.g. https://api.anthropic.com/v1
    custom_api_key: str = Field("")  # custom: API key for custom endpoint

    # Image retrieval (session-level; requires image_retrieval_enabled on active KB)
    image_retriever_top_k: int = Field(1, ge=1, le=4)

    # Prompt
    system_prompt: str = Field("")  # empty = use server default
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

    def _load_config() -> RagConfig:
        cfg = RagConfig()
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                data.pop(
                    "system_prompt", None
                )  # always load prompt from file, not JSON
                cfg = RagConfig(**data)
            except Exception as exc:
                log.warning(f"Could not load rag_config.json: {exc} — using defaults")
        cfg.system_prompt = _read_custom_prompt()
        return cfg

    def _save_config(cfg: RagConfig) -> None:
        _write_custom_prompt(cfg.system_prompt)
        # Persist everything except system_prompt (that lives in the prompt file)
        data = cfg.model_dump()
        data.pop("system_prompt", None)
        config_path.write_text(json.dumps(data, indent=2))

    @router.get("/config", response_model=RagConfig)
    async def get_config() -> RagConfig:
        return _load_config()

    @router.post("/config", response_model=RagConfig)
    async def save_config(cfg: RagConfig) -> RagConfig:
        _save_config(cfg)
        log.info(
            f"Session config saved: llm={cfg.llm_backend}/{cfg.llm_model} "
            f"top_k={cfg.retriever_top_k} temp={cfg.llm_temperature}"
        )
        if agent_rebuild_callback is not None:
            agent_rebuild_callback(cfg)
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
    async def get_presets(kb_id: str) -> list:
        path = db_dir / f"rag_presets_{kb_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return []

    @router.post("/presets/{kb_id}")
    async def save_presets(kb_id: str, presets: Annotated[list, Body()]) -> dict:
        path = db_dir / f"rag_presets_{kb_id}.json"
        path.write_text(json.dumps(presets, indent=2))
        return {"saved": len(presets)}

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
        cfg = _load_config()
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

    @router.get("/ollama-models")
    async def get_ollama_models(host: str = "localhost:11434") -> list[str]:
        """Fetch available model names from an Ollama instance."""
        import httpx

        if not host:
            return []
        base = host if host.startswith("http") else f"http://{host}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{base}/api/tags")
                if r.status_code == 200:
                    data = r.json()
                    return sorted([m["name"] for m in data.get("models", [])])
        except Exception as exc:
            log.warning(f"Could not fetch Ollama models from {host}: {exc}")
        return []

    @router.get("/status")
    async def server_status() -> dict:
        return {"started_at": _SERVER_STARTED_AT}

    return router
