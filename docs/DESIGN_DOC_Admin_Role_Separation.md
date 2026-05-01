# Design Doc: Image Captioning via Main LLM

**Status:** Some groundwork done, see changelog for version 0.2.35  


## Related Backlog Entries


### Admin / User Role Separation

Currently all authenticated users share a single password and have full access to the technical interface, including RAG configuration, reindexing, model selection, system prompt editing, and preset management.

**Goal:** introduce a two-tier access model so that end users only see the chat interface, while admins retain full configuration access.

**Scope:**
- Add an admin flag to the session (e.g. a separate admin password or a role field in the auth cookie)
- Hide parts of the RAG Config panel from non-admin sessions
- Hide or lock: reindex buttons, model selectors, system prompt editor, embedding configuration, vector store settings, preset create/delete (list to be fully defined)
- **Stop indexing button** (red, in the sidebar progress bar) — admin-only; end users should see the progress indicator but not be able to cancel a running indexing job
- End users retain: chat, conversation history, language toggle, theme toggle
- No per-user accounts required at this stage — two passwords (user / admin) is sufficient for the prototype
- **Fallback admin account** — a local admin credential (env var or config file) that works independently of SSO/AD, so the system is never locked out if the directory is unreachable; required before SSO integration is attempted

**Why:** prototype rollout to a small trusted team is fine without this, but broader internal use requires that non-technical users cannot accidentally break the configuration or trigger a full reindex.

### Active Directory / SSO Integration

**Goal:** optional AD/SSO login as an alternative to the shared-password model, for organisations that manage users centrally.

**Scope:**
- LDAP / Active Directory bind for authentication
- Map AD groups to roles (e.g. domain users → user role, IT group → admin role)
- Feature should be opt-in and deactivatable — shared-password mode remains the default for simpler deployments
- Builds naturally on top of the admin/user role separation above

**Why:** relevant for enterprise rollout where user management via AD is already in place and individual password distribution is impractical.


---

## Problem


Currently all authenticated have full access to the technical interface, including RAG configuration, reindexing, model selection, system prompt editing, and preset management. There is a clear role of admin role separation.

The Goal of this app is to serve the RAG process transparently, so the normal user could

---

## Things to consider

**Surface Admin Settings as read only to the normal user**

**Prepare for a future admin full page**

**Have multiple modes: 1: all users are admins (default), 2: simple admin-protection, no user management, 3: user management with external directory, designated external admins, one emergency fallback admin without the external directory

**API Protection** Admin-only API Endpoints should be protected too. A user should not be able to manually poll API endpoints with fields or settings that are admin-only.

**Fundamentally improve preset function** Admins should set company-wide presets, users can save their own with their own query parameters. The UX around presets is now quite confusing - we have a disk symbol for saving next to the presets, and we have a save at the bottom of the rag parrameters panel. Which does which? Which is just my current state and which is effecting a preset? What happens when I select a preset in the dropdown? Should have clear "load preset" buttons for example, if we actually have a current state.

**per KB dependencies between ingestion and Retrieval** options such as image retrieval should not be possible, when image indexing is not on on that kb.


**Hint about when a re-index is needed for something to really come into effect. Breaking changes vs. changes that would only affect newly ingested docs.**
See analysis:

The dots
Defined at rag-config-panel.tsx:133-138:


green  (instant)  — change takes effect on the next query, no re-index needed
amber  (reindex)  — change requires a re-index to take effect
They appear in the SectionHeader component as a 1.5×1.5 rounded dot next to the title, with a tooltip (effectTitle).

Where each section sits
Section	Dot	Static or dynamic?
Prompt	green	always effect="instant"
LLM	green	always effect="instant"
Retrieval	green	always effect="instant"
Embedding	green or amber	dynamic — computed on each render
The Embedding section logic (lines 1138–1155)
The dot is amber when either:

The KB has never been indexed (!activeKb?.last_indexed), or
Any of these fields in the UI (kbConfig) differ from what activeKb reports (i.e. what was last fetched from the server):

