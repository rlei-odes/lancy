"""Intent: the reranker judges each candidate on its whole content.

The ranking prompt used to show the first 400 characters of every candidate.
For a long chunk that is a fraction of it, and for a padded table it can be no
content at all: a glossary chunk whose answer sat 80 % in showed the reranker
"## Begriffe / Definitionen | Artefakt | Definition" — and lost to a short Linux
snippet that happened to mention "key".

The contract:
  - each candidate appears in full, with table padding compacted;
  - only when the whole pool would exceed the prompt budget is each candidate
    cut, all to the same share, so no candidate is starved for another.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.retriever.reranking_retriever import RerankingRetriever
from conversational_toolkit.vectorstores.base import ChunkRecord


def record(chunk_id: str, content: str) -> ChunkRecord:
    return ChunkRecord(id=chunk_id, title=chunk_id, content=content, mime_type="text/plain", metadata={}, embedding=[])


class Base:
    def __init__(self, chunks: list[ChunkRecord]) -> None:
        self.chunks = chunks
        self.top_k = len(chunks)

    async def retrieve(self, query, filters=None):
        return self.chunks


class PromptLLM:
    def __init__(self) -> None:
        self.prompt = ""

    async def generate(self, messages, **kwargs):
        self.prompt = messages[-1].content[0].text
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text='{"ranking": [0, 1]}')])


def rank(chunks: list[ChunkRecord], **kwargs) -> str:
    llm = PromptLLM()
    asyncio.run(RerankingRetriever(Base(chunks), llm, top_k=1, **kwargs).retrieve("surrogate key"))
    return llm.prompt


def test_a_passage_deep_in_a_long_chunk_reaches_the_reranker():
    long = "intro " * 1000 + "surrogate keys are technical keys"

    assert "surrogate keys are technical keys" in rank([record("a", long), record("b", "short")])


def test_table_padding_is_compacted_before_ranking():
    table = "| Artefakt" + " " * 120 + "| Definition" + " " * 120 + "|\n|" + "-" * 200 + "|"

    prompt = rank([record("a", table), record("b", "short")])

    assert "| Artefakt | Definition |\n|---|" in prompt


def test_over_budget_every_candidate_is_cut_to_the_same_share():
    prompt = rank([record("a", "A" * 5000), record("b", "B" * 100)], max_prompt_chars=2000)

    assert prompt.count("A") <= 1000 and prompt.count("A") >= 900
    assert "B" * 100 in prompt  # a short candidate fits its share untouched
