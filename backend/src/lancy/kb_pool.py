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

    def unload(self, kb_id: str) -> None:
        """Remove a KB from the pool.

        In-flight streams hold a local reference to LoadedKB and complete
        safely — Python reference counting prevents premature GC.
        """
        if kb_id not in self._pool:
            return
        name = self._pool[kb_id].kb.name
        del self._pool[kb_id]
        if self._active_id == kb_id:
            self._active_id = next(iter(self._pool), None)
        if not self._pool:
            self._emb = None
            self._emb_key = None
        log.info(f"KBPool: unloaded '{name}' (id={kb_id}), pool={list(self._pool)}")

    async def reset(self, kb: Any, cfg: Any, build_fn: Callable) -> LoadedKB:
        """Clear all entries (embedding config switch) then load a single KB."""
        cleared = list(self._pool)
        self._pool.clear()
        self._emb = None
        self._emb_key = None
        self._active_id = None
        log.info(f"KBPool: reset — cleared {cleared}")
        return await self.load(kb, cfg, build_fn)

    async def rebuild_all_agents(self, cfg: Any, build_fn: Callable) -> None:
        """Rebuild every loaded KB's agent after a RAG session config change."""
        for kb_id, entry in list(self._pool.items()):
            self._loading.add(kb_id)
            try:
                loop = asyncio.get_event_loop()
                _, new_agent, _ = await loop.run_in_executor(None, build_fn, entry.kb, cfg)
                entry.agent = new_agent
                entry.probe_bm25 = None
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
