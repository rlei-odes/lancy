"""Intent: KB secrets never leave the API, and a masked value never overwrites one.

`KBInfo` is both the on-disk registry record and the response model of every KB
endpoint, so `GET /api/v1/kb` returned the pgvector password and the embedding
API key in clear text to any caller. `_redact` masks them on the way out.

Masking alone would be worse than the leak: the config panel loads whatever GET
returned into a form and PUTs the whole object back, so an unchanged save would
write the mask over the stored credential and lock the KB out of its database.
`_unmask` treats an echoed-back mask as "unchanged" and restores the real value.

Every value below is a placeholder. Never put a real host, user or password in a
test: it is committed permanently and defeats the point of the fix.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lancy.kb_router import (
    _API_KEY_MASK,
    KBCreate,
    KBInfo,
    KBRegistry,
    _redact,
    _unmask,
    create_kb_router,
)

PASSWORD = "pw-placeholder-do-not-use"
API_KEY = "sk-placeholder-do-not-use"
USER = "dbuser"
HOST = "dbhost"
DB = "dbname"
CONN = f"postgresql://{USER}:{PASSWORD}@{HOST}:5432/{DB}"


def kb(**overrides):
    """A pgvector KB carrying both secrets, unless overridden."""
    fields = {
        "id": "kb1",
        "vs_path": "/tmp/vs_kb1",
        "name": "KB One",
        "vs_type": "pgvector",
        "vs_connection_string": CONN,
        "embedding_custom_api_key": API_KEY,
    }
    fields.update(overrides)
    return KBInfo(**fields)


def echo(redacted: KBInfo) -> KBCreate:
    """What the edit form PUTs back when the user changed nothing."""
    return KBCreate(
        name=redacted.name,
        vs_type=redacted.vs_type,
        vs_connection_string=redacted.vs_connection_string,
        embedding_custom_api_key=redacted.embedding_custom_api_key,
    )


# ─── the secrets do not leave ─────────────────────────────────────────────────


def test_the_password_is_not_in_the_response():
    out = _redact(kb())

    assert PASSWORD not in out.vs_connection_string
    # Any leading run of the password is a leak, not just the whole string.
    for n in range(4, len(PASSWORD) + 1):
        assert PASSWORD[:n] not in out.vs_connection_string


def test_the_embedding_api_key_is_not_in_the_response():
    out = _redact(kb())

    assert API_KEY not in out.embedding_custom_api_key
    assert out.embedding_custom_api_key == _API_KEY_MASK


def test_no_secret_survives_anywhere_in_the_serialised_record():
    """Guards against a future field carrying the secret past the two we mask."""
    dumped = _redact(kb()).model_dump_json()

    assert PASSWORD not in dumped
    assert API_KEY not in dumped


def test_every_kb_in_the_registry_is_redacted():
    reg = KBRegistry(active="kb1", bases={"kb1": kb(), "kb2": kb(id="kb2", name="KB Two")})

    out = KBRegistry(
        active=reg.active,
        bases={kb_id: _redact(k) for kb_id, k in reg.bases.items()},
    )

    assert PASSWORD not in out.model_dump_json()


def test_what_is_kept_is_still_useful():
    """The panel has to stay diagnosable — only the password is secret."""
    out = _redact(kb())

    assert HOST in out.vs_connection_string
    assert "5432" in out.vs_connection_string
    assert DB in out.vs_connection_string
    assert USER in out.vs_connection_string  # username is not a secret


# ─── a masked value never overwrites a stored one ─────────────────────────────


def test_an_unchanged_save_keeps_the_real_connection_string():
    """The regression that would lock the KB out of its own database."""
    stored = kb()

    result = _unmask(echo(_redact(stored)), stored)

    assert result.vs_connection_string == CONN


def test_an_unchanged_save_keeps_the_real_api_key():
    stored = kb()

    result = _unmask(echo(_redact(stored)), stored)

    assert result.embedding_custom_api_key == API_KEY


def test_a_changed_connection_string_is_written_through():
    stored = kb()
    new = f"postgresql://other:{PASSWORD}@otherhost:5432/otherdb"

    result = _unmask(echo(_redact(stored)).model_copy(update={"vs_connection_string": new}), stored)

    assert result.vs_connection_string == new


def test_a_changed_api_key_is_written_through():
    stored = kb()

    result = _unmask(
        echo(_redact(stored)).model_copy(update={"embedding_custom_api_key": "sk-rotated"}),
        stored,
    )

    assert result.embedding_custom_api_key == "sk-rotated"


def test_clearing_a_secret_still_clears_it():
    """An empty field is a deliberate edit, not an echoed mask."""
    stored = kb()

    result = _unmask(
        echo(_redact(stored)).model_copy(
            update={"vs_connection_string": "", "embedding_custom_api_key": ""}
        ),
        stored,
    )

    assert result.vs_connection_string == ""
    assert result.embedding_custom_api_key == ""


def test_the_mask_is_not_restored_onto_a_kb_that_had_no_secret():
    """A KB with no stored connection string must not gain the mask as a value."""
    stored = kb(vs_type="chromadb", vs_connection_string="", embedding_custom_api_key="")

    out = _redact(stored)

    assert out.vs_connection_string == ""
    assert out.embedding_custom_api_key == ""


def test_a_chromadb_kb_round_trips_unchanged():
    stored = kb(vs_type="chromadb", vs_connection_string="", embedding_custom_api_key="")

    result = _unmask(echo(_redact(stored)), stored)

    assert result.vs_connection_string == ""
    assert result.embedding_custom_api_key == ""


# ─── through the actual endpoints ─────────────────────────────────────────────
# The helpers above can be correct while an endpoint forgets to call them, so
# these drive the router itself.


@pytest.fixture
def client(tmp_path):
    async def activate(kb_info, reset=False):
        return None

    registry = {"active": "kb1", "bases": {"kb1": json.loads(kb().model_dump_json())}}
    (tmp_path / "knowledge_bases.json").write_text(json.dumps(registry))

    app = FastAPI()
    app.include_router(create_kb_router(db_dir=tmp_path, activate_callback=activate))
    return TestClient(app), tmp_path / "knowledge_bases.json"


def test_get_kb_does_not_serve_the_secrets(client):
    api, _ = client

    body = api.get("/api/v1/kb").text

    assert PASSWORD not in body
    assert API_KEY not in body


def test_the_registry_on_disk_still_holds_the_real_secrets(client):
    """Redaction is a response concern — it must never reach storage."""
    api, registry_path = client

    api.get("/api/v1/kb")

    assert PASSWORD in registry_path.read_text()


def test_saving_the_form_unchanged_does_not_destroy_the_credential(client):
    """The full round-trip: GET, PUT it straight back, credential must survive."""
    api, registry_path = client
    fetched = api.get("/api/v1/kb").json()["bases"]["kb1"]

    response = api.put("/api/v1/kb/kb1", json=fetched)

    assert response.status_code == 200
    stored = json.loads(registry_path.read_text())["bases"]["kb1"]
    assert stored["vs_connection_string"] == CONN
    assert stored["embedding_custom_api_key"] == API_KEY


def test_the_put_response_is_also_redacted(client):
    api, _ = client
    fetched = api.get("/api/v1/kb").json()["bases"]["kb1"]

    body = api.put("/api/v1/kb/kb1", json=fetched).text

    assert PASSWORD not in body
    assert API_KEY not in body


def test_a_real_edit_still_reaches_the_registry(client):
    api, registry_path = client
    fetched = api.get("/api/v1/kb").json()["bases"]["kb1"]
    fetched["vs_connection_string"] = f"postgresql://u:{PASSWORD}@newhost:5432/newdb"

    api.put("/api/v1/kb/kb1", json=fetched)

    stored = json.loads(registry_path.read_text())["bases"]["kb1"]
    assert "newhost" in stored["vs_connection_string"]


def test_activate_does_not_serve_the_secrets(client):
    api, _ = client

    body = api.post("/api/v1/kb/kb1/activate").text

    assert PASSWORD not in body
    assert API_KEY not in body


@pytest.mark.parametrize(
    "conn",
    [
        f"postgresql+asyncpg://{USER}:{PASSWORD}@{HOST}:5432/{DB}",
        f"postgres://{USER}:{PASSWORD}@10.0.0.1:5432/{DB}",
        f"postgresql://{USER}:p%40ss%3Aword@{HOST}:5432/{DB}",
    ],
    ids=["asyncpg", "postgres-ip", "encoded"],
)
def test_every_accepted_url_shape_round_trips(conn):
    """A shape that redacts but fails to restore would destroy the credential."""
    stored = kb(vs_connection_string=conn)

    redacted = _redact(stored)
    assert PASSWORD not in redacted.vs_connection_string
    assert "p%40ss" not in redacted.vs_connection_string

    assert _unmask(echo(redacted), stored).vs_connection_string == conn
