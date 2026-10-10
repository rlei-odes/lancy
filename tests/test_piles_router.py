"""Intent: the pile API never serves one session's documents to another.

`create_piles_router` scopes every endpoint to the `x-session-id` header the Next.js
middleware injects. These tests drive the HTTP surface rather than the database so
they cover the part that is actually reachable: the header handling, the ownership
checks, the quota rejections and the upload path that writes to disk.

The contract pinned down here:

  - another session's pile id is a 404, on every endpoint, so a response can never
    confirm that someone else's pile exists
  - a pile's synthetic KBInfo inherits the active KB's embedding, store and chunking
    config, which is what keeps it compatible with the one embedding model the app runs
  - uploads land under the pile's own directory with a sanitised name
  - processing refuses to start while an admin reindex holds the embedding model
  - a failed run records the exception *class*, never its message, which can carry a
    connection string or a local path
"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lancy.database import finish_pile_processing, get_pile
from lancy.kb_router import KBInfo
from lancy.piles_router import MAX_PILES_PER_OWNER, create_piles_router

ME = {"x-session-id": "session-aaa"}
THEM = {"x-session-id": "session-bbb"}

ACTIVE_KB = KBInfo(
    id="default",
    name="Standard",
    vs_path="/tmp/vs_default",
    embedding_backend="local",
    embedding_model="nomic-ai/nomic-embed-text-v1",
    max_file_size_mb=5,
    max_chunk_tokens=512,
    pdf_ocr_enabled=True,
    vs_type="chromadb",
)


@pytest.fixture
def ctx(tmp_path):
    """Router plus the mutable state the tests assert against."""
    state = {"indexing": False, "processed": [], "result": (7, 1), "raise": None}

    async def process(kb: KBInfo, reset: bool):
        state["processed"].append((kb, reset))
        if state["raise"] is not None:
            raise state["raise"]
        return state["result"]

    app = FastAPI()
    app.include_router(
        create_piles_router(
            db_dir=tmp_path / "db",
            piles_dir=tmp_path / "user-piles",
            get_active_kb=lambda: ACTIVE_KB,
            process_callback=process,
            status_factory=lambda: {"indexing": state["indexing"]},
        )
    )
    state["client"] = TestClient(app)
    state["db"] = tmp_path / "db" / "user_config.db"
    state["piles_dir"] = tmp_path / "user-piles"
    return state


def _create(client, name="Tender docs", headers=ME) -> str:
    r = client.post("/api/v1/piles", json={"name": name}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _upload(client, pile_id, name="report.pdf", content=b"x" * 64, headers=ME):
    return client.post(
        f"/api/v1/piles/{pile_id}/files",
        files={"file": (name, content, "application/pdf")},
        headers=headers,
    )


# ─── ownership ────────────────────────────────────────────────────────────────


def test_a_pile_is_only_visible_to_its_owner(ctx):
    pile_id = _create(ctx["client"])
    assert [p["id"] for p in ctx["client"].get("/api/v1/piles", headers=ME).json()] == [pile_id]
    assert ctx["client"].get("/api/v1/piles", headers=THEM).json() == []


@pytest.mark.parametrize(
    "method, suffix",
    [
        ("get", ""),
        ("delete", ""),
        ("post", "/process"),
        ("delete", "/files/report.pdf"),
    ],
)
def test_another_session_gets_404_on_every_endpoint(ctx, method, suffix):
    """A 403 would confirm the pile exists. Every path must answer 404 instead."""
    pile_id = _create(ctx["client"])
    r = getattr(ctx["client"], method)(f"/api/v1/piles/{pile_id}{suffix}", headers=THEM)
    assert r.status_code == 404


def test_another_session_cannot_upload_into_the_pile(ctx):
    pile_id = _create(ctx["client"])
    assert _upload(ctx["client"], pile_id, headers=THEM).status_code == 404


def test_a_missing_pile_and_someone_elses_are_indistinguishable(ctx):
    pile_id = _create(ctx["client"])
    theirs = ctx["client"].get(f"/api/v1/piles/{pile_id}", headers=THEM)
    absent = ctx["client"].get("/api/v1/piles/does-not-exist", headers=THEM)
    assert theirs.status_code == absent.status_code == 404
    assert theirs.json() == absent.json()


def test_two_sessions_may_each_own_a_pile_of_the_same_name(ctx):
    _create(ctx["client"], "Shared name", headers=ME)
    r = ctx["client"].post("/api/v1/piles", json={"name": "Shared name"}, headers=THEM)
    assert r.status_code == 200


def test_one_session_cannot_reuse_a_name(ctx):
    _create(ctx["client"], "Tender docs")
    r = ctx["client"].post("/api/v1/piles", json={"name": "Tender docs"}, headers=ME)
    assert r.status_code == 409


# ─── uploads ──────────────────────────────────────────────────────────────────


def test_upload_lands_in_the_piles_own_directory(ctx):
    pile_id = _create(ctx["client"])
    r = _upload(ctx["client"], pile_id, "Angebot März.pdf")
    assert r.status_code == 200, r.text
    stored = r.json()["filename"]
    assert r.json()["source_file"] == "Angebot März.pdf"
    assert (ctx["piles_dir"] / pile_id / stored).read_bytes() == b"x" * 64


def test_upload_rejects_an_unsupported_extension(ctx):
    pile_id = _create(ctx["client"])
    assert _upload(ctx["client"], pile_id, "payload.exe").status_code == 422


def test_upload_honours_the_active_kbs_size_limit(ctx):
    """The limit is the active KB's max_file_size_mb, not a separate pile setting."""
    pile_id = _create(ctx["client"])
    too_big = b"x" * (6 * 1_048_576)  # ACTIVE_KB allows 5 MB
    assert _upload(ctx["client"], pile_id, "big.pdf", too_big).status_code == 413


