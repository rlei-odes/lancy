# Design Doc: Multi-KB Concurrent Retrieval

**Status:** Draft v2 — inputs from design review incorporated  
**Branch:** `feat/multi-kb-concurrent`

---

## 1. Problem Statement

The current architecture maintains a single globally active KB: one `(vs_proxy, agent_proxy, emb_proxy)` triplet that every request shares. Switching KBs via `POST /api/v1/kb/{id}/activate` is a destructive swap — it swaps all three proxies in-place and tears down the previous KB's retriever.

This creates two practical problems:

- **No concurrent multi-user KB access.** If Alice is querying KB-A and Bob needs KB-B, Bob's activation will interrupt Alice mid-stream.
- **Expensive, blocking KB switches.** `_build_components()` loads the embedding model synchronously. On a local model (e.g. `nomic-embed-text-v1` via SentenceTransformers) this can take 10–30 seconds. During that window the event loop is partially blocked, producing "Backend not responding" errors in the frontend. The RAG Panel sidebar also remains interactive, allowing config changes against a partially-torn-down KB.

The two known UX bugs in the backlog (sidebar not locked during KB change; "Backend not responding" on switch) are both symptoms of this single-instance architecture.

---

## 2. Goals

- Allow multiple users to query **different KBs simultaneously** without mutual interference.
- Each active conversation is **bound to a KB at creation time** and continues to use that KB regardless of what other users do.
- KBs in the concurrent pool **share one embedding model instance**, keeping GPU memory consumption flat.
- KB loading is **lazy and non-blocking** — the first query to a not-yet-loaded KB triggers load; the server stays responsive during that time.
- Fix the two known bugs as a byproduct: lock the sidebar during KB load; eliminate the "Backend not responding" window.

## 3. Non-Goals

