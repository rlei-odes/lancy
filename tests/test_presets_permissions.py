"""Intent: presets are scoped per user, and protected seeds survive a user's save.

`save_presets` is a full-replace write, which is the risky shape: it deletes the
scope before re-inserting it. Three rules keep that from destroying other people's
data or the shipped seeds.

  - an admin writes the shared scope (user_id NULL); a user writes only their own
  - KB presets are admin-only and silently dropped for anyone else
  - protection is a ceiling on deletion: 0 is the owner's own, 1 is admin-seeded
    (an admin may replace it, a user may not), 2 is immutable for everyone

A user must never be able to delete a seed, overwrite one by reusing its name, or
disturb another user's presets by saving their own.
"""

import json

import pytest

from lancy.database import _connect, get_presets, init_db, save_presets


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "user_config.db"
    init_db(path)
    return path


def seed(db, *, name, protected, kb_id=None, user_id=None, type_="retrieval", data=None):
    """Insert a preset directly, bypassing the permission rules under test."""
    with _connect(db) as conn:
        conn.execute(
            "INSERT INTO presets (user_id, kb_id, type, name, data_json, protected) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, kb_id, type_, name, json.dumps(data or {"retriever_top_k": 5}), protected),
        )
        conn.commit()


def names(presets, kind="retrieval"):
    return [p["name"] for p in presets[kind]]


def preset(name, **data):
    return {"name": name, "data": data or {"retriever_top_k": 5}}


# ─── scoping: one user's save does not touch another's ────────────────────────


def test_a_user_save_is_invisible_to_another_user(db):
    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("Alice only")]})

    assert names(get_presets(db, "kb1", "alice")) == ["Alice only"]
    assert names(get_presets(db, "kb1", "bob")) == []


def test_a_user_save_does_not_delete_another_users_presets(db):
    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("Alice")]})
    save_presets(db, "kb1", "bob", "user", {"retrieval": [preset("Bob")]})

    assert names(get_presets(db, "kb1", "alice")) == ["Alice"]
    assert names(get_presets(db, "kb1", "bob")) == ["Bob"]


def test_an_admin_save_is_visible_to_everyone(db):
    save_presets(db, "kb1", "admin-user", "admin", {"retrieval": [preset("Shared")]})

    assert names(get_presets(db, "kb1", "alice")) == ["Shared"]
    assert names(get_presets(db, "kb1", "bob")) == ["Shared"]


def test_an_admin_save_writes_the_shared_scope_not_their_own(db):
    save_presets(db, "kb1", "admin-user", "admin", {"retrieval": [preset("Shared")]})

    with _connect(db) as conn:
        owners = [r[0] for r in conn.execute("SELECT user_id FROM presets").fetchall()]

    assert owners == [None], "an admin preset must not be filed under their user id"


def test_presets_are_scoped_per_kb(db):
    save_presets(db, "kb1", None, "admin", {"retrieval": [preset("For KB1")]})

    assert names(get_presets(db, "kb1", "alice")) == ["For KB1"]
    assert names(get_presets(db, "kb2", "alice")) == []


def test_a_global_preset_shows_for_every_kb(db):
    seed(db, name="Global", protected=1, kb_id=None)

    assert names(get_presets(db, "kb1", "alice")) == ["Global"]
    assert names(get_presets(db, "kb99", "alice")) == ["Global"]


# ─── protection: seeds survive a user's full-replace save ─────────────────────


def test_a_user_save_cannot_delete_an_admin_seed(db):
    seed(db, name="Seeded", protected=1, kb_id="kb1")

    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("Mine")]})

    assert "Seeded" in names(get_presets(db, "kb1", "alice"))


def test_a_user_save_cannot_overwrite_a_protected_global_seed_by_reusing_its_name(db):
    seed(db, name="Balanced", protected=1, kb_id=None, data={"retriever_top_k": 5})

    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("Balanced", retriever_top_k=999)]})

    stored = {p["name"]: p["data"] for p in get_presets(db, "kb1", "alice")["retrieval"]}
    assert stored["Balanced"] == {"retriever_top_k": 5}


