"""Intent: a failing ingestion must not escape the reindex background task.

`POST /reindex` hands `rebuild_callback` to Starlette as a BackgroundTask. An
exception that escapes that task is not returned to any caller — uvicorn logs it
as "Exception in ASGI application" and the reindex silently reports nothing.

The handler used to catch only `RuntimeError`. An unreachable Postgres surfaces
as `ConnectionRefusedError` (an `OSError`), so pointing a pgvector KB at a dead
host produced a full ASGI traceback per attempt instead of a handled failure.
These tests pin the exception classes that must stay contained.
"""

import pytest
from fastapi.testclient import TestClient

import lancy.main as main

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
        ConnectionRefusedError(111, "Connect call failed ('192.168.1.202', 5432)"),
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
