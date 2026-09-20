"""Intent: admins own the company-wide presets; users cannot overwrite them.

`DESIGN_DOC_Admin_Role_Separation.md` says admins "create, edit, delete
company-wide presets" and that users inherit "sane defaults coming from
admin-set presets". The implementation seeded `Default` at protection level 2 —
"fully immutable; nobody can delete or overwrite" — so the one preset that
actually matters was the one nobody could set. `Default` is the preset the KB
switch loads by name, which made it the de-facto company default and
simultaneously unreachable.

Two things have to hold for an admin edit to take:

  - the write must not be skipped by the protection check, and
  - it must land in the scope the loader reads from. Seeds live globally
    (kb_id NULL); ordinary saves are KB-scoped, and `get_presets` lists global
    rows first, so a KB-scoped copy named `Default` would be shadowed by the
    seed it was meant to replace and silently do nothing.

Level 2 stays in the model and stays immutable for everyone — it is the level
for anything that must never be edited, even though no seed uses it today.
"""

import json

import pytest

from lancy.database import (
    get_default_preset,
    get_presets,
    init_db,
    save_presets,
    seed_presets,
)

SEEDS = {
    "retrieval": [
        {"name": "Default", "protected": 1, "data": {"retriever_top_k": 5}},
        {"name": "Fast", "protected": 1, "data": {"retriever_top_k": 4}},
        {"name": "Frozen", "protected": 2, "data": {"retriever_top_k": 3}},
    ],
    "kb": [{"name": "Nano", "protected": 1, "data": {"max_chunk_tokens": 256}}],
}


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "conversations.db"
    seeds_path = tmp_path / "presets.json"
    seeds_path.write_text(json.dumps(SEEDS))
    init_db(db_path)
    seed_presets(db_path, seeds_path)
    return db_path, seeds_path


def save(db_path, role, name, data, user_id=None, kb_id="kb-one"):
    save_presets(db_path, kb_id, user_id, role, {"retrieval": [{"name": name, "data": data}], "kb": []})


def named(db_path, name, kb_id="kb-one", user_id=None):
    return [p for p in get_presets(db_path, kb_id, user_id)["retrieval"] if p["name"] == name]


# ─── the admin can set the company default ────────────────────────────────────


def test_an_admin_can_change_the_default_preset(db):
    db_path, _ = db

    save(db_path, "admin", "Default", {"retriever_top_k": 9})

    assert get_default_preset(db_path) == {"retriever_top_k": 9}


def test_the_default_preset_is_readable_at_all(db):
    """It was looked up by `protected=2`; lowering the level must not hide it."""
    db_path, _ = db

    assert get_default_preset(db_path) == {"retriever_top_k": 5}


def test_the_admin_edit_does_not_leave_a_shadowed_duplicate(db):
    """Two rows named Default would list the untouched seed first and win."""
    db_path, _ = db

    save(db_path, "admin", "Default", {"retriever_top_k": 9})

    entries = named(db_path, "Default")
    assert len(entries) == 1
    assert entries[0]["data"] == {"retriever_top_k": 9}


def test_the_admin_edit_is_what_the_kb_switch_would_load(db):
    """The loader matches on name through get_presets, not get_default_preset."""
    db_path, _ = db

    save(db_path, "admin", "Default", {"retriever_top_k": 9})

    first_match = next(p for p in get_presets(db_path, "kb-one", "u1")["retrieval"] if p["name"] == "Default")
    assert first_match["data"] == {"retriever_top_k": 9}


def test_the_admin_edit_survives_a_restart(db):
    """Startup re-seeds; INSERT OR IGNORE must keep the admin's data."""
    db_path, seeds_path = db
    save(db_path, "admin", "Default", {"retriever_top_k": 9})

    seed_presets(db_path, seeds_path)

    assert get_default_preset(db_path) == {"retriever_top_k": 9}


def test_an_admin_can_change_the_other_seeded_presets_too(db):
    db_path, _ = db

    save(db_path, "admin", "Fast", {"retriever_top_k": 2})

    assert named(db_path, "Fast")[0]["data"] == {"retriever_top_k": 2}


# ─── users cannot ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["Default", "Fast"], ids=["default", "fast"])
def test_a_user_cannot_overwrite_an_admin_preset(db, name):
    db_path, _ = db
    before = named(db_path, name)[0]["data"]

    save(db_path, "user", name, {"retriever_top_k": 99}, user_id="u1")

    assert named(db_path, name, user_id="u1")[0]["data"] == before


def test_a_user_still_cannot_overwrite_a_preset_the_admin_just_edited(db):
    """The edited row must keep the seed's level, not drop to unprotected."""
    db_path, _ = db
    save(db_path, "admin", "Default", {"retriever_top_k": 9})

    save(db_path, "user", "Default", {"retriever_top_k": 99}, user_id="u1")

    assert get_default_preset(db_path) == {"retriever_top_k": 9}


def test_a_user_preset_of_their_own_still_saves(db):
    db_path, _ = db

    save(db_path, "user", "My Tuning", {"retriever_top_k": 7}, user_id="u1")

    assert named(db_path, "My Tuning", user_id="u1")[0]["data"] == {"retriever_top_k": 7}


# ─── level 2 stays immutable for everyone ─────────────────────────────────────


@pytest.mark.parametrize("role", ["admin", "user"], ids=["admin", "user"])
def test_nobody_can_overwrite_a_fully_immutable_preset(db, role):
    db_path, _ = db

    save(db_path, role, "Frozen", {"retriever_top_k": 99}, user_id=None if role == "admin" else "u1")

    assert named(db_path, "Frozen", user_id=None if role == "admin" else "u1")[0]["data"] == {
        "retriever_top_k": 3
    }