embedding_backend, embedding_model, nomic_prefix,
max_file_size_mb, embedding_batch_size, max_chunk_tokens,
vs_type, vs_connection_string,
embedding_ollama_host, embedding_custom_base_url,
pdf_ocr_enabled,
image_indexing_enabled, image_embedding_model, image_captioning_enabled
Otherwise it's green (current config matches what the KB was indexed with).

One gap worth noting
image_retrieval_enabled lives in kbConfig (KB-level, saved via PUT /kb/{id}) but lives in the Retrieval section with a green dot. This is correct semantically — toggling it doesn't require a re-index, it just changes what's fetched at query time. But it's slightly surprising that a KB-config field is in the session-like Retrieval section.

More importantly: the amber/green logic compares kbConfig against the last-fetched activeKb. If you change an embedding setting, save it, then click the Refresh button (before re-indexing), the server returns the newly-saved values, activeKb updates to match kbConfig, and the dot incorrectly flips back to green — even though the index is stale. The last_indexed guard only helps for KBs that have never been indexed at all. For already-indexed KBs there's no "last indexed config snapshot" tracked, so the dot can mislead after a save+refresh cycle.

Apply (bottom, enabled only when dirty)
Pushes the current UI state to the server: POSTs session config to /api/v1/rag/config, PUTs KB config + name/dirs to /api/v1/kb/{id}. Clears dirty. No effect on presets.

Save preset (disk icon next to presets dropdown)
Snapshots the current session + KB config into a named preset and POSTs it to /api/v1/rag/presets/{kbId}. Does not write to the running config endpoints — the server continues using whatever was last Applied. Does not clear dirty.

Refresh (↺ icon, top right)
Re-fetches the KB registry and session config from the server, discarding any unsaved UI changes. fetchSessionConfig calls setDirty(false) on success, so any pending changes are silently lost. No confirmation prompt.

---

## Section Dot / Colour Indicator Rework

### Current state (before rework)

Each section header carries a coloured dot:
- **Green** (`instant`) — hardcoded on Prompt, LLM, Retrieval sections
- **Amber** (`reindex`) — dynamic on Embedding only, computed by comparing current `kbConfig` fields against what `activeKb` reports from the server (i.e. what was last indexed)

This tells users "this section needs a re-index", but three of the four sections always show green regardless of whether anything has been edited.

### Agreed direction for users

Replace the static green dots with per-section **unsaved-change indicators** based on a saved-state snapshot:

**Implementation:**
- Add two refs (`savedSession`, `savedKbConfig`) initialised to defaults, updated at: initial load, successful Apply, and KB switch. These represent the last committed server state.
- Before the render `return`, compute four dirty flags by comparing current state against those refs:
  - `promptDirty` — `follow_up_count`, `system_prompt`
  - `llmDirty` — all LLM session fields
  - `retrievalDirty` — all retrieval session fields + `kbConfig.image_retrieval_enabled`
  - `embeddingDirty` — all `kbConfig` embedding fields
- All four `SectionHeader` calls become dynamic: green when the section matches saved state, amber when it has unsaved changes.

**Semantics change:**
- Green = no unsaved changes in this section
- Amber = unsaved changes in this section — click Apply to persist

Tooltip strings updated accordingly in all four lang files (`en`, `de`, `fr`, `it`).

**Already done as groundwork:**
- "Save" button at the bottom renamed to "Apply" across all four lang files
- `saveAsPreset` no longer clears `dirty` — saving a named preset no longer suppresses the unsaved-changes indicator

### Agreed direction for admins

For the Embedding section (admin-only), retain the existing concept of re-index state indicators but with a richer colour signal:

- **Amber** — embedding settings have been changed and saved, but a re-index has not yet been run. New ingestions will pick up the new config; pre-existing chunks are unaffected.
- **Red** — a breaking change has been made (e.g. embedding model or vector store type changed) that invalidates the existing index. A full reset re-index is required for the KB to function correctly.

