# Presets

A preset is a named snapshot of settings that can be loaded in one click. Lancy keeps two independent kinds, and they are not interchangeable.

| Kind | Snapshots | Effect | Who may write |
|---|---|---|---|
| **Retrieval** | top-K, BM25, RRF-K, query expansion, HyDE, reranking, candidate pool | Immediate, per query | Admins (shared) and users (their own) |
| **KB** | embedding model and backend, chunking, OCR, vector store, image indexing | Requires a re-index | Admins only |

---

## Protection levels

Every preset carries a level that decides who may overwrite it. Levels are shown in the dropdown as a prefix.

| Level | Prefix | Who may overwrite |
|---|---|---|
| 0 | `★` | its owner — the admin for shared presets, the user for personal ones |
| 1 | `·` | admins only; users saving over one get an error |
| 2 | `◆` | nobody, admins included |

Level 2 exists for settings that must never change. No shipped preset uses it.

Seeded presets live in `backend/src/lancy/seeds/presets.json` and are inserted on startup if missing. Editing that file does **not** change a preset that already exists in the database — seeding uses `INSERT OR IGNORE`, so admin edits are never overwritten by a restart. The protection level *is* refreshed on every startup.

---

## The `Default` retrieval preset

`Default` is the company-wide baseline. It is special in one way: **switching to a knowledge base loads it**, replacing the current retrieval settings. It is matched by that exact name.

It is also what seeds a user's very first session, before they have adjusted anything.

To change what everyone gets:

1. Log in as admin and select any knowledge base.
2. Adjust the retrieval settings in the RAG Parameters panel.
3. Click the save icon next to the Retrieval preset dropdown, type `Default`, confirm.

The edit is stored globally, not against the KB you happened to have open, so it applies everywhere. It keeps its `·` level, so users still cannot overwrite it.

The same applies to `Fast`, `Multi-Document` and `Quality`: an admin saving under one of those names updates that shared preset for everyone.

---

## What users can do

Users load any preset, including the admin ones, and save their own under any name that is not taken by a protected preset. Personal presets are stored against the user's identity and are invisible to everyone else. A user who tries to save over `Default` gets an explicit error rather than a silent no-op.

Note that a user's own preset is **not** re-applied when they switch KB — `Default` is. They have to re-select their preset afterwards.

---

## Known rough edges

- Saving a preset reports success without waiting for the server to confirm, so an unrelated failure (network, permissions) can still show a success toast. Tracked in `BACKLOG.md`.
- There are two save controls in the panel — the disk icon saves a *preset*, the Apply button at the bottom pushes the *current state* to the server. They do different things; see `DESIGN_DOC_Admin_Role_Separation.md` for the planned clean-up.
