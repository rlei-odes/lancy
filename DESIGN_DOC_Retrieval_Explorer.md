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

---

## 8. Chunk Browser (v2)

**Status:** Draft — needs review before implementation

### 8.1 Goal

Browse the raw contents of the vector store: what was chunked, how many chunks per file, what the text looks like. Filter by file and/or keyword. Spot bad chunking, verify a file was indexed, sanity-check metadata. No query required, no ranking.

### 8.2 Page Layout Change

The Explorer currently renders only the Retrieval Probe. Adding the Chunk Browser requires a **tab switcher** so the two sub-views coexist.

Two options:

**Option A — tabs below the header bar**
```
┌──────────────────────────────────┬───────────────────┐
│ ← Retrieval Explorer             │                   │
│ [Retrieval Probe] [Chunk Browser]│  RAG Config Panel │
│ ─────────────────────────────────│                   │
│  sub-view content                │                   │
└──────────────────────────────────┴───────────────────┘
```

**Option B — tabs inline in the header bar** (more compact)
```
┌──────────────────────────────────────────────────────┐
│ ← │ [Retrieval Probe] [Chunk Browser]   (subtitle)  │
└──────────────────────────────────────────────────────┘
```

**Decision: Option A.** Keeps the header clean and provides a visible structural landmark. Tabs sit just below the header bar divider, full-width across the main content area only (not spanning the RAG config sidebar).

A left-hand side navigation panel for the Explorer is intentionally deferred — that real estate is reserved for a future admin section where it will be more warranted.

Tab state is local React state, defaults to `"probe"`. Not persisted.

### 8.3 Backend: `POST /api/v1/rag/chunks`

Changed from GET to POST because the filter payload can be an arbitrary key/value map — query string encoding of a dict is awkward and brittle.

**Request body (`ChunkBrowseRequest`):**

```python
class FilterCondition(BaseModel):
    key: str = Field(..., max_length=100)
    op: Literal["eq"] = "eq"   # only "eq" in v1; Literal widens as new ops are added
    value: str = Field(..., max_length=500)

class ChunkBrowseRequest(BaseModel):
    filters: list[FilterCondition] = Field(default_factory=list)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)
```

**Why a list of condition objects instead of `dict[str, str]`:** a flat dict locks the format to equality-only forever. The list-of-conditions shape lets future operators (`contains`, `gte`, `in` for array membership) be added without a breaking API change — just widen the `Literal` and handle the new op. In v1 only `"eq"` is implemented; FastAPI rejects any other value at validation time.

