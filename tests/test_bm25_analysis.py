"""Intent: keyword search matches words, not filler, across inflection and language.

BM25 used to lowercase the text and split it into words, nothing more. So
"What is a surrogate key?" searched for "what", "is" and "a" too, "key" never
matched "keys", and "Schlüssel" never matched "Schlüsseln".

The analysis now drops stop words and reduces words to their stems, the same
way for chunks and questions. The corpus mixes languages — German course
material says "surrogate keys" in English — so:
  - each chunk is stemmed in its own language, detected from its stop words;
  - a question word is expanded to its stem in every supported language, plus
    the word itself, so it matches a chunk in any of them;
  - stop words are dropped in the question's own language only: "war" is a
    German stop word but an English noun.

HyDE writes a hypothetical answer to widen semantic search. As a keyword query
its hundred-odd words are noise, so keyword search skips it.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

from conversational_toolkit.agents.base import QueryWithContext
from conversational_toolkit.agents.rag import RAG
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.retriever.base import SemanticOnlyQuery
from conversational_toolkit.retriever.bm25_retriever import BM25Retriever
from conversational_toolkit.retriever.text_analysis import analyze_document, analyze_query
from conversational_toolkit.vectorstores.base import ChunkRecord

# ─── analysis ─────────────────────────────────────────────────────────────────


def test_filler_words_are_dropped_from_the_question():
    terms = analyze_query("What is a surrogate key?")

    assert not {"what", "is", "a"} & set(terms)
    assert "key" in terms and "surrogate" in terms


def test_stop_words_are_dropped_in_the_questions_language_only():
    assert "war" in analyze_query("What are the causes of war")  # an English noun, a German stop word


def test_a_question_of_only_stop_words_keeps_its_words():
    """Searching for nothing finds nothing; the words are all there is to go on."""
    assert analyze_query("what is it") != []


def test_a_chunk_is_stemmed_in_its_own_language():
    german = analyze_document("Die Schlüsseln werden in der Tabelle gespeichert")
    english = analyze_document("The keys are stored in the table")

    assert "schlussel" in german and "die" not in german
    assert "key" in english and "the" not in english


# ─── retrieval ────────────────────────────────────────────────────────────────


def record(chunk_id: str, content: str) -> ChunkRecord:
    return ChunkRecord(id=chunk_id, title="", content=content, mime_type="text/plain", metadata={}, embedding=[])


FILLER = [
    record(f"f{i}", text)
    for i, text in enumerate(
        [
            "Der Kunde bestellt ein Produkt und bezahlt die Rechnung",
            "The server restarts every night after the backup job",
            "Il cliente riceve la fattura per posta",
            "Le client reçoit la facture par courrier",
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


def test_singular_finds_plural():
    assert search("surrogate key", record("doc", "Every dimension table needs surrogate keys")) == ["doc"]


def test_an_english_term_inside_a_german_chunk_is_still_found():
    """The case that motivated this: the glossary is German, the term English."""
    glossary = record(
        "glossary", "Trennung von Business Keys und technischen Schlüsseln (surrogate keys) im Data Vault"
    )

    assert search("What is a surrogate key?", glossary) == ["glossary"]


def test_german_inflection_matches():
    assert search("Schlüssel", record("doc", "Die Schlüsseln der Hub-Tabellen werden getrennt verwaltet")) == ["doc"]


def test_a_filler_word_alone_matches_nothing():
    assert search("What is a", record("doc", "What is a good name? It is a question.")) == []


def test_keyword_search_ignores_a_semantic_only_query():
    retriever = BM25Retriever(Store(FILLER), top_k=10)

    assert asyncio.run(retriever.retrieve(SemanticOnlyQuery("The server restarts every night"))) == []


# ─── HyDE stays out of keyword search ─────────────────────────────────────────


class RecordingRetriever:
    def __init__(self) -> None:
        self.top_k = 5
        self.queries: list[str] = []

    async def retrieve(self, query, filters=None):
        self.queries.append(query)
        return []


class UtilityLLM:
    async def generate(self, messages, **kwargs):
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text="a hypothetical answer")])

    async def generate_stream(self, messages, **kwargs):
        yield LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text='{"answer":"ok"}')])


def test_the_hyde_text_is_marked_semantic_only():
    retriever = RecordingRetriever()
    rag = RAG(llm=UtilityLLM(), utility_llm=UtilityLLM(), retrievers=[retriever], system_prompt="sys", enable_hyde=True)

    async def drive():
        async for _ in rag.answer_stream(QueryWithContext(query="the question", history=[])):
            pass

    asyncio.run(drive())

    hyde = [q for q in retriever.queries if q == "a hypothetical answer"]
    assert hyde and all(isinstance(q, SemanticOnlyQuery) for q in hyde)
    assert not isinstance(next(q for q in retriever.queries if q == "the question"), SemanticOnlyQuery)
