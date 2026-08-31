"""Intent: every SQLite handle is closed, and users may only override retrieval fields.

Two separate contracts live in `lancy.database`:

1. `_connect` must close the connection, not merely commit it. `with sqlite3.connect(...)`
   commits but leaves the handle open until the object happens to be reclaimed —
   that is what put 87 open user_config.db handles in one process and drove it to
   its 1024-descriptor ceiling, after which every unrelated open() failed too.

2. `USER_RETRIEVAL_FIELDS` is the allowlist that keeps a non-admin session from
   overriding admin-only settings. `rag_router` filters user input through it, so
   anything added to that set becomes writable by any logged-in user.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from lancy.database import (
    USER_RETRIEVAL_FIELDS,
    _connect,
    get_user_retrieval,
    init_db,
    set_user_retrieval,
)


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "user_config.db"
    init_db(path)
    return path


# ─── contract 1: connections are closed, not just committed ───────────────────


def test_connect_closes_the_handle_on_success(db):
    """The fd leak: a committed connection is still an open descriptor."""
    with _connect(db) as conn:
        conn.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_connect_closes_the_handle_when_the_body_raises(db):
    """A failing request must not leak the descriptor either."""
    with pytest.raises(RuntimeError):
        with _connect(db) as conn:
            raise RuntimeError("boom")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="needs /proc")
def test_repeated_writes_do_not_accumulate_descriptors(db):
    """The observed symptom: a batch run left one process holding 87 handles.

    WAL mode costs two descriptors per connection (the db and its -wal), so a
    leak here grows fast. 30 round-trips must end where they started.
    """
    def open_fds() -> int:
        return len(os.listdir("/proc/self/fd"))

    set_user_retrieval(db, "warmup", {"retriever_top_k": 1})
    before = open_fds()

    for i in range(30):
        set_user_retrieval(db, f"user-{i}", {"retriever_top_k": i})
        get_user_retrieval(db, f"user-{i}")

    assert open_fds() == before


def test_user_retrieval_round_trips(db):
    set_user_retrieval(db, "abc", {"retriever_top_k": 7, "bm25_enabled": False})
    assert get_user_retrieval(db, "abc") == {"retriever_top_k": 7, "bm25_enabled": False}


def test_unknown_user_reads_as_none(db):
    assert get_user_retrieval(db, "nobody") is None


# ─── contract 2: the user-writable allowlist stays retrieval-only ─────────────


@pytest.mark.parametrize(
    "admin_only_field",
    [
        "llm_backend",
        "llm_model",
        "system_prompt",
        "embedding_backend",
        "embedding_model",
        "custom_api_key",
    ],
)
def test_admin_only_settings_are_not_user_writable(admin_only_field):
    """rag_router filters user input through this set — anything in it is user-writable."""
    assert admin_only_field not in USER_RETRIEVAL_FIELDS


def test_allowlist_is_exactly_the_documented_retrieval_knobs():
    """Pinned deliberately: widening this set is a permission change, not a tweak.

    If you are adding a genuine retrieval knob, update this list in the same commit.
    """
    assert USER_RETRIEVAL_FIELDS == {
        "retriever_top_k",
        "rrf_k",
        "bm25_enabled",
        "query_expansion",
        "hyde_enabled",
        "reranking_enabled",
        "reranking_candidate_pool",
        "image_retriever_top_k",
    }
