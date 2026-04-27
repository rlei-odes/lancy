# Design Doc: Image Captioning via Main LLM

**Status:** Proposal  
**Related:** `DESIGN_DOC_Image_Retrieval.md` (existing VL-embedding approach)

---

## Problem

When docling converts a PDF to markdown, images become `<!-- image -->` placeholders in the text output. Image-heavy sections end up as nearly-empty chunks like:

```
## Water-Activated Tape

<!-- image -->
```

This chunk carries no searchable information. If a user asks "what is water-activated tape?", this chunk will never surface — nothing in it matches the query semantically. The image content is invisible to retrieval.

The existing image retrieval pipeline (VL embeddings + separate image vector store) solves this, but requires:
- A multimodal embedding model (Qwen3-VL-Embedding-2B)
- A second vector store per KB
- A multimodal LLM at query time to interpret retrieved images
- Significant GPU memory

This document proposes a lighter alternative: use the main LLM at **ingest time** to caption each image, and embed the caption text directly into the text chunk.

---

## Proposed Approach

### Concept

After chunking, before storing text chunks, run a captioning pass:

1. For each extracted image chunk (`mime_type="image/*"`), call the configured main LLM with a constrained captioning prompt.
2. The LLM extracts literal visible text (OCR-like) and describes what it sees — no speculation.
3. The caption is injected into the corresponding text chunk, replacing `<!-- image -->`.
4. The enriched text chunk is stored in the regular text vector store.
5. Image chunks are discarded after captioning (or kept for the image VS if `image_indexing_enabled` is also on).

### Prompt Approach

The captioning prompt must constrain the LLM to stay close to the image:

```
You are captioning an image for a document retrieval system.

1. Extract all visible text exactly as it appears (labels, numbers, headings, table cells, legends).
2. In 2–3 sentences, describe what the image shows. Only describe what is visually present — no interpretation, no background knowledge.

Respond with:
VISIBLE TEXT: <extracted text, or "none">
DESCRIPTION: <visual description>
```

The two-field output keeps extracted text (high-precision retrieval) and description (semantic retrieval) separate and concatenated into the chunk.

### Chunk Transformation

Before captioning:
```
title: "## Water-Activated Tape"
content: "## Water-Activated Tape\n\n<!-- image -->"
mime_type: "text/markdown"
```

After captioning:
```
title: "## Water-Activated Tape"
content: "## Water-Activated Tape\n\n<!-- image content -->\nVISIBLE TEXT: 50m, tear-resistant, water-activated\nDESCRIPTION: A roll of brown paper tape next to an application gun. A table lists adhesive strength by substrate.\n<!-- end image content -->"
mime_type: "text/markdown"
```

This chunk is now indexable, searchable, and contextually coherent.

### Image–Text Matching

The PDF chunker exports images as numbered files: `{stem}-picture-1.png`, `{stem}-picture-2.png`, etc., in document order. The markdown contains `<!-- image -->` occurrences in the same document order. The i-th image file from a given source PDF corresponds to the i-th `<!-- image -->` across all text chunks from that same source file (in chunk order).

Matching algorithm (in ingestion pipeline, per source file):
1. Collect all text chunks from source file, preserving chunker output order.
2. Collect all image chunks from source file, sorted by filename (picture number).
3. Walk through text chunks left-to-right; for each `<!-- image -->` encountered, pop the next image chunk and replace the placeholder with the caption.
4. Remaining image chunks without a corresponding placeholder are appended as standalone text chunks.

This ordering assumption holds for standard docling output and breaks only if docling's iteration order diverges from its markdown export order — which is unlikely but worth a log warning when counts mismatch.

### Where the Toggle Lives

KB-level config: `image_captioning_enabled: bool = False`

It must be KB-level (not session-level) because the captions are baked into the text chunks at index time. Toggling it requires a full re-index.

It is independent of `image_indexing_enabled`. Both can be active simultaneously (captions enrich text retrieval; VL embeddings enrich image retrieval).

### LLM Requirement

The main LLM must accept image inputs (multimodal). Examples that work via Ollama: `llava`, `llava-phi3`, `gemma3`, `qwen2-vl`, `minicpm-v`.

If the configured LLM is not vision-capable, the ingestion phase must fail loudly — not silently skip. The backend should attempt a test call and raise a clear error if the model rejects an image payload.

### UI Toggle

Add to the KB configuration panel (alongside `image_indexing_enabled`):

```
[ ] Enable image captioning
    At ingest, the main LLM generates a text caption for each image and
    embeds it in the document chunk. Requires a multimodal main LLM.
    Re-index required when changed.
```

---

## Implementation Sketch

### Files to Touch

