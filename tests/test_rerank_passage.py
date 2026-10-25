"""Intent: a candidate cut for the reranker keeps the passage that matters.

The reranker sends each candidate whole. Only when the pool exceeds its prompt
budget is every candidate cut to the same share — and cutting from the start
loses a passage deep in a long chunk again.

The contract:
  - a cut candidate shows the window around the query terms: the most distinct
    terms first, then the most occurrences;
  - with no term in it, the start of the candidate;
  - a cut is marked with an ellipsis, so the model sees it is reading a part.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.retriever.base import KeywordQuery
from conversational_toolkit.retriever.reranking_retriever import RerankingRetriever, _passage
from conversational_toolkit.retriever.text_analysis import analyze_query
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


def rank(query: str, chunks: list[ChunkRecord], budget: int) -> str:
    llm = PromptLLM()
    asyncio.run(RerankingRetriever(Base(chunks), llm, top_k=1, max_prompt_chars=budget).retrieve(query))
    return llm.prompt


INTRO = "The chapter introduces the overall structure of the warehouse. " * 40


def test_a_cut_candidate_shows_the_passage_around_the_query_terms():
    long = INTRO + "Surrogate keys are technical keys without business meaning. " + INTRO

    prompt = rank("surrogate key", [record("a", long), record("b", "short")], budget=600)

    assert "Surrogate keys are technical keys" in prompt


def test_without_a_query_term_the_start_is_shown():
    prompt = rank("surrogate key", [record("a", "Start of the text. " + INTRO), record("b", "short")], budget=600)

    assert "Start of the text." in prompt


def test_the_window_with_more_distinct_terms_wins():
    repeated = "key key key key " + "filler " * 60
    both = "surrogate key " + "filler " * 60
    text = repeated + both

    window = _passage(text, analyze_query("surrogate key"), width=100)

    assert "surrogate key" in window


def test_a_cut_is_marked():
    text = "filler " * 50 + "surrogate key" + " filler" * 50

    window = _passage(text, analyze_query("surrogate key"), width=60)

    assert window.startswith("… ") and window.endswith(" …")


def test_a_window_narrower_than_a_word_still_returns_something():
    text = "Donaudampfschifffahrtsgesellschaftskapitän " * 20

    assert _passage(text, analyze_query("Donaudampfschifffahrtsgesellschaftskapitän"), width=10)


def test_extra_keyword_terms_also_place_the_window():
    long = INTRO + "Der Ersatzschlüssel ist technisch. " + INTRO

    prompt = rank(KeywordQuery("surrogate key", ["Ersatzschlüssel"]), [record("a", long), record("b", "x")], 600)

    assert "Ersatzschlüssel" in prompt