def test_an_admin_save_cannot_delete_a_fully_immutable_preset(db):
    seed(db, name="Immutable", protected=2, kb_id="kb1")

    save_presets(db, "kb1", None, "admin", {"retrieval": [preset("New")]})

    assert "Immutable" in names(get_presets(db, "kb1", "alice"))


def test_an_admin_save_may_replace_an_admin_seed(db):
    seed(db, name="Seeded", protected=1, kb_id="kb1")

    save_presets(db, "kb1", None, "admin", {"retrieval": [preset("Replacement")]})

    assert names(get_presets(db, "kb1", "alice")) == ["Replacement"]


def test_a_user_can_replace_their_own_unprotected_presets(db):
    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("First"), preset("Second")]})
    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("Third")]})

    assert names(get_presets(db, "kb1", "alice")) == ["Third"]


def test_saving_an_empty_list_clears_only_the_users_own_presets(db):
    seed(db, name="Seeded", protected=1, kb_id="kb1")
    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("Mine")]})

    save_presets(db, "kb1", "alice", "user", {"retrieval": []})

    remaining = names(get_presets(db, "kb1", "alice"))
    assert remaining == ["Seeded"]


# ─── KB presets are admin-only ────────────────────────────────────────────────


def test_a_user_cannot_create_kb_presets(db):
    save_presets(db, "kb1", "alice", "user", {"kb": [preset("Sneaky")]})

    assert names(get_presets(db, "kb1", "alice"), "kb") == []


def test_an_admin_can_create_kb_presets(db):
    save_presets(db, "kb1", None, "admin", {"kb": [preset("Chunking")]})

    assert names(get_presets(db, "kb1", "alice"), "kb") == ["Chunking"]


def test_a_user_save_does_not_wipe_admin_kb_presets(db):
    save_presets(db, "kb1", None, "admin", {"kb": [preset("Chunking")]})

    save_presets(db, "kb1", "alice", "user", {"kb": [], "retrieval": [preset("Mine")]})

    assert names(get_presets(db, "kb1", "alice"), "kb") == ["Chunking"]


def test_kb_presets_are_never_personal(db):
    """get_presets only reads user_id IS NULL for type='kb'."""
    seed(db, name="Personal KB", protected=0, kb_id="kb1", user_id="alice", type_="kb")

    assert names(get_presets(db, "kb1", "alice"), "kb") == []


# ─── shape and robustness ─────────────────────────────────────────────────────


def test_malformed_entries_are_skipped_not_fatal(db):
    save_presets(
        db,
        "kb1",
        "alice",
        "user",
        {"retrieval": [{"name": "no data"}, {"data": {}}, preset("Good")]},
    )

    assert names(get_presets(db, "kb1", "alice")) == ["Good"]


def test_protection_level_is_reported_so_the_ui_can_hide_delete(db):
    seed(db, name="Seeded", protected=1, kb_id="kb1")
    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("Mine")]})

    levels = {p["name"]: p["protected"] for p in get_presets(db, "kb1", "alice")["retrieval"]}

    assert levels == {"Seeded": 1, "Mine": 0}


def test_global_presets_sort_before_kb_specific_ones(db):
    seed(db, name="ZZZ global", protected=1, kb_id=None)
    seed(db, name="AAA kb", protected=1, kb_id="kb1")

    assert names(get_presets(db, "kb1", "alice")) == ["ZZZ global", "AAA kb"]


def test_admin_presets_sort_before_personal_ones(db):
    save_presets(db, "kb1", None, "admin", {"retrieval": [preset("ZZZ admin")]})
    save_presets(db, "kb1", "alice", "user", {"retrieval": [preset("AAA personal")]})

    assert names(get_presets(db, "kb1", "alice")) == ["ZZZ admin", "AAA personal"]


def test_preset_data_round_trips_unchanged(db):
    data = {"retriever_top_k": 12, "bm25_enabled": False, "rrf_k": 60.5, "note": "Zürich"}
    save_presets(db, "kb1", "alice", "user", {"retrieval": [{"name": "P", "data": data}]})

    assert get_presets(db, "kb1", "alice")["retrieval"][0]["data"] == data
