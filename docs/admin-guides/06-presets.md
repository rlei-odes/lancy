# Presets

A preset is a named snapshot of settings that can be loaded in one click. Lancy keeps two independent kinds, and they are not interchangeable.

| Kind | Snapshots | Effect | Who may write |
|---|---|---|---|
| **Retrieval** | top-K, BM25, RRF-K, query expansion, HyDE, reranking, candidate pool | Immediate, per query | Admins only |
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

`Default` is the company-wide baseline. It is special in one way: **switching knowledge base in the settings panel loads it**, replacing the retrieval settings shown there. It is matched by that exact name. Switching knowledge base in the chat only selects it and changes no settings.

To change the `Default` preset:

1. Log in as admin and open **Admin → Knowledge & RAG**.
2. Adjust the retrieval settings.
3. Click the save icon next to the Retrieval preset dropdown, type `Default`, confirm.

The edit is stored globally, not against the KB you happened to have open. It keeps its `·` level.

A preset only changes what the panel shows. What every user's questions run with is what the admin last saved with the **Save** button at the bottom of the panel.

The same applies to `Fast`, `Multi-Document` and `Quality`: an admin saving under one of those names updates that shared preset for everyone.

---

## What users can do

Users see the settings and presets read-only, in the settings panel of the Advanced Features page. They cannot save settings: `POST /api/v1/rag/config` is admin-only, because a settings save rebuilds the agents everyone answers with. Every user's questions run with the settings the admin saved.

Retrieval settings users saved in earlier versions are still in `user_config.db` but no longer applied.

---

## Known rough edges

- Saving a preset reports success without waiting for the server to confirm, so an unrelated failure (network, permissions) can still show a success toast. Tracked in `BACKLOG.md`.
- There are two save controls in the panel — the disk icon saves a *preset*, the Apply button at the bottom pushes the *current state* to the server. They do different things; see `DESIGN_DOC_Admin_Role_Separation.md` for the planned clean-up.
