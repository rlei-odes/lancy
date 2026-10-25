"""Intent: generated search text is written in a language the corpus has.

HyDE and query expansion produce text that is embedded and matched against the
corpus, so they work best in the corpus language — which nothing told them.

The contract:
  - ingestion detects each document's language and the KB counts documents per
    language;
  - a language counts as a corpus language once it holds 10 % of the documents,
    so two Italian documents in an 800-document KB earn nothing;
  - an admin-set list replaces the detected one;
  - HyDE and query expansion write in the query's language when the corpus has
    it, otherwise in the corpus's main language; with nothing known, in the
    query's language as before;
  - documents indexed before detection existed get their language from the
    store on the next reindex, without re-chunking.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio
import json

from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.utils.retriever import hyde_expansion, language_rule, query_expansion
from conversational_toolkit.vectorstores.base import ChunkRecord
from lancy.kb_stats import backfill_languages, language_distribution, write_kb_stats
from lancy.language import corpus_languages, detect, detect_document

GERMAN = "Der Ersatzschlüssel wird in der Datenbank als technischer Schlüssel verwendet und ist immer eindeutig."
ENGLISH = "A surrogate key is a unique identifier for an entity, used by the database instead of a natural key."


# ─── detection ────────────────────────────────────────────────────────────────


def test_a_documents_language_is_detected():
    assert detect(GERMAN) == "de"
    assert detect(ENGLISH) == "en"


def test_too_little_text_stays_unlabelled():
    """A bare title is a guess, not a measurement."""
    assert detect("Kapitel 3") is None


def test_a_document_is_judged_on_its_chunks_together():
    assert detect_document(["Kapitel 3", GERMAN]) == "de"


# ─── which languages count ────────────────────────────────────────────────────


def test_a_stray_language_is_ignored():
    assert corpus_languages({"de": 700, "en": 98, "it": 2}) == ["de", "en"]


def test_the_main_language_comes_first():
    assert corpus_languages({"en": 30, "de": 70}) == ["de", "en"]


def test_an_admin_list_replaces_the_detected_one():
    assert corpus_languages({"de": 700}, override=["fr", "de"]) == ["fr", "de"]


def test_nothing_detected_means_no_corpus_language():
    assert corpus_languages({}) == []


# ─── the prompts ──────────────────────────────────────────────────────────────


class PromptLLM:
    def __init__(self, reply: str = "a query") -> None:
        self.reply = reply
        self.prompt = ""

    async def generate(self, messages, max_tokens=None, **kwargs):
        self.prompt = " ".join(c.text or "" for m in messages for c in m.content if c.type == "text")
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text=self.reply)])


def test_without_corpus_languages_text_follows_the_query():
    assert "same language as the original query" in language_rule([])


def test_a_single_language_corpus_gets_that_language():
    llm = PromptLLM()
    asyncio.run(query_expansion("What is a surrogate key?", llm, 1, languages=["German"]))

    assert "Write in German" in llm.prompt


def test_a_mixed_corpus_keeps_the_query_language_it_has_and_falls_back_to_the_main_one():
    rule = language_rule(["German", "English"])

    assert "German, English" in rule
    assert "otherwise in German" in rule


class MessagesLLM(PromptLLM):
    async def generate(self, messages, max_tokens=None, **kwargs):
        self.messages = messages
        return await super().generate(messages, max_tokens=max_tokens)


def test_hyde_writes_in_the_corpus_language_and_says_so_next_to_the_query():
    """In the system prompt alone, the rule lost to the query's language."""
    llm = MessagesLLM()
    asyncio.run(hyde_expansion("Wie werden Scope-3-Emissionen berichtet?", llm, languages=["English"]))

    assert "Write in English" in llm.messages[-1].content[0].text


# ─── counting documents ───────────────────────────────────────────────────────


def chunk(text: str, file: str, h: str, language: str | None) -> Chunk:
    metadata = {"source_file": file, "file_hash": h}
    if language:
        metadata["language"] = language
    return Chunk(title="", content=text, mime_type="text/plain", metadata=metadata)


def test_ingestion_stats_count_documents_per_language(tmp_path):
    chunks = [
        chunk(GERMAN, "a.pdf", "h1", "de"),
        chunk(GERMAN, "a.pdf", "h1", "de"),  # a second chunk of the same document
        chunk(GERMAN, "b.pdf", "h2", "de"),
        chunk(ENGLISH, "c.pdf", "h3", "en"),
        chunk("x", "d.pdf", "h4", None),  # undetectable
    ]
    write_kb_stats(tmp_path, "kb", chunks, True, 4, 0, 0)

    stats = json.loads((tmp_path / "kb_stats_kb.json").read_text())
    assert stats["language_distribution"] == {"de": 2, "en": 1}


def test_a_document_in_several_versions_counts_once():
    cpd = {
        "h1": {"source_file": "a.pdf", "language": "de"},
        "h2": {"source_file": "a.pdf", "language": "de"},
    }
    assert language_distribution(cpd) == {"de": 1}


class Store:
    def __init__(self, chunks: list[ChunkRecord]) -> None:
        self.chunks = chunks
        self.filters = None

    async def get_chunks_by_filter(self, filters=None):
        self.filters = filters
        wanted = set(filters["file_hash"])
        return [c for c in self.chunks if c.metadata["file_hash"] in wanted]


def stored(text: str, h: str) -> ChunkRecord:
    return ChunkRecord(id=h, title="", content=text, mime_type="text/plain", metadata={"file_hash": h}, embedding=[])


def test_documents_indexed_before_detection_get_their_language_from_the_store(tmp_path):
    (tmp_path / "kb_stats_kb.json").write_text(
        json.dumps(
            {
                "chunks_per_document": {
                    "old": {"source_file": "old.pdf", "chunk_count": 1},  # no language key: never detected
                    "new": {"source_file": "new.pdf", "chunk_count": 1, "language": "en"},
                }
            }
        )
    )
    store = Store([stored(GERMAN, "old"), stored(ENGLISH, "new")])

    assert asyncio.run(backfill_languages(tmp_path, "kb", store)) == 1

    stats = json.loads((tmp_path / "kb_stats_kb.json").read_text())
    assert store.filters == {"file_hash": ["old"]}  # only the undetected document is read
    assert stats["language_distribution"] == {"de": 1, "en": 1}


def test_a_kb_whose_documents_all_carry_a_language_is_not_read(tmp_path):
    (tmp_path / "kb_stats_kb.json").write_text(
        json.dumps({"chunks_per_document": {"h": {"source_file": "a.pdf", "language": "de"}}})
    )
    store = Store([])

    assert asyncio.run(backfill_languages(tmp_path, "kb", store)) == 0
    assert store.filters is None
