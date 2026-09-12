"""Intent: the OpenAI-compatible endpoint answers, and keeps answering.

`POST /v1/chat/completions` calls `agent.answer()`. It shipped working, because
the agent it was handed was a `CustomRAG`, which inherits `answer()` from the
Agent base. The multi-KB change replaced that agent with `DispatchingAgent` —
a plain class implementing only `answer_stream()` — so every request raised
`AttributeError` and returned 500, streaming or not.

Nothing caught it: `DispatchingAgent` does not subclass `Agent`, so no interface
obliged it to have `answer()`, the failure only surfaced per request, and no test
covered the route. It stayed broken for four months while the README advertised
it, because the web UI uses `/api/v1/messages` and never touches this path.

So the test that matters is the boring one — a request returns 200 with an
answer — plus the interface test that stops the two agent entry points from
drifting apart again.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conversational_toolkit.agents.base import AgentAnswer
from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.llms.base import MessageContent, Roles

from lancy.kb_pool import DispatchingAgent
from lancy.openai_compat_router import create_openai_compat_router

ANSWER = "Python is used in data engineering."


def chunk(title, source_file):
    return Chunk(
        title=title,
        content="…",
        mime_type="text/plain",
        metadata={"source_file": source_file},
    )


class FakeKB:
    def __init__(self, kb_id, name):
        self.id = kb_id
        self.name = name


class FakeAgent:
    """Stands in for DispatchingAgent: records the call, returns a fixed answer."""

    def __init__(self, sources=None, loaded=("kb-one",)):
        self.sources = sources or []
        self.seen = None
        self.seen_kb_id = "<unset>"
        self.loaded = [FakeKB(k, k.upper()) for k in loaded]

    def loaded_kbs(self):
        return self.loaded

    async def answer(self, query_with_context, kb_id=None):
        self.seen = query_with_context
        self.seen_kb_id = kb_id
        return AgentAnswer(
            content=[MessageContent(type="text", text=ANSWER)],
            role=Roles.ASSISTANT,
            sources=self.sources,
        )


def client(agent):
    app = FastAPI()
    app.include_router(create_openai_compat_router(agent))
    return TestClient(app)


def ask(agent, **overrides):
    body = {"model": "rag-assistant", "messages": [{"role": "user", "content": "Why Python?"}]}
    body.update(overrides)
    return client(agent).post("/v1/chat/completions", json=body)


# ─── the endpoint answers at all ──────────────────────────────────────────────


def test_a_plain_request_succeeds():
    """The regression: this returned 500 for four months."""
    response = ask(FakeAgent())

    assert response.status_code == 200


def test_the_answer_is_in_the_openai_response_shape():
    body = ask(FakeAgent()).json()

    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert ANSWER in body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "stop"


def test_a_streaming_request_succeeds():
    """Streaming broke too — the failing call sat above the stream branch."""
    response = ask(FakeAgent(), stream=True)

    assert response.status_code == 200
    assert ANSWER in response.text
    assert response.text.rstrip().endswith("data: [DONE]")


def test_the_stream_emits_parseable_openai_chunks():
    response = ask(FakeAgent(), stream=True)

    payloads = [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ") and not line.endswith("[DONE]")
    ]

    assert [p["object"] for p in payloads] == ["chat.completion.chunk"] * 2
    assert payloads[0]["choices"][0]["delta"]["content"] == ANSWER
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"


# ─── what reaches the agent ───────────────────────────────────────────────────


def test_the_last_user_message_becomes_the_query():
    agent = FakeAgent()

    ask(agent, messages=[
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ])

    assert agent.seen.query == "second"


def test_earlier_turns_become_history_without_the_query():
    agent = FakeAgent()

    ask(agent, messages=[
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ])

    texts = [c.text for m in agent.seen.history for c in m.content]
    assert texts == ["first", "reply"]


def test_a_request_without_a_user_message_is_rejected():
    response = ask(FakeAgent(), messages=[{"role": "assistant", "content": "hi"}])

    assert response.status_code == 400
    assert "error" in response.json()


# ─── choosing a knowledge base ────────────────────────────────────────────────
# An external caller must reach loaded KBs only. Loading one on demand can evict
# the pool when its embedding model differs, which would break the UI's users.


def test_the_default_model_means_the_active_kb():
    agent = FakeAgent()

    ask(agent, model="rag-assistant")

    assert agent.seen_kb_id is None


def test_a_loaded_kb_can_be_addressed_by_id():
    agent = FakeAgent(loaded=("kb-one", "kb-two"))

    ask(agent, model="kb-two")

    assert agent.seen_kb_id == "kb-two"


def test_an_unloaded_kb_is_refused():
    """The KBs greyed out in the selector must not be reachable."""
    agent = FakeAgent(loaded=("kb-one",))

    response = ask(agent, model="kb-unloaded")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_a_refused_kb_is_never_queried():
    """Refusal has to happen before the agent is touched at all."""
    agent = FakeAgent(loaded=("kb-one",))

    ask(agent, model="kb-unloaded")

    assert agent.seen is None


def test_models_lists_the_loaded_kbs_and_the_active_alias():
    agent = FakeAgent(loaded=("kb-one", "kb-two"))

    data = client(agent).get("/v1/models").json()["data"]

    assert [m["id"] for m in data] == ["rag-assistant", "kb-one", "kb-two"]


def test_models_does_not_advertise_unloaded_kbs():
    agent = FakeAgent(loaded=("kb-one",))

    ids = [m["id"] for m in client(agent).get("/v1/models").json()["data"]]

    assert "kb-unloaded" not in ids


# ─── generation settings belong to the KB ─────────────────────────────────────


def test_temperature_and_max_tokens_are_accepted_but_not_honoured():
    """Real clients always send these; they must not 422, and must not apply."""
    agent = FakeAgent()

    response = ask(agent, temperature=0.9, max_tokens=64)

    assert response.status_code == 200
    assert not hasattr(agent.seen, "temperature")


# ─── sources ──────────────────────────────────────────────────────────────────


def test_sources_are_listed_in_english():
    agent = FakeAgent(sources=[chunk("Intro", "intro.pdf")])

    content = ask(agent).json()["choices"][0]["message"]["content"]

    assert "**Sources:**" in content
    assert "Quellen" not in content
    assert "Intro (intro.pdf)" in content


def test_repeated_sources_are_listed_once():
    agent = FakeAgent(sources=[chunk("Intro", "intro.pdf"), chunk("Intro", "intro.pdf")])

    content = ask(agent).json()["choices"][0]["message"]["content"]

    assert content.count("Intro (intro.pdf)") == 1


def test_an_answer_without_sources_gets_no_source_block():
    content = ask(FakeAgent()).json()["choices"][0]["message"]["content"]

    assert "**Sources:**" not in content


# ─── the interface that broke ─────────────────────────────────────────────────


class FakeEntry:
    def __init__(self, agent, kb_id="kb1"):
        self.agent = agent
        self.kb = FakeKB(kb_id, kb_id.upper())


class FakePool:
    """Mirrors the real pool's contract: get() never loads, it only looks up."""

    def __init__(self, entries=()):
        self.by_id = {e.kb.id: e for e in entries}
        self.loads = []

    def get(self, kb_id):
        return self.by_id.get(kb_id)

    def get_active(self):
        return next(iter(self.by_id.values()), None)

    def entries(self):
        return list(self.by_id.values())


