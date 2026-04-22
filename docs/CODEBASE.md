# Codebase Structure

An orientation guide to the repository layout. See [ARCHITECTURE.md](ARCHITECTURE.md) for the technical deep-dive into how the components interact.

---

## Repository Tree

```
.
├── backend/
│   └── src/lancy/
│       ├── main.py                   # FastAPI entry point
│       ├── kb_router.py              # KB registry, hot-swap, indexing control
│       ├── rag_router.py             # RAG query endpoints
│       ├── openai_compat_router.py   # /v1/chat/completions endpoint
│       └── feature0_baseline_rag.py  # RAG pipeline factories and ingestion
│
├── conversational-toolkit/
│   └── src/conversational_toolkit/
│       ├── agents/                   # RAG agent (retrieval + generation)
│       ├── chunking/                 # PDF, EPUB, DOCX, Markdown chunkers
│       ├── embeddings/               # SentenceTransformer, Ollama, LiteLLM
│       ├── llms/                     # OpenAI, Ollama, Anthropic, LiteLLM
│       ├── retriever/                # BM25, semantic, hybrid retriever
│       └── vectorstores/             # ChromaDB, pgvector
│
├── frontend/
│   └── src/
│       ├── components/sections/
│       │   ├── rag-config-panel.tsx  # RAG Parameters panel
│       │   └── sidebar/              # History, session labels, config badges
│       └── pages/
│           ├── login.tsx
│           └── api/auth/
│
├── data/                             # Demo document corpus (PrimePack AG scenario)
├── docs/                             # Architecture, setup guides, screenshots
├── prompts/                          # system_prompt.default.md + custom (gitignored)
├── CHANGELOG.md
├── CONTRIBUTORS.md
└── LICENSE
```

---

## Module Notes

To be expanded as the codebase evolves.
