"""Intent: a query is answered by the KB it names, or not at all.

`DispatchingAgent._entry_for` resolves its pool entry with
`self._pool.get(kb_id) or self._pool.get_active()`. A conversation whose
persisted kb_id is not in the pool is therefore answered out of whatever KB is
active — no error, no warning, nothing in the response to tell the two apart.
The user asks one knowledge base and is answered by another.

This is reachable in normal operation: conversation kb_id lives in the DB,
pool membership does not. A restart loads only `registry.active`, and
`POST /kb/{id}/deactivate` unloads a KB without clearing `active`.

The contract is the one `answer(kb_id=...)` already implements for the
OpenAI-compatible endpoint: an unloaded KB is reported, never substituted.
Reporting it means naming it — "no knowledge base is loaded" is false and
unhelpful when three others are loaded and only this conversation's is missing.

Fakes mirror `test_openai_compat.py`, which covers the same class through the
explicit-kb_id door. pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

import pytest

from lancy.kb_pool import DispatchingAgent


class FakeKB:
    def __init__(self, kb_id):
        self.id = kb_id
        self.name = kb_id.upper()


class FakeAgent:
    """Records that it answered, so a silent substitution is visible."""

    def __init__(self, kb_id="kb1"):
        self.kb_id = kb_id
        self.seen = None

    async def answer_stream(self, query_with_context):
        self.seen = query_with_context
        yield f"answer from {self.kb_id}"

    async def answer(self, query_with_context):
        self.seen = query_with_context
        return f"answer from {self.kb_id}"


class FakeEntry:
    def __init__(self, agent, kb_id="kb1"):
        self.agent = agent
        self.kb = FakeKB(kb_id)


class FakePool:
    """get() never loads, it only looks up — same contract as the real pool."""

    def __init__(self, entries=()):
        self.by_id = {e.kb.id: e for e in entries}

    def get(self, kb_id):
        return self.by_id.get(kb_id)

    def get_active(self):
        return next(iter(self.by_id.values()), None)

    def entries(self):
        return list(self.by_id.values())


class FakeConvDB:
    def __init__(self, kb_id):
        self.kb_id = kb_id

    async def get_conversation_by_id(self, conversation_id):
        return type("Conv", (), {"kb_id": self.kb_id})()


class _Query:
    conversation_id = "c1"


def dispatching(*entries, conv_kb="kb1", active="kb1"):
    return DispatchingAgent(
        FakePool(entries), conv_db=FakeConvDB(conv_kb), active_kb_id_fn=lambda: active
    )


def text(chunk) -> str:
    """FakeAgent yields plain strings; the not-loaded path yields an AgentAnswer."""
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        return " ".join(p.text for p in content if p.type == "text")
    return str(chunk)


def streamed(agent) -> str:
    async def collect():
        return "".join([text(c) async for c in agent.answer_stream(_Query())])

    return asyncio.run(collect())


# ─── the substitution must not happen ─────────────────────────────────────────


def test_a_conversation_bound_to_an_unloaded_kb_is_not_served_by_another():
    loaded = FakeAgent("kb-a")
    agent = dispatching(FakeEntry(loaded, "kb-a"), conv_kb="kb-b", active="kb-a")

    streamed(agent)

    assert loaded.seen is None


def test_the_unloaded_kb_is_named_in_the_response():
    """Silence is the bug; the user has to learn which KB is missing."""
    agent = dispatching(FakeEntry(FakeAgent("kb-a"), "kb-a"), conv_kb="kb-b", active="kb-a")

    assert "kb-b" in streamed(agent)


def test_the_non_streaming_path_does_not_substitute_either():
    loaded = FakeAgent("kb-a")
    agent = dispatching(FakeEntry(loaded, "kb-a"), conv_kb="kb-b", active="kb-a")

    answer = asyncio.run(agent.answer(_Query()))

    assert loaded.seen is None
    assert "kb-b" in text(answer)


def test_deactivating_the_active_kb_does_not_redirect_to_a_survivor():
    """`deactivate` unloads without clearing `registry.active` — the live trigger.

    The conversation carries no kb_id, so it resolves to `active`, which the
    registry still names after the pool dropped it.
    """
    survivor = FakeAgent("kb-a")
    agent = dispatching(FakeEntry(survivor, "kb-a"), conv_kb=None, active="kb-b")

    streamed(agent)

    assert survivor.seen is None


@pytest.mark.parametrize("method", ["answer", "answer_stream"], ids=["non-stream", "stream"])
def test_neither_entry_point_falls_back(method):
    """The two paths drifted apart once before; pin them together."""
    loaded = FakeAgent("kb-a")
    agent = dispatching(FakeEntry(loaded, "kb-a"), conv_kb="kb-b", active="kb-a")

    if method == "answer":
        asyncio.run(agent.answer(_Query()))
    else:
        streamed(agent)

    assert loaded.seen is None


# ─── what must keep working ───────────────────────────────────────────────────


def test_a_loaded_kb_still_answers():
    wanted = FakeAgent("kb-b")
    agent = dispatching(
        FakeEntry(FakeAgent("kb-a"), "kb-a"), FakeEntry(wanted, "kb-b"),
        conv_kb="kb-b", active="kb-a",
    )

    assert "answer from kb-b" in streamed(agent)
    assert wanted.seen is not None


def test_a_conversation_with_no_kb_still_uses_the_active_one():
    """A new conversation has no kb_id — it must not become an error."""
    agent = dispatching(FakeEntry(FakeAgent("kb-a"), "kb-a"), conv_kb=None, active="kb-a")

    assert "answer from kb-a" in streamed(agent)


def test_an_empty_pool_still_reports_that_nothing_is_loaded():
    """With nothing loaded at all, naming one KB would be the wrong diagnosis."""
    agent = dispatching(conv_kb=None, active="kb-a")

    assert "No knowledge base is loaded" in streamed(agent)