def dispatching(*entries):
    return DispatchingAgent(FakePool(entries), conv_db=None, active_kb_id_fn=lambda: "kb1")


@pytest.mark.parametrize("method", ["answer", "answer_stream"])
def test_dispatching_agent_offers_both_agent_entry_points(method):
    """The bug was one path having a method the other lacked."""
    assert callable(getattr(dispatching(), method, None))


@pytest.mark.parametrize("stream", [False, True], ids=["non-stream", "stream"])
def test_the_endpoint_works_against_the_real_dispatching_agent(stream):
    """The wiring the production 500 came from: this router, that agent.

    The tests above pass a stand-in that already has `answer()`, so they cannot
    see the defect. Only assembling the two real pieces can.
    """
    api = client(dispatching(FakeEntry(FakeAgent())))

    response = api.post("/v1/chat/completions", json={
        "model": "rag-assistant",
        "messages": [{"role": "user", "content": "Why Python?"}],
        "stream": stream,
    })

    assert response.status_code == 200
    assert ANSWER in response.text


def test_answer_delegates_to_the_resolved_kb():
    inner = FakeAgent()

    # pytest-asyncio is not a dependency; the rest of the suite is sync too.
    result = asyncio.run(dispatching(FakeEntry(inner)).answer(_Query()))

    assert result.content[0].text == ANSWER
    assert inner.seen is not None


def test_answer_reports_when_no_kb_is_loaded():
    """Must return an answer, not raise — the router has no error handling."""
    result = asyncio.run(dispatching().answer(_Query()))

    assert "No knowledge base is loaded" in result.content[0].text


def test_an_explicit_kb_id_selects_that_kb():
    one, two = FakeAgent(), FakeAgent()
    agent = dispatching(FakeEntry(one, "kb-one"), FakeEntry(two, "kb-two"))

    asyncio.run(agent.answer(_Query(), kb_id="kb-two"))

    assert two.seen is not None
    assert one.seen is None


def test_an_unloaded_kb_id_does_not_fall_back_to_the_active_kb():
    """Falling back would silently answer from the wrong corpus."""
    inner = FakeAgent()
    agent = dispatching(FakeEntry(inner, "kb-one"))

    result = asyncio.run(agent.answer(_Query(), kb_id="kb-missing"))

    assert inner.seen is None
    assert "No knowledge base is loaded" in result.content[0].text


def test_loaded_kbs_reports_what_the_pool_holds():
    agent = dispatching(FakeEntry(FakeAgent(), "kb-one"), FakeEntry(FakeAgent(), "kb-two"))

    assert [kb.id for kb in agent.loaded_kbs()] == ["kb-one", "kb-two"]


class _Query:
    """Minimal stand-in: only conversation_id is read off the query object."""

    conversation_id = None
