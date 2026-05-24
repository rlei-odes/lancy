"""
SQLite persistence layer.

Single database file: db/user_config.db
Tables:
  user_config — per-browser retrieval config override (keyed by session UUID)
  presets     — retrieval and KB presets; user_id=NULL means admin/shared,
                kb_id=NULL means global (shown for every KB)

All public functions accept db_path so callers stay testable without patching globals.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("uvicorn")

_sqlite_lock = threading.Lock()
_BACKUP_INTERVAL_SECONDS = 86_400  # 24 h

# Fields a non-admin user may override in their own per-browser config.
# Everything else (LLM, embedding, prompt) is admin-only and lives in rag_config.json.
USER_RETRIEVAL_FIELDS: frozenset[str] = frozenset({
    "retriever_top_k",
    "rrf_k",
    "bm25_enabled",
    "query_expansion",
    "hyde_enabled",
    "reranking_enabled",
    "reranking_candidate_pool",
    "image_retriever_top_k",
})


# ─── Connection setup ─────────────────────────────────────────────────────────


def _configure_conn(conn: sqlite3.Connection) -> None:
    """Per-connection pragmas — these are not persistent and must be set each time."""
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")


# ─── Backup ───────────────────────────────────────────────────────────────────


def _maybe_backup(db_path: Path) -> None:
    """Copy the DB to <name>.db.bak if it exists and is older than the backup interval."""
    if not db_path.exists():
        return
    age = datetime.now(timezone.utc).timestamp() - db_path.stat().st_mtime
    if age < _BACKUP_INTERVAL_SECONDS:
        return
    bak = db_path.with_suffix(".db.bak")
    try:
        with sqlite3.connect(db_path) as src, sqlite3.connect(bak) as dst:
            src.backup(dst)
        log.info(f"SQLite backup written to {bak}")
    except Exception as exc:
        log.warning(f"SQLite backup failed: {exc}")


# ─── Init ─────────────────────────────────────────────────────────────────────


def _migrate_presets_unique_index(conn: sqlite3.Connection) -> None:
    """
    One-time migration: replace the broken inline UNIQUE(user_id, kb_id, type, name) with
    a COALESCE-based expression index. SQLite treats each NULL as distinct in plain UNIQUE
    constraints, so INSERT OR IGNORE never fires for NULL-keyed rows and seeds accumulate
    a new copy on every startup. The expression index maps NULL → '' so uniqueness is
    properly enforced regardless of NULL keys.
    """
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_presets_unique'"
    ).fetchone():
        return
    # Deduplicate before creating the index — keep the row with the lowest id per logical key.
    conn.execute("""
        DELETE FROM presets WHERE id NOT IN (
            SELECT MIN(id) FROM presets
            GROUP BY COALESCE(user_id, ''), COALESCE(kb_id, ''), type, name
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX idx_presets_unique
        ON presets(COALESCE(user_id, ''), COALESCE(kb_id, ''), type, name)
    """)
    log.info("Migrated presets table: added NULL-safe expression unique index and deduplicated rows")


def init_db(db_path: Path) -> None:
    """Initialise the database, run migrations, and back up if due."""
    _maybe_backup(db_path)
    with sqlite3.connect(db_path) as conn:
        _configure_conn(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        # Enable incremental auto-vacuum. Existing DBs need a one-time VACUUM to convert.
        if conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:  # 2 = INCREMENTAL
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("VACUUM")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_config (
                user_id     TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS presets (
                id        INTEGER PRIMARY KEY,
                user_id   TEXT,
                kb_id     TEXT,
                type      TEXT NOT NULL,
                name      TEXT NOT NULL,
                data_json TEXT NOT NULL,
                protected INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Compound index covers every query pattern: WHERE kb_id=? AND user_id=? AND type=?
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_presets_lookup
            ON presets(kb_id, user_id, type)
        """)
        _migrate_presets_unique_index(conn)
        conn.commit()


# ─── user_config CRUD ─────────────────────────────────────────────────────────


