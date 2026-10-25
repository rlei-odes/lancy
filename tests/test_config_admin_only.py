"""Intent: RAG settings are the admin's; every user answers with them.

Users used to keep a personal overlay of the retrieval fields. It was never
personal in effect: any settings save rebuilds the shared agents with the saver's
config, so the last user to save changed retrieval for everyone, and an overlay
saved once kept overriding whatever the admin set later.

The contract:
  - a user reads the admin's baseline, whatever their saved overlay says;
  - a user cannot save settings, and the shared agents are not rebuilt;
  - an admin save still updates the baseline and rebuilds the agents.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lancy.database import set_user_retrieval
from lancy.rag_router import create_rag_router

ADMIN = {"x-user-role": "admin", "x-session-id": "admin-session"}
USER = {"x-user-role": "user", "x-session-id": "user-session"}


def setup(tmp_path):
    rebuilds: list = []

    async def rebuild(cfg):
        rebuilds.append(cfg)

    app = FastAPI()
    app.include_router(
        create_rag_router(
            db_dir=tmp_path,
            vector_store_factory=lambda: None,
            rebuild_callback=None,
            prompts_dir=tmp_path / "prompts",
            agent_rebuild_callback=rebuild,
        )
    )
    return TestClient(app), rebuilds


def save(client, headers, **changes):
    cfg = client.get("/api/v1/rag/config", headers=headers).json()
    return client.post("/api/v1/rag/config", headers=headers, json={**cfg, **changes})


def test_a_user_reads_the_admins_settings_not_a_saved_overlay(tmp_path):
    client, _ = setup(tmp_path)
    assert save(client, ADMIN, retriever_top_k=11).status_code == 200
    set_user_retrieval(tmp_path / "user_config.db", "user-session", {"retriever_top_k": 3})

    assert client.get("/api/v1/rag/config", headers=USER).json()["retriever_top_k"] == 11


def test_a_user_cannot_save_settings_or_rebuild_the_agents(tmp_path):
    client, rebuilds = setup(tmp_path)

    response = save(client, USER, retriever_top_k=2)

    assert response.status_code == 403
    assert rebuilds == []
    assert client.get("/api/v1/rag/config", headers=ADMIN).json()["retriever_top_k"] != 2


def test_an_admin_save_reaches_users_and_rebuilds_the_agents(tmp_path):
    client, rebuilds = setup(tmp_path)

    assert save(client, ADMIN, retriever_top_k=9).status_code == 200

    assert client.get("/api/v1/rag/config", headers=USER).json()["retriever_top_k"] == 9
    assert [cfg.retriever_top_k for cfg in rebuilds] == [9]
