# CLAUDE.md — Agent Instructions general Instructions


## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

## Language

Always respond and generate code in **English**. This includes code comments, variable names, commit messages, and documentation — even if the user writes in German or another language.

## Emoji Usage

Use emojis sparingly. Avoid them in commit messages, PR descriptions, and documentation unless they genuinely improve readability. In code comments, only use them for visual warnings (`# ⚠️`). Prefer clear text over emoji-heavy content.

## No Advertising or Branding

- No "Generated with Claude Code" footers in PRs, issues, or documentation
- No links to Anthropic, Claude, or AI tool providers
- No `Co-Authored-By: Claude ...` trailers in commit messages
- Keep all content professional and focused on the task


## Security and Error Handling

### Error Handling

Fail loudly on critical operations. Do not silently swallow errors:

```python
# BAD — hides failures, execution continues
result = some_critical_call() or None

# GOOD — surfaces the failure clearly
try:
    result = some_critical_call()
except Exception as exc:
    log.error(f"Critical operation failed: {exc}")
    raise
```

Acceptable use of fallbacks: optional config reads, graceful degradation to a known-good default (e.g., `main.py` falls back to `mistral-nemo:12b` if the configured LLM fails to build).


### Prevent Secrets Leaking

- **Never print secrets to logs** — no passwords, API keys, or tokens in log output or error messages
- Use environment variables for all credentials; never hardcode them
- `rag_config.json` and `knowledge_bases.json` are gitignored — they may contain local paths and credentials

### Public Publication — What Gets Committed and What Doesn't

**Goal:** Anyone should be able to clone this repo, run a pre-configured demo out of the box, adapt it to their own situation, and publish their fork publicly without accidentally leaking their data, documents, or internal prompts. This means all customized configurations, settings should not be published. This needs to be managed in .gitignore


---


# CLAUDE.md — Agent Instructions specific for this Project

## Project Overview

Lancy is a full-stack RAG system: FastAPI backend + Next.js frontend + local LLM inference via Ollama.
Fork of the SDSC SME-KT-ZH Collaboration RAG, extended with multi-KB support, hybrid retrieval (BM25 + semantic + RRF), and a full authenticated web UI.

**Start/stop:** `./start.sh` / `./stop.sh` — starts both services in the background, logs to `logs/`.

---

## Key Files

| File | Role |
|---|---|
| `backend/src/lancy/main.py` | FastAPI entry point — builds the RAG system, wires routers |
| `backend/src/lancy/rag_router.py` | RAG query endpoints, `RagConfig` session model |
| `backend/src/lancy/kb_router.py` | KB registry, hot-swap, indexing control |
| `backend/src/lancy/openai_compat_router.py` | `/v1/chat/completions` OpenAI-compatible endpoint |
| `backend/src/lancy/feature0_baseline_rag.py` | `build_llm()`, `build_embedding_model()`, `build_vector_store()` factories |
| `conversational-toolkit/src/conversational_toolkit/agents/` | Agent implementations (`RAG`, `ToolAgent`) |
| `conversational-toolkit/src/conversational_toolkit/utils/retriever.py` | Query utilities: standalone reformulation, expansion, HyDE, RRF, context assembly |
| `conversational-toolkit/src/conversational_toolkit/retriever/` | Retriever implementations: semantic, BM25, reranking |
| `conversational-toolkit/src/conversational_toolkit/embeddings/` | Embedding model backends (local sentence-transformers, Ollama, OpenAI-compatible) |
| `conversational-toolkit/src/conversational_toolkit/llms/` | LLM backends (Ollama, OpenAI-compatible) |
| `conversational-toolkit/src/conversational_toolkit/vectorstores/` | Vector store backends (ChromaDB) |
| `conversational-toolkit/src/conversational_toolkit/chunking/` | Chunkers (PDF/Markdown via Docling, MarkItDown) |
| `frontend/src/components/sections/rag-config-panel.tsx` | RAG Parameters panel — all session-configurable settings |
| `backend/src/lancy/db/rag_config.json` | Active RAG session config (persisted, loaded on startup) |
| `backend/src/lancy/db/knowledge_bases.json` | KB registry (all KB definitions + active flag) |
| `prompts/system_prompt.default.md` | Default system prompt — overridden by `system_prompt.custom.md` (gitignored) or UI session input |


