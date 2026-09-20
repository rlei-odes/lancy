"""Intent: `registry.active` only ever names a KB the pool actually loaded.

`activate_kb()` writes `reg.active = kb_id` and `_save(reg)` *before* awaiting
`activate_callback`. When the callback fails the endpoint reports the failure,
but knowledge_bases.json has already been rewritten and now names a KB that was
never loaded.

That file is not inert: `_active_kb_id()` in main.py re-reads `active` from disk
on every query, so a failed activation immediately re-points every conversation
without its own kb_id at a KB the pool does not hold — which is how the wrong-KB
answers in test_kb_dispatch_fallback.py get reached in production.

A restart does clear it (the pool is empty at boot, so nothing can conflict),
but every query until then is served from the wrong place.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lancy.kb_pool import EmbeddingConflict
from lancy.kb_router import KBInfo, create_kb_router


def kb(kb_id: str, **overrides) -> dict:
    fields = {"id": kb_id, "vs_path": f"/tmp/vs_{kb_id}", "name": kb_id.upper()}
    fields.update(overrides)
    return json.loads(KBInfo(**fields).model_dump_json())


def build(tmp_path, activate):
    """Router over a two-KB registry with `kb-a` active."""
    registry = {"active": "kb-a", "bases": {"kb-a": kb("kb-a"), "kb-b": kb("kb-b")}}
    registry_path = tmp_path / "knowledge_bases.json"
    registry_path.write_text(json.dumps(registry))

    app = FastAPI()
    app.include_router(create_kb_router(db_dir=tmp_path, activate_callback=activate))
    # The generic-failure case must reach us as a response, not a raised exception.
    return TestClient(app, raise_server_exceptions=False), registry_path


def active(registry_path) -> str:
    return json.loads(registry_path.read_text())["active"]


# ─── a failed activation must not be recorded ─────────────────────────────────


def test_an_embedding_conflict_does_not_move_the_active_kb(tmp_path):
    async def activate(kb_info, reset=False):
        raise EmbeddingConflict("kb-b", ("local", "model-a"), ("local", "model-b"))

    api, registry_path = build(tmp_path, activate)

    response = api.post("/api/v1/kb/kb-b/activate")

    assert response.status_code == 409
    assert active(registry_path) == "kb-a"


def test_any_activation_failure_leaves_the_active_kb_alone(tmp_path):
    """Only EmbeddingConflict is handled; an unreachable store must not persist either."""
    async def activate(kb_info, reset=False):
        raise ConnectionRefusedError("vector store unreachable")

    api, registry_path = build(tmp_path, activate)

    api.post("/api/v1/kb/kb-b/activate")

    assert active(registry_path) == "kb-a"


def test_the_failed_kb_is_not_named_anywhere_as_active(tmp_path):
    """Guards the whole record, not just the one key we happen to check."""
    async def activate(kb_info, reset=False):
        raise EmbeddingConflict("kb-b", ("local", "model-a"), ("local", "model-b"))

    api, registry_path = build(tmp_path, activate)

    api.post("/api/v1/kb/kb-b/activate")

    stored = json.loads(registry_path.read_text())
    assert stored["active"] == "kb-a"
    assert set(stored["bases"]) == {"kb-a", "kb-b"}  # nothing else was disturbed


# ─── a successful activation still is ─────────────────────────────────────────


def test_a_successful_activation_is_persisted(tmp_path):
    calls = []

    async def activate(kb_info, reset=False):
        calls.append(kb_info.id)

    api, registry_path = build(tmp_path, activate)

    response = api.post("/api/v1/kb/kb-b/activate")

    assert response.status_code == 200
    assert calls == ["kb-b"]
    assert active(registry_path) == "kb-b"


def test_the_callback_runs_before_the_registry_is_written(tmp_path):
    """The ordering itself: the callback must not observe the new value early.

    Without this, moving the write could be 'fixed' by reverting on failure,
    which still leaves a window where a concurrent query reads the wrong KB.
    """
    seen = []

    async def activate(kb_info, reset=False):
        seen.append(active(registry_path))

    api, registry_path = build(tmp_path, activate)

    api.post("/api/v1/kb/kb-b/activate")

    assert seen == ["kb-a"]


@pytest.mark.parametrize("reset", [True, False], ids=["reset", "no-reset"])
def test_the_reset_flag_reaches_the_callback_unchanged(tmp_path, reset):
    """Reordering the save must not disturb how the callback is invoked."""
    seen = []

    async def activate(kb_info, reset=False):
        seen.append(reset)

    api, _ = build(tmp_path, activate)

    api.post(f"/api/v1/kb/kb-b/activate?reset={str(reset).lower()}")

    assert seen == [reset]