def test_a_traversal_filename_cannot_escape_the_pile_directory(ctx):
    pile_id = _create(ctx["client"])
    r = _upload(ctx["client"], pile_id, "../../../../etc/shadow.pdf")
    assert r.status_code == 200
    written = list((ctx["piles_dir"] / pile_id).iterdir())
    assert [p.name for p in written] == ["shadow.pdf"]


def test_deleting_a_file_removes_it_from_disk(ctx):
    pile_id = _create(ctx["client"])
    stored = _upload(ctx["client"], pile_id).json()["filename"]
    r = ctx["client"].delete(f"/api/v1/piles/{pile_id}/files/{stored}", headers=ME)
    assert r.status_code == 200
    assert not (ctx["piles_dir"] / pile_id / stored).exists()


def test_a_rejected_upload_leaves_nothing_behind(ctx):
    """The upload streams to a scratch name and is renamed only on success, so a file
    the next ingestion run would pick up never appears for a rejected upload."""
    pile_id = _create(ctx["client"])
    assert _upload(ctx["client"], pile_id, "big.pdf", b"x" * (6 * 1_048_576)).status_code == 413
    assert list((ctx["piles_dir"] / pile_id).iterdir()) == []
    assert ctx["client"].get(f"/api/v1/piles/{pile_id}", headers=ME).json()["files"] == []


def test_the_recorded_size_is_what_was_actually_written(ctx):
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id, "doc.pdf", b"x" * 4096)
    files = ctx["client"].get(f"/api/v1/piles/{pile_id}", headers=ME).json()["files"]
    assert files[0]["size_bytes"] == 4096


def test_the_pile_limit_is_enforced(ctx):
    for n in range(MAX_PILES_PER_OWNER):
        _create(ctx["client"], f"pile {n}")
    r = ctx["client"].post("/api/v1/piles", json={"name": "one too many"}, headers=ME)
    assert r.status_code == 409


# ─── processing ───────────────────────────────────────────────────────────────


def test_processing_inherits_the_active_kbs_ingestion_config(ctx):
    """A pile must be built with the embedding model the rest of the app runs, or
    its vectors are not comparable to anything."""
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    assert ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME).status_code == 200

    kb, _reset = ctx["processed"][0]
    assert kb.embedding_backend == ACTIVE_KB.embedding_backend
    assert kb.embedding_model == ACTIVE_KB.embedding_model
    assert kb.vs_type == ACTIVE_KB.vs_type
    assert kb.max_chunk_tokens == ACTIVE_KB.max_chunk_tokens
    assert kb.pdf_ocr_enabled == ACTIVE_KB.pdf_ocr_enabled
    # Its data dir is the pile's own upload directory, and its id namespaces the store.
    assert kb.data_dirs == [str(ctx["piles_dir"] / pile_id)]
    assert kb.id == f"pile_{pile_id}"


def test_processing_does_not_leak_the_pile_name_into_the_shared_status(ctx):
    """kb_name shows in the index-status badge, which every session can see."""
    pile_id = _create(ctx["client"], "Secret project Q3")
    _upload(ctx["client"], pile_id)
    ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME)
    kb, _ = ctx["processed"][0]
    assert "Secret project Q3" not in kb.name


def test_processing_marks_the_pile_ready_with_the_stores_counts(ctx):
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    ctx["result"] = (120, 3)
    ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME)

    row = get_pile(ctx["db"], pile_id, "session-aaa")
    assert (row["status"], row["chunk_count"], row["file_count"]) == ("ready", 120, 3)
    assert row["embedding_key"] == "local/nomic-ai/nomic-embed-text-v1"

    body = ctx["client"].get(f"/api/v1/piles/{pile_id}", headers=ME).json()
    assert body["has_store"] is True
    assert body["embedding_current"] is True


def test_an_empty_pile_cannot_be_processed(ctx):
    pile_id = _create(ctx["client"])
    assert ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME).status_code == 400


def test_processing_waits_for_a_running_reindex(ctx):
    """One embedding model and one chunking pool — the two runs must not overlap."""
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    ctx["indexing"] = True
    assert ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME).status_code == 409
    assert ctx["processed"] == []


