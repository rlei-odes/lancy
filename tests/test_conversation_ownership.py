"""Intent: a conversation is visible and changeable only to the user who owns it.

Every route under /conversations/{id} took the caller's user id and never compared
it with the conversation's owner: knowing an id was enough to read another user's
messages and sources, rename the conversation, delete it, or react to its answers.

The contract:
  - each of those routes answers the owner as before;
  - another user gets 404, the same as for an id that does not exist, so the
    response does not reveal which ids exist;
  - a reaction must name a message of that conversation.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from conversational_toolkit.api.routes.api import create_api_router


class Conversations:
    def __init__(self) -> None:
        self.rows = {"c1": SimpleNamespace(id="c1", user_id="alice")}

    async def get_conversation_by_id(self, conversation_id):
        if conversation_id not in self.rows:
            raise ValueError(f"Conversation with id {conversation_id} not found")
        return self.rows[conversation_id]


class Messages:
    async def get_message_by_id(self, message_id):
        if message_id == "m1":
            return SimpleNamespace(id="m1", conversation_id="c1")
        if message_id == "m-other":
            return SimpleNamespace(id="m-other", conversation_id="c2")
        raise ValueError(f"Message with id {message_id} not found")


class Controller:
    """Records which actions ran; ownership is the router's job."""

    def __init__(self) -> None:
        self.conversation_db = Conversations()
        self.message_db = Messages()
        self.ran: list[str] = []

    async def get_conversation_by_id(self, conversation_id):
        self.ran.append("get")
        return {
            "id": conversation_id,
            "user_id": "alice",
            "title": "t",
            "create_timestamp": 0,
            "update_timestamp": 0,
            "messages": [],
        }

    async def update_conversation(self, conversation_id, updates):
        self.ran.append("update")
        return {"id": conversation_id, "user_id": "alice", "title": "new", "create_timestamp": 0, "update_timestamp": 0}

    async def delete_conversation(self, conversation_id):
        self.ran.append("delete")
        return True

    async def get_messages_by_conversation_id(self, conversation_id):
        self.ran.append("messages")
        return []

    async def add_reaction(self, reaction_input, message_id, user_id):
        self.ran.append("react")
        return {"id": "r1", "user_id": user_id, "message_id": message_id, "content": "like", "note": None}


class Auth:
    @staticmethod
    async def get_current_user_id(x_user: str = Header(...)) -> str:
        return x_user


@pytest.fixture
def setup():
    controller = Controller()
    app = FastAPI()
    app.include_router(create_api_router(controller, Auth()))  # type: ignore[arg-type]
    return controller, TestClient(app, raise_server_exceptions=False)


ROUTES = [
    ("get", "/api/v1/conversations/c1", None),
    ("put", "/api/v1/conversations/c1", {"title": "new"}),
    ("delete", "/api/v1/conversations/c1", None),
    ("get", "/api/v1/conversations/c1/messages", None),
    ("post", "/api/v1/conversations/c1/messages/m1/reactions", {"content": "like"}),
]


def call(client, method, url, body, user):
    kwargs = {"headers": {"x-user": user}}
    if body is not None:
        kwargs["json"] = body
    return client.request(method, url, **kwargs)


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_the_owner_is_served(setup, method, url, body):
    controller, client = setup

    assert call(client, method, url, body, "alice").status_code == 200
    assert controller.ran


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_another_user_gets_404_and_nothing_runs(setup, method, url, body):
    controller, client = setup

    response = call(client, method, url, body, "mallory")

    assert response.status_code == 404
    assert controller.ran == []


def test_a_foreign_conversation_looks_like_a_missing_one(setup):
    _, client = setup

    foreign = call(client, "get", "/api/v1/conversations/c1/messages", None, "mallory")
    missing = call(client, "get", "/api/v1/conversations/nope/messages", None, "mallory")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_a_reaction_must_name_a_message_of_that_conversation(setup):
    controller, client = setup

    response = call(client, "post", "/api/v1/conversations/c1/messages/m-other/reactions", {"content": "like"}, "alice")

    assert response.status_code == 404
    assert controller.ran == []