- **Per-user accounts or session isolation.** Lancy currently has no per-user accounts — all users share one password (role-based: user / admin). We are not adding a concept like "Alice is permanently assigned to KB-A, Bob to KB-B." KB choice is per-conversation: whoever starts a conversation picks the KB for it, and that choice sticks for the life of that conversation. Two users can independently start conversations against different KBs at the same time — that is the concurrency this feature enables — but there is no server-enforced mapping of identity → KB.
- **Mid-conversation KB switch.** A conversation is bound at creation. Switching KB requires starting a new conversation.
- **Multiple simultaneous embedding models.** The pool is constrained to one active embedding config. KBs with different embedding backends/models require a full pool reset (same cost as today's KB switch, but rare).
- **Automatic memory management / eviction** in v1. That is a follow-on.

---

## 4. Current Architecture

```
POST /api/v1/kb/{id}/activate
    → on_kb_activate(kb)
        → _build_components(kb, cfg)          # loads emb, vs, builds agent
        → vs_proxy.switch(new_vs)             # global swap
        → agent_proxy.switch(new_agent)       # global swap
        → emb_proxy.switch(new_emb)           # global swap

GET /api/v1/rag/chat (via ConversationalToolkitController)
    → agent_proxy.answer_stream(...)          # always uses current global agent
```

`knowledge_bases.json` has a single `active` field. The frontend reads this and reflects it as the "active KB" globally. `rag_config.json` is also a single global config.

The `Conversation` record already stores `kb_id` and `kb_name` as metadata at creation time — but this is decorative; routing doesn't use it.

---

## 5. Proposed Architecture

### 5.1 Backend: KBPool

Introduce a `KBPool` class in a new `backend/src/lancy/kb_pool.py` — not in `main.py`, which is already large enough. `main.py` imports and instantiates it.

```python
@dataclass
class LoadedKB:
    kb: KBInfo
    vs: VectorStore
    agent: CustomRAG
    probe_bm25: BM25Retriever | None = None  # lazy-init cache for the Retrieval Probe

class KBPool:
    def __init__(self):
        self._pool: dict[str, LoadedKB] = {}
        self._emb: Any = None
        self._emb_key: tuple[str, str] | None = None  # (backend, model)
        self._loading: set[str] = set()               # kb_ids being loaded

    def emb_key_for(self, kb: KBInfo) -> tuple[str, str]:
        return (kb.embedding_backend, kb.embedding_model)

    def is_compatible(self, kb: KBInfo) -> bool:
        if self._emb_key is None:
            return True  # pool is empty — any KB is compatible
        return self.emb_key_for(kb) == self._emb_key

    async def load(self, kb: KBInfo, cfg: RagConfig) -> LoadedKB:
        """Load a KB into the pool. No-op if already loaded. Raises if incompatible."""
        if kb.id in self._pool:
            return self._pool[kb.id]
        if not self.is_compatible(kb):
            raise EmbeddingConflict(kb.id, self._emb_key, self.emb_key_for(kb))
        # Heavy work goes to executor so the event loop stays free
        loop = asyncio.get_event_loop()
        self._loading.add(kb.id)
        try:
            vs, agent, emb = await loop.run_in_executor(
                None, _build_components, kb, cfg
            )
        finally:
            self._loading.discard(kb.id)
        if self._emb is None:
            self._emb = emb
            self._emb_key = self.emb_key_for(kb)
        entry = LoadedKB(kb=kb, vs=vs, agent=agent)
        self._pool[kb.id] = entry
        return entry

    def get(self, kb_id: str) -> LoadedKB | None:
        return self._pool.get(kb_id)

    def unload(self, kb_id: str) -> None:
        self._pool.pop(kb_id, None)
        if not self._pool:
            self._emb = None
            self._emb_key = None

    def reset(self, kb: KBInfo, cfg: RagConfig) -> Coroutine:
        """Unload all KBs and reload with a new embedding config."""
        self._pool.clear()
        self._emb = None
        self._emb_key = None
        return self.load(kb, cfg)
```

`_build_components()` in `main.py` already takes `(kb, cfg)` and returns `(vs, agent, emb)`. The only change is wrapping its call in `run_in_executor` — this alone fixes the "Backend not responding" bug.

### 5.2 Per-Conversation KB Routing

The `ConversationalToolkitController` holds a single `agent`. Replace `agent_proxy` with a `DispatchingAgent`:

```python
class DispatchingAgent:
    """Wraps KBPool. Routes answer_stream() to the correct per-conversation agent.
    Implements the same interface as CustomRAG so the controller sees one agent."""

    def __init__(self, pool: KBPool, conv_db, fallback_kb_id: str):
        self._pool = pool
        self._conv_db = conv_db
        self._fallback = fallback_kb_id

    async def answer_stream(self, query_with_context):
        kb_id = await self._resolve_kb(query_with_context.conversation_id)
        entry = self._pool.get(kb_id)
        if entry is None:
            raise RuntimeError(f"KB '{kb_id}' is not loaded. Activate it first.")
        async for chunk in entry.agent.answer_stream(query_with_context):
            yield chunk

    async def _resolve_kb(self, conversation_id: str | None) -> str:
        if conversation_id:
            conv = await self._conv_db.get(conversation_id)
            if conv and conv.metadata and conv.metadata.get("kb_id"):
                return conv.metadata["kb_id"]
        return self._fallback
```

The `controller` receives `DispatchingAgent` as its `agent`. No changes to the controller library are required.

**Routing decision (Q1):** The preferred approach is to pass `conversation_id` through `query_with_context`. This requires verifying that `ConversationalToolkitController` populates it before calling `answer_stream`. If it does not, the fallback is a Python `contextvars.ContextVar` set in the HTTP middleware from the request body — keeping the routing logic in `DispatchingAgent` either way.

### 5.3 Startup / Activation Flow

**Startup:** Load only the "active" KB (as today). The pool starts with one entry. This preserves cold-start behavior.

**Activate (load into pool):** `POST /api/v1/kb/{id}/activate` semantics change:
- If KB is compatible with the current pool embedding → add to pool (lazy; actually loaded on first query or eagerly in background).
- If incompatible → requires `?reset=true` query param → `pool.reset(kb, cfg)`.
- Response is immediate; loading happens asynchronously in a background task.
- A new `GET /api/v1/kb/pool` endpoint exposes which KBs are currently loaded and which are loading.

**Deactivate (unload):** `POST /api/v1/kb/{id}/deactivate` (new endpoint) → `pool.unload(kb_id)`. Conversations already using this KB finish their in-flight stream; new conversations cannot pick this KB until it is reloaded.

**`knowledge_bases.json` `active` field:** Keep for backward compatibility and to designate the "default KB" for new conversations when no KB is explicitly chosen. It no longer means "the only KB in use".

### 5.4 API Changes

| Endpoint | Change |
|---|---|
| `POST /api/v1/kb/{id}/activate` | Returns 409 if incompatible and `?reset=true` not set. Load is now async (fires background task). |
| `POST /api/v1/kb/{id}/deactivate` | **New.** Removes KB from pool. |
| `GET /api/v1/kb/pool` | **New.** Returns `{ loaded: [kb_id, ...], loading: [kb_id, ...], emb_key: {backend, model} }` |
| `POST /api/v1/kb/{id}/activate` | **New param** `?reset=true` — clears pool before loading (for incompatible embedding switch). |
| `POST /api/v1/messages` (from toolkit) | `MessageInput` extended with optional `kb_id`/`kb_name`. Used by the client to specify the KB for new conversations; ignored for messages in existing conversations. |

### 5.5 Frontend Changes

#### KB Dropdown in the RAG Config Panel (existing, updated behavior)

The existing KB dropdown in the RAG Config Panel remains the entry point for KB selection — no new "new conversation" flow or modal is added. Its behavior changes:

- Selecting a KB no longer triggers a blocking global swap. It calls `POST /api/v1/kb/{id}/activate`, which adds the KB to the pool asynchronously. That KB becomes the **default KB for the next new conversation** started by this client.
- **Compatible KBs** (same embedding backend + model as the current pool) appear normally and are selectable by any role.
- **Incompatible KBs** (different embedding config) are handled by role:
  - *User role:* greyed out and not selectable. Tooltip: *"Uses a different embedding model."*
  - *Admin role:* shown with a red/warning indicator and remain selectable. Selecting one fires `POST /api/v1/kb/{id}/activate?reset=true`, which clears the entire pool before loading the new KB. No confirmation dialog in v1 — the red indicator is the warning.
- The "active KB" label in the panel reflects the pool's current default (the `active` field in `knowledge_bases.json`). When viewing a conversation that is bound to a different KB, the panel can optionally surface that KB as read-only context; this is a UI detail to decide during implementation.

#### Sidebar Locking During Load

When a KB is loading (present in the pool's `loading` set as reported by `GET /api/v1/kb/pool`):
- Disable the KB dropdown and any reindex/rebuild controls.
- Show a loading indicator (spinner next to the KB name).
- Poll `GET /api/v1/kb/pool` every 2 s until the KB_id leaves `loading`.

This directly fixes the bug: the sidebar is locked for the duration of the blocking model load.

---

## 6. Embedding Compatibility Contract

A KB is **compatible** with the current pool if:
```
kb.embedding_backend == pool.emb_key[0]
and kb.embedding_model  == pool.emb_key[1]
```

This is intentionally strict. Two KBs using `nomic-ai/nomic-embed-text-v1` via `local` backend are compatible; the same model via `ollama` backend is not (different code path, potentially different vector space due to task prefixes).

**Why not share across backends?** The embedding _vectors_ produced by the same model via `local` vs `ollama` may differ numerically due to batching, quantization, and normalization differences. Sharing an `emb` instance across backends risks silent retrieval degradation.

**Nomic prefix flag:** The `nomic_prefix` flag on each KB is part of the embedding call, not just the model name. A future refinement should include it in the compatibility key. For v1, keep it out of scope — document it as a known limitation.

---

## 7. Pool Eviction (future)

Not required for v1. Placeholder design:

- Track `last_query_at` per pool entry.
- When total loaded KBs exceed `MAX_POOL_SIZE` (env var, default: 3), evict the LRU entry on the next `pool.load()`.
- Surface eviction events in `GET /api/v1/kb/pool` (add `evicted_at` field).
- Option: `pinned: bool` flag per KB to prevent eviction (for high-traffic KBs).

---

## 8. Known Issues Being Fixed

| Bug | Root Cause | Fix |
|---|---|---|
| "Backend not responding" during KB switch | `_build_components()` runs blocking model-load on the event loop | Wrap in `run_in_executor`; load is now async |
| Sidebar editable during KB switch | No client-side lock | Poll `GET /api/v1/kb/pool`; disable sidebar while `kb_id` in `loading` |
| Multi-user KB display resets on reload | Frontend derived active KB from server global state (`kbRegistry.active`) | Per-tab `sessionStorage` persists the selected KB; restored on mount if still valid |
| Multi-user new conversations use wrong KB | `conversation_metadata_provider` is a global callback; last session to call `activate` wins | Client passes `kb_id`/`kb_name` in `MessageInput` for new conversations; backend prefers it over global active |

---

## 9. Decisions

**Q1 — Routing via `conversation_id` (decided: yes).**  
`DispatchingAgent` resolves the KB by looking up `conversation_id` in the conversation DB. First verify that `ConversationalToolkitController` populates `query_with_context.conversation_id`; if not, use a `contextvars.ContextVar` set in HTTP middleware.

**Q2 — `kb_id` derived from conversation record (decided: yes, with one exception).**  
For existing conversations, `kb_id` is read from the stored conversation record — no per-message override. For *new* conversations, the client passes `kb_id`/`kb_name` in the `MessageInput` body so the frontend's per-session KB selection (stored in `sessionStorage`) is used instead of the global server active. This was necessary because `conversation_metadata_provider` is a global callback with no per-session context; in a multi-user scenario the global active KB can be set by any session at any time.

**Q3 — Default KB from `knowledge_bases.json` `active` field (decided: yes).**  
The `active` field in `knowledge_bases.json` designates the default. It is loaded at startup and is the fallback in `DispatchingAgent._resolve_kb()` when no conversation context is available.

**Q4 — Draining guard for in-flight streams (decided: yes, lightweight impl).**  
`pool.unload()` sets a `draining` flag on the `LoadedKB` entry rather than removing it immediately. A `ref_count` on the entry (`+1` on stream start, `-1` on finish) triggers the actual removal when it hits zero. This is ~10 lines of code and prevents a hard cut of live streams.

**Q5 — RAG config changes rebuild all pool entries (decided: all, run sequentially in executor).**  
`on_agent_rebuild(cfg)` is an admin-only action and therefore rare. When called, it rebuilds the agent for every KB currently in the pool (each call wrapped in `run_in_executor`). LLM instances are cheap to reinstantiate (Ollama/LiteLLM are HTTP clients, no weights loaded client-side), so the cost is low even with 2–3 KBs resident.

**Q6 — Retrieval Probe targets the pool's active KB (decided).**  
`retrieve_callback` uses `vs` and `emb` from whichever KB is currently designated active in `knowledge_bases.json`. The RAG Parameters sidebar is visible on the Retrieval Explorer page, making the selected KB unambiguous to the user. No per-KB selector needed in the probe UI.

**Q7 — BM25 cache is per loaded KB (decided).**  
The `_probe_bm25` closure variable in `build_server()` moves into `LoadedKB.probe_bm25`. Each pool entry manages its own lazy-init BM25 cache, reset to `None` when the entry is reloaded.

---

## 10. Implementation Plan

Phase 1 — Backend (no frontend changes required for basic function):

1. Extract `KBPool` class; move pool state out of closures in `build_server()`.
2. Wrap `_build_components()` call in `run_in_executor` → fix "backend not responding".
3. Implement `DispatchingAgent`; wire into `controller`.
4. Add `GET /api/v1/kb/pool` and `POST /api/v1/kb/{id}/deactivate` endpoints.
5. Change `POST /api/v1/kb/{id}/activate` to be non-destructive (add-to-pool) with `?reset=true` escape hatch.
6. Write tests: pool load/unload/compatibility, dispatching routing.

Phase 2 — Frontend:

7. Update existing RAG Panel KB dropdown: compatible KBs selectable normally; incompatible KBs greyed out (user role) or red (admin role).
8. Sidebar locking during KB load (poll `GET /api/v1/kb/pool`, disable dropdown + reindex controls).
9. Per-conversation KB badge in conversation list / header (shows which KB a conversation was bound to).
10. Admin: wire incompatible KB selection to `?reset=true` activate call (no confirmation dialog in v1 — red indicator is the warning).
