"""
FastAPI server entry point — Lancy

Environment variables:
    BACKEND           — LLM backend: 'ollama' (default) | 'openai' | 'anthropic'
    MODEL             — Override LLM model name
    OPENAI_API_KEY    — Required when BACKEND=openai or EMBEDDING_BACKEND=openai
    ANTHROPIC_API_KEY — Required when BACKEND=anthropic
    ALLOW_ORIGINS     — Comma-separated CORS origins, e.g. https://your-domain.example.com
    LOG_FILE          — Path to the log file (enables rotating file logging). See logging_config.py.
    LOG_MAX_BYTES     — Max file size before rotation (default: 10 MB)
    LOG_BACKUP_COUNT  — Number of rotated files to keep (default: 5)

Multi-KB:
    Knowledge bases are managed via /api/v1/kb endpoints.
    Each KB has its own vector store and embedding config.
    The active KB is persisted in db/knowledge_bases.json.
    Switching KBs hot-swaps the agent without a server restart.

RAG config API (session params — no re-index needed):
    GET  /api/v1/rag/config      — current session config (retrieval, LLM, prompt)
    POST /api/v1/rag/config      — save session config
    POST /api/v1/rag/reindex     — (re)index the active KB
    GET  /api/v1/rag/store-info  — chunk count + file list for active KB

KB API:
    GET    /api/v1/kb                 — list all KBs + active KB
    POST   /api/v1/kb                 — create KB
    PUT    /api/v1/kb/{id}            — update KB
    DELETE /api/v1/kb/{id}            — delete KB
    POST   /api/v1/kb/{id}/activate   — switch active KB
"""

import asyncio
import json
import logging
import math
import os
import pathlib
import re
from pathlib import Path
from typing import Any

# Load litellm.env from project root if LITELLM env vars are not set via systemd
_litellm_env = Path(__file__).parent.parent.parent / "litellm.env"
if _litellm_env.exists():
    for _line in _litellm_env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            if _k.strip() not in os.environ:
                os.environ[_k.strip()] = _v.strip()

import uvicorn

from lancy.logging_config import build_log_config, configure_loguru

from conversational_toolkit.agents.base import AgentAnswer
from conversational_toolkit.agents.rag import RAG
from conversational_toolkit.api.server import create_app
from conversational_toolkit.conversation_database.controller import (
    ConversationalToolkitController,
)
from conversational_toolkit.conversation_database.sqlite.conversation import SQLiteConversationDatabase
from conversational_toolkit.conversation_database.sqlite.message import SQLiteMessageDatabase
from conversational_toolkit.conversation_database.sqlite.reactions import SQLiteReactionDatabase
from conversational_toolkit.conversation_database.sqlite.source import SQLiteSourceDatabase
from conversational_toolkit.conversation_database.sqlite.user import SQLiteUserDatabase
from conversational_toolkit.conversation_database.postgres.conversation import PostgreSQLConversationDatabase
from conversational_toolkit.conversation_database.postgres.message import PostgreSQLMessageDatabase
from conversational_toolkit.conversation_database.postgres.reactions import PostgreSQLReactionDatabase
from conversational_toolkit.conversation_database.postgres.source import PostgreSQLSourceDatabase
from conversational_toolkit.conversation_database.postgres.user import PostgreSQLUserDatabase
from conversational_toolkit.llms.base import MessageContent
from conversational_toolkit.retriever.bm25_retriever import BM25Retriever
from conversational_toolkit.retriever.hybrid_retriever import HybridRetriever
from conversational_toolkit.retriever.reranking_retriever import RerankingRetriever
from conversational_toolkit.vectorstores.base import VectorStore

from lancy.feature0_baseline_rag import (
    _ROOT,
    build_embedding_model,
    build_llm,
    make_vector_store,
    _make_retriever,
)
from lancy.ingestion import (
    _index_status,
    cancel_indexing,
    enqueue_upload,
    run_ingestion,
    upload_worker,
)
from lancy.admin_router import create_admin_router, run_auto_cleanup
from lancy.branding_router import create_branding_router
from lancy.kb_router import KBInfo, create_kb_router
from lancy.kb_stats import write_kb_stats
from lancy.openai_compat_router import create_openai_compat_router
from lancy.rag_router import (
    ChunkResult,
    ChunkScores,
    RagConfig,
    ReindexResult,
    RetrieveRequest,
    RetrieveResponse,
    create_rag_router,
)
from lancy.utils.json import parse_llm_json_stream

