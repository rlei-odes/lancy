# Technical Design: Retrieval Explorer — Chunk Inspector & Retrieval Probe

_Status: revised draft v2 — Q&A round incorporated_

---

## 1. Problem Statement

Tuning a RAG pipeline requires balancing multiple parameters: `top_k`, BM25 weighting, reranking thresholds, and chunking strategies. Currently, the only way to assess retrieval quality is to run a full RAG query and judge the final LLM answer. This conflates retrieval precision with LLM reasoning/formatting quality.

There is no easy way for users to:
- Inspect the raw chunks indexed for a specific document.
- Verify metadata accuracy (source files, hashes, custom tags).
- Compare different retrieval methods (Semantic vs. BM25 vs. Hybrid) side-by-side.
- Understand why a specific relevant chunk was *not* retrieved or was ranked low.

---

## 2. Proposed Solution

A dedicated **Retrieval Explorer** page (`/explorer`). It provides direct access to the vector store and the retrieval pipeline, bypassing the LLM for the core use case. It is integrated into the existing SPA and reuses the existing RAG Config sidebar.

### 2.1 Core Features

- **Retrieval Probe**: A test bench for queries. Enter a question, toggle retrieval methods (BM25, semantic, reranking), and see exactly what each retriever returns — with scores broken out per method.
- **Chunk Browser**: Deferred to v2. Requires server-side pagination and filtering over potentially very large KBs — scoping that properly is a separate task.

### 2.2 LLM Dependency

The probe is designed to work without the LLM:

| Feature | LLM needed? |
|---|---|
| Semantic retrieval | No (embedding model only) |
| BM25 retrieval | No |
| Hybrid (RRF fusion) | No |
| Reranking | **Yes** — uses `utility_llm` |

Reranking is available in the probe when the LLM is configured and reachable. If reranking is toggled on but the LLM is unavailable, the endpoint falls back to the pre-rerank order and returns a flag indicating this (`reranking_skipped: true`).

**Reranking candidate pool in the probe:** when reranking is enabled, the probe uses `reranking_candidate_pool` (from session config) as the total fetch size — consistent with the normal RAG flow. Results `top_k+1` to `reranking_candidate_pool` are shown dimmed, labelled "reranker candidate" (the reranker saw them but ranked them below the cut). When reranking is disabled, fetch size is `top_k + ceil(top_k * 0.4)` and dimmed cards are labelled "outside k".

HyDE and query expansion are **excluded from the probe** — they require an LLM and add no diagnostic value for retrieval inspection.

---

## 3. Architecture

### 3.1 Score Collection Strategy

`HybridRetriever._rrf_merge()` discards individual retriever scores — only the fused RRF score survives. To expose per-method scores, the probe endpoint **bypasses `HybridRetriever` entirely**.

Instead, the endpoint calls the semantic and BM25 sub-retrievers directly (in parallel, via `asyncio.gather`), collects both `ChunkMatch` lists, joins them by `chunk.id`, applies RRF fusion itself, and builds the score breakdown. This requires no changes to `HybridRetriever`.

Result shape per chunk:
```
semantic_score  — raw cosine similarity (None if BM25-only result)
bm25_score      — raw BM25 Okapi score (None if semantic-only result)
rrf_score       — fused RRF score (None if only one method enabled)
pre_rerank_rank — original rank before LLM reranking (None if reranking off)
final_rank      — rank in the returned list (1-based)
```

### 3.2 BM25 Filter Behavior

`BM25Retriever` loads all corpus chunks at init. Metadata filters (e.g. `source_file`) are **not** supported per-query in BM25. When a `source_file` filter is active, BM25 results are post-filtered by `source_file` after retrieval. This is consistent and avoids score distortion from asking BM25 to rank an artificially narrowed corpus.

### 3.3 Backend Endpoints

#### `POST /api/v1/rag/retrieve`

Runs the retrieval pipeline up to (optionally) reranking, no LLM answer generation.

**Request (`RetrieveRequest`):**
```json
{
  "query": "How is the carbon footprint calculated?",
  "bm25_enabled": true,
  "reranking_enabled": false,
  "filters": { "source_file": "report_2024.pdf" }
}
```

