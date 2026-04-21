# Contributors

---

## TRAG Extensions

**rlei-odes** · GitHub: [@rlei-odes](https://github.com/rlei-odes)

- Content-hash deduplication for ingestion (cross-run and within-batch, SHA-256, pre-parse filtering)
- Implementation of image ingestion and retrieval - basics already present in baseline

**Vonlanthen INSIGHT** · https://www.vonlanthen.tv · GitHub: [@voninsight](https://github.com/voninsight)  

Production stack built on top of the SDSC baseline:

- Multi-knowledge-base architecture (KB registry, hot-swap without restart)
- pgvector backend alongside ChromaDB (per-KB vector store selector)
- Hybrid retrieval: BM25 + semantic vector search via Reciprocal Rank Fusion (RRF)
- Query expansion, HyDE (Hypothetical Document Embeddings), LLM reranking
- Async refactoring: non-blocking SentenceTransformer, ChromaDB, BM25 (`run_in_executor`)
- Critical bug fix: stream sentinel in `controller.py` (AttributeError on response end)
- MarkItDown chunker: EPUB, DOCX, DOC support
- LiteLLM and Anthropic LLM backends
- OpenAI-compatible endpoint (`/v1/chat/completions`, `/v1/models`)
- RAG config panel: collapsible right-side panel with presets and live tuning
- Session labels and config badges per conversation
- Generation statistics: query duration, tokens/second, model name
- Login / session cookie authentication
- Internationalization: DE / EN / FR / IT
- Systemd user services and nginx deployment configuration
- Multi-device proxy rewrite (SERVER_URL="" pattern)

---

## Upstream Project

This project is a fork of the **SME-KT-ZH Collaboration RAG** project by the
[Swiss Data Science Center (SDSC)](https://datascience.ch), ETH Zürich / EPFL.

Upstream repository: https://github.com/SwissDataScienceCenter/sme-kt-zh-collaboration-rag

**SDSC team contributions to the baseline:**

- Original RAG pipeline design and architecture
- PrimePack AG scenario dataset with deliberate evidence-quality flaws
- Workshop notebooks (feature0 – feature4): baseline RAG, evaluation, structured outputs,
  advanced retrieval, agents and tool use
- RAGAS evaluation framework integration
- Structured evidence outputs (VERIFIED / CLAIMED / MISSING / MIXED)
- Notebook review and finalization (Paulina Koerner, 2026)

---

*Apache License 2.0. Both copyright holders must be retained in derivative works.*
*See [LICENSE](LICENSE) for details.*