def get_user_retrieval(db_path: Path, user_id: str) -> dict | None:
    with sqlite3.connect(db_path) as conn:
        _configure_conn(conn)
        row = conn.execute(
            "SELECT config_json FROM user_config WHERE user_id = ?", (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def set_user_retrieval(db_path: Path, user_id: str, data: dict) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _sqlite_lock:
        with sqlite3.connect(db_path) as conn:
            _configure_conn(conn)
            conn.execute(
                "INSERT OR REPLACE INTO user_config (user_id, config_json, updated_at) "
                "VALUES (?, ?, ?)",
                (user_id, json.dumps(data), now),
            )
            conn.commit()


# ─── presets CRUD ─────────────────────────────────────────────────────────────


def get_presets(db_path: Path, kb_id: str, user_id: str | None) -> dict:
    """
    Return merged presets for a KB. Retrieval presets include both admin/global
    and the user's personal presets. KB presets are admin-only.
    Order: global before KB-specific, admin before personal, then by name.
    The `protected` field is included so the frontend can hide delete buttons and
    filter seeds out of save payloads.
    """
    with sqlite3.connect(db_path) as conn:
        _configure_conn(conn)
        rows = conn.execute("""
            SELECT name, data_json, protected FROM presets
            WHERE type = 'retrieval'
              AND (kb_id = ? OR kb_id IS NULL)
              AND (user_id IS NULL OR user_id = ?)
            ORDER BY user_id NULLS FIRST, kb_id NULLS FIRST, name
        """, (kb_id, user_id)).fetchall()
        retrieval = [{"name": r[0], "data": json.loads(r[1]), "protected": r[2]} for r in rows]

        rows = conn.execute("""
            SELECT name, data_json, protected FROM presets
            WHERE type = 'kb'
              AND (kb_id = ? OR kb_id IS NULL)
              AND user_id IS NULL
            ORDER BY kb_id NULLS FIRST, name
        """, (kb_id,)).fetchall()
        kb = [{"name": r[0], "data": json.loads(r[1]), "protected": r[2]} for r in rows]

    return {"retrieval": retrieval, "kb": kb}


def save_presets(
    db_path: Path,
    kb_id: str,
    user_id: str | None,
    role: str,
    presets: dict,
) -> None:
    """
    Full-replace save for a (kb_id, user_id, type) scope.
    Admin writes to the shared scope (user_id=NULL); users write to their own.
    KB presets are silently ignored for non-admin callers.

    Protection rules:
      protected=0  deletable by owner (user or admin)
      protected=1  admin-seeded; users cannot delete or overwrite
      protected=2  fully immutable; nobody can delete or overwrite
    """
    retrieval = presets.get("retrieval", [])
    kb_presets = presets.get("kb", [])
    scope_user_id = None if role == "admin" else user_id
    # Admins may modify up to protected=1; nobody touches protected=2
    max_deletable = 1 if role == "admin" else 0

    with _sqlite_lock:
        with sqlite3.connect(db_path) as conn:
            _configure_conn(conn)

            # Delete only unprotected rows in this scope — never touch seeds
            conn.execute(
                "DELETE FROM presets WHERE type='retrieval' AND kb_id=? AND user_id IS ? AND protected <= ?",
                (kb_id, scope_user_id, max_deletable),
            )
            for p in retrieval:
                if "name" not in p or "data" not in p:
                    continue
                # Safety net: skip if a global seed with higher protection already owns this name
                seed = conn.execute(
                    "SELECT protected FROM presets "
                    "WHERE type='retrieval' AND kb_id IS NULL AND user_id IS NULL AND name=?",
                    (p["name"],),
                ).fetchone()
                if seed and seed[0] > max_deletable:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO presets (user_id, kb_id, type, name, data_json, protected) "
                    "VALUES (?, ?, 'retrieval', ?, ?, 0)",
                    (scope_user_id, kb_id, p["name"], json.dumps(p["data"])),
                )

            # KB presets: admin only
            if role == "admin":
                conn.execute(
                    "DELETE FROM presets WHERE type='kb' AND kb_id=? AND user_id IS NULL AND protected <= ?",
                    (kb_id, max_deletable),
                )
                for p in kb_presets:
                    if "name" not in p or "data" not in p:
                        continue
                    seed = conn.execute(
                        "SELECT protected FROM presets "
                        "WHERE type='kb' AND kb_id IS NULL AND user_id IS NULL AND name=?",
                        (p["name"],),
                    ).fetchone()
                    if seed and seed[0] > max_deletable:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO presets (user_id, kb_id, type, name, data_json, protected) "
                        "VALUES (NULL, ?, 'kb', ?, ?, 0)",
                        (kb_id, p["name"], json.dumps(p["data"])),
                    )

            conn.commit()


