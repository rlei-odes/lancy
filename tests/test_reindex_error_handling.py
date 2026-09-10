"""Intent: a failing ingestion is contained, and says so in /reindex-status.

`POST /reindex` hands `rebuild_callback` to Starlette as a BackgroundTask. An
exception that escapes that task is not returned to any caller — uvicorn logs it
as "Exception in ASGI application" and the reindex silently reports nothing.

The handler used to catch only `RuntimeError`. An unreachable Postgres surfaces
as `ConnectionRefusedError` (an `OSError`), so pointing a pgvector KB at a dead
host produced a full ASGI traceback per attempt instead of a handled failure.
These tests pin the exception classes that must stay contained.

Containment alone still left the failure invisible: a failed run reports the same
zero counts as one that legitimately had nothing to index, and the frontend detects
completion by watching `finished_at` change — which the early return never advanced.
So `run_ingestion` also records how the run ended, and the second half of this file
pins that contract: `outcome` of ok/cancelled/failed, `error` carrying the exception
*class name only* (the status endpoint is readable by any logged-in session and
exception text can embed the connection string), and `finished_at` advancing so the
UI re-renders at all.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import lancy.ingestion as ingestion
import lancy.main as main
from lancy.ingestion import _IndexingCancelled, run_ingestion
from lancy.kb_router import KBInfo

REINDEX_URL = "/api/v1/rag/reindex"


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions=True (the default) re-raises anything escaping a
    # background task, which is exactly the regression under test.
    return TestClient(main.app)


@pytest.fixture
def idle(monkeypatch):
    """Force the 'not currently indexing' branch so /reindex reaches the task."""
    monkeypatch.setitem(main._index_status, "indexing", False)


@pytest.mark.parametrize(
    "exc",
    [
        # asyncpg on a host with nothing listening on 5432
        ConnectionRefusedError(111, "Connect call failed ('10.0.0.1', 5432)"),
        # host unreachable / DNS gone — the other shapes of a dead DB host
        OSError(113, "No route to host"),
        TimeoutError("connection timed out"),
        # the class the handler already caught — must keep working
        RuntimeError("embedding dimension mismatch"),
        # anything unforeseen still must not take the task down
        ValueError("unexpected"),
    ],
    ids=["refused", "unreachable", "timeout", "runtime", "unforeseen"],
)
def test_ingestion_failure_is_contained(client, idle, monkeypatch, exc):
    async def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(main, "run_ingestion", _boom)

    # Before the fix this raised `exc` out of the TestClient for every case
    # except RuntimeError.
    resp = client.post(REINDEX_URL, json={"reset": False})

    assert resp.status_code == 200
    assert resp.json() == {"started": True}


def test_reindex_reports_started_before_the_task_runs(client, idle, monkeypatch):
    """The endpoint is fire-and-forget: failure must not change its response."""
    calls: list[bool] = []

    async def _boom(*args, **kwargs):
        calls.append(True)
        raise ConnectionRefusedError(111, "Connect call failed")

    monkeypatch.setattr(main, "run_ingestion", _boom)

    resp = client.post(REINDEX_URL, json={"reset": True})

    assert resp.status_code == 200
    # TestClient runs background tasks before returning, so the task really ran
    # and really raised — the assertion above proves it was swallowed, not skipped.
    assert calls == [True]


# ─── the status contract: how the last run ended ──────────────────────────────


@pytest.fixture
def kb(tmp_path) -> KBInfo:
    """A KB whose data dir is empty — the run fails before it reads any files."""
    return KBInfo(name="test", id="test", vs_path=str(tmp_path / "vs"), data_dirs=[str(tmp_path)])


@pytest.fixture
def stale_status(monkeypatch):
    """Leave a previous run's outcome behind, so the reset is what clears it."""
    monkeypatch.setitem(ingestion._index_status, "outcome", "ok")
    monkeypatch.setitem(ingestion._index_status, "error", "")
    monkeypatch.setitem(ingestion._index_status, "finished_at", "1999-01-01T00:00:00+00:00")


def _raise_from_vector_store(monkeypatch, exc: BaseException) -> None:
    """Fail at `make_vector_store`, inside run_ingestion's try and before any
    embedding model is loaded — the same place an unreachable Postgres fails."""

    def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(ingestion, "make_vector_store", _boom)


def _run(kb) -> tuple:
    # pytest-asyncio is not a dependency; the rest of the suite is sync too.
    return asyncio.run(run_ingestion(kb, reset=False, db_dir=None, cfg=None))


def test_failure_records_outcome_error_and_finished_at(kb, stale_status, monkeypatch):
    _raise_from_vector_store(
        monkeypatch, ConnectionRefusedError(111, "Connect call failed ('10.0.0.1', 5432)")
    )

    with pytest.raises(ConnectionRefusedError):
        _run(kb)

    s = ingestion._index_status
    assert s["outcome"] == "failed"
    # Class name only. The message names the host and port, and for a SQLAlchemy
    # error would carry the connection URL.
    assert s["error"] == "ConnectionRefusedError"
    assert "10.0.0.1" not in s["error"]
    # Must advance past the stale value or the frontend never notices the run ended.
    assert s["finished_at"] != "1999-01-01T00:00:00+00:00"
    assert s["finished_at"]
    # No result to report — the panel must fall back to the outcome, not show "0 chunks".
    assert s["last_result"] is None
    assert s["indexing"] is False


def test_cancellation_is_not_reported_as_a_successful_empty_run(kb, stale_status, monkeypatch):
    """Cancellation returns zero counts like a no-op run; only `outcome` separates them."""
    _raise_from_vector_store(monkeypatch, _IndexingCancelled())

    result = _run(kb)

    assert result == (0, 0, 0, 0)
    assert ingestion._index_status["outcome"] == "cancelled"
    assert ingestion._index_status["error"] == ""
    assert ingestion._index_status["indexing"] is False


def test_a_new_run_clears_the_previous_outcome(kb, monkeypatch):
    """A stale 'failed' must not label the next run's result."""
    monkeypatch.setitem(ingestion._index_status, "outcome", "failed")
    monkeypatch.setitem(ingestion._index_status, "error", "ConnectionRefusedError")
    _raise_from_vector_store(monkeypatch, ValueError("something else"))

    with pytest.raises(ValueError):
        _run(kb)

    # Overwritten by this run rather than carried over from the last one.
    assert ingestion._index_status["error"] == "ValueError"
