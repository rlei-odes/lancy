"""Intent: an admin can see why keyword search did or did not match.

Keyword search analyses every query variant into terms — stop words dropped,
words stemmed — and the terms were only visible as a backend log line.

The contract:
  - the Retrieval Probe returns the analysed terms and, per chunk, the words
    of its content that matched them;
  - the BM25 Debug tab lists recent questions, each with its query variants,
    their terms, the language detected and the top keyword hits;
  - recording is off by default and holds nothing while off: the entries
    contain users' questions.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

import pytest

from conversational_toolkit.retriever.base import KeywordQuery
from conversational_toolkit.retriever.bm25_retriever import BM25Retriever
from conversational_toolkit.retriever.text_analysis import analyze_query, matched_words
from conversational_toolkit.vectorstores.base import ChunkRecord
from lancy import bm25_debug


def record(chunk_id: str, content: str) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id, title="", content=content, mime_type="text/plain", metadata={"source_file": "f.pdf"}, embedding=[]
    )


class Store:
    def __init__(self, chunks: list[ChunkRecord]) -> None:
        self.chunks = chunks

    async def get_chunks_by_filter(self, filters=None):
        return self.chunks


CHUNKS = [
    record("keys", "Every dimension table needs its surrogate keys"),
    record("server", "The server restarts every night after the backup job"),
    record("report", "The report lists all open orders of the week"),
    record("invoice", "The customer receives the invoice by mail"),
]


@pytest.fixture(autouse=True)
def recording_off():
    bm25_debug.set_enabled(False)
    yield
    bm25_debug.set_enabled(False)


# ─── probe ────────────────────────────────────────────────────────────────────


def test_the_probe_highlights_the_words_that_matched():
    assert matched_words("Every dimension table needs its Surrogate keys", analyze_query("surrogate key")) == [
        "surrogate",
        "keys",
    ]


def test_a_stop_word_never_counts_as_a_match():
    assert matched_words("The key is the thing", analyze_query("the key")) == ["key"]


# ─── listener ─────────────────────────────────────────────────────────────────


def test_each_search_reports_its_terms_language_and_hits():
    seen: list[dict] = []
    retriever = BM25Retriever(Store(CHUNKS), top_k=5)
    retriever.search_listener = seen.append

    asyncio.run(retriever.retrieve(KeywordQuery("What is a surrogate key?", ["technical key"])))

    (search,) = seen
    assert "surrogate" in search["terms"] and "what" not in search["terms"]
    assert "technical" in search["expansion_terms"]
    assert search["language"] == "english"
    assert [h["id"] for h in search["hits"]] == ["keys"]  # a zero-score filler is not a hit
    assert "surrog" in search["hits"][0]["matched_terms"]


# ─── recording ────────────────────────────────────────────────────────────────


def ask(question: str, variants: list[str]) -> None:
    retriever = BM25Retriever(Store(CHUNKS), top_k=5)
    retriever.search_listener = bm25_debug.record

    async def answer():
        bm25_debug.begin(question)
        # Variants run as concurrent tasks, as the agent runs them.
        await asyncio.gather(*[retriever.retrieve(v) for v in variants])

    asyncio.run(answer())


def test_nothing_is_kept_while_recording_is_off():
    ask("surrogate key", ["surrogate key"])

    assert bm25_debug.searches() == []


def test_a_question_is_kept_with_all_its_variants():
    bm25_debug.set_enabled(True)
    ask("surrogate key", ["surrogate key", "dimension table keys"])

    (entry,) = bm25_debug.searches()
    assert entry["question"] == "surrogate key"
    assert sorted(v["query"] for v in entry["variants"]) == ["dimension table keys", "surrogate key"]


def test_turning_recording_off_empties_it():
    bm25_debug.set_enabled(True)
    ask("surrogate key", ["surrogate key"])
    bm25_debug.set_enabled(False)

    assert bm25_debug.searches() == []