- `top_k` and `lookahead` are **not** request parameters — they are derived server-side from the active session config:
  - `top_k` = `session.retriever_top_k`
  - `lookahead` = `ceil(top_k * 0.4)` (fixed formula, not user-configurable in v1)
  - Total results returned: `top_k + lookahead`
- `filters`: optional metadata filter; applied as post-filter on BM25 results, passed natively to semantic search

**Response (`RetrieveResponse`):**
```json
{
  "chunks": [
    {
      "id": "...",
      "content": "...",
      "metadata": { "source_file": "...", "file_hash": "...", "chunk_index": 3 },
      "final_rank": 1,
      "scores": {
        "semantic_score": 0.85,
        "bm25_score": 12.4,
        "rrf_score": 0.033,
        "pre_rerank_rank": 4
      }
    }
  ],
  "reranking_skipped": false,
  "total_returned": 15
}
```

### 3.4 Backend Wiring

The probe endpoint needs access to the embedding model and sub-retrievers, which live inside `_build_components()` in `main.py`. Add one optional callback to `create_rag_router`:

- `retrieve_callback`: `async (RetrieveRequest) -> RetrieveResponse` — implemented in `main.py`, has access to the current vs/emb/bm25/reranker instances and the session config

This keeps all retriever construction in `main.py` and the router stays thin.

---

## 4. Frontend

### 4.1 Routing

The Explorer is a new route `/explorer` in the existing SPA. Navigation: a link in the sidebar footer (visible to all users; role gating deferred).

> **Layout decision:** Home and Explorer have different left-side content (Home has the conversation sidebar; Explorer has none), so a shared full-page `DashboardLayout` is not warranted. Instead:
> - `DisclaimerDialog` and `BackendStatus` move from `home.tsx` to `_app.tsx` (they are app-level globals).
> - The toggle strip + `RagConfigPanel` are extracted into a thin `RagConfigSidebar` component — the one piece genuinely shared between Home and Explorer.
> - Explorer has its own standalone layout: no left sidebar, imports `RagConfigSidebar` directly.
> - Home stays structurally unchanged; it switches to using `RagConfigSidebar` instead of inlining the toggle/panel.

### 4.2 Page Layout

The Retrieval Probe fills the main content area, with the RAG Config sidebar to the right (same sidebar as the chat view):

```
┌──────────────────────────────────┬───────────────────┐
│  RETRIEVAL PROBE                 │  RAG Config Panel │
│  [query input] [method toggles]  │                   │
│  ─────────────────────────────── │                   │
│  result cards ...                │                   │
│  ...                             │                   │
└──────────────────────────────────┴───────────────────┘
```

### 4.3 Chunk Cards (shared between both sections)

Each chunk is displayed as a card with:
- **Capped height** — content truncated at ~4 lines
- **Expand on click** — clicking the card expands it to full content; clicking again collapses
- **Metadata row** — below the content: `source_file`, `chunk_index`, `file_hash` (monospace), and any additional metadata fields as `key: value` pairs
- **Score badges** (Retrieval Probe only — not shown in Chunk Browser):
  - `BM25 12.4` — amber badge, raw score
  - `SEM 0.85` — cyan badge, semantic score
  - `RRF 0.033` — violet badge, fused score (only when both methods active)
  - `PRE-RANK #4` — gray badge, shown when reranking is active and the chunk moved in rank

### 4.4 Retrieval Probe — The Cut-off Visual

Results are ordered by final rank. Total results returned depends on the active mode:

- **Reranking off:** `top_k + ceil(top_k * 0.4)` results. Cards `1–top_k` active; remainder dimmed with label "outside k".
- **Reranking on:** `reranking_candidate_pool` results. Cards `1–top_k` active; remainder dimmed with label "reranker candidate" — these are the chunks the reranker saw and deprioritised.

The cut-off position is fixed to the response — it does **not** move when the sidebar slider changes after a query. Changing `top_k` in the sidebar requires an explicit re-run to see the new result set (keeps the tool predictable; avoids stale data behind a moving line).

