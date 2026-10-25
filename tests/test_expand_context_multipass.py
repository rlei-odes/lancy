"""Intent: a question about documents too large for the context still gets an answer from all of them.

"Ask using only these" sends every chunk of the picked documents in one LLM call.
A long document exceeds the budget on its own — a 247k-token book against a
budget of a fraction of that — and the UI blocked the question.

The contract:
  - documents that fit the budget go in one call, as before;
  - larger ones are read in passes: consecutive chunks, in document order, each
    pass within the budget; every pass gives a partial answer citing its chunks
    as [id], or NOTHING RELEVANT, which is dropped;
  - partial answers too long together are merged in rounds before the final call;
  - the final answer streams from the partial answers, with every chunk available
    for its citations;
  - progress is reported per pass, so the UI can show "part 4 of 13".

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio
import json

import pytest

from conversational_toolkit.agents.base import QueryWithContext
from conversational_toolkit.agents.rag import RAG, _answer_field, split_into_passes
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.vectorstores.base import ChunkRecord


def chunk(i: int, text: str) -> ChunkRecord:
    return ChunkRecord(
        id=f"id{i}",
        title="",
        content=text,
        mime_type="text/markdown",
        metadata={"source_file": "book.pdf"},
        embedding=[],
    )


class Store:
    def __init__(self, chunks: list[ChunkRecord]) -> None:
        self.chunks = chunks

    async def get_chunks_by_filter(self, filters=None):
        return self.chunks


def reply(text: str) -> LLMMessage:
    return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text=text)])


class LLM:
    """Map and merge calls go to 'generate', the final answer to 'generate_stream'."""

    def __init__(self, partial=lambda user_text: "a fact [id0]") -> None:
        self.partial = partial
        self.calls: list[tuple[str, str]] = []  # (system prompt, user text) per generate call
        self.final: list[str] = []

    async def generate(self, messages, **kwargs):
        system, user = messages[0].content[0].text, messages[-1].content[0].text
        self.calls.append((system, user))
        return reply(json.dumps({"answer": self.partial(user), "used_sources_id": [], "follow_up_questions": []}))

    async def generate_stream(self, messages, **kwargs):
        self.final.append("".join(c.text or "" for c in messages[-1].content))
        yield reply('{"answer": "final", "used_sources_id": [], "follow_up_questions": []}')


def agent(llm: LLM, chunks: list[ChunkRecord], budget: int | None) -> RAG:
    return RAG(
        llm=llm,
        utility_llm=llm,
        retrievers=[],
        system_prompt="sys",
        expand_context_system_prompt="single",
        vector_store=Store(chunks),
        expand_context_budget_chars=budget,
        expand_context_map_prompt="MAP",
        expand_context_merge_prompt="MERGE",
        expand_context_reduce_prompt="REDUCE",
    )


def ask(rag: RAG) -> list:
    async def drive():
        return [
            a async for a in rag.answer_stream(QueryWithContext(query="what?", history=[], expand_context=["book.pdf"]))
        ]

    return asyncio.run(drive())


BOOK = [chunk(i, f"chapter {i} " * 10) for i in range(10)]  # 110 characters each


# ─── splitting ────────────────────────────────────────────────────────────────


def test_passes_keep_document_order_and_the_budget():
    assert split_into_passes([40, 40, 40, 40, 40], 100) == [[0, 1], [2, 3], [4]]


def test_a_chunk_over_the_budget_is_a_pass_of_its_own():
    assert split_into_passes([30, 500, 30], 100) == [[0], [1], [2]]


# ─── one call when it fits ────────────────────────────────────────────────────


def test_documents_within_the_budget_are_read_in_one_call():
    llm = LLM()
    ask(agent(llm, BOOK, budget=10_000))

    assert llm.calls == [] and len(llm.final) == 1
    assert all(f'id="id{i}"' in llm.final[0] for i in range(10))


def test_without_a_budget_everything_is_one_call():
    llm = LLM()
    ask(agent(llm, BOOK, budget=None))

    assert llm.calls == [] and len(llm.final) == 1


# ─── passes ───────────────────────────────────────────────────────────────────


def test_each_pass_reads_only_its_part_in_order():
    llm = LLM()
    ask(agent(llm, BOOK, budget=250))  # two 110-character chunks per pass

    maps = [user for system, user in llm.calls if system == "MAP"]
    assert len(maps) == 5
    assert 'id="id0"' in maps[0] and 'id="id1"' in maps[0] and 'id="id2"' not in maps[0]
    assert 'id="id8"' in maps[4] and 'number="5" of="5"' in maps[4]


def test_progress_is_reported_per_pass():
    llm = LLM()
    rag = agent(llm, BOOK, budget=250)
    seen: list[tuple[int, int]] = []
    rag.progress_callback = lambda number, total: seen.append((number, total))

    ask(rag)

    assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


def test_a_part_with_nothing_relevant_is_left_out_of_the_final_answer():
    llm = LLM(partial=lambda user: "the answer [id4]" if 'id="id4"' in user else "NOTHING RELEVANT")
    ask(agent(llm, BOOK, budget=250))

    (final,) = llm.final
    assert "the answer [id4]" in final and "NOTHING RELEVANT" not in final


def test_the_final_answer_can_cite_every_chunk():
    rag = agent(LLM(), BOOK, budget=250)
    captured: list = []

    async def capture(answer):
        captured.append(answer)
        return answer

    rag._answer_post_processing = capture  # type: ignore[method-assign]
    ask(rag)
    assert {s.id for s in captured[-1].sources} == {c.id for c in BOOK}


# ─── merging ──────────────────────────────────────────────────────────────────


def test_partial_answers_too_long_together_are_merged_first():
    long_partial = "x" * 94 + " [id0]"  # 100 characters: five exceed a 250-character budget, two fit
    llm = LLM(partial=lambda user: long_partial if "<sources>" in user else "merged [id0]")
    ask(agent(llm, BOOK, budget=250))

    merges = [user for system, user in llm.calls if system == "MERGE"]
    assert merges, "merge round expected"
    assert "merged [id0]" in llm.final[0]


# ─── details ──────────────────────────────────────────────────────────────────


def test_the_partial_answer_is_the_answer_field_of_a_fenced_json_reply():
    assert _answer_field('```json\n{"answer": "a fact [id1]", "used_sources_id": ["id1"]}\n```') == "a fact [id1]"
    assert _answer_field("plain text") == "plain text"


def test_a_budget_without_the_prompts_fails_at_construction():
    with pytest.raises(ValueError):
        RAG(llm=LLM(), utility_llm=LLM(), retrievers=[], system_prompt="s", expand_context_budget_chars=100)
