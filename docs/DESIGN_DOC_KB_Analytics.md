# Design Doc: KB Analytics Tab

**Status:** Draft  
**Scope:** Explorer page — third tab ("Analytics"), alongside Retrieval Probe and Chunk Browser.

---

## Problem

Once a KB grows beyond a handful of documents it becomes hard to reason about its health: are chunks well-sized? Which documents dominate? When was it last populated? These questions currently require manual queries against the vector store. A dedicated analytics tab answers them at a glance.

---

## What We Want to Show

| Panel | Data |
|---|---|
| Chunk size distribution | Histogram with uniform 200-char buckets: 0–200, 200–400, … 1800–2000, 2000+ |
| Chunks per document distribution | Histogram: one bar per chunk count (1–100) on x-axis, number of documents with that count on y-axis, plus a single 100+ bar. Reveals the distribution and outliers without labelling individual documents. |
| Ingestion timeline | Bar chart of chunks indexed per run, with timestamp |
| Summary row | Total chunks, total documents, avg/p50/p95 chunk size |
| Retrieval hit frequency _(optional, later)_ | Which chunks/documents appear most in retrieval results |

---

## Core Design Question: When Do We Compute Stats?

### Option A — Live query on page load

Fetch all chunks from ChromaDB at request time and compute on the fly.

**The problem:** `collection.get(include=["metadatas", "documents"])` for a 50k-chunk KB returns ~50–100 MB of data into Python memory, takes several seconds, and ties up the event loop. `get_source_files()` and `get_file_hashes()` already do a metadata-only scan and are noticeably slow on larger KBs — a full document scan is an order of magnitude heavier.

**Verdict:** Not suitable. Rejected.

---

### Option B — Pre-compute at ingestion time (chosen)

During `_run_ingestion()`, text chunks are already fully loaded in memory as Python `Chunk` objects (line ~595 in `main.py`, after `load_chunks()` returns and before `build_vector_store()` runs). Computing stats at that point costs nothing extra — the data is already there.

After ingestion completes, write a `kb_stats_{kb_id}.json` sidecar file to the `db/` directory. The stats endpoint reads this file — a near-instant operation.

**Why this is the right call:**
- Zero runtime cost on analytics page load
- Natural hook already exists: `rebuild_callback()` and `_bg_ingest()` both call `kb_router.update_stats()` at the end of a run — the new stats computation slots in at the same point
- Stats are a stable snapshot of the last index run, which is exactly what's useful
- File survives server restarts, requires no schema migration

---

## Stats JSON Schema

File: `backend/src/lancy/db/kb_stats_{kb_id}.json`

```json
{
  "kb_id": "default",
  "computed_at": "2026-04-25T10:00:00Z",
  "scope": "full",
  "total_chunks": 1247,
  "total_documents": 23,
  "avg_chunk_chars": 734,
  "p50_chunk_chars": 680,
  "p95_chunk_chars": 1850,
  "chunk_size_distribution": {
    "0-200": 45,
    "200-400": 120,
    "400-600": 192,
    "600-800": 210,
    "800-1000": 276,
    "1000-1200": 180,
    "1200-1400": 98,
    "1400-1600": 64,
    "1600-1800": 34,
    "1800-2000": 16,
    "2000+": 12
  },
  "chunks_per_document": {
    "a1b2c3d4": {"source_file": "report_2024.pdf", "chunk_count": 87, "indexed_at": "2026-04-20T10:00:00Z"},
    "e5f6a7b8": {"source_file": "guidelines.md",   "chunk_count": 34, "indexed_at": "2026-04-20T10:00:00Z"}
  },
  "chunks_per_document_distribution": {
    "1": 3, "2": 2, "3": 1, "4": 2, "5": 0,
    "...": "one entry per integer 1–100",
    "100+": 2
  },
  "ingestion_history": [
    {
      "timestamp": "2026-04-20T10:00:00Z",
      "chunks_added": 1100,
      "files_added": 20,
      "files_skipped_store": 0,
      "files_skipped_batch": 0,
      "was_reset": true
    },
    {
      "timestamp": "2026-04-25T10:00:00Z",
      "chunks_added": 147,
      "files_added": 3,
      "files_skipped_store": 20,
      "files_skipped_batch": 0,
      "was_reset": false
    }
  ]
}
```

`scope` is `"full"` for a reset run or when the entire KB was computed, `"incremental"` for a partial run where only new-chunk data was merged in. Shown in the UI as a small notice.

---

## Incremental Indexing — The Tricky Case

For a **reset run**, all chunks are in memory — straightforward full recompute.

For an **incremental run** (new files added, existing files skipped), only the new chunks are in memory. The existing sidecar has cumulative stats for the chunks already in the store.

**`chunks_per_document` is keyed by `file_hash`, not filename.** Each hash entry is immutable once written — an incremental run adds new entries, never modifies existing ones. This avoids any double-counting when a file is modified and re-indexed: the old hash entry stays as-is, the new hash gets its own entry. The display layer groups by `source_file` to show per-filename totals, and can flag filenames with multiple live hash entries as having stale versions in the store (useful hygiene information).

**Merge strategy:**
- `total_chunks`: add new count to stored total
- `total_documents`: union of unique `source_file` values across all hash entries
- `chunk_size_distribution`: add bucket counts from new chunks to stored counts
- `chunks_per_document`: insert new hash entries (never overwrite)
- `chunks_per_document_distribution`: recompute fully after every merge — group `chunks_per_document` by `source_file`, sum chunk counts across all hashes per file, then tally how many files land on each integer 1–100 and aggregate the rest into `100+`
- `avg/p50/p95`: recompute from the merged distribution (approximate from histogram bins — acceptable precision)
- `ingestion_history`: append new entry

