# Embedding Models

Lancy ships four local embedding models, ordered by memory footprint. Picking the right one affects retrieval quality, indexing speed, and hardware requirements.

---

## Models

| Model | Tier | Dims | Context | Params | ~VRAM |
|---|---|---|---|---|---|
| all-MiniLM-L6-v2 | Nano | 384 | 256 tokens | ~22M | ~85 MB |
| nomic-ai/nomic-embed-text-v1 | Mini | 768 | 8192 tokens | ~137M | ~550 MB |
| intfloat/multilingual-e5-large | Medium | 1024 | 512 tokens | ~560M | ~2.2 GB |
| BAAI/bge-m3 | Large | 1024 | 8192 tokens | ~570M | ~2.3 GB |

All four run fully offline via SentenceTransformer. No API key or internet connection required after the initial model download.

---

## When to use which

**all-MiniLM-L6-v2 (Nano)** — English only. Use for rapid prototyping, low-resource environments, or when indexing speed matters more than quality. 256-token context means chunks longer than that are silently truncated — keep `max_chunk_tokens` at or below 256.

**nomic-ai/nomic-embed-text-v1 (Mini)** — English-focused with reasonable cross-lingual performance. 8192-token context is its standout feature for a model this small. Good default when content and queries are in the same language and GPU memory is limited.

**intfloat/multilingual-e5-large (Medium)** — 50+ languages, explicitly trained for cross-lingual retrieval. A German query against English documents (or vice versa) works reliably. Solid choice for multilingual corpora where bge-m3's extra weight is not justified.

**BAAI/bge-m3 (Large)** — Best overall retrieval quality. 100+ languages, 8192-token context, and trained with three retrieval objectives simultaneously (dense, sparse/BM25-like, multi-vector/ColBERT-style). Lancy uses dense retrieval only, but the multi-objective training produces better dense representations than a pure-dense model. Consistently leads multilingual-e5-large on BEIR/MTEB benchmarks. Use this unless hardware is the constraint.

---

## BGE-M3 vs multilingual-e5-large

The two largest models have nearly identical parameter counts (~560–570M), so the choice is not about memory. The differences that matter:

- **Context window**: BGE-M3 handles 8192 tokens vs e5-large's 512. For chunked RAG this is partially moot, but large chunks won't be silently truncated.
- **Architecture**: BGE-M3 is trained with dense, sparse, and multi-vector objectives in one model. Even when using only dense retrieval, multi-objective training yields stronger representations.
- **Benchmark scores**: BGE-M3 leads e5-large on both mono- and multilingual BEIR/MTEB tracks. It is roughly 2 years newer.
- **Batch size**: BGE-M3 uses a smaller default batch size (10 vs 20) — higher quality comes at indexing speed cost.

e5-large earns its place as the Medium tier: it is a capable multilingual model, and its 512-token context is a real constraint rather than a flaw.

---

## Context window and chunk size

Each model has a hard context window. Chunks longer than the limit are truncated without warning, degrading retrieval quality silently.

The `max_chunk_tokens` slider in KB configuration turns **red** when the value exceeds the selected model's context window — use that as a guardrail.

| Model | Max safe `max_chunk_tokens` |
|---|---|
| all-MiniLM-L6-v2 | 256 |
| nomic-ai/nomic-embed-text-v1 | 8192 |
| intfloat/multilingual-e5-large | 512 |
| BAAI/bge-m3 | 8192 |

The chunk size distribution chart in KB Analytics highlights buckets (in characters) that exceed the model's context window in orange.

---

## Backends

The four models above all use the `local` backend. Lancy also supports:

| Backend | Use case |
|---|---|
| `ollama` | Offload embedding inference to a separate Ollama server (GPU on another machine) |
| `litellm` | Hosted models via a LiteLLM proxy (e.g., Voyage AI, Cohere) |
| `custom` | Direct OpenAI-compatible API (OpenAI, Azure OpenAI, self-hosted vLLM) |

Switching backends unlocks different model families (e.g., Voyage AI `voyage-3-large` with 32k context via `litellm`), but for a fully offline deployment the `local` backend covers all four tiers.

Image embedding uses a separate dedicated model (`Qwen3-VL-Embedding-2B` by default) and is configured independently from the text embedding backend.

---

## Changing the model on an existing KB

Changing the embedding model requires a full re-index (`reset=True`). The vector store dimension is fixed at collection creation time — a dimension mismatch will cause ingestion to fail. Re-indexing discards all existing vectors and rebuilds from the stored document chunks.
