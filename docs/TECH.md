# Technology Credits

## Upstream Foundation

**[SME-KT-ZH Collaboration RAG](https://github.com/SwissDataScienceCenter/sme-kt-zh-collaboration-rag)** — Swiss Data Science Center (SDSC), ETH Zürich / EPFL
The upstream project this fork is built on. Contributed the original RAG pipeline, workshop notebooks (feature0–feature4), RAGAS evaluation integration, and structured evidence output design.

---

## Backend

**[FastAPI](https://fastapi.tiangolo.com/)** — async Python web framework powering the API layer.

**[Ollama](https://ollama.com/)** — local LLM inference server. Primary backend for chat and embedding models.

**[ChromaDB](https://www.trychroma.com/)** — embedded vector database used as the default vector store per KB.

**[pgvector](https://github.com/pgvector/pgvector)** — PostgreSQL extension for vector similarity search; optional per-KB backend alongside ChromaDB.

**[Sentence Transformers](https://www.sbert.net/)** — HuggingFace library for local embedding model inference.

**[Docling](https://github.com/DS4SD/docling)** — IBM document understanding library used for parsing PDFs, DOCX, images, and other formats during ingestion.

**[rank_bm25](https://github.com/dorianbrown/rank_bm25)** — BM25 lexical retrieval, combined with semantic search via Reciprocal Rank Fusion (RRF).

**[LiteLLM](https://github.com/BerriAI/litellm)** — optional LLM backend proxy supporting 100+ providers behind a unified OpenAI-compatible interface.

**[OpenAI Python SDK](https://github.com/openai/openai-python)** — used for OpenAI and OpenAI-compatible endpoints (including Ollama's OpenAI API and the project's own `/v1/chat/completions` passthrough).

---

## Frontend

**[Next.js](https://nextjs.org/)** — React framework for the web UI; handles routing, server-side API proxying, and static export.

**[Tailwind CSS](https://tailwindcss.com/)** — utility-first CSS framework.

**[Radix UI](https://www.radix-ui.com/)** — unstyled, accessible UI primitives (dialogs, dropdowns, selects, etc.).

**[i18next](https://www.i18next.com/) / [react-i18next](https://react.i18next.com/)** — internationalization framework; UI ships in DE, EN, FR, and IT.

**[react-markdown](https://github.com/remarkjs/react-markdown) + rehype/remark plugins** — renders LLM responses as formatted Markdown including math (KaTeX) and syntax highlighting.

**[Recharts](https://recharts.org/)** — charting library used for KB analytics and retrieval statistics.

**[TanStack Table](https://tanstack.com/table)** — headless table library for the chunk browser and admin views.