log = logging.getLogger("uvicorn")

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
BACKEND = os.getenv("BACKEND", "ollama")

# Comma-separated list of allowed CORS origins.
# Example: ALLOW_ORIGINS=https://rag.example.com,https://demo.example.com
_raw_origins = os.getenv("ALLOW_ORIGINS", "")
ALLOW_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()] or [
    "http://localhost:3000",
    "http://localhost:8080",
]

for _secret_name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LITELLM_API_KEY"):
    _secret_file = pathlib.Path(f"/secrets/{_secret_name}")
    if _secret_name not in os.environ and _secret_file.exists():
        os.environ[_secret_name] = _secret_file.read_text().strip()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DB_DIR = Path(__file__).parent / "db"
_DB_DIR.mkdir(exist_ok=True)

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"
_PROMPTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """Load system prompt from file. Custom overrides default; both live in prompts/.
    Custom file is gitignored — safe for internal/confidential instructions."""
    for name in ("system_prompt.custom.md", "system_prompt.default.md"):
        p = _PROMPTS_DIR / name
        if p.exists():
            content = p.read_text().strip()
            if content:
                return content
    # Should never reach here if default file is present, but kept as safety net.
    return ""


# ---------------------------------------------------------------------------
# JSON schema for structured LLM output
# ---------------------------------------------------------------------------
json_schema = {
    "type": "object",
    "name": "AnswerSchema",
    "description": "Structured answer with source citations and follow-up questions.",
    "properties": {
        "answer": {
            "type": "string",
            "description": "The answer to the user's question in Markdown format.",
        },
        "used_sources_id": {
            "type": "array",
            "description": "IDs of the sources used. No invented IDs.",
            "items": {"type": "string"},
        },
        "follow_up_questions": {
            "type": "array",
            "description": "Possible follow-up questions based on the sources. Only when sources were used.",
            "items": {"type": "string"},
        },
    },
    "required": ["answer", "used_sources_id", "follow_up_questions"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Custom RAG with post-processing
# ---------------------------------------------------------------------------
_UUID_RE = re.compile(
    r"\[?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]?",
    re.IGNORECASE,
)

# Global query status (polled by frontend via GET /api/v1/rag/query-status)
_query_status: dict = {"active": False, "phase": "idle"}


class CustomRAG(RAG):
    def __init__(self, *args, follow_up_count: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.follow_up_count = follow_up_count

    async def answer_stream(self, query_with_context):
        _query_status.update({"active": True, "phase": "retrieving"})

        def update_phase(phase: str) -> None:
            _query_status["phase"] = phase

        try:
            first = True
            async for chunk in super().answer_stream(query_with_context, phase_callback=update_phase):
                if first:
                    _query_status["phase"] = "generating"
                    first = False
                yield chunk
        except Exception as exc:
            # Yield the error as a visible message so the frontend doesn't spin forever.
            # Common cases: model not pulled, Ollama not running, network error.
            error_text = str(exc)
            if "not found" in error_text and "404" in error_text:
                msg = f"LLM model not found. Run: ollama pull {self.llm.model}"
            elif "connection" in error_text.lower() or "refused" in error_text.lower():
                msg = "Cannot reach the LLM. Is it running?"
            else:
                msg = f"LLM error: {error_text}"
            log.error(f"LLM stream error: {exc}")
            yield AgentAnswer(content=[MessageContent(type="text", text=msg)])
        finally:
            _query_status.update({"active": False, "phase": "idle"})

    async def _answer_post_processing(self, answer: AgentAnswer) -> AgentAnswer:
        json_answer = parse_llm_json_stream(
            answer.content[0].text if answer.content else ""
        )
        content = json_answer.get("answer", "")
        # Fallback: if JSON parsing lost the content, use the raw accumulated text.
        # This prevents a silent empty-answer when the model output doesn't match
        # the expected JSON schema on the final chunk.
        if not content and answer.content:
            content = answer.content[0].text
        relevant_source_ids = json_answer.get("used_sources_id", [])
        follow_up_questions = json_answer.get("follow_up_questions", [])[:self.follow_up_count]
        unique_sources = list({s.id: s for s in answer.sources}.values())

        # Replace any inline UUID references with clickable source citation links.
        # Angle brackets allow spaces in CommonMark URLs; source:// is intercepted
        # by the frontend Markdown component to show a content popup.
        id_to_file = {s.id: s.metadata.get("source_file", "") for s in unique_sources}

        def _replace_uuid(m: re.Match) -> str:
            uid = m.group(1)
            filename = id_to_file.get(uid, "")
            if not filename:
                return ""
            return f"[{filename}](<source://{filename}>)"

        content = _UUID_RE.sub(_replace_uuid, content)

        # Also handle [N]-style footnote citations (produced by models that don't follow
        # the (filename.pdf) format, e.g. mistral-nemo fallback).
        # Map [1] → used_sources_id[0], [2] → used_sources_id[1], etc.
        ordered_sources = [
            next((s for s in unique_sources if s.id == sid), None)
            for sid in relevant_source_ids
        ]
        for i, source in enumerate((s for s in ordered_sources if s), 1):
            fname = source.metadata.get("source_file", "")
            if fname and f"[{i}]" in content:
                content = content.replace(f"[{i}]", f"[{fname}](<source://{fname}>)")

        return AgentAnswer(
            content=[MessageContent(type="text", text=content)],
            sources=[s for s in unique_sources if s.id in relevant_source_ids],
            follow_up_questions=follow_up_questions,
            retrieval_stats=answer.retrieval_stats,
        )


# ---------------------------------------------------------------------------
# Hot-swap proxy — delegates all attribute access to a swappable inner object.
# Used so the controller / retrievers keep their references while the KB changes.
# ---------------------------------------------------------------------------
class _Proxy:
    def __init__(self, obj: object) -> None:
        object.__setattr__(self, "_obj", obj)

    def switch(self, obj: object) -> None:
        object.__setattr__(self, "_obj", obj)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_obj"), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(object.__getattribute__(self, "_obj"), name, value)


# ---------------------------------------------------------------------------
# Component builder
# ---------------------------------------------------------------------------
def _build_components(kb: KBInfo, cfg: RagConfig) -> tuple[VectorStore, CustomRAG, Any]:
    """Instantiate (vector_store, agent, embedding_model) for the given KB + session config."""
    emb = build_embedding_model(
        kb.embedding_backend,
        kb.embedding_model,
        ollama_host=kb.embedding_ollama_host or "",
        custom_base_url=kb.embedding_custom_base_url or "",
        custom_api_key=kb.embedding_custom_api_key or "",
    )
    vs_type = getattr(kb, "vs_type", "chromadb") or "chromadb"
    vs_conn = getattr(kb, "vs_connection_string", "") or ""
    vs = make_vector_store(
        vs_type=vs_type,
        db_path=Path(kb.vs_path) if vs_type == "chromadb" else None,
        embedding_model_name=kb.embedding_model,
        vs_connection_string=vs_conn,
        table_name=f"rag_{kb.id.replace('-', '_')}",
    )

    top_k = cfg.retriever_top_k
    # When reranking, fetch a larger candidate pool first
    retriever_k = cfg.reranking_candidate_pool if cfg.reranking_enabled else top_k
    semantic = _make_retriever(emb, vs, retriever_k, kb.nomic_prefix)
    retrievers = (
        [semantic, BM25Retriever(vs, top_k=retriever_k)]
        if cfg.bm25_enabled
        else [semantic]
    )
    hybrid = HybridRetriever(retrievers=retrievers, top_k=retriever_k)

    _is_ollama_via_litellm = cfg.llm_backend == "litellm" and (
        cfg.llm_model or ""
    ).startswith("ollama/")
    llm_fmt = (
        None
        if cfg.llm_backend == "ollama" or _is_ollama_via_litellm
        else {"type": "json_object"}
        if cfg.llm_backend == "litellm"
        else {
            "type": "json_schema",
            "json_schema": {"schema": json_schema, "name": "AnswerSchema"},
        }
    )
    ollama_host = cfg.ollama_host.strip() or None
    try:
        llm = build_llm(
            backend=cfg.llm_backend,
            model_name=cfg.llm_model or None,
            temperature=cfg.llm_temperature,
            response_format=llm_fmt,
            ollama_host=ollama_host,
            num_ctx=cfg.num_ctx,
            max_tokens=cfg.llm_max_tokens if cfg.llm_backend != "ollama" else None,
            custom_base_url=getattr(cfg, "custom_base_url", ""),
            custom_api_key=getattr(cfg, "custom_api_key", ""),
        )
    except ValueError as exc:
        log.warning(
            f"LLM build failed ({exc}), falling back to ollama/mistral-nemo:12b"
        )
        llm = build_llm(
            backend="ollama",
            model_name="mistral-nemo:12b",
            temperature=cfg.llm_temperature,
            ollama_host=ollama_host,
            num_ctx=cfg.num_ctx,
        )

    # Use a separate smaller/faster model for preprocessing (query rewriting, HyDE, reranking).
    # Always built without response_format — utility tasks use their own output schemas, not the
    # RAG answer schema. Falling back to llm is a last resort; that instance carries
    # response_format and will cause reranking to fail on strict backends (vLLM, Anthropic).
    _utility_model_cfg = cfg.utility_llm_model.strip()
    _has_separate = _utility_model_cfg and _utility_model_cfg != (cfg.llm_model or "").strip()
    _utility_model_name = _utility_model_cfg if _has_separate else (cfg.llm_model or None)
    try:
        utility_llm = build_llm(
            backend=cfg.llm_backend,
            model_name=_utility_model_name,
            temperature=cfg.llm_temperature,
            ollama_host=ollama_host,
            num_ctx=cfg.num_ctx,
            max_tokens=512 if cfg.llm_backend != "ollama" else None,
            custom_base_url=getattr(cfg, "custom_base_url", ""),
            custom_api_key=getattr(cfg, "custom_api_key", ""),
        )
        if _has_separate:
            log.info(f"Utility LLM: {cfg.llm_backend}/{_utility_model_name}")
    except Exception as exc:
        log.warning(
            f"Utility LLM build failed ({exc}), using main LLM for preprocessing"
        )
        utility_llm = llm

    final_retriever = (
        RerankingRetriever(hybrid, utility_llm, top_k=top_k)
        if cfg.reranking_enabled
        else hybrid
    )

    all_retrievers = [final_retriever]

    if kb.image_retrieval_enabled:
        image_emb = build_embedding_model("qwen3vl", kb.image_embedding_model)
        image_vs = make_vector_store(
            vs_type=vs_type,
            db_path=Path(kb.vs_path + "_images") if vs_type == "chromadb" else None,
            embedding_model_name=kb.image_embedding_model,
            vs_connection_string=vs_conn,
            table_name=f"rag_{kb.id.replace('-', '_')}_images",
        )
        image_retriever = _make_retriever(
            image_emb, image_vs, cfg.image_retriever_top_k, False
        )
        all_retrievers.append(image_retriever)
        log.info(
            f"Image retrieval enabled: {kb.image_embedding_model} top_k={cfg.image_retriever_top_k}"
        )

    base_prompt = cfg.system_prompt.strip() or _load_system_prompt()

    agent = CustomRAG(
        llm=llm,
        utility_llm=utility_llm,
        system_prompt=base_prompt,  # file list injected asynchronously after build
        retrievers=all_retrievers,
        number_query_expansion=cfg.query_expansion,
        enable_hyde=cfg.hyde_enabled,
        follow_up_count=cfg.follow_up_count,
    )
    return vs, agent, emb


async def _inject_source_files(agent: Any, vs: VectorStore, base_prompt: str) -> None:
    """Append the indexed file list to the agent's system prompt.
    Called asynchronously after _build_components so the VectorStore abstraction is respected."""
    try:
        indexed_files = await vs.get_source_files()
    except Exception:
        return
    if indexed_files:
        file_list = "\n".join(f"- {f}" for f in indexed_files)
        agent.system_prompt = (
            base_prompt
            + f"\n\nINDEXED FILES ({len(indexed_files)} files):\n{file_list}"
        )



# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------
def build_server():
    # ── Session config ────────────────────────────────────────────────────
    session_cfg_path = _DB_DIR / "rag_config.json"
    try:
        session_cfg = (
            RagConfig(**json.loads(session_cfg_path.read_text()))
            if session_cfg_path.exists()
            else RagConfig()
        )
    except Exception:
        session_cfg = RagConfig()

    # ── Active KB (bootstraps knowledge_bases.json on first run) ─────────
    kb_registry_path = _DB_DIR / "knowledge_bases.json"
    try:
        if kb_registry_path.exists():
            reg = json.loads(kb_registry_path.read_text())
            active_id = reg["active"]
            active_kb = KBInfo(**reg["bases"][active_id])
        else:
            active_kb = KBInfo(
                id="default",
                name="Standard",
                data_dirs=["data/"],
                vs_path=str(_DB_DIR / "vs_text"),
            )
    except Exception:
        active_kb = KBInfo(
            id="default",
            name="Standard",
            data_dirs=["data/"],
            vs_path=str(_DB_DIR / "vs_text"),
        )

    # ── Initial components ────────────────────────────────────────────────
    init_vs, init_agent, init_emb = _build_components(active_kb, session_cfg)
    vs_proxy = _Proxy(init_vs)
    agent_proxy = _Proxy(init_agent)
    emb_proxy = _Proxy(init_emb)
    _probe_bm25: BM25Retriever | None = None

    _secret_key = os.getenv("SECRET_KEY", "1234567890")

    # ── Controller ────────────────────────────────────────────────────────
    # ── Conversation database — SQLite default, Postgres if DATABASE_URL is set ──
    _database_url = os.getenv("DATABASE_URL", "")
    if _database_url:
        _conn = _database_url
        if _conn.startswith("postgresql://"):
            _conn = _conn.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif _conn.startswith("postgres://"):
            _conn = _conn.replace("postgres://", "postgresql+asyncpg://", 1)
        from sqlalchemy.ext.asyncio import create_async_engine as _cae
        _db_engine = _cae(_conn, pool_pre_ping=True)
        _conv_db = PostgreSQLConversationDatabase(_db_engine)
        _msg_db = PostgreSQLMessageDatabase(_db_engine)
        _react_db = PostgreSQLReactionDatabase(_db_engine)
        _src_db = PostgreSQLSourceDatabase(_db_engine)
        _user_db = PostgreSQLUserDatabase(_db_engine)
        log.info(f"Conversation DB: PostgreSQL ({_database_url[:40]}...)")
    else:
        from sqlalchemy.ext.asyncio import create_async_engine as _cae
        _db_engine = _cae(f"sqlite+aiosqlite:///{_DB_DIR}/conversations.db")
        _conv_db = SQLiteConversationDatabase(_db_engine)
        _msg_db = SQLiteMessageDatabase(_db_engine)
        _react_db = SQLiteReactionDatabase(_db_engine)
        _src_db = SQLiteSourceDatabase(_db_engine)
        _user_db = SQLiteUserDatabase(_db_engine)
        log.info(f"Conversation DB: SQLite ({_DB_DIR}/conversations.db)")

    controller = ConversationalToolkitController(
        conversation_db=_conv_db,
        message_db=_msg_db,
        reaction_db=_react_db,
        source_db=_src_db,
        user_db=_user_db,
        agent=agent_proxy,
    )

    # ── conversation_metadata_provider — injects active KB + config into new conversations ──
    def _conversation_metadata() -> dict:
        try:
            kb = kb_router.get_active_kb()
            cfg = (
                RagConfig(**json.loads(session_cfg_path.read_text()))
                if session_cfg_path.exists()
                else session_cfg
            )
            return {
                "kb_id": kb.id,
                "kb_name": kb.name,
                "rag_config_snapshot": {
                    "retriever_top_k": cfg.retriever_top_k,
                    "rrf_k": cfg.rrf_k,
                    "bm25_enabled": cfg.bm25_enabled,
                    "reranking_enabled": cfg.reranking_enabled,
                    "reranking_candidate_pool": cfg.reranking_candidate_pool,
                    "hyde_enabled": cfg.hyde_enabled,
                    "query_expansion": cfg.query_expansion,
                    "llm_backend": cfg.llm_backend,
                    "llm_model": cfg.llm_model,
                    "llm_temperature": cfg.llm_temperature,
                    "utility_llm_model": cfg.utility_llm_model or None,
                    "embedding_backend": kb.embedding_backend,
                    "embedding_model": kb.embedding_model,
                    "vs_type": kb.vs_type,
                },
            }
        except Exception as _e:
            log.error(f"_conversation_metadata failed: {_e!r}")
            return {}

    app = create_app(
        controller=controller,
        allow_origins=ALLOW_ORIGINS,
        conversation_metadata_provider=_conversation_metadata,
        secret_key=_secret_key,
    )

    # ── KB Router ─────────────────────────────────────────────────────────
    async def on_kb_activate(kb: KBInfo) -> None:
        nonlocal _probe_bm25
        log.info(f"KB switch → '{kb.name}' (id={kb.id})")
        try:
            cfg = (
                RagConfig(**json.loads(session_cfg_path.read_text()))
                if session_cfg_path.exists()
                else session_cfg
            )
        except Exception:
            cfg = session_cfg
        new_vs, new_agent, new_emb = _build_components(kb, cfg)
        vs_proxy.switch(new_vs)
        agent_proxy.switch(new_agent)
        emb_proxy.switch(new_emb)
        _probe_bm25 = None
        log.info(f"Agent ready for KB '{kb.name}'")

    async def _upload_cb(file_path: Path, kb: KBInfo, extra_metadata: dict) -> None:
        try:
            upload_cfg = (
                RagConfig(**json.loads(session_cfg_path.read_text()))
                if session_cfg_path.exists()
                else session_cfg
            )
        except Exception:
            upload_cfg = session_cfg
        await enqueue_upload(
            file_path, kb, extra_metadata,
            vs_proxy=vs_proxy, kb_router=kb_router, db_dir=_DB_DIR, db_engine=_db_engine, cfg=upload_cfg,
        )

    kb_router = create_kb_router(
        db_dir=_DB_DIR,
        activate_callback=on_kb_activate,
        project_root=_ROOT,
        upload_callback=_upload_cb,
    )
    app.include_router(kb_router)

    # ── Startup: load stats if VS is already populated ────────────────────
    async def _auto_cleanup_loop() -> None:
        while True:
            try:
                await run_auto_cleanup(_DB_DIR, _db_engine)
            except Exception as exc:
                log.warning(f"Auto-cleanup error: {exc}")
            await asyncio.sleep(86_400)

    async def _startup() -> None:
        asyncio.create_task(upload_worker())  # noqa: RUF006
        asyncio.create_task(_auto_cleanup_loop())  # noqa: RUF006
        configure_loguru()
        await _conv_db.create_table()
        await _msg_db.create_table()
        await _react_db.create_table()
        await _src_db.create_table()
        await _user_db.create_table()
        from sqlalchemy import text as _sa_text
        _pk = "id BIGSERIAL PRIMARY KEY" if _database_url else "id INTEGER PRIMARY KEY"
        async with _db_engine.begin() as _conn:
            await _conn.execute(_sa_text(f"""
                CREATE TABLE IF NOT EXISTS ingest_events (
                    {_pk},
                    ts           TEXT    NOT NULL,
                    kb_id        TEXT    NOT NULL,
                    document_id  TEXT    NOT NULL,
                    filename     TEXT    NOT NULL,
                    status       TEXT    NOT NULL,
                    chunks       INTEGER,
                    file_size_mb REAL,
                    duration_ms  INTEGER,
                    error        TEXT
                )
            """))
        vs = object.__getattribute__(vs_proxy, "_obj")
        agent = object.__getattribute__(agent_proxy, "_obj")
        count = await vs.count()
        if count > 0:
            try:
                indexed_files = await vs.get_source_files()
                n_files = len(indexed_files)
            except Exception:
                indexed_files = []
                n_files = 0
            kb_router.update_stats(active_kb.id, count, n_files)
            base_prompt = session_cfg.system_prompt.strip() or _load_system_prompt()
            await _inject_source_files(agent, vs, base_prompt)
            log.info(f"Vector store loaded: {count} chunks from {n_files} files.")
        else:
            log.info("Vector store empty — use the UI to index a knowledge base.")

    app.add_event_handler("startup", _startup)

    # ── RAG Config / Reindex router ───────────────────────────────────────
    async def rebuild_callback(cfg: RagConfig, reset: bool) -> ReindexResult:
        nonlocal _probe_bm25
        try:
            reg = json.loads(kb_registry_path.read_text())
            kb = KBInfo(**reg["bases"][reg["active"]])
        except Exception:
            kb = active_kb

        try:
            chunks_n, files_n, skipped_store_n, skipped_batch_n = await run_ingestion(
                kb, reset, db_dir=_DB_DIR, cfg=cfg, db_engine=_db_engine
            )
        except RuntimeError as exc:
            log.error(f"Ingestion failed for KB '{kb.name}': {exc}")
            return ReindexResult(
                chunks_indexed=0, files_processed=0,
                files_skipped=0, files_skipped_store=0, files_skipped_batch=0,
                reset=reset,
            )

        # Rebuild so BM25 re-indexes new content
        new_vs, new_agent, new_emb = _build_components(kb, cfg)
        _probe_bm25 = None
        base_prompt = cfg.system_prompt.strip() or _load_system_prompt()
        await _inject_source_files(new_agent, new_vs, base_prompt)
        vs_proxy.switch(new_vs)

        # Use the actual VS total, not just the delta from this run.
        # When all files are already indexed (incremental run, nothing new),
        # chunks_n == 0, which would overwrite the correct count with 0.
        try:
            total_count = await new_vs.count()
            total_files = len(await new_vs.get_source_files())
        except Exception:
            total_count = chunks_n
            total_files = files_n
        kb_router.update_stats(kb.id, total_count, total_files)
        agent_proxy.switch(new_agent)
        emb_proxy.switch(new_emb)

        from datetime import datetime, timezone

        result = ReindexResult(
            chunks_indexed=chunks_n,
            files_processed=files_n,
            files_skipped=skipped_store_n + skipped_batch_n,
            files_skipped_store=skipped_store_n,
            files_skipped_batch=skipped_batch_n,
            reset=reset,
        )
        _index_status["last_result"] = result.model_dump()
        _index_status["finished_at"] = datetime.now(timezone.utc).isoformat()
        return result

    def on_agent_rebuild(cfg: RagConfig) -> None:
        nonlocal _probe_bm25
        kb = kb_router.get_active_kb()
        new_vs, new_agent, new_emb = _build_components(kb, cfg)
        vs_proxy.switch(new_vs)
        agent_proxy.switch(new_agent)
        emb_proxy.switch(new_emb)
        _probe_bm25 = None
        log.info(f"Agent rebuilt with llm={cfg.llm_backend}/{cfg.llm_model}")

    async def retrieve_callback(req: RetrieveRequest) -> RetrieveResponse:
        nonlocal _probe_bm25
        try:
            cfg = (
                RagConfig(**json.loads(session_cfg_path.read_text()))
                if session_cfg_path.exists()
                else RagConfig()
            )
        except Exception:
            cfg = RagConfig()

        top_k = cfg.retriever_top_k
        fetch_k = (
            cfg.reranking_candidate_pool
            if req.reranking_enabled
            else top_k + math.ceil(top_k * 0.4)
        )

        # Semantic retrieval — embed query and search VS directly
        emb_vectors = await emb_proxy.get_embeddings(req.query)
        sem_results = await vs_proxy.get_chunks_by_embedding(
            emb_vectors[0], fetch_k, req.filters or None
        )

        # BM25 retrieval — lazy-init and cache the retriever
        bm25_results: list = []
        if req.bm25_enabled:
            if _probe_bm25 is None:
                _probe_bm25 = BM25Retriever(vs_proxy, top_k=fetch_k)
            else:
                _probe_bm25.top_k = fetch_k
            bm25_results = await _probe_bm25.retrieve(req.query)
            # Post-filter by source_file if requested (BM25 doesn't support native filters)
            if req.filters and req.filters.get("source_file"):
                sf = req.filters["source_file"]
                bm25_results = [
                    c for c in bm25_results if c.metadata.get("source_file") == sf
                ]

        # Build per-method score maps: {chunk_id: (rank, raw_score)}
        sem_map = {c.id: (i + 1, c.score) for i, c in enumerate(sem_results)}
        bm25_map = {c.id: (i + 1, c.score) for i, c in enumerate(bm25_results)}

        # Fuse results
        chunk_map = {c.id: c for c in [*sem_results, *bm25_results]}
        all_ids = list(chunk_map.keys())

        rrf_scores: dict[str, float] = {}
        if req.bm25_enabled and sem_results and bm25_results:
            for cid in all_ids:
                score = 0.0
                if cid in sem_map:
                    score += 1.0 / (cfg.rrf_k + sem_map[cid][0])
                if cid in bm25_map:
                    score += 1.0 / (cfg.rrf_k + bm25_map[cid][0])
                rrf_scores[cid] = score
            ordered_ids = sorted(all_ids, key=lambda c: rrf_scores[c], reverse=True)[
                :fetch_k
            ]
        elif sem_results:
            ordered_ids = [c.id for c in sem_results[:fetch_k]]
        else:
            ordered_ids = [c.id for c in bm25_results[:fetch_k]]

        # Optional LLM-based reranking
        reranking_skipped = False
        pre_rerank_ranks: dict[str, int] = {}
        if req.reranking_enabled:
            try:
                utility_llm = agent_proxy.utility_llm
                candidates = [chunk_map[cid] for cid in ordered_ids if cid in chunk_map]
                for i, cid in enumerate(ordered_ids):
                    pre_rerank_ranks[cid] = i + 1
                reranker = RerankingRetriever(
                    retriever=None, llm=utility_llm, top_k=top_k  # type: ignore[arg-type]
                )
                ranked_indices = await reranker._llm_rerank(req.query, candidates)
                ordered_ids = [candidates[i].id for i in ranked_indices[:fetch_k]]
            except Exception as exc:
                log.warning(f"Probe reranking failed, returning pre-rerank order: {exc}")
                reranking_skipped = True

        # Build response
        chunks: list[ChunkResult] = []
        for rank, cid in enumerate(ordered_ids, start=1):
            chunk = chunk_map.get(cid)
            if chunk is None:
                continue
            chunks.append(
                ChunkResult(
                    id=cid,
                    content=chunk.content,
                    metadata=chunk.metadata or {},
                    final_rank=rank,
                    scores=ChunkScores(
                        semantic_score=sem_map[cid][1] if cid in sem_map else None,
                        bm25_score=bm25_map[cid][1] if cid in bm25_map else None,
                        rrf_score=rrf_scores.get(cid),
                        pre_rerank_rank=pre_rerank_ranks.get(cid),
                    ),
                )
            )

        return RetrieveResponse(
            chunks=chunks,
            top_k=top_k,
            total_returned=len(chunks),
            reranking_skipped=reranking_skipped,
        )

    rag_router = create_rag_router(
        db_dir=_DB_DIR,
        prompts_dir=_PROMPTS_DIR,
        vector_store_factory=lambda: vs_proxy,
        rebuild_callback=rebuild_callback,
        status_factory=lambda: dict(_index_status),
        query_status_factory=lambda: dict(_query_status),
        agent_rebuild_callback=on_agent_rebuild,
        cancel_callback=cancel_indexing,
        retrieve_callback=retrieve_callback,
    )
    app.include_router(rag_router)

    # ── OpenAI-compatible endpoint ────────────────────────────────────────
    openai_router = create_openai_compat_router(agent_proxy)
    app.include_router(openai_router)

    # ── Admin API ─────────────────────────────────────────────────────────
    admin_router = create_admin_router(
        db_dir=_DB_DIR,
        db_engine=_db_engine,
        is_sqlite=not bool(os.getenv("DATABASE_URL", "")),
        get_active_kb=kb_router.get_active_kb,
    )
    app.include_router(admin_router)

    # ── Branding API + static file serving for uploaded avatars ───────────
    branding_router = create_branding_router(db_dir=_DB_DIR)
    app.include_router(branding_router)

    from fastapi.staticfiles import StaticFiles
    uploads_dir = _DB_DIR / "uploads"
    uploads_dir.mkdir(exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

    return app


app = build_server()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        reload=False,
        log_level="info",
        log_config=build_log_config(),
    )
