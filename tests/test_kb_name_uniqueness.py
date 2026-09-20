"""Intent: two Knowledge Bases can never carry the same name.

`create_kb` slugifies the name into an id and `_slug` disambiguates a collision
by appending `-2`, `-3`, ... So a second KB named "KB One" is accepted, lands at
id `kb-one-2` with its own `vs_` store, and is indistinguishable from the first
in the KB dropdown — which renders `name`, not `id`. Nothing is overwritten and
nothing is lost; the user simply owns two identical-looking KBs and no way to
tell which one a query answered from.

The contract these tests pin down:

  - a colliding name is rejected with 409, on create and on rename alike
  - a rejected request leaves the registry byte-for-byte untouched
  - collision is judged on the slug, because that is what `_slug` collapses:
    "KB One", "kb one" and "kb-one" are the same name for this purpose
  - a KB keeping its own name on save is not a collision — the edit form PUTs
    the whole record back unchanged, so treating that as one breaks every save
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lancy.kb_router import KBInfo, create_kb_router

EXISTING = "KB One"


def kb(**overrides) -> dict:
    fields = {"id": "kb-one", "vs_path": "/tmp/vs_kb-one", "name": EXISTING}
    fields.update(overrides)
    return json.loads(KBInfo(**fields).model_dump_json())


@pytest.fixture
def client(tmp_path):
    async def activate(kb_info, reset=False):
        return None

    registry = {"active": "kb-one", "bases": {"kb-one": kb()}}
    registry_path = tmp_path / "knowledge_bases.json"
    registry_path.write_text(json.dumps(registry))

    app = FastAPI()
    app.include_router(create_kb_router(db_dir=tmp_path, activate_callback=activate))
    return TestClient(app), registry_path


def bases(registry_path) -> dict:
    return json.loads(registry_path.read_text())["bases"]


# ─── create ───────────────────────────────────────────────────────────────────


def test_creating_a_kb_with_an_existing_name_is_rejected(client):
    api, _ = client

    response = api.post("/api/v1/kb", json={"name": EXISTING, "data_dirs": ["data/"]})

    assert response.status_code == 409


def test_a_rejected_create_does_not_reach_the_registry(client):
    """The 409 is worthless if the duplicate is written anyway."""
    api, registry_path = client

    api.post("/api/v1/kb", json={"name": EXISTING, "data_dirs": ["data/"]})

    assert list(bases(registry_path)) == ["kb-one"]


@pytest.mark.parametrize(
    "name",
    ["kb one", "KB ONE", "kb-one", "  KB One  "],
    ids=["lowercase", "uppercase", "hyphenated", "padded"],
)
def test_a_name_that_slugifies_onto_an_existing_id_is_rejected(client, name):
    """These all collapse to `kb-one` — the very cases `_slug` disambiguated."""
    api, _ = client

    response = api.post("/api/v1/kb", json={"name": name, "data_dirs": ["data/"]})

    assert response.status_code == 409


def test_no_suffixed_id_is_ever_created_through_the_api(client):
    """`_slug`'s `-2` fallback is the bug's signature; it must stop appearing."""
    api, registry_path = client

    api.post("/api/v1/kb", json={"name": EXISTING, "data_dirs": ["data/"]})

    assert not any(kb_id.startswith("kb-one-") for kb_id in bases(registry_path))


def test_a_distinct_name_is_still_created(client):
    api, registry_path = client

    response = api.post("/api/v1/kb", json={"name": "KB Two", "data_dirs": ["data/"]})

    assert response.status_code == 200
    assert response.json()["id"] == "kb-two"
    assert sorted(bases(registry_path)) == ["kb-one", "kb-two"]


# ─── rename ───────────────────────────────────────────────────────────────────


def test_renaming_a_kb_onto_another_kbs_name_is_rejected(client):
    """Blocking create alone still leaves a rename to reach the same state."""
    api, _ = client
    api.post("/api/v1/kb", json={"name": "KB Two", "data_dirs": ["data/"]})

    response = api.put("/api/v1/kb/kb-two", json={"name": EXISTING, "data_dirs": ["data/"]})

    assert response.status_code == 409


def test_a_rejected_rename_leaves_the_stored_name_alone(client):
    api, registry_path = client
    api.post("/api/v1/kb", json={"name": "KB Two", "data_dirs": ["data/"]})

    api.put("/api/v1/kb/kb-two", json={"name": EXISTING, "data_dirs": ["data/"]})

    assert bases(registry_path)["kb-two"]["name"] == "KB Two"


def test_saving_a_kb_under_its_own_name_still_works(client):
    """The edit form PUTs the whole record back — this must not become a 409."""
    api, _ = client
    fetched = api.get("/api/v1/kb").json()["bases"]["kb-one"]

    response = api.put("/api/v1/kb/kb-one", json=fetched)

    assert response.status_code == 200
    assert response.json()["name"] == EXISTING


def test_renaming_a_kb_to_a_free_name_still_works(client):
    api, registry_path = client

    response = api.put("/api/v1/kb/kb-one", json={"name": "KB Renamed", "data_dirs": ["data/"]})

    assert response.status_code == 200
    assert bases(registry_path)["kb-one"]["name"] == "KB Renamed"