The distinction between amber and red requires tracking which fields constitute a breaking change (embedding model, vector store type, embedding backend) versus additive changes (batch size, OCR toggle, captioning) that only affect newly ingested documents. This logic does not exist yet and needs to be designed.

---




## Current Auth Architecture

### How auth actually works today

Auth in Lancy is entirely in the **Next.js frontend middleware**, not in the backend. The toolkit's `PasscodeProvider` class exists in the codebase but is not wired up anywhere in Lancy — ignore it for design purposes.

Three components:

**`frontend/src/middleware.ts`** — a Next.js middleware that guards every route on every request. It checks for one of two things:
- A `rag_auth` cookie whose value equals the raw `APP_PASSWORD` env var (browser users after login)
- An `Authorization: Bearer {APP_PASSWORD}` header (API clients like Open WebUI or curl)

If neither is present, browser requests are redirected to `/login` and API requests get a 401.

**`frontend/src/pages/api/auth/login.ts`** — the login endpoint. Accepts a POST with `{ password }`, compares it against `APP_PASSWORD` from `.env`, and on success sets a `rag_auth` cookie containing the raw key value, valid for 30 days. This is the only way browser users authenticate.

**Backend is unprotected at the network level** — `main.py` does not verify any password. The backend is only protected indirectly because the frontend proxy is the sole entry point in a normal deployment. Anyone who can reach the backend port directly (e.g. on the same host or LAN) can call all API endpoints without any credentials. This is acceptable for the current local/intranet deployment model but must be addressed before any internet-facing deployment.

### The two cookies

Two completely separate cookies exist simultaneously in a logged-in browser session:

| Cookie | Set by | Purpose | Current value |
|---|---|---|---|
| `rag_auth` | Next.js login endpoint | Auth gate — "are you allowed in?" | Raw `APP_PASSWORD` string |
| `access_token` | Backend JWT (`SessionCookieProvider`) | Identity — "who are you?" | Always `"admin"` (hardcoded) |

These serve different purposes and are checked by different layers. `rag_auth` is checked by the Next.js middleware on every request. `access_token` is checked by the backend when it needs to scope data to a user (conversation history).

The `access_token` cookie currently resolves every browser to the same `"admin"` identity. This was a deliberate simplification of an earlier per-browser UUID approach. The code in `SessionCookieProvider` has the commented-out UUID generation and a note: *"Re-introduce for SSO/AD integration."*

### What this means for mode 2

**Role encoding:** The `rag_auth` cookie holds the raw password value. In mode 2, the middleware would compare it against two env vars (`USER_KEY`, `ADMIN_KEY`) to determine role. A user-password login gets `rag_auth=<user_key>`, an admin-password login gets `rag_auth=<admin_key>`. The role is derivable on every request from the cookie value alone — no session store needed.

**Per-user identity:** The `rag_auth` cookie cannot provide per-user identity because all users of the same role share the same key value. The `access_token` JWT is the right mechanism — re-enabling per-browser UUID generation in `SessionCookieProvider` gives each browser a stable unique identity without requiring user accounts. That identity can key per-user retrieval config on the backend.

**Backend protection:** For mode 2, admin-only API endpoints (reindex, KB create/edit/delete, full config write) should also be protected at the backend level — not just hidden in the UI. The role determined from `rag_auth` needs to be forwarded to the backend, either as a signed header added by the Next.js proxy or by issuing separate JWT tokens per role. This is the API protection gap noted in the "Things to consider" section above.

---

## Field-level Access Control

Three tiers: **admin-only** (hidden from users), **read-only for users** (visible but locked), **user-editable**.

### KB Management (create / edit / delete / switch)

- KB-level
- Create, edit, delete: admin-only
- Switch active KB: user-editable — users may legitimately pick their document set in a multi-KB deployment

### Presets (load / save / delete)

