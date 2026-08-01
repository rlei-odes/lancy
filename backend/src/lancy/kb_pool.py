"""KBPool — manages a pool of concurrently loaded Knowledge Bases.

All KBs in the pool share one embedding model instance; (embedding_backend,
embedding_model) must match for concurrent use. KBs with a different embedding
config require a full pool reset before they can be loaded.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("uvicorn")


async def _dispose_vs(vs_instance: Any) -> None:
    """Close a vector store's asyncpg connection pool, if it has one.

    Mirrors ingestion._dispose_vs (kept local to avoid a pool -> ingestion import).
    make_vector_store("pgvector", ...) opens a fresh pool per call, so a replaced
    instance must be disposed or its connections leak until Postgres runs out.
    No-op for ChromaDB stores, which have no `.engine`.
    """
    try:
        engine = getattr(vs_instance, "engine", None)
        if engine is not None:
            await engine.dispose()
    except Exception as exc:
        log.warning(f"KBPool: vector store dispose failed: {exc}")


async def _dispose_agent_stores(agent: Any) -> None:
    """Dispose the extra stores an agent owns (image store, when enabled)."""
    for store in getattr(agent, "aux_vector_stores", None) or []:
        await _dispose_vs(store)


# A stream captures its LoadedKB — and through it the vector store — before it
# starts yielding, so a store that has just been replaced can still be serving a
# query. Refcounting used to make that safe; an explicit dispose does not. Wait
# out the retrieval window before closing the pool. Retrieval happens at the head
# of answer_stream, so this only has to outlast retrieval, not generation.
_DISPOSE_GRACE_SECONDS = 60.0

_pending_disposals: set = set()


def _dispose_soon(vs: Any = None, agent: Any = None) -> None:
    """Schedule a displaced store and/or agent's stores for deferred disposal."""

    async def _run() -> None:
        await asyncio.sleep(_DISPOSE_GRACE_SECONDS)
        if vs is not None:
            await _dispose_vs(vs)
        if agent is not None:
            await _dispose_agent_stores(agent)

    try:
        task = asyncio.create_task(_run())
    except RuntimeError:  # no running loop (e.g. teardown) — process exit closes them
        return
    # asyncio holds only a weak reference to tasks; without this the disposal can
    # be garbage-collected before it runs.
    _pending_disposals.add(task)
    task.add_done_callback(_pending_disposals.discard)


def _release_shared_embedding_model() -> None:
    """Drop the process-wide embedding model and return its VRAM.

    Imported lazily so this module stays free of heavyweight (torch-pulling)
    imports at load time.
    """
    try:
        from lancy.feature0_baseline_rag import clear_embedding_cache

        clear_embedding_cache()
    except Exception as exc:  # never let cleanup break a pool operation
        log.warning(f"KBPool: embedding cache release failed: {exc}")


class EmbeddingConflict(Exception):
    """Raised when adding a KB whose embedding config differs from the pool's."""

    def __init__(self, kb_id: str, pool_key: tuple | None, kb_key: tuple) -> None:
        pool_str = f"{pool_key[0]}/{pool_key[1]}" if pool_key else "none"
        kb_str = f"{kb_key[0]}/{kb_key[1]}"
        super().__init__(
            f"KB '{kb_id}' uses embedding {kb_str}, "
            f"but pool is locked to {pool_str}. "
            "Pass reset=True to clear the pool first."
        )
        self.kb_id = kb_id


@dataclass
class LoadedKB:
    kb: Any       # KBInfo
    vs: Any       # VectorStore
    agent: Any    # CustomRAG
    probe_bm25: Any = None  # BM25Retriever | None, lazy-init per-KB cache for Retrieval Probe


