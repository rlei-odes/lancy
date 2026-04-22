# Lancy — Open-Source RAG System

> Ask questions of your documents. See exactly how the answer was found.

Lancy is a self-hosted, production-ready Retrieval-Augmented Generation system.
It brings transparency to the RAG process: every response shows its sources, evidence quality,
retrieval settings, and generation stats — no black box. Investigate your document chunks with a retrieval explorer.

---

![Lancy Frontend](docs/screenshots/Lancy_Frontend.png)

---

## Features

| Area | Description |
|------|-------------|
| **Multi-KB architecture** | Multiple independent knowledge bases with hot-swap — no restart required |
| **Hybrid retrieval** | BM25 + semantic search fused via Reciprocal Rank Fusion (RRF) |
| **Query techniques** | Query expansion, HyDE, LLM reranking — all configurable per session |
| **Ingestion deduplication** | SHA-256 content hashing prevents duplicate chunks across runs and within a single batch — dedup happens before parsing, so no wasted embedding work |
| **Image retrieval** | Dual-collection pipeline: images extracted from PDFs and standalone files are embedded separately (Qwen3-VL) and injected into LLM context alongside text chunks |
| **Structured outputs** | Evidence-level tagging per claim: VERIFIED / CLAIMED / MISSING / MIXED |
| **RAG config panel** | Collapsible right-side panel with presets and live parameter tuning |
| **Transparent sessions** | Per-conversation config snapshot: KB · LLM · T= · emb: · k= · BM25 · Rerank · HyDE displayed as badges |
| **Generation stats** | Query duration, tokens/second, and model name shown per response |
| **Source citations** | Every answer links back to the source chunks it was grounded on |
| **Indexing control** | Real-time progress, mid-run cancellation, guard against concurrent indexing |
| **OpenAI-compatible API** | `POST /v1/chat/completions` — works with Open WebUI, curl, n8n, Cursor |
| **Multiple LLM backends** | Ollama (local), OpenAI, Anthropic, LiteLLM — switchable at runtime |
| **Multiple embedding backends** | `local` (SentenceTransformer, fully offline), `ollama`, `litellm`, `custom` |
| **Multiple vector stores** | ChromaDB (local, zero-config) or pgvector (PostgreSQL) — selectable per KB |
| **Document formats** | PDF, Markdown, XLSX, EPUB, DOCX |
| **Auth** | Password-protected login via session cookie (`API_KEY` in `.env`) |
| **i18n** | DE / EN / FR / IT |

---

## Documentation

- [Setup Guide](docs/admin-guides/setup-guide.md) — installation, environment variables, deployment
- [Architecture](docs/ARCHITECTURE.md) — system design, retrieval pipeline, sequence diagrams
- [Codebase](docs/CODEBASE.md) — repository layout and module overview

---

## Demo Dataset (PrimePack AG)

The bundled dataset models **PrimePack AG**, a packaging company evaluating supplier sustainability claims.
It is designed for RAG stress-testing — evidence quality varies deliberately. Developed by SDSC.

| Prefix | Content |
|--------|---------|
| `ART_` | Artificial scenario documents with deliberate evidence flaws |
| `EPD_` | Third-party verified Environmental Product Declarations |
| `SPEC_` | Product specifications and datasheets |
| `REF_` | Regulatory reference documents (GHG Protocol, CSRD, ISO 14024) |
| `EVALUATION_` | Ground-truth Q&A pairs — not indexed, used for evaluation only |

Conflicts are intentional (old vs. new datasheet with different GWP figures).
The correct answer to missing data is "we don't know" — the system should say so.

---

## Contributing

Bug reports, feature requests, and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Intended Use & Security Notice

Lancy is designed for **on-premises deployment** — all data and LLM inference stay within your own infrastructure. No data leaves your network unless you configure an external LLM or embedding backend. Some components might check for updates online - isolate if desired.

This system is intended for **trusted internal networks** (corporate LAN, VPN, private server):

- Authentication is a single shared password — no individual user accounts
- No rate limiting or abuse protection on the API
- All indexed documents are accessible to anyone with the password

Restrict access at the network level (firewall, VPN, or reverse proxy with additional auth) before any broader deployment.

---

## The Name

Lancy comes from *Calathea lancifolia*, the plant in the project icon. Turns out it is also a town near Geneva, Switzerland - fitting for the project's origin.

---

## License & Credits

Apache License 2.0 — see [LICENSE](LICENSE).

**Copyright 2026 Swiss Data Science Center (SDSC), ETH Zürich / EPFL**
**Copyright 2026 Vonlanthen INSIGHT**
**Copyright 2026 rlei-odes**

Lancy is a fork of the [SDSC SME-KT-ZH Collaboration RAG](https://github.com/SwissDataScienceCenter/sme-kt-zh-collaboration-rag),
extended with a full production stack by [Vonlanthen INSIGHT](https://www.vonlanthen.tv)
and further developed as Lancy by [rlei-odes](https://github.com/rlei-odes).

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for a detailed list of contributions.

Any fork or derivative must retain all copyright notices as required by the Apache License 2.0.
