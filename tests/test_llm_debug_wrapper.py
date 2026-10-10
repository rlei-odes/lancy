"""Intent: the debug wrapper is a transparent stand-in for any LLM, signature included.

`DebugLLM` wraps the agent and utility LLMs in main.py **unconditionally** — debug
mode only decides whether a log entry gets written. So the wrapper sits in the path
of every ordinary query, and a parameter it fails to accept is a TypeError for all
users rather than a debug-only inconvenience.

That is exactly what happened: 5a2688b added `max_tokens` to the `llm.generate()`
call in `query_expansion` to cap a runaway, `DebugLLM.generate` was last touched
four months earlier, and query expansion raised

    DebugLLM.generate() got an unexpected keyword argument 'max_tokens'

for anyone who had it enabled. Nothing caught it, because the wrapper subclasses an
ABC that cannot enforce a signature — only the method's presence.

So these tests pin the signature against the base class, and check the arguments are
actually forwarded rather than merely accepted.
"""

import asyncio
import inspect

import pytest

from conversational_toolkit.llms.base import LLM, LLMMessage, MessageContent, Roles
from lancy.llm_debug import DebugLLM


class RecordingLLM(LLM):
    """Records exactly how it was called."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    async def generate(self, conversation, max_tokens=None):
        self.calls.append({"conversation": conversation, "max_tokens": max_tokens})
        return LLMMessage(
            role=Roles.ASSISTANT,
            content=[MessageContent(type="text", text="ok")],
        )

    async def generate_stream(self, conversation):
        self.calls.append({"conversation": conversation, "stream": True})
        yield LLMMessage(
            role=Roles.ASSISTANT,
            content=[MessageContent(type="text", text="ok")],
        )


def conversation() -> list[LLMMessage]:
    return [LLMMessage(role=Roles.USER, content=[MessageContent(type="text", text="hi")])]


# ─── signature parity with the base class ─────────────────────────────────────


@pytest.mark.parametrize("method", ["generate", "generate_stream"])
def test_the_wrapper_signature_matches_the_base_class(method):
    """The ABC guarantees the method exists, not that it takes the same arguments.
    A divergence here is only discoverable at runtime, on a real query."""
    base = inspect.signature(getattr(LLM, method))
    wrapper = inspect.signature(getattr(DebugLLM, method))

    assert list(wrapper.parameters) == list(base.parameters), (
        f"DebugLLM.{method} parameters {list(wrapper.parameters)} do not match "
        f"LLM.{method} {list(base.parameters)} — the wrapper is in the path of every "
        "query, so a missing parameter is a TypeError for all users"
    )


@pytest.mark.parametrize("method", ["generate", "generate_stream"])
def test_the_wrapper_keeps_the_base_defaults(method):
    """A parameter that exists but is required where the base made it optional moves
    the same breakage to callers that omit it."""
    base = inspect.signature(getattr(LLM, method))
    wrapper = inspect.signature(getattr(DebugLLM, method))

    for name, base_param in base.parameters.items():
        assert wrapper.parameters[name].default == base_param.default, (
            f"DebugLLM.{method} changed the default of {name!r}"
        )


# ─── forwarding ───────────────────────────────────────────────────────────────


def test_max_tokens_reaches_the_inner_llm():
    """Accepting the argument is not enough — swallowing it silently restores the
    unbounded generation 5a2688b set out to cap."""
    inner = RecordingLLM()

    asyncio.run(DebugLLM(inner).generate(conversation(), max_tokens=96))

    assert inner.calls[0]["max_tokens"] == 96


def test_omitting_max_tokens_passes_none_through():
    """The instance budget applies when no per-call override is given."""
    inner = RecordingLLM()

    asyncio.run(DebugLLM(inner).generate(conversation()))

    assert inner.calls[0]["max_tokens"] is None


def test_the_query_expansion_call_shape_works_through_the_wrapper():
    """The exact call that was failing: utils/retriever.query_expansion computes a
    budget and passes it positionally-by-keyword into the utility LLM."""
    inner = RecordingLLM()
    wrapped = DebugLLM(inner)

    budget = 32 * 3 + 32  # the formula in query_expansion
    result = asyncio.run(wrapped.generate(conversation(), max_tokens=budget))

    assert inner.calls[0]["max_tokens"] == 128
    assert result.content[0].text == "ok"


def test_generate_returns_the_inner_response_unchanged():
    inner = RecordingLLM()
    result = asyncio.run(DebugLLM(inner).generate(conversation()))
    assert result.role == Roles.ASSISTANT
    assert result.content[0].text == "ok"


def test_generate_stream_passes_chunks_through():
    inner = RecordingLLM()
    async def drain():
        return [c async for c in DebugLLM(inner).generate_stream(conversation())]

    chunks = asyncio.run(drain())
    assert [c.content[0].text for c in chunks] == ["ok"]