def test_a_failed_run_records_the_exception_class_not_its_message(ctx):
    """The message can carry a connection string or a local path, and this value is
    rendered in the UI."""
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    ctx["raise"] = RuntimeError("postgresql://user:hunter2@primeb/db is unreachable")
    ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME)

    row = get_pile(ctx["db"], pile_id, "session-aaa")
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError"
    assert "hunter2" not in json.dumps(row)


def test_a_reprocess_after_an_embedding_change_resets_the_store(ctx):
    """Vectors from a different model are not comparable and may differ in dimension,
    so the store is rebuilt rather than extended."""
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    finish_pile_processing(ctx["db"], pile_id, chunk_count=5, file_count=1, embedding_key="local/some-old-model")
    r = ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME)
    assert r.json()["reset"] is True
    assert ctx["processed"][-1][1] is True


def test_a_reprocess_on_the_same_embedding_extends_the_store(ctx):
    """Incremental: run_ingestion's hash dedup skips files already embedded."""
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    finish_pile_processing(
        ctx["db"],
        pile_id,
        chunk_count=5,
        file_count=1,
        embedding_key="local/nomic-ai/nomic-embed-text-v1",
    )
    r = ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME)
    assert r.json()["reset"] is False
    assert ctx["processed"][-1][1] is False


def test_a_stale_embedding_still_counts_as_having_a_store(ctx):
    """Batch analysis reads chunk text and never embeds, so a pile built with a
    since-replaced model remains analyzable while not being retrieval-ready."""
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    finish_pile_processing(ctx["db"], pile_id, chunk_count=5, file_count=1, embedding_key="local/some-old-model")
    body = ctx["client"].get(f"/api/v1/piles/{pile_id}", headers=ME).json()
    assert body["has_store"] is True
    assert body["embedding_current"] is False


# ─── deletion ─────────────────────────────────────────────────────────────────


def test_deleting_a_pile_removes_its_uploads_and_store_artifacts(ctx):
    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id)
    stats = ctx["db"].parent / f"kb_stats_pile_{pile_id}.json"
    stats.write_text("{}")

    assert ctx["client"].delete(f"/api/v1/piles/{pile_id}", headers=ME).status_code == 200
    assert not (ctx["piles_dir"] / pile_id).exists()
    assert not stats.exists()
    assert ctx["client"].get(f"/api/v1/piles/{pile_id}", headers=ME).status_code == 404


def test_mode_1_deployments_without_a_session_cookie_still_work(ctx):
    """No APP_PASSWORD means the middleware injects no x-session-id. Piles then scope
    to "admin", the same identity conversation history already falls back to."""
    r = ctx["client"].post("/api/v1/piles", json={"name": "No cookie"})
    assert r.status_code == 200
    assert [p["name"] for p in ctx["client"].get("/api/v1/piles").json()] == ["No cookie"]
    # And that pile is not visible to a session that does carry an id.
    assert ctx["client"].get("/api/v1/piles", headers=ME).json() == []


# ─── the "a pile is structurally a KB" assumption ─────────────────────────────


def test_the_ingestion_prepass_discovers_a_piles_uploads(ctx):
    """The seam the whole design rests on: a pile's synthetic KBInfo must be something
    the normal ingestion pipeline can actually walk. If `_collect_candidate_files`
    does not find the uploads, processing silently indexes nothing.

    Checked without an embedding model, so this runs anywhere.
    """
    from lancy.feature0_baseline_rag import _collect_candidate_files

    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id, "contract.pdf")
    _upload(ctx["client"], pile_id, "notes.md", b"# notes\n")
    _upload(ctx["client"], pile_id, "sheet.xlsx", b"fake xlsx bytes")
    ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME)

    kb, _reset = ctx["processed"][0]
    found = _collect_candidate_files([Path(d) for d in kb.data_dirs], max_file_size_mb=kb.max_file_size_mb)
    assert sorted(f.name for f in found) == ["contract.pdf", "notes.md", "sheet.xlsx"]


def test_the_prepass_applies_the_inherited_size_limit(ctx):
    """max_file_size_mb comes from the active KB, and the pre-pass enforces it — so a
    file that slipped past the upload check would still be skipped, not half-indexed."""
    from lancy.feature0_baseline_rag import _collect_candidate_files

    pile_id = _create(ctx["client"])
    _upload(ctx["client"], pile_id, "small.pdf", b"x" * 1024)
    ctx["client"].post(f"/api/v1/piles/{pile_id}/process", headers=ME)
    kb, _ = ctx["processed"][0]

    # Write a file past the limit directly, bypassing the endpoint's own check.
    (ctx["piles_dir"] / pile_id / "huge.pdf").write_bytes(b"x" * (6 * 1_048_576))
    found = _collect_candidate_files([Path(d) for d in kb.data_dirs], max_file_size_mb=kb.max_file_size_mb)
    assert [f.name for f in found] == ["small.pdf"]