- Preset-level
- Admin: create, edit, delete company-wide presets; admin presets are marked and not editable by users
- User: load any preset, save and delete their own personal presets (personal presets stored per-user once user accounts exist; today shared per-KB)

**Presets need to be split by concern before role separation can work cleanly.**

Currently a preset is a flat snapshot of the entire `KBConfig + SessionConfig` — embedding model, LLM model, retrieval settings, everything in one object (`PresetData extends KBConfig, SessionConfig`). This conflates two distinct concerns that belong to different phases of the RAG pipeline and map to different config objects.

The correct split:

- **KB presets** — snapshot `KBConfig`: embedding model, embedding backend, chunking settings, OCR, vector store type, image indexing, etc. These affect ingestion and require a re-index to take effect. Admin-only, stored per-KB.
- **Retrieval presets** — snapshot `SessionConfig`: top-K, BM25, RRF-K, query expansion, HyDE, reranking, LLM model, temperature, system prompt, follow-up count. These affect querying and take effect immediately. User-editable (within their permitted field scope).

This maps cleanly onto the existing data model and pipeline phases. Role access follows naturally: KB presets are admin territory (ingestion), retrieval presets are user territory (querying). The preset schema, storage, and load/save logic in `rag-config-panel.tsx` all need to be split accordingly.

### Prompt Section

| Field | Level | Access |
|---|---|---|
| Follow-up count | session | user-editable |
| System prompt | session | admin-only |

**Note — system prompt architecture:** currently global, not per-KB. The prompt is stored in `prompts/system_prompt.custom.md` (falling back to `system_prompt.default.md`), neither of which is KB-aware. Target architecture should be: per-KB prompt → global custom fallback (`system_prompt.custom.md`) → default fallback (`system_prompt.default.md`). This requires a backend change to store and load the prompt per KB.

### LLM Section

All fields read-only for users (visible but locked). Users can see which model and temperature is active, but cannot change backend, credentials, or infrastructure settings.

| Field | Level | Access |
|---|---|---|
| LLM backend | session | read-only for users |
| LLM model | session | read-only for users |
| Ollama host | session | admin-only |
| Custom base URL / API key | session | admin-only (hide from users — credentials) |
| Utility LLM model | session | read-only for users |
| num_ctx / max tokens | session | read-only for users |
| Temperature | session | read-only for users |

### Retrieval Section

All fields user-editable, with sane defaults coming from admin-set presets. Exception: image retrieval is read-only and forced off when the active KB does not have image indexing enabled.

| Field | Level | Access |
|---|---|---|
| Top-K | session | user-editable |
| BM25 enabled | session | user-editable |
| RRF-K | session | user-editable |
| Query expansion | session | user-editable |
| HyDE enabled | session | user-editable |
| Reranking enabled | session | user-editable |
| Reranking candidate pool | session | user-editable |
| Image retrieval enabled | KB-level | user-editable if KB has image indexing on; read-only + forced off otherwise |
| Image retriever top-K | session | user-editable if image retrieval is on |

### Embedding Section

All fields are KB-level and admin-only — infrastructure, re-index implications, credentials. Nothing shown to users.

### Default Section State

For normal users, all sections (Prompt, LLM, Retrieval, Embedding) are collapsed by default. Users expand what they need. For admins, the current default open/closed state is preserved.

### Action Buttons

| Button | Access |
|---|---|
| Apply | user-accessible (within their session scope) |
| Re-index (incremental) | admin-only |
| Re-index (full reset) | admin-only |
| Save preset | user-accessible (personal presets) |
| Refresh | user-accessible — allows reverting unsaved changes by reloading server state |
| Stop indexing (sidebar) | admin-only |

---

## User Session Isolation

### Current State

All session config is server-persisted to a single shared file (`rag_config.json`). There is no concept of user identity. Every authenticated client reads from and writes to the same state. This includes all retrieval settings, LLM settings, and the active KB selection.

### The Problem in Mode 2

