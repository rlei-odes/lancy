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
from conversational_toolkit.retriever.text_analysis import (
    analyze_document,
    analyze_query,
    index_document,
    matched_words,
    query_words,
)
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


# ─── English terms inside other-language chunks ───────────────────────────────
#
# The German stemmer leaves "keys" as "keys"; the question's "key" stems to "key" in
# every language. A German chunk also indexes each word's English stem, so the plural
# of an English loanword is found. The extra stems do not count as chunk length, and a
# question word scores once however many of its forms a chunk carries.


def test_an_english_plural_inside_a_german_chunk_matches():
    glossary = record("glossary", "Die Trennung von Business Keys und technischen Schlüsseln im Data Vault")

    assert search("key", glossary) == ["glossary"]


def test_the_probe_highlights_the_english_plural_in_a_german_chunk():
    text = "Die Trennung von Business Keys und technischen Schlüsseln"

    assert "keys" in matched_words(text, analyze_query("key"))


def test_english_stems_do_not_make_a_chunk_longer():
    terms, length = index_document("Die Trennung von Business Keys und technischen Schlüsseln")

    assert "key" in terms and "keys" in terms
    assert length == len(analyze_document("Die Trennung von Business Keys und technischen Schlüsseln"))


def test_a_question_word_scores_once_by_its_best_form():
    """A chunk carrying both 'keys' and 'key' must not score the word twice."""
    retriever = BM25Retriever(Store([record("de", "Die Business Keys der Tabelle"), *FILLER]), top_k=10)
    asyncio.run(retriever.retrieve("key"))  # builds the index

    forms = query_words("keys")[0]
    one_word = retriever._score([forms])
    per_form = [retriever._bm25.get_scores([t]) for t in forms]

    assert one_word[0] == max(s[0] for s in per_form)


# ─── length normalisation ─────────────────────────────────────────────────────


def test_without_length_normalisation_one_mention_in_a_long_chunk_competes():
    """b = 0 ignores length; b = 1 scales a term fully by it. The setting sits between."""
    long_score = {}
    for b in (0.0, 0.6, 1.0):
        retriever = BM25Retriever(
            Store([record("long", "Weitere Begriffe folgen hier. " * 60 + "surrogate key"), *FILLER]), top_k=10, b=b
        )
        long_score[b] = asyncio.run(retriever.retrieve("surrogate key"))[0].score

    assert long_score[0.0] > long_score[0.6] > long_score[1.0]
