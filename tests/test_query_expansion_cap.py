"""Intent: query expansion returns the number of queries it was asked for.

`query_expansion` prompts for `expansion_number` queries and then returns every
line the model produced, unbounded. Each returned line becomes its own full
retrieval pass in `RAG.answer_stream` — an embedding call and a vector search.

Observed in production with expansion set to 1: the model fell into a
repetition loop (elements / components / framework, cycling) and emitted 204
lines. That became 206 query variants, 206 concurrent searches, and the vector
store's connection pool (size 5, overflow 10) timed out after 30s. The UI
blamed the database, which was healthy throughout.

The token cap on the utility LLM is what bounds the loop today, which makes the
damage a function of an unrelated setting. The parameter has to be authoritative
instead: the caller said how many queries it wants, and a model that ignores
that must not be able to multiply the retrieval work.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

import pytest

from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.utils.retriever import query_expansion


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.max_tokens: int | None = None
        self.prompt = ""

    async def generate(self, messages, max_tokens=None, **kwargs):
        self.max_tokens = max_tokens
        self.prompt = " ".join(c.text or "" for m in messages for c in m.content if c.type == "text")
        return LLMMessage(
            role=Roles.ASSISTANT, content=[MessageContent(type="text", text=self.reply)]
        )


def expand(reply: str, n: int = 1) -> list[str]:
    return asyncio.run(query_expansion("original question", FakeLLM(reply), n))


# ─── the parameter is the bound ───────────────────────────────────────────────


def test_a_repetition_loop_cannot_multiply_the_retrieval_work():
    """The production failure: 204 lines when one was asked for."""
    loop = "\n".join(
        ["cloud strategy elements", "cloud strategy components", "cloud strategy framework"] * 68
    )

    assert len(expand(loop, n=1)) == 1


@pytest.mark.parametrize("n", [1, 2, 5, 10])
def test_never_more_than_requested(n):
    twenty = "\n".join(f"query {i}" for i in range(20))

    assert len(expand(twenty, n=n)) == n


def test_the_queries_returned_are_the_first_ones_offered():
    """Truncation must keep the model's own ordering, not sample arbitrarily."""
    assert expand("alpha\nbravo\ncharlie", n=2) == ["alpha", "bravo"]


def test_duplicates_are_dropped_before_the_cap():
    """Capping first would return the same query twice and search it twice."""
    assert expand("alpha\nalpha\nbravo", n=2) == ["alpha", "bravo"]


# ─── what must still work ─────────────────────────────────────────────────────


def test_fewer_lines_than_requested_returns_what_came():
    assert expand("alpha", n=3) == ["alpha"]


def test_blank_lines_are_not_queries():
    """An empty query would be a wasted embedding call and a useless search."""
    assert expand("alpha\n\n   \nbravo", n=3) == ["alpha", "bravo"]


def test_surrounding_whitespace_is_stripped():
    assert expand("  alpha  \n\tbravo", n=2) == ["alpha", "bravo"]


def test_an_empty_response_yields_no_queries():
    assert expand("", n=3) == []


# ─── the model is not allowed to run away in the first place ──────────────────
# Truncating the result still pays for the tokens: the runaway above took ~19s
# of a ~42s answer to produce 1024 tokens, of which one line was used.


def ask(n: int) -> FakeLLM:
    llm = FakeLLM("alpha")
    asyncio.run(query_expansion("original question", llm, n))
    return llm


def test_the_generation_is_bounded():
    """Without a per-call bound this inherits the utility LLM's whole budget."""
    llm = ask(1)

    assert llm.max_tokens is not None
    assert llm.max_tokens < 512


def test_the_bound_leaves_room_for_the_queries_asked_for():
    """A bound that truncates a legitimate answer would be its own bug."""
    llm = ask(10)

    assert llm.max_tokens >= 10 * 8


def test_the_bound_grows_with_the_number_of_queries():
    assert ask(10).max_tokens > ask(1).max_tokens


# ─── the prompt gives the model a reachable target ────────────────────────────


def test_the_prompt_asks_for_one_unambiguous_count():
    """"Produce 1 queries total: some ... some ..." is what started the loop."""
    assert "exactly 1" in ask(1).prompt


def test_the_prompt_does_not_ask_for_other_languages():
    """An English question against an English corpus spent a slot on Spanish."""
    assert "English" not in ask(3).prompt
