"""Intent: a pile belongs to exactly one session, and its store is never mistaken
for the admin corpus.

Piles let a user upload their own documents and process them into a private vector
store. Three properties carry the feature:

1. **Ownership is the lookup.** Every read is keyed on (pile_id, owner_id), so there
   is no code path that resolves a pile without its owner. A guessed id from another
   session has to be indistinguishable from one that does not exist.

2. **A changed file set invalidates the store.** Adding or removing a file after
   processing leaves the store describing a set the pile no longer has, so the pile
   must fall out of `ready` — otherwise batch analysis silently reads a stale corpus.

3. **`vs_type` is recorded before the work starts.** A processing run that dies
   halfway may already have created a pgvector table; if the backend were stamped
   only on success, nothing would know how to drop it and the table would be orphaned.
"""

import sqlite3
from pathlib import Path

import pytest

from lancy.database import (
    add_pile_file,
    begin_pile_processing,
    create_pile,
    delete_pile,
    delete_pile_file,
    finish_pile_processing,
    get_pile,
    init_db,
    list_pile_files,
    list_piles,
    pile_usage,
    set_pile_status,
)
from lancy.piles_router import _safe_filename, embedding_key

OWNER = "session-aaa"
OTHER = "session-bbb"


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "user_config.db"
    init_db(path)
    return path


@pytest.fixture
def pile(db) -> str:
    create_pile(db, "p1", OWNER, "Tender docs")
    return "p1"


# ─── property 1: ownership is the lookup ──────────────────────────────────────


def test_another_owner_cannot_resolve_the_pile(db, pile):
    """The security property: a correct id with the wrong owner yields nothing."""
    assert get_pile(db, pile, OWNER) is not None
    assert get_pile(db, pile, OTHER) is None


def test_another_owner_cannot_delete_the_pile(db, pile):
    assert delete_pile(db, pile, OTHER) is False
    assert get_pile(db, pile, OWNER) is not None
    assert delete_pile(db, pile, OWNER) is True
    assert get_pile(db, pile, OWNER) is None


def test_listing_is_scoped_to_one_owner(db, pile):
    create_pile(db, "p2", OTHER, "Their docs")
    assert [p["id"] for p in list_piles(db, OWNER)] == [pile]
    assert [p["id"] for p in list_piles(db, OTHER)] == ["p2"]


def test_two_owners_may_use_the_same_pile_name(db, pile):
    """The uniqueness constraint is per owner, not global."""
    create_pile(db, "p2", OTHER, "Tender docs")
    assert get_pile(db, "p2", OTHER)["name"] == "Tender docs"


def test_one_owner_cannot_reuse_a_pile_name(db, pile):
    """Two identically named piles would be indistinguishable in the picker."""
    with pytest.raises(sqlite3.IntegrityError):
        create_pile(db, "p9", OWNER, "Tender docs")


def test_deleting_a_pile_takes_its_file_rows(db, pile):
    add_pile_file(db, pile, "a.pdf", "a.pdf", 10)
    delete_pile(db, pile, OWNER)
    assert list_pile_files(db, pile) == []


# ─── property 2: a changed file set invalidates the store ─────────────────────


def _make_ready(db, pile_id):
    finish_pile_processing(db, pile_id, chunk_count=42, file_count=1, embedding_key="local/nomic")


def test_adding_a_file_to_a_ready_pile_marks_it_stale(db, pile):
    add_pile_file(db, pile, "a.pdf", "a.pdf", 10)
    _make_ready(db, pile)
    assert get_pile(db, pile, OWNER)["status"] == "ready"

    add_pile_file(db, pile, "b.pdf", "b.pdf", 20)
    assert get_pile(db, pile, OWNER)["status"] == "stale"


def test_removing_a_file_from_a_ready_pile_marks_it_stale(db, pile):
    add_pile_file(db, pile, "a.pdf", "a.pdf", 10)
    _make_ready(db, pile)
    assert delete_pile_file(db, pile, "a.pdf") is True
    assert get_pile(db, pile, OWNER)["status"] == "stale"


def test_removing_a_missing_file_changes_nothing(db, pile):
    _make_ready(db, pile)
    assert delete_pile_file(db, pile, "nope.pdf") is False
    assert get_pile(db, pile, OWNER)["status"] == "ready"


def test_uploading_into_a_draft_pile_leaves_it_draft(db, pile):
    """Only a pile that already has a store can go stale."""
    add_pile_file(db, pile, "a.pdf", "a.pdf", 10)
    assert get_pile(db, pile, OWNER)["status"] == "draft"


def test_a_failed_pile_does_not_become_stale(db, pile):
    set_pile_status(db, pile, "failed", "RuntimeError")
    add_pile_file(db, pile, "a.pdf", "a.pdf", 10)
    row = get_pile(db, pile, OWNER)
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError"