In mode 2 (two passwords, no individual accounts), user-adjustable settings like top-K and retrieval toggles are still stored in the shared `rag_config.json`. When a user clicks Apply, they overwrite the config for all concurrent users. If two users adjust settings simultaneously, the last write wins and the other user's changes are silently lost. Personal presets are also stored per-KB with no user scoping, so they are visible and deletable by any user on that KB.

This is a **known and accepted limitation for mode 2**. For the target use case — a small trusted team — simultaneous config editing is rare, and the conflict surface is further reduced because admin-only fields (LLM, embedding) are locked for users. The realistic worst case is two users tuning retrieval settings at the same time, which is an edge case in practice.

### What "Personal" Means in Mode 2

Without user identity, "personal presets" cannot be truly personal — they are scoped to the KB, not to the individual. In mode 2, personal presets are better understood as *user-created shared presets* that any user of the same KB can see and load. Distinguishing them visually from admin presets (which are set intentionally for everyone) is still valuable, but the privacy guarantee is absent.

### Path to True Isolation (Mode 3)

Per-user session isolation requires individual identity, which only arrives with mode 3 (external directory / SSO). Once user identity is available:

- Session config (retrieval settings) can be stored and loaded per user, so each user has their own independent state that does not affect others.
- Personal presets become truly personal — stored against the user's identity, invisible to others.
- Apply writes to the user's own config, not a shared file.
- The shared `rag_config.json` becomes the admin-set baseline that is loaded for new users or users who have not yet applied their own settings.

### Intermediate Option: Cookie Identity for Mode 2

The `access_token` JWT cookie (issued by `SessionCookieProvider`, used for conversation history) currently resolves every browser to the hardcoded identity `"admin"`. The original codebase generated a unique per-browser UUID here, which was deliberately collapsed to a fixed value when the system was simplified for single-user use. The code comment reads: *"Re-introduce for SSO/AD integration."*

Re-enabling per-browser UUID generation would give each browser its own stable identity without requiring user accounts. That identity could key per-user retrieval config on the backend — a `rag_config_{user_id}.json` alongside the shared admin baseline. Only the user-editable fields (the retrieval section: top-K, BM25, RRF-K, query expansion, HyDE, reranking settings) live in the per-user file; LLM and embedding settings remain in the shared `rag_config.json` and are never overwritten by user actions. The fallback chain on load would be: user-specific file → shared admin baseline → code defaults.

The role (user vs admin) is separately available from the `rag_auth` cookie value — see the "Current Auth Architecture" section above.

If the cookie is cleared, a new UUID is issued and the user falls back to the admin baseline and presets — which is acceptable behaviour and matches the user's expectation.

This is a viable path for mode 2 isolation that requires no new auth infrastructure. The main work is: re-enable UUID generation in `SessionCookieProvider`, scope retrieval config reads/writes to the `user_id` from the cookie, and define the fallback chain in the backend. The `passcode` cookie value simultaneously identifies the role (user vs admin passcode), which is sufficient for the two-password model.

---

## Storage Backend for Per-User Data

### Why not JSON

The current JSON file approach works for a single global config, but breaks down once data is keyed by user:

- Concurrent writes require manual locking — the last writer wins silently
- No atomic updates across multiple records
- Any cross-user query (e.g. "which users have reranking enabled?") requires loading and parsing all files
- Per-user preset files would proliferate and need their own garbage collection

### SQLite

SQLite is the right backend for per-user settings storage. It handles row-level writes, atomic transactions, and simple queries without an external server. FastAPI + SQLite is a well-worn path via SQLAlchemy or the stdlib `sqlite3` module.

**Schema sketch (mode 3 target):**

```sql
users        (id TEXT PK, role TEXT, created_at TIMESTAMP)
user_config  (user_id TEXT FK, config_json TEXT, updated_at TIMESTAMP)
presets      (id INTEGER PK, user_id TEXT nullable, kb_id TEXT, type TEXT, name TEXT, data_json TEXT)
```

