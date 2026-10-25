"""Intent: keyword search finds documents that use other words or another language.

Keyword search matches the question's own words. "surrogate key" never matched
"Ersatzschlüssel" or "technischer Schlüssel", though semantic search would.

The contract:
  - a utility-LLM step turns the question into search terms: its key terms,
    their synonyms and inflections, and translations into the corpus languages;
  - the terms feed keyword search only, weighted below the question's own
    words, so a synonym cannot outrank the word the user typed;
  - with the step on, expanded queries go to semantic search only: keyword
    search takes the question and its weighted terms.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

from conversational_toolkit.agents.base import QueryWithContext
from conversational_toolkit.agents.rag import RAG
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.retriever.base import KeywordQuery, SemanticOnlyQuery
from conversational_toolkit.retriever.bm25_retriever import BM25Retriever
from conversational_toolkit.utils.retriever import MAX_KEYWORDS, keyword_extraction
from conversational_toolkit.vectorstores.base import ChunkRecord


def record(chunk_id: str, content: str) -> ChunkRecord:
    return ChunkRecord(id=chunk_id, title="", content=content, mime_type="text/plain", metadata={}, embedding=[])


FILLER = [
    record(f"f{i}", text)
    for i, text in enumerate(
        [
            "Der Kunde bestellt ein Produkt und bezahlt die Rechnung",
            "The server restarts every night after the backup job",
            "Die Lieferung erfolgt innerhalb von drei Tagen",
            "The report lists all open orders of the week",
        ]
    )
]


class Store:
    def __init__(self, chunks: list[ChunkRecord]) -> None:
        self.chunks = chunks

    async def get_chunks_by_filter(self, filters=None):
        return self.chunks


def search(query: str, *chunks: ChunkRecord) -> list[str]:
    retriever = BM25Retriever(Store([*chunks, *FILLER]), top_k=10)
    return [m.id for m in asyncio.run(retriever.retrieve(query)) if m.ranks]


# ─── keyword search ───────────────────────────────────────────────────────────


GERMAN = record("de", "Der Ersatzschlüssel identifiziert jede Zeile der Tabelle eindeutig")


def test_the_question_alone_misses_a_translation():
    assert search("surrogate key", GERMAN) == []


def test_a_translated_term_finds_the_document():
    assert search(KeywordQuery("surrogate key", ["Ersatzschlüssel"]), GERMAN) == ["de"]


def test_the_typed_word_outranks_a_synonym():
    typed = record("typed", "Each table has a surrogate key column")
    synonym = record("synonym", "Each table has a technical key column")

    ranked = search(KeywordQuery("surrogate key", ["technical key"]), synonym, typed)

    assert ranked.index("typed") < ranked.index("synonym")


# ─── the extraction step ──────────────────────────────────────────────────────


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompt = ""

    async def generate(self, messages, max_tokens=None, **kwargs):
        self.prompt = " ".join(c.text or "" for m in messages for c in m.content if c.type == "text")
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text=self.reply)])


def test_terms_come_back_clean_and_once():
    reply = '1. surrogate key\n- Surrogate Key\n* "technical key"\n\nErsatzschlüssel\n'

    terms = asyncio.run(keyword_extraction("What is a surrogate key?", FakeLLM(reply)))

    assert terms == ["surrogate key", "technical key", "Ersatzschlüssel"]


def test_a_runaway_answer_is_cut():
    reply = "\n".join(f"term {i}" for i in range(200))

    assert len(asyncio.run(keyword_extraction("q", FakeLLM(reply)))) == MAX_KEYWORDS


def test_translations_go_into_the_corpus_languages():
    llm = FakeLLM("x")
    asyncio.run(keyword_extraction("What is a surrogate key?", llm, ["German", "English"]))

    assert "translations of each key term into German, and into no other language" in llm.prompt


def test_the_questions_own_language_is_no_translation_target():
    """Asked to translate into the language it is reading, a model picks languages of its own."""
    llm = FakeLLM("x")
    asyncio.run(keyword_extraction("How are the scope 3 emissions reported?", llm, ["English"]))

    assert "translation" not in llm.prompt


def test_without_corpus_languages_nothing_is_translated():
    llm = FakeLLM("x")
    asyncio.run(keyword_extraction("What is a surrogate key?", llm))

    assert "translation" not in llm.prompt


# ─── the agent ────────────────────────────────────────────────────────────────


class RecordingRetriever:
    def __init__(self) -> None:
        self.top_k = 5
        self.queries: list[str] = []

    async def retrieve(self, query, filters=None):
        self.queries.append(query)
        return []


class UtilityLLM:
    """Answers the keyword step with terms and query expansion with one query."""

    async def generate(self, messages, **kwargs):
        prompt = messages[-1].content[0].text
        reply = "Ersatzschlüssel" if "List the words" in prompt else "an expanded query"
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text=reply)])

    async def generate_stream(self, messages, **kwargs):
        yield LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text='{"answer":"ok"}')])


def run(rag: RAG) -> None:
    async def drive():
        async for _ in rag.answer_stream(QueryWithContext(query="surrogate key", history=[])):
            pass

    asyncio.run(drive())


def test_the_question_carries_its_terms_and_expanded_queries_go_to_semantic_search():
    retriever = RecordingRetriever()
    rag = RAG(
        llm=UtilityLLM(),
        utility_llm=UtilityLLM(),
        retrievers=[retriever],
        system_prompt="sys",
        number_query_expansion=1,
        keyword_extraction=True,
        corpus_languages=lambda: ["German"],
    )
    run(rag)

    question = next(q for q in retriever.queries if q == "surrogate key")
    expanded = next(q for q in retriever.queries if q == "an expanded query")
    assert isinstance(question, KeywordQuery) and question.expansions == ("Ersatzschlüssel",)
    assert isinstance(expanded, SemanticOnlyQuery)


def test_without_the_step_expanded_queries_still_reach_keyword_search():
    retriever = RecordingRetriever()
    rag = RAG(
        llm=UtilityLLM(),
        utility_llm=UtilityLLM(),
        retrievers=[retriever],
        system_prompt="sys",
        number_query_expansion=1,
    )
    run(rag)

    assert not any(isinstance(q, (KeywordQuery, SemanticOnlyQuery)) for q in retriever.queries)