def test_reprocessing_clears_the_previous_error(db, pile):
    set_pile_status(db, pile, "failed", "RuntimeError")
    begin_pile_processing(db, pile, "chromadb")
    row = get_pile(db, pile, OWNER)
    assert row["status"] == "processing"
    assert row["error"] == ""


# ─── property 3: vs_type is recorded before the work starts ───────────────────


def test_begin_processing_records_the_store_backend(db, pile):
    """Deletion needs the backend even if the run never finishes."""
    assert get_pile(db, pile, OWNER)["vs_type"] == ""
    begin_pile_processing(db, pile, "pgvector")
    row = get_pile(db, pile, OWNER)
    assert row["vs_type"] == "pgvector"
    assert row["status"] == "processing"


def test_a_crashed_run_still_leaves_the_backend_known(db, pile):
    begin_pile_processing(db, pile, "pgvector")
    set_pile_status(db, pile, "failed", "OSError")
    assert get_pile(db, pile, OWNER)["vs_type"] == "pgvector"


def test_finish_records_counts_and_the_embedding_pair(db, pile):
    begin_pile_processing(db, pile, "chromadb")
    finish_pile_processing(db, pile, chunk_count=120, file_count=3, embedding_key="local/nomic-ai/nomic-embed-text-v1")
    row = get_pile(db, pile, OWNER)
    assert (row["status"], row["chunk_count"], row["file_count"]) == ("ready", 120, 3)
    assert row["embedding_key"] == "local/nomic-ai/nomic-embed-text-v1"
    assert row["processed_at"]


# ─── quota accounting ─────────────────────────────────────────────────────────


def test_usage_sums_bytes_across_an_owners_piles(db, pile):
    create_pile(db, "p2", OWNER, "More docs")
    add_pile_file(db, pile, "a.pdf", "a.pdf", 1_000)
    add_pile_file(db, "p2", "b.pdf", "b.pdf", 2_500)
    assert pile_usage(db, OWNER) == (2, 3_500)


def test_usage_ignores_other_owners(db, pile):
    create_pile(db, "p2", OTHER, "Theirs")
    add_pile_file(db, "p2", "big.pdf", "big.pdf", 10**9)
    add_pile_file(db, pile, "a.pdf", "a.pdf", 10)
    assert pile_usage(db, OWNER) == (1, 10)


def test_replacing_a_file_does_not_double_count(db, pile):
    add_pile_file(db, pile, "a.pdf", "a.pdf", 1_000)
    add_pile_file(db, pile, "a.pdf", "a.pdf", 2_000)
    assert pile_usage(db, OWNER) == (1, 2_000)


# ─── upload filenames ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "original, expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),  # directory parts stripped
        ("/absolute/path/doc.pdf", "doc.pdf"),
        ("Angebot März 2026.pdf", "Angebot_M_rz_2026.pdf"),
        ("..pdf", "upload.pdf"),  # nothing usable left in the stem
    ],
)
def test_filenames_are_made_safe_without_losing_the_extension(original, expected):
    """The extension has to survive: _collect_candidate_files selects by suffix, so
    a mangled one drops the file from every future ingestion run."""
    assert _safe_filename(original, set()) == expected


def test_a_long_name_keeps_its_extension():
    name = "a" * 400 + ".pdf"
    out = _safe_filename(name, set())
    assert out.endswith(".pdf")
    assert len(out) <= 140


def test_colliding_names_are_suffixed_not_overwritten():
    taken = {"report.pdf", "report_2.pdf"}
    assert _safe_filename("report.pdf", taken) == "report_3.pdf"


def test_traversal_cannot_escape_the_pile_directory(tmp_path):
    """What the sanitiser is actually defending: the resolved path stays inside."""
    pile_dir = tmp_path / "piles" / "p1"
    pile_dir.mkdir(parents=True)
    safe = _safe_filename("../../../../etc/shadow.pdf", set())
    assert (pile_dir / safe).resolve().parent == pile_dir.resolve()


# ─── embedding key ────────────────────────────────────────────────────────────


def test_embedding_key_round_trips_a_model_name_containing_slashes():
    """The key is "<backend>/<model>" and model names carry slashes of their own,
    so consumers must split once from the left."""

    class _KB:
        embedding_backend = "local"
        embedding_model = "nomic-ai/nomic-embed-text-v1"

    key = embedding_key(_KB())
    assert key == "local/nomic-ai/nomic-embed-text-v1"
    backend, _, model = key.partition("/")
    assert (backend, model) == ("local", "nomic-ai/nomic-embed-text-v1")