---

## Development Guidelines

### Code Style

- Follow the conventions already present in the file being modified — indentation, naming, structure
- Python: async/await throughout, type hints, `match`/`case` for backend switching
- TypeScript: functional React components, Tailwind for styling, no inline styles
- Do not introduce new dependencies without a clear reason

### Modular Architecture

**Never hardcode technology-specific calls in router or agent code.** All LLM, embedding, and vector store construction goes through the factory functions in `feature0_baseline_rag.py`:
- `build_llm(backend, model_name, ...)` — all LLM instantiation
- `build_embedding_model(backend, model_name, ...)` — all embedding instantiation
- `build_vector_store(backend, ...)` — all vector store instantiation

If support for a new backend is needed, add a case to the relevant factory — do not construct the client inline elsewhere.

### Architecture Constraints

- `SERVER_URL` in `frontend/.env` must be **empty** — the frontend proxies all API calls server-side. Setting it to a hostname breaks requests on local/NAT networks.
- All blocking operations (embedding inference, ChromaDB queries, BM25 indexing) must use `asyncio.run_in_executor`. Do not introduce synchronous blocking calls in async routes.
- Changing the embedding model on an existing KB requires a full re-index with `reset=True` — the vector store dimension is locked at collection creation time.
- KB hot-swap is in-memory only: `POST /api/v1/kb/{id}/activate` switches the active KB without a restart.

**The pattern:**
- `prompts/system_prompt.default.md` is the PrimePack example — it ships with the repo and shows what a well-structured prompt looks like. It is the starting point for new users.
- Users who adapt Lancy to their own use case create `prompts/system_prompt.custom.md` (gitignored) and point their documents to a path outside the project folder. Their fork stays clean for public sharing.
- The `data/` folder contains the committed PrimePack demo dataset. Users who adapt Lancy to their own use case should point their KB at a path **outside** the project directory so their documents are never accidentally committed.
- If adding new config files that may contain local paths, credentials, or user-specific data, add them to `.gitignore` and provide a `.example` template instead.


---

## Don't Assume — Verify

Before diagnosing or fixing a problem:
1. Read the actual logs — `logs/backend.log`, `logs/frontend.log`
2. Check the actual config files (`rag_config.json`, `knowledge_bases.json`)
3. Verify the state of what's running, not what you think should be running

Do not speculate about what's wrong without first reading what the system actually reports. No fix attempts without reading the relevant log output first.

---

## Commit Convention

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

| Type | Use for |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code restructuring without behaviour change |
| `docs` | Documentation only |
| `chore` | Maintenance, dependency updates |

Scopes: `backend`, `frontend`, `retrieval`, `kb`, `config`, `docs`, `scripts`

Examples:
```
feat(kb): add per-KB BM25 toggle
fix(frontend): correct SERVER_URL proxy behaviour
docs: move known issues to README
chore(scripts): rewrite start.sh for local portability
```

---

## Commit and Push Workflow

This is currently a solo fork — committing directly to `main` is fine for day-to-day work and small improvements. Use a feature branch when a change is large, experimental, or spans multiple sessions where incomplete code would be disruptive on `main`.

1. Make the changes
2. Describe to the user what was changed and why
3. Commit with a conventional commit message
4. **Never push automatically** — always ask the user before pushing to remote. The user decides when to push.

---

## Documentation Drift

When making changes, check whether documentation needs updating:
- `README.md` — if features changed
- `docs/ARCHITECTURE.md` — if the backend structure, retrieval pipeline, or data flow changed
- `docs/admin-guides/setup-guide.md` — if env vars, deployment, or repo structure changed

Update affected docs in the same set of changes. Do not leave docs out of sync with the code.

---

## Changelog

Larger changes — new features, fixes with meaningful impact, structural decisions — should be documented in [CHANGELOG.md](CHANGELOG.md) under a new version entry. Follow the existing Keep a Changelog format. Add an entry whenever a session produces changes worth tracking, not just for every small tweak.

When bumping the version in the changelog, also update `frontend/src/config.ts` — that's where the version displayed in the UI footer comes from.

---

## Open Tasks

Feature and development tasks live in [BACKLOG.md](BACKLOG.md).

---

## Known Issues

See [README.md — Known Issues](README.md#known-issues) for the current list.