| File | Change |
|---|---|
| `kb_router.py` | Add `image_captioning_enabled: bool = False` to `KBCreate` / `KBInfo` |
| `feature0_baseline_rag.py` | Pass `captioning_enabled` and `llm` client to `load_chunks()` or handle in caller |
| `ingestion.py` | New `_caption_image_chunks()` function; call after chunking, before embedding |
| `rag-config-panel.tsx` | Add captioning toggle to KB config section |

### Ingestion Flow (modified)

```
load_chunks(file)
  → [text_chunks, image_chunks]         # existing

if captioning_enabled and image_chunks:
  _caption_image_chunks(
      text_chunks, image_chunks, llm    # mutates text_chunks in-place
  )
  image_chunks = []                     # consumed; not stored separately

embed_and_store(text_chunks, text_vs)
```

`_caption_image_chunks` iterates text chunks, finds `<!-- image -->` placeholders, calls LLM once per image (async, via `run_in_executor`), and replaces the placeholder. Images without a matching placeholder are appended as new text chunks.

The LLM call reuses the already-constructed `llm` instance from `main.py` — no second model is loaded.

---

## Pros

- **No extra dependencies.** Reuses the already-configured main LLM. No second model, no second vector store, no extra GPU memory at query time.
- **Standard text retrieval.** Captions live in the regular text index — BM25, semantic, and RRF all work on them without modification.
- **Works with any query-time LLM.** Once captions are indexed, retrieval works with any LLM, including text-only models.
- **Context coherence.** Caption is embedded alongside the section title and surrounding text — the chunk stays semantically whole.
- **Visible in chunk browser.** The caption content is human-readable in any chunk inspection tool.
- **Hardware-friendly at query time.** GPU is needed at ingest, but only briefly and only if you're running a local VL model. Cloud-based LLMs (via LiteLLM) need no GPU at all.
- **Complementary to VL-embedding approach.** Both can be active: captions improve text retrieval; VL embeddings enable image-to-image similarity. They are not mutually exclusive.

---

## Cons

- **Ingest time increases.** One LLM round-trip per image. For a document with 20 images, that's 20 sequential (or parallel-batched) calls. Depending on model and hardware, this could add minutes.
- **Requires multimodal LLM at ingest.** If the configured main LLM is text-only, captioning fails. Users must switch to a VL-capable model, run indexing, then can switch back. This is a footgun — needs a clear error message and ideally a pre-flight check.
- **Caption quality is LLM-dependent.** A weak VL model will produce vague captions. A hallucinating model could inject false information into the index. The constrained prompt mitigates this but does not eliminate it.
- **Re-index required to change mode.** Toggling `image_captioning_enabled` after initial indexing requires a full re-index. Same constraint as all KB-level settings, but worth highlighting since the tradeoff (ingest cost) is larger than most.
- **Ordering assumption is fragile.** The image–text matching relies on docling's image numbering matching the markdown `<!-- image -->` order. This is almost always true but is an implicit contract, not an enforced one. A mismatch silently produces wrong caption–chunk associations.
- **No visual retrieval.** Unlike the VL-embedding approach, this does not enable "find images similar to this query image" or multimodal similarity search. Text captions are a lossy representation.
- **Ingest-time GPU spike.** Running a VL model just for indexing, then not needing it at query time, creates an uneven resource profile. On a shared machine this could cause contention.

---

## Comparison with Existing VL-Embedding Approach

| Property | Caption pipeline (this doc) | VL-embedding retrieval |
|---|---|---|
| Extra model at query time | No | Yes (Qwen3-VL-Embedding-2B) |
| Extra vector store | No | Yes (`vs_<slug>_images`) |
| Retrieval works with text-only LLM | Yes | No (images sent to LLM at query time) |
| BM25 retrieval on image content | Yes | No |
| Visual similarity search | No | Yes |
| Ingest time penalty | Per-image LLM call | VL embedding batch |
| Requires VL model at ingest | Yes (main LLM must be VL) | No (uses separate embedding model) |
| Caption visible in chunk browser | Yes | No (base64 stored in image VS) |

The two approaches are complementary, not competing. The caption pipeline is the better default for document-heavy corpora where a VL embedding model is not available or not wanted at query time.

---

## Open Questions

1. **Should captioning be async-parallelised?** Multiple images per document could be captioned in parallel (`asyncio.gather`). This would reduce ingest time but increase peak VRAM usage. Probably worth a configurable batch size.
2. **What if the LLM is accessed via LiteLLM / a cloud API?** Vision calls to cloud APIs cost money. Captioning 500 images during a re-index could have non-trivial API cost. A warning in the UI when captioning is enabled with a cloud LLM backend would be appropriate.
3. **Should a dedicated, smaller VL model be configurable for captioning?** Using `minicpm-v` for ingest while using `llama3.1` for queries would decouple the two. This is the `IMAGE_MODE=caption` variant from the backlog. Worth considering as a follow-on option.
4. **Progress reporting.** Long ingest runs with many images benefit from progress events. The existing SSE ingestion progress stream should report captioning progress separately from embedding progress.
