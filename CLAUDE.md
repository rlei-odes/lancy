# CLAUDE.md — Agent Instructions


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

Fail loudly on critical operations — do not silently swallow errors. Log and re-raise; never use `result = call() or None` to hide failures. Acceptable fallbacks: optional config reads, graceful degradation to a known-good default.

- **Never print secrets to logs** — no passwords, API keys, or tokens in log output or error messages
- Use environment variables for all credentials; never hardcode them
- `rag_config.json` and `knowledge_bases.json` are gitignored — they may contain local paths and credentials
- If adding new config files with local paths or credentials, add them to `.gitignore` and provide a `.example` template

---


# Project: Lancy

Lancy is a full-stack RAG system: FastAPI backend + Next.js frontend + local LLM inference via Ollama.
Fork of the SDSC SME-KT-ZH Collaboration RAG, extended with multi-KB support, hybrid retrieval (BM25 + semantic + RRF), and a full authenticated web UI.

**Start/stop:** `./start.sh` / `./stop.sh` — starts both services in the background, logs to `logs/`.

---

## Key Files

The non-obvious ones — everything else is findable by name or via the graphify graph:

| File | Role |
|---|---|
| `backend/src/lancy/feature0_baseline_rag.py` | `build_llm()`, `build_embedding_model()`, `build_vector_store()` factories — all backend construction goes here |
| `backend/src/lancy/db/rag_config.json` | Active RAG session config (persisted, loaded on startup) |
| `backend/src/lancy/db/knowledge_bases.json` | KB registry (all KB definitions + active flag) |
| `frontend/src/components/sections/rag-config-panel.tsx` | RAG Parameters panel — all session-configurable settings |
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

---

## Don't Assume — Verify

Before diagnosing or fixing a problem:
1. Read the actual logs — `logs/backend.log`, `logs/frontend.log`
2. Check the actual config files (`rag_config.json`, `knowledge_bases.json`)
3. Verify the state of what's running, not what you think should be running

---

## Commit Convention

Use [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <description>`

Types: `feat`, `fix`, `refactor`, `docs`, `chore`
Scopes: `backend`, `frontend`, `retrieval`, `kb`, `config`, `docs`, `scripts`

---

## Commit and Push Workflow

Solo fork — commit directly to `main` for day-to-day work; use a feature branch for large or multi-session changes.

1. Make the changes
2. Describe to the user what was changed and why
3. Commit with a conventional commit message
4. **Never push automatically** — always ask the user before pushing to remote.

---

## Documentation Drift

When making changes, check whether documentation needs updating:
- `README.md` — if features changed
- `docs/ARCHITECTURE.md` — if the backend structure, retrieval pipeline, or data flow changed
- `docs/admin-guides/setup-guide.md` — if env vars, deployment, or repo structure changed

---

## Changelog

Larger changes should be documented in [CHANGELOG.md](CHANGELOG.md) under a new version entry (Keep a Changelog format). When bumping the version, also update `frontend/src/config.ts` — that's where the UI footer version comes from.

---

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