### 4.5 RAG Config Sidebar in Explorer Context

The existing `RagConfigPanel` is reused as-is. In Explorer context, the sidebar drives which config the probe runs against. Changing any setting (BM25, reranking, top_k) takes effect on the next explicit re-submit.

---

## 5. Out of Scope (v1)

- **Chunk Browser** — deferred to v2; requires proper server-side pagination and mandatory filter UX for large KBs
- Explorer probe history / "Recent Probes" in sidebar — volatile React state only; no backend persistence
- Pinnable probes / ground-truth library
- Comparative view (two configs side-by-side)
- Direct editing of chunk content
- Export to CSV/JSON
- Image retrieval results in the probe
- HyDE and query expansion in the probe
- `lookahead` as a user-configurable parameter (reranking-off formula: `ceil(top_k * 0.4)`; reranking-on uses `reranking_candidate_pool`)

---

## 6. Open Questions

_(none outstanding — all layout and config questions resolved)_

---

## 7. Implementation Checklist

### Backend
- [x] Add `RetrieveRequest` / `RetrieveResponse` models to `rag_router.py`
- [x] Add `retrieve_callback` optional param to `create_rag_router`
- [x] Implement `POST /api/v1/rag/retrieve` (delegates to retrieve_callback)
- [x] Implement `retrieve_callback` in `main.py`: read session config, run BM25 + semantic in parallel, join scores, RRF, optional rerank on `top_k + lookahead` candidates

### Frontend
- [x] Move `DisclaimerDialog` and `BackendStatus` from `home.tsx` to `_app.tsx`
- [x] Extract `RagConfigSidebar` component (toggle strip + `RagConfigPanel`) from `home.tsx`
- [x] Update `home.tsx` to use `RagConfigSidebar`
- [x] Add `/explorer` route (`pages/explorer.tsx`) — no left sidebar, uses `RagConfigSidebar`
- [x] Implement `RetrievalProbe` component (query input, method toggles, result cards with cut-off visual)
- [x] Implement `ChunkCard` component (capped height, expand on click, metadata row, score badges, pre-rerank badge)
- [x] Add "Retrieval Explorer" link to sidebar footer

---

## 99. Implementation Notes

### Backend (2 files changed)

**`rag_router.py`** — added `RetrieveRequest`, `ChunkScores`, `ChunkResult`, `RetrieveResponse` models + `POST /api/v1/rag/retrieve` endpoint + optional `retrieve_callback` param to `create_rag_router`.

**`main.py`** — `_build_components` now returns the embedding model as a 3rd value; added `emb_proxy` alongside the existing `vs_proxy` and `agent_proxy`. `retrieve_callback` runs BM25 + semantic in parallel (`asyncio.gather`), builds a per-method score map keyed by chunk ID, does RRF fusion inline, and optionally LLM-reranks using the agent's `utility_llm`. The BM25 retriever instance is cached in `_probe_bm25` and invalidated on KB switch and reindex so the corpus stays in sync without rebuilding on every probe call.

### Frontend (7 files changed/created)

| File | Change |
|---|---|
| `pages/_app.tsx` | `DisclaimerDialog` + `BackendStatus` moved here — global, mounted once |
| `components/template/home.tsx` | Stripped down; now uses `RagConfigSidebar` |
| `components/sections/rag-config-sidebar.tsx` | New — extracted toggle strip + `RagConfigPanel` |
| `components/sections/sidebar/history.tsx` | Added Explorer link (flask icon) in the footer button row |
| `components/ui/chunk-card.tsx` | New — expandable card: large rank number, score badges (SEM cyan / BM25 amber / RRF violet / PRE-RANK gray), capped content with expand/collapse, metadata row in monospace |
| `components/sections/retrieval-probe.tsx` | New — query textarea, BM25/Reranking toggles, source filter, ranked result list with cutoff divider |
| `pages/explorer.tsx` | New — `/explorer` route: header with back button, scrollable probe area, `RagConfigSidebar` on the right |