class KBPool:
    """Asyncio-safe pool of loaded KBs sharing one embedding model instance."""

    def __init__(self) -> None:
        self._pool: dict[str, LoadedKB] = {}
        self._emb: Any = None
        self._emb_key: tuple[str, str] | None = None
        self._loading: set[str] = set()
        self._active_id: str | None = None

    # ── Compatibility ─────────────────────────────────────────────────────────

    @staticmethod
    def _key(kb: Any) -> tuple[str, str]:
        return (kb.embedding_backend, kb.embedding_model)

    def is_compatible(self, kb: Any) -> bool:
        return self._emb_key is None or self._key(kb) == self._emb_key

    # ── Core operations ───────────────────────────────────────────────────────

    async def load(self, kb: Any, cfg: Any, build_fn: Callable) -> LoadedKB:
        """Add a KB to the pool. No-op if already loaded. Raises EmbeddingConflict if incompatible."""
        if kb.id in self._pool:
            self._active_id = kb.id
            return self._pool[kb.id]
        if not self.is_compatible(kb):
            raise EmbeddingConflict(kb.id, self._emb_key, self._key(kb))

        log.info(f"KBPool: loading '{kb.name}' (id={kb.id}) ...")
        self._loading.add(kb.id)
        try:
            loop = asyncio.get_event_loop()
            vs, agent, emb = await loop.run_in_executor(None, build_fn, kb, cfg)
        finally:
            self._loading.discard(kb.id)

        if self._emb is None:
            self._emb = emb
            self._emb_key = self._key(kb)

        entry = LoadedKB(kb=kb, vs=vs, agent=agent)
        self._pool[kb.id] = entry
        self._active_id = kb.id
        log.info(f"KBPool: loaded '{kb.name}', pool={list(self._pool)}")
        return entry

    def get(self, kb_id: str) -> LoadedKB | None:
        return self._pool.get(kb_id)

    def get_active(self) -> LoadedKB | None:
        if self._active_id and self._active_id in self._pool:
            return self._pool[self._active_id]
        return next(iter(self._pool.values()), None)

    def set_active(self, kb_id: str) -> None:
        if kb_id in self._pool:
            self._active_id = kb_id

    async def unload(self, kb_id: str) -> None:
        """Remove a KB from the pool and release the resources it owned.

        In-flight streams hold a local reference to LoadedKB and complete
        safely — Python reference counting prevents premature GC. The vector
        store's connection pool, however, is not reclaimed by refcounting; it
        must be disposed explicitly or its Postgres connections leak.
        """
        if kb_id not in self._pool:
            return
        entry = self._pool.pop(kb_id)
        if self._active_id == kb_id:
            self._active_id = next(iter(self._pool), None)
        if not self._pool:
            self._emb = None
            self._emb_key = None
            _release_shared_embedding_model()
        _dispose_soon(vs=entry.vs, agent=entry.agent)
        log.info(f"KBPool: unloaded '{entry.kb.name}' (id={kb_id}), pool={list(self._pool)}")

    async def reset(self, kb: Any, cfg: Any, build_fn: Callable) -> LoadedKB:
        """Clear all entries (embedding config switch) then load a single KB."""
        cleared = list(self._pool)
        evicted = list(self._pool.values())
        self._pool.clear()
        self._emb = None
        self._emb_key = None
        self._active_id = None
        # The incoming KB uses a different embedding config by definition (that is
        # what forces a reset), so the cached model is dead weight on the GPU.
        # Release it immediately — load() is about to allocate the replacement and
        # the GPU may not have room for both. The evicted stores are cheap by
        # comparison and go through the deferred path, which keeps any in-flight
        # query working.
        _release_shared_embedding_model()
        for entry in evicted:
            _dispose_soon(vs=entry.vs, agent=entry.agent)
        log.info(f"KBPool: reset — cleared {cleared}")
        return await self.load(kb, cfg, build_fn)

    async def rebuild_all_agents(self, cfg: Any, build_fn: Callable) -> None:
        """Rebuild every loaded KB's agent after a RAG session config change."""
        for kb_id, entry in list(self._pool.items()):
            self._loading.add(kb_id)
            try:
                loop = asyncio.get_event_loop()
                new_vs, new_agent, emb = await loop.run_in_executor(None, build_fn, entry.kb, cfg)
                # Adopt the whole tuple. The new agent's retrievers are bound to
                # new_vs and emb; keeping the previous ones on the entry left two
                # live vector stores and two embedding models per rebuild, neither
                # of which was ever released. The displaced store owns a connection
                # pool, so it is disposed rather than merely dropped.
                old_vs, old_agent = entry.vs, entry.agent
                entry.vs = new_vs
                entry.agent = new_agent
                entry.probe_bm25 = None
                self._emb = emb
                _dispose_soon(
                    vs=old_vs if old_vs is not new_vs else None,
                    agent=old_agent if old_agent is not new_agent else None,
                )
                log.info(f"KBPool: rebuilt agent for '{kb_id}'")
            except Exception as exc:
                log.error(f"KBPool: agent rebuild failed for '{kb_id}': {exc}")
            finally:
                self._loading.discard(kb_id)

    @property
    def emb(self) -> Any:
        return self._emb

    def status(self) -> dict:
        emb_key = (
            {"backend": self._emb_key[0], "model": self._emb_key[1]}
            if self._emb_key
            else None
        )
        return {
            "loaded": list(self._pool),
            "loading": list(self._loading),
            "active": self._active_id,
            "emb_key": emb_key,
        }


class DispatchingAgent:
    """Routes answer_stream() to the correct per-conversation agent via KBPool.

    Passed to ConversationalToolkitController as its single agent. Resolves the
    target KB by looking up conversation.kb_id from the DB; falls back to the
    pool's active KB when no match is found.
    """

    def __init__(
        self,
        pool: KBPool,
        conv_db: Any,
        active_kb_id_fn: Callable[[], str],
    ) -> None:
        self._pool = pool
        self._conv_db = conv_db
        self._active_kb_id_fn = active_kb_id_fn

    async def _resolve_kb_id(self, conversation_id: str | None) -> str:
        if conversation_id:
            try:
                conv = await self._conv_db.get_conversation_by_id(conversation_id)
                if conv and conv.kb_id:
                    return conv.kb_id
            except Exception:
                pass
        return self._active_kb_id_fn()

    async def answer_stream(self, query_with_context: Any):
        from conversational_toolkit.agents.base import AgentAnswer
        from conversational_toolkit.llms.base import MessageContent

        kb_id = await self._resolve_kb_id(
            getattr(query_with_context, "conversation_id", None)
        )
        entry = self._pool.get(kb_id) or self._pool.get_active()
        if entry is None:
            yield AgentAnswer(
                content=[MessageContent(
                    type="text",
                    text="No knowledge base is loaded. Please activate one first.",
                )]
            )
            return
        async for chunk in entry.agent.answer_stream(query_with_context):
            yield chunk

    @property
    def utility_llm(self) -> Any:
        """Exposes the active KB's utility LLM (used by the Retrieval Probe)."""
        entry = self._pool.get_active()
        return entry.agent.utility_llm if entry else None