**Edge case:** a file is deleted from disk and a reset re-index is triggered. This clears the sidecar and recomputes from scratch — handled correctly by the reset path.

**Note:** if a file is modified and incrementally re-indexed, both the old and new hash entries will be present in `chunks_per_document`, reflecting that both versions' chunks are actually in the vector store. This is correct — it accurately represents what is indexed, and surfaces store bloat rather than hiding it.

---

## Backend Implementation

### New stats computation function

In `main.py` (or a small `kb_stats.py` utility), add:

```python
def _compute_chunk_stats(chunks: list[Chunk]) -> dict:
    sizes = [len(c.content) for c in chunks]
    step, max_val = 200, 2000
    bucket_keys = [f"{i}-{i+step}" for i in range(0, max_val, step)] + [f"{max_val}+"]
    buckets = dict.fromkeys(bucket_keys, 0)
    for s in sizes:
        idx = min(s // step, max_val // step)
        buckets[bucket_keys[idx]] += 1
    per_hash: dict[str, dict] = {}
    for c in chunks:
        h = c.metadata.get("file_hash", "unknown")
        if h not in per_hash:
            per_hash[h] = {"source_file": c.metadata.get("source_file", "?"), "chunk_count": 0, "indexed_at": now_iso}
        per_hash[h]["chunk_count"] += 1
    return {"size_distribution": buckets, "chunks_per_document": per_hash, "sizes": sizes}
```

### Where to hook it in

Stats must be computed **inside `_run_ingestion()`**, not in the callers. `_run_ingestion()` returns a count tuple — by the time `rebuild_callback()` or `_bg_ingest()` calls `kb_router.update_stats()`, the chunk objects are gone. The sidecar write goes at the same point where `n_files` is computed (line ~712 in `main.py`), while `chunks` is still in scope. `db_dir` and `kb_id` are passed in as parameters.

### New API endpoint

```
GET /api/v1/kb/{kb_id}/stats
```

Reads and returns `kb_stats_{kb_id}.json`. Returns 404 if the KB has not been indexed yet.

---

## Retrieval Hit Frequency (Optional, Later)

This requires query-time instrumentation — the analytics pipeline above cannot produce it. The natural hook is in `retrieve_callback()` in `main.py`: after building the `RetrieveResponse`, append chunk IDs to a per-KB query log.

The log can be a simple append-only JSONL file (`kb_query_log_{kb_id}.jsonl`), aggregated lazily on stats read. This is additive and does not need to block the main feature — design it as a later addition.

---

## Frontend

**Design language:** follow the Retrieval Probe tab as the reference. Reuse its patterns directly: `rounded-xl border border-border bg-card shadow-sm overflow-hidden` panel cards, `px-4 py-3 border-b border-border bg-muted/30` card headers with a lucide icon, `text-muted-foreground` for secondary labels, the destructive-styled error block, and the centred empty state with a dimmed icon. The KB selector and any other controls should feel like they belong in the same component family.

**Charting library:** Recharts (not yet installed — `npm install recharts`). React-native, no D3 imperative wrangling, integrates cleanly with Tailwind via `className`.

### Tab registration

`explorer.tsx` already has `type Tab = "probe" | "browser"`. Extend to `"probe" | "browser" | "analytics"`. Add the tab button alongside the existing two. The existing tab-strip loop handles rendering automatically.

### KbAnalytics component

New file: `frontend/src/components/sections/kb-analytics.tsx`

**KB selector:** a dropdown at the top of the panel populated from the existing `GET /api/v1/kb` response (already fetched elsewhere in the admin UI). Defaults to the active KB. Lets the user inspect any KB's stats without switching the active one. Changing the selection re-fetches `GET /api/v1/kb/{kb_id}/stats`.

**States:**
- Loading skeleton while fetching
- "No stats yet — re-index this KB to generate analytics" if 404
- Charts rendered when data is available

**Four panels rendered with Recharts:**
1. `BarChart` — chunk size distribution (200-char buckets on x, count on y)
2. `BarChart` — chunks per document distribution (x-axis: chunk count 1–100 + `100+`; y-axis: number of documents). 101 bars, thin but shows the full shape of the distribution clearly at `max-w-6xl`.
3. `BarChart` — ingestion timeline (run timestamp on x, chunks_added on y, bar fill differs for reset vs. incremental runs)
4. Summary strip — total chunks, total documents, avg/p50/p95 sizes

**Interactivity:**
- `<Tooltip />` on all charts — free from Recharts, shows exact value on hover. Custom `content` prop where useful (e.g. percentage of total alongside raw count on chart 1).
- `<ResponsiveContainer />` wraps each chart.
- **Recharts theming:** Recharts does not pick up Tailwind CSS variables automatically. Use a small helper that reads `getComputedStyle(document.documentElement)` to resolve `--primary`, `--muted-foreground`, etc. into actual colour strings. All three charts share this helper so they respond consistently to dark/light mode switches.
- **Planned (requires chunk length filter feature):** clicking a bar in chart 1 navigates to the Chunk Browser tab pre-filtered to that size range. Both tabs live in `explorer.tsx` — lifted state variable, no routing needed.

### Layout

The analytics tab uses `max-w-6xl` like the chunk browser (wider than the probe's `max-w-3xl`) — charts need horizontal space.

---

## What Is Not In Scope

- Cross-KB comparison (selector shows one KB at a time)
- Export to CSV/PNG
- Real-time stats update during indexing (stats are written at the end of a run, not live)
- Retrieval hit frequency (separate instrumentation work)
- Histogram click-through to chunk browser (depends on chunk length filter feature)