- `user_id = NULL` on a preset row → admin/shared preset, visible to all
- `user_id = <uuid>` → personal preset, scoped to that browser identity
- `user_config` row per user holds their retrieval settings; falls back to the shared admin baseline if absent

**For mode 2** (no individual accounts), the `users` table is not needed yet. SQLite still provides value as a cleaner store for the shared config and shared presets, replacing the current JSON files. Migration from JSON → SQLite can happen incrementally: swap the storage layer without changing the API surface.

### Unlocked by SQLite

Per-session and per-query statistics become practical once a database is in place:

- **Query stats** — latency, retrieval chunk count, reranking swaps, model used, token counts — stored per query, queryable by time range or KB
- **Session stats** — number of turns, total tokens, active preset at time of query
- **Indexing history** — already partially tracked in the analytics panel; could be persisted durably instead of in-memory

These are additive — the schema can grow a `query_log` table without touching the settings tables.

---

## Mode 1 → Mode 2 Transition Flow

### Default state (Mode 1)

Mode 1 is the default and requires no configuration. All users who log in with `APP_PASSWORD` are treated as admins. Nothing changes for existing deployments.

### Activating Mode 2 from settings

The settings page (dark mode, language, etc.) is always accessible to all authenticated users — it is not behind role separation. This is where the Mode 2 activation lives:

1. User navigates to settings → finds a "Role Separation" section
2. Sets an admin password and confirms a dialog explaining: "From now on, users who log in with the regular password see a read-only view. Log in with the admin password to access configuration."
3. The admin password is written to `auth_config.json` (gitignored, alongside `rag_config.json`) via a backend endpoint — no restart required.

Once `auth_config.json` contains an `admin_key`, Mode 2 is active. The existing `APP_PASSWORD` becomes the user password; `admin_key` is the admin password.

### Elevating to admin in Mode 2

The settings page shows an "Admin Login" button when Mode 2 is active and the current session is user-level. Clicking it navigates to the existing `/login` page (with a query param to hint the label, e.g. `?mode=admin`). The user enters the admin password, the cookie is updated, and they return with admin access.

---

## Auth Architecture for Mode 2

### The core seam: `getRole()`

The middleware's role check must be extracted into a single `getRole(request) → "admin" | "user" | null` function. This is the only thing that changes between modes:

```
Mode 1:  cookie == APP_PASSWORD                      → "admin"
Mode 2:  cookie == admin_key → "admin",
         cookie == APP_PASSWORD   → "user"
Mode 3:  verify JWT / SSO token, read role claim → "admin" | "user"
```

All downstream logic — frontend components, API protection, preset scoping — consumes the role, not the raw cookie value. Replacing the `getRole()` implementation is sufficient to move between modes.

### Cookie scheme

The `rag_auth` cookie continues to hold the raw key value. The middleware derives role by comparing it against the known key values. No role string is stored in the cookie itself (which would be forgeable).

### Admin password storage

`ADMIN_KEY` is stored in `auth_config.json` (gitignored), not as an env var, so it can be written from the UI without a restart. The middleware and login endpoint read from this file (falling back to env var if present, for scripted deployments).

### Frontend role consumption

A `/api/auth/me` endpoint reads the cookie and returns `{ role: "admin" | "user" }`. A `useRole()` React hook wraps this call. All UI components that conditionally render based on role consume `useRole()` — this surface does not change when moving to Mode 3.

### Implementation steps (Mode 2)

1. Extract `getRole()` from `middleware.ts`
2. Add `/api/auth/me` endpoint → returns `{ role }`
3. Add `useRole()` React hook
4. Backend: `GET/POST /api/v1/admin/auth-config` — reads/writes `admin_key` in `auth_config.json`
5. Settings: "Role Separation" section with admin password field and confirmation dialog
6. Settings: "Admin Login" button (shown when Mode 2 active and current role is user)
7. Modify `/api/auth/login` to check both keys and set cookie accordingly