**Filter logic:**
1. Translate `req.filters` to the VS interface: extract `"eq"` conditions into `dict[str, str]` and pass to `vs.get_chunks_by_filter()`. Future non-eq ops can be handled as router-level post-filters until the VS layer supports them natively.
2. Each `VectorStore` implementation translates the neutral `{key: value}` dict to its own query format (ChromaDB: `$eq`/`$and`; pgvector: SQL `WHERE` — pgvector's native SQL makes range queries and sorted pagination straightforward, a future improvement opportunity).
3. Pagination at the store level: fetch `limit + 1` rows; if `limit + 1` come back, set `has_more = True`, return only `limit`. Never loads the full dataset.
4. Sort order: insertion order (ChromaDB default). Predictable and cheap — no server-side sort needed.

**No total count.** ChromaDB has no filtered row count without fetching all IDs. The response carries `has_more`; the summary reads "showing {{shown}} chunks."

**Keyword / content search:** excluded for v1. Content search is the Retrieval Probe's job.

**New Pydantic models** (in `rag_router.py`, `# ─── Chunk browser ───` section):

```python
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
    metadata: dict[str, Any]  # missing/empty fields simply absent — frontend renders blank

class ChunkBrowseResponse(BaseModel):
    chunks: list[ChunkBrowseItem]
    returned: int
    offset: int
    has_more: bool
```

**Endpoint sketch:**

```python
@router.post("/chunks", response_model=ChunkBrowseResponse)
async def browse_chunks(req: ChunkBrowseRequest) -> ChunkBrowseResponse:
    try:
        vs = vector_store_factory()
        # translate eq conditions to neutral {key: value} dict for the VS layer
        eq_filters = {f.key: f.value for f in req.filters if f.op == "eq"} or None
        chunks = await vs.get_chunks_by_filter(eq_filters, limit=req.limit + 1, offset=req.offset)
        has_more = len(chunks) > req.limit
        page = chunks[: req.limit]
        return ChunkBrowseResponse(
            chunks=[ChunkBrowseItem(id=c.id, content=c.content, title=c.title, metadata=c.metadata) for c in page],
            returned=len(page),
            offset=req.offset,
            has_more=has_more,
        )
    except Exception as exc:
        log.warning(f"browse-chunks error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
```

**Note:** `get_chunks_by_filter` needs two changes:
1. Accept neutral `dict[str, str]` and translate to native query format internally (ChromaDB: `$eq`/`$and`)
2. Accept `limit` and `offset` parameters and pass them to the underlying store (`collection.get(limit=..., offset=...)`) — this is what prevents loading the full KB

The base class docstring should document both: the neutral filter contract and the new `limit`/`offset` parameters.

### 8.4 Frontend: `ChunkBrowser` Component

Location: `frontend/src/components/sections/chunk-browser.tsx`

#### 8.4.1 Table vs. Cards

**Decision: table layout using TanStack Table v8 (`@tanstack/react-table`).** The chunk browser is a data inspection tool — metadata fields are tabular, and a table makes it easy to scan many chunks quickly and spot patterns across rows. Cards are better when each item is a self-contained reading unit; the expandable row pattern handles chunk content well within a table. TanStack Table v8 is chosen for its column sorting, virtualization, and expandable row support — relevant at the scale of hundreds of thousands of chunks. No other table library exists in the codebase; this is a new `npm install`.

**Table layout:**

| # | File | Title | chunk_index | file_hash | [other meta cols...] |
|---|---|---|---|---|---|
| 1 | report.pdf | Executive Summary | 0 | a3f2... | ... |
| 2 | report.pdf | Key Findings | 1 | a3f2... | ... |

- **#** column: 1-based absolute position (`offset + rowIndex + 1`)
- **File** column: basename of `source_file` metadata value. Full path available on hover (tooltip). _Note: display of full path vs. basename may need iteration once real file paths are tested._
- Other columns: driven by what metadata keys exist in the current result set
- **Content:** not a table column — shown in an **expandable row** below the clicked row. Clicking a row toggles the content pane (same expand/collapse behaviour as ChunkCard but inline below the row rather than replacing it).

**Column set for v1:** `#`, `file`, `title`, `chunk_index`, `file_hash`, `mime_type`. Additional metadata columns (e.g. `author`, `document_class`, `document_type` from the Lancy schema) appear automatically when present in the result metadata. Implementation: derive columns from the union of keys present in the returned `chunks[].metadata`, minus internal ones (`embedding`, `id`).

#### 8.4.2 Metadata Filter UI

The filter bar sits above the table in a controls card. Users build filter conditions as key/value pairs.

**Structure:**

```
[File (basename dropdown ▾] [+ Add filter ▾]  [Browse button]
  └── filter row: [key dropdown/input] [=] [value input] [× remove]
  └── filter row: [key dropdown/input] [=] [value input] [× remove]
```

- **File dropdown:** special-cased as a first-class control (most common filter). Populated from `store-info.file_list`. Display: basename. Value sent: full path stored in metadata. Option: "All files" = no `source_file` filter.
- **Add filter button:** adds a key/value row for any other metadata field. Key input: free text with a `<datalist>` of known field names (the Lancy schema keys + any keys seen in the last result). Value input: free text. Remove button (×) on each row.
- All conditions (file + additional filter rows) are ANDed and sent as the `filters` dict in the POST body.
- Changing any filter and clicking Browse resets to `offset=0` and replaces results.

**Known metadata keys for the datalist** (from the Lancy schema proposal):

```
document_id, title, author, document_class, document_type,
document_created_at, document_released_at, source_url, tags,
source_file, file_hash, chunk_index, mime_type
```

These are suggestions only — the user can type any key. As the schema evolves, this list grows without backend changes.

#### 8.4.3 Pagination

"Load more" style:
- Initial fetch: `offset=0, limit=50`
- If `total > offset + returned`: show "Load more" button below the table
- Clicking appends next page (`offset += 50`)
- Summary line above table: `"{{total}} chunks · showing 1–{{shown}}"` (updated after each load)
- Re-running Browse (any filter change): reset offset=0, replace all rows

#### 8.4.4 Filter placement

**Decision: Option A — all filters above the table, Browse button triggers server fetch.**

Rationale: column-level (client-side) filters only operate on the loaded rows, not the full dataset. With load-more pagination this is misleading — the user would see 3 results and not know if there are 200 more in the store. Server-side filters are the only correct option here.

_Follow-up (v2):_ consider adding TanStack column filters as a local-refinement layer on top of already-loaded rows, with an explicit label ("filtering {{n}} loaded rows") to distinguish from server-side filtering.

#### 8.4.5 States

- **Idle** (no Browse run): prompt — "Add a filter and click Browse to inspect indexed chunks."
- **Loading**: spinner on Browse button, table disabled
- **Error**: red error box below controls
- **Empty**: "No chunks matched the current filters."
- **Results**: table + optional "Load more"

### 8.5 i18n Keys

Add under `explorer` in all four language files (`en.ts`, `de.ts`, `fr.ts`, `it.ts`):

```ts
// Tab labels
tabProbe: "Retrieval Probe",
tabBrowser: "Chunk Browser",

// Chunk browser controls
browserFileLabel: "File",
browserFileAll: "All files",
browserAddFilter: "Add filter",
browserFilterKeyPlaceholder: "metadata key…",
browserFilterValuePlaceholder: "value…",
browserBrowse: "Browse",
browserBrowsing: "Loading…",

// Chunk browser results
browserResultsSummary: "{{total}} chunks · showing 1–{{shown}}",
browserLoadMore: "Load more",
browserColNum: "#",
browserColFile: "File",
browserColChunkIndex: "Index",
browserColHash: "Hash",

// Chunk browser states
browserEmptyTitle: "No chunks matched the current filters.",
browserIdleTitle: "Add a filter and click Browse to inspect indexed chunks.",
```

`probeTitle` stays as-is for the card header inside the probe sub-view.

### 8.6 Design Decisions

#### Columns

**Fixed baseline + dynamic extras.** Baseline columns: `#`, `File`, `Title`, `chunk_index`, `mime_type`. Additional metadata keys found in the result set are appended as extra columns. Any missing or empty field renders as a blank cell — no error, no placeholder text. This handles the current test environment (no Lancy schema metadata yet) gracefully without special-casing.

**`file_hash`:** omitted from table columns — too long to read in a cell. Shown in full in the expandable row only.

**`tags`:** array type — not rendered as a table column. Shown in the expandable row (comma-joined). Excluded from the filter key datalist in v1 (equality match doesn't work for array membership; array-contains filtering deferred).

#### Filtering

**`tags` excluded from filter datalist** — noted above.

**File list fetch trigger:** on tab switch to "Chunk Browser". Cheap call, guarantees the list is fresh after a reindex without needing a dedicated refresh button.

#### Expandable Row

**Shows:** full chunk text only — no metadata repeat (it's already visible in the table columns). Monospace, no truncation, max-height with vertical scroll for very long chunks.

#### Performance

**True server-side pagination.** `limit`/`offset` pushed to the vector store — never load more than `limit + 1` rows. No cap, no total count. `has_more` flag drives the "Load more" button. Summary line: "showing {{shown}} chunks" (no total).

_Future:_ pgvector's SQL layer makes filtered counts and `ORDER BY` trivial, enabling "newest first" and exact totals. Worth revisiting when pgvector support lands.

---

### 8.7 Backend Scope — Full Picture

There are exactly four callers of `get_chunks_by_filter`:

| Caller | What it passes | Safe after change? |
|---|---|---|
| `rag_router.py` — `store_info` | `{}` (empty dict) | ✅ unchanged |
| `bm25_retriever.py` | `None` (no args — needs all chunks for index build) | ✅ unchanged |
| `context_window_retriever.py` | ChromaDB-native `{"$and": [{"source_file": {"$eq": ...}}, {"chunk_index": {"$eq": ...}}]}` | ❌ breaks — must update |
| New browse endpoint | neutral `{key: value}` | ✅ the goal |

**Important pre-existing inconsistency:** `context_window_retriever.py` currently passes ChromaDB-native filter syntax. `postgres.py` already accepts neutral `{key: value}` pairs and translates to SQL internally. This means `context_window_retriever` almost certainly already fails silently when pgvector is the active backend — it's not something we're introducing. Our change fixes it as a side effect.

The fix for `context_window_retriever.py` is clean:
```python
# before (ChromaDB-specific, breaks pgvector)
{"$and": [{"source_file": {"$eq": source}}, {"chunk_index": {"$eq": idx + offset}}]}

# after (neutral, works with both backends)
{"source_file": source, "chunk_index": str(idx + offset)}
```

**5 files change in `conversational-toolkit`**, nothing in the RAG query path changes behaviour:

1. `vectorstores/base.py` — add `limit: int | None = None` and `offset: int = 0` to abstract method; update docstring: _"filters is a flat `{field: value}` dict, all conditions ANDed, implementations translate to native query format"_
2. `vectorstores/chromadb.py` — translate neutral `{key: value}` → ChromaDB `$eq`/`$and` internally; pass `limit`/`offset` to `collection.get(limit=..., offset=...)`
3. `vectorstores/postgres.py` — already neutral ✅; add `limit`/`offset` to the SQL query (`.limit()` / `.offset()`)
4. `retriever/context_window_retriever.py` — switch to neutral format (fixes latent pgvector bug)
5. `backend/src/lancy/rag_router.py` — new `ChunkBrowseRequest/Item/Response` models + `POST /api/v1/rag/chunks` endpoint

### 8.8 Implementation Checklist

**Backend — conversational-toolkit**
- [ ] `vectorstores/base.py`: add `limit`/`offset` to `get_chunks_by_filter` signature; update docstring with neutral filter contract
- [ ] `vectorstores/chromadb.py`: translate neutral `{key: value}` → ChromaDB `$eq`/`$and` internally; pass `limit`/`offset` to `collection.get()`
- [ ] `vectorstores/postgres.py`: add `limit`/`offset` to SQL query
- [ ] `retriever/context_window_retriever.py`: replace ChromaDB-native syntax with neutral `{"source_file": source, "chunk_index": str(idx + offset)}`

**Backend — lancy**
- [ ] `rag_router.py`: add `ChunkBrowseRequest`, `ChunkBrowseItem`, `ChunkBrowseResponse` models
- [ ] `rag_router.py`: add `POST /api/v1/rag/chunks` endpoint (fetch `limit+1`, derive `has_more`, return page)
- [ ] Verify: empty filters; single filter; multi-filter AND; `has_more` correct; missing metadata fields pass through as-is (blank in UI)

**Frontend**
- [ ] `npm install @tanstack/react-table` in `frontend/`
- [ ] `explorer.tsx`: add `activeTab` state + tab switcher UI below the header bar (Option A)
- [ ] `chunk-browser.tsx` (new):
  - [ ] File dropdown (basename display, full value sent; populated on tab switch via `store-info`)
  - [ ] Add-filter rows (key datalist + value input + remove; `tags` excluded from datalist)
  - [ ] Browse button
  - [ ] TanStack Table: fixed baseline columns (`#`, File, Title, chunk_index, mime_type) + dynamic extra columns from result metadata; blank cell for missing/empty fields
  - [ ] Expandable row: full chunk text only (scrollable, monospace) — metadata already in table columns
  - [ ] Load-more driven by `has_more`; summary line "showing {{shown}} chunks"
  - [ ] States: idle, loading, error, empty
- [ ] i18n: add keys to `en.ts` first; translate to `de.ts`, `fr.ts`, `it.ts`

**Frontend**
- [ ] `npm install @tanstack/react-table` in `frontend/`
- [ ] Add `activeTab` state + tab switcher UI to `explorer.tsx` (below header bar, Option A)
- [ ] Create `chunk-browser.tsx`:
  - [ ] File dropdown (basename display, full value sent; populated on tab switch via `store-info`)
  - [ ] Add-filter rows (key datalist + value input + remove; `tags` excluded from datalist)
  - [ ] Browse button
  - [ ] TanStack Table: fixed baseline columns + dynamic extra columns; blank cell for missing/empty metadata
  - [ ] Expandable row: full content (scrollable) + all metadata key/value pairs
  - [ ] Load-more pagination driven by `has_more`; summary "showing {{shown}} chunks"
  - [ ] All four states: idle, loading, error, empty
- [ ] Add i18n keys to `en.ts`, `de.ts`, `fr.ts`, `it.ts`

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