# ─── Seeding ──────────────────────────────────────────────────────────────────


def seed_presets(db_path: Path, seeds_path: Path) -> None:
    """
    Insert missing presets from a JSON seed file as global admin presets.
    Uses INSERT OR IGNORE so existing preset data (including admin edits) is preserved.
    The protected level is always kept up-to-date regardless of INSERT OR IGNORE.
    New names added to the seed file are picked up on next startup.
    """
    if not seeds_path.exists():
        return
    try:
        data = json.loads(seeds_path.read_text())
    except Exception as exc:
        log.warning(f"Could not read preset seeds from {seeds_path}: {exc}")
        return

    inserted = 0
    with _sqlite_lock:
        with sqlite3.connect(db_path) as conn:
            _configure_conn(conn)
            for p in data.get("retrieval", []):
                protected = p.get("protected", 1)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO presets (user_id, kb_id, type, name, data_json, protected) "
                    "VALUES (NULL, NULL, 'retrieval', ?, ?, ?)",
                    (p["name"], json.dumps(p["data"]), protected),
                )
                inserted += cur.rowcount
                # Always enforce the correct protection level even on existing rows
                conn.execute(
                    "UPDATE presets SET protected=? "
                    "WHERE user_id IS NULL AND kb_id IS NULL AND type='retrieval' AND name=?",
                    (protected, p["name"]),
                )
            for p in data.get("kb", []):
                protected = p.get("protected", 1)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO presets (user_id, kb_id, type, name, data_json, protected) "
                    "VALUES (NULL, NULL, 'kb', ?, ?, ?)",
                    (p["name"], json.dumps(p["data"]), protected),
                )
                inserted += cur.rowcount
                conn.execute(
                    "UPDATE presets SET protected=? "
                    "WHERE user_id IS NULL AND kb_id IS NULL AND type='kb' AND name=?",
                    (protected, p["name"]),
                )
            conn.commit()

    if inserted:
        log.info(f"Seeded {inserted} preset(s) from {seeds_path.name}")


def get_default_preset(db_path: Path) -> dict | None:
    """Return the data dict of the immutable Default retrieval preset, or None."""
    with sqlite3.connect(db_path) as conn:
        _configure_conn(conn)
        row = conn.execute(
            "SELECT data_json FROM presets "
            "WHERE type='retrieval' AND name='Default' AND protected=2 AND user_id IS NULL",
        ).fetchone()
    return json.loads(row[0]) if row else None


# ─── JSON migration ───────────────────────────────────────────────────────────


def migrate_json_presets(db_path: Path, db_dir: Path) -> None:
    """
    One-time migration: import rag_presets_*.json files as admin presets.
    INSERT OR IGNORE means already-migrated or seeded presets are never overwritten.
    """
    migrated = 0
    for json_path in sorted(db_dir.glob("rag_presets_*.json")):
        kb_id = json_path.stem.removeprefix("rag_presets_")
        try:
            raw = json.loads(json_path.read_text())
        except Exception:
            continue
        # Support both old (flat list) and new ({retrieval, kb}) formats
        retrieval = raw if isinstance(raw, list) else raw.get("retrieval", [])
        kb_list = [] if isinstance(raw, list) else raw.get("kb", [])

        with _sqlite_lock:
            with sqlite3.connect(db_path) as conn:
                _configure_conn(conn)
                for p in retrieval:
                    if isinstance(p, dict) and "name" in p and "data" in p:
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO presets "
                            "(user_id, kb_id, type, name, data_json) "
                            "VALUES (NULL, ?, 'retrieval', ?, ?)",
                            (kb_id, p["name"], json.dumps(p["data"])),
                        )
                        migrated += cur.rowcount
                for p in kb_list:
                    if isinstance(p, dict) and "name" in p and "data" in p:
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO presets "
                            "(user_id, kb_id, type, name, data_json) "
                            "VALUES (NULL, ?, 'kb', ?, ?)",
                            (kb_id, p["name"], json.dumps(p["data"])),
                        )
                        migrated += cur.rowcount
                conn.commit()

    if migrated:
        log.info(f"Migrated {migrated} preset(s) from JSON files to SQLite")
