"""Intent: every chunk sent to the LLM is kept with a record of how it was found.

The chat UI shows, per answer, which documents the search turned up and which
of them the LLM cited. That needs two things the pipeline used to throw away:

  - provenance: which search method found a chunk (semantic, BM25) and at what
    rank, and where the reranker moved it from. Each fusion step used to rebuild
    the chunk from its fused score alone.
  - the uncited chunks: only the ones in `used_sources_id` were saved.

The contract:
  - `ChunkMatch.ranks` maps a method to the chunk's best 1-based rank in it;
    fusion keeps the best rank per method, the reranker adds `pre_rerank`.
  - `AgentAnswer.retrieved` holds every chunk sent to the LLM in retrieval mode.
  - The controller saves each retrieved chunk once, flagged `cited` or not.
    The API's `sources` stays cited-only and unchanged in shape; `retrieved`
    lists them all, in the order they went to the LLM.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

from conversational_toolkit.agents.base import AgentAnswer, QueryWithContext
from conversational_toolkit.agents.rag import RAG
from conversational_toolkit.conversation_database.controller import (
    ConversationalToolkitController,
    MessageInput,
)
from conversational_toolkit.conversation_database.in_memory.conversation import InMemoryConversationDatabase
from conversational_toolkit.conversation_database.in_memory.message import InMemoryMessageDatabase
from conversational_toolkit.conversation_database.in_memory.reactions import InMemoryReactionDatabase
from conversational_toolkit.conversation_database.in_memory.source import InMemorySourceDatabase
from conversational_toolkit.conversation_database.in_memory.user import InMemoryUserDatabase
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.retriever.bm25_retriever import BM25Retriever
from conversational_toolkit.retriever.hybrid_retriever import HybridRetriever
from conversational_toolkit.retriever.reranking_retriever import RerankingRetriever
from conversational_toolkit.retriever.vectorstore_retriever import VectorStoreRetriever
from conversational_toolkit.utils.retriever import reciprocal_rank_fusion
from conversational_toolkit.vectorstores.base import ChunkMatch, ChunkRecord


def record(chunk_id: str, content: str | None = None) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        title=f"title {chunk_id}",
        content=content or f"content of {chunk_id}",
        mime_type="text/plain",
        metadata={"source_file": f"{chunk_id}.pdf"},
        embedding=[],
    )


def match(chunk_id: str, **ranks: int) -> ChunkMatch:
    return ChunkMatch(**record(chunk_id).model_dump(), score=0.0, ranks=ranks)


class SemanticStore:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    async def get_chunks_by_embedding(self, embedding, top_k, filters=None):
        return [ChunkMatch(**record(i).model_dump(), score=1.0) for i in self.ids[:top_k]]


class KeywordStore:
    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts

    async def get_chunks_by_filter(self, filters=None):
        return [record(i, content=text) for i, text in self.texts.items()]


class FakeEmbeddings:
    async def get_embeddings(self, text):
        return [[0.0]]


class FixedRetriever:
    """Returns fixed lists per query, so fusion is observable."""

    def __init__(self, by_query: dict[str, list[ChunkMatch]], top_k: int = 10) -> None:
        self.by_query = by_query
        self.top_k = top_k

    async def retrieve(self, query, filters=None):
        return self.by_query.get(query, self.by_query.get("*", []))


def run(coro):
    return asyncio.run(coro)


# ─── the leaf retrievers name themselves ──────────────────────────────────────


def test_semantic_search_records_its_rank():
    retriever = VectorStoreRetriever(FakeEmbeddings(), SemanticStore(["a", "b"]), top_k=2)

    results = run(retriever.retrieve("q"))

    assert [r.ranks for r in results] == [{"semantic": 1}, {"semantic": 2}]


def test_keyword_search_records_rank_only_for_actual_term_matches():
    """BM25 fills its top_k with zero-score chunks; those share no term with the query."""
    # Filler keeps "alpha" rare: in a tiny corpus its IDF would drop to zero.
    filler = {f"f{i}": f"filler text {i}" for i in range(6)}
    store = KeywordStore({"a": "alpha beta", "b": "gamma delta", "c": "alpha alpha", **filler})
    retriever = BM25Retriever(store, top_k=3)

    results = run(retriever.retrieve("alpha"))

    by_id = {r.id: r.ranks for r in results}
    assert by_id["c"] == {"bm25": 1}
    assert by_id["a"] == {"bm25": 2}
    assert by_id["b"] == {}


# ─── fusion keeps every method's best rank ────────────────────────────────────


def test_hybrid_fusion_keeps_the_rank_from_each_method():
    hybrid = HybridRetriever(
        retrievers=[
            FixedRetriever({"*": [match("a", semantic=1), match("b", semantic=2)]}),
            FixedRetriever({"*": [match("b", bm25=1), match("c", bm25=2)]}),
        ],
        top_k=10,
    )

    results = run(hybrid.retrieve("q"))

    by_id = {r.id: r.ranks for r in results}
    assert by_id == {"a": {"semantic": 1}, "b": {"semantic": 2, "bm25": 1}, "c": {"bm25": 2}}


def test_query_variant_fusion_keeps_the_best_rank_per_method():
    fused = reciprocal_rank_fusion(
        [
            [match("a", semantic=3, bm25=1)],
            [match("a", semantic=1)],
        ]
    )

    assert fused[0].ranks == {"semantic": 1, "bm25": 1}


def test_fusion_of_plain_records_still_works():
    """Not every caller hands in matches; records carry no ranks to merge."""
    fused = reciprocal_rank_fusion([[record("a")], [record("a"), record("b")]])

    assert [c.id for c in fused] == ["a", "b"]


def test_the_reranker_records_where_a_chunk_came_from():
    class RankLLM:
        async def generate(self, messages, **kwargs):
            return LLMMessage(
                role=Roles.ASSISTANT, content=[MessageContent(type="text", text='{"ranking": [2, 0, 1]}')]
            )

    base = FixedRetriever({"*": [match("a", semantic=1), match("b", semantic=2), match("c", bm25=1)]})
    reranker = RerankingRetriever(base, RankLLM(), top_k=2)

    results = run(reranker.retrieve("q"))

    assert [(r.id, r.ranks) for r in results] == [
        ("c", {"bm25": 1, "pre_rerank": 3}),
        ("a", {"semantic": 1, "pre_rerank": 1}),
    ]


class RankLLM:
    async def generate(self, messages, **kwargs):
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text='{"ranking": [2, 0, 1]}')])


def test_the_reranker_hands_back_what_it_discarded():
    """The candidates that missed the cut are part of what the search found."""
    base = FixedRetriever({"*": [match("a", semantic=1), match("b", semantic=2), match("c", bm25=1)]})
    reranker = RerankingRetriever(base, RankLLM(), top_k=2)

    kept, discarded = run(reranker.rank_pool(["q"], rank_query="q"))

    assert [c.id for c in kept] == ["c", "a"]
    assert [(c.id, c.ranks) for c in discarded] == [("b", {"semantic": 2, "pre_rerank": 2})]


# ─── the agent hands back everything it sent ──────────────────────────────────

ANSWER_JSON = '{"answer":"ok","used_sources_id":["b"],"follow_up_questions":[]}'


class StreamLLM:
    async def generate(self, messages, **kwargs):
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text="")])

    async def generate_stream(self, messages, **kwargs):
        yield LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text=ANSWER_JSON)])


class ChunkStore:
    async def get_chunks_by_filter(self, filters=None):
        return [record("x")]


def agent() -> RAG:
    return RAG(
        llm=StreamLLM(),
        utility_llm=StreamLLM(),
        retrievers=[FixedRetriever({"*": [match("a", semantic=1), match("b", bm25=1)]})],
        system_prompt="sys",
        vector_store=ChunkStore(),
    )


def last_answer(query: QueryWithContext) -> AgentAnswer:
    async def drive():
        answer = None
        async for answer in agent().answer_stream(query):
            pass
        return answer

    return run(drive())


def test_the_answer_carries_every_chunk_sent_to_the_llm():
    answer = last_answer(QueryWithContext(query="q", history=[]))

    assert [c.id for c in answer.retrieved] == ["a", "b"]


def test_the_answer_carries_what_the_reranker_discarded():
    base = FixedRetriever({"*": [match("a", semantic=1), match("b", semantic=2), match("c", bm25=1)]})
    rag = RAG(
        llm=StreamLLM(),
        utility_llm=StreamLLM(),
        retrievers=[RerankingRetriever(base, RankLLM(), top_k=2)],
        system_prompt="sys",
    )

    async def drive():
        answer = None
        async for answer in rag.answer_stream(QueryWithContext(query="q", history=[])):
            pass
        return answer

    answer = run(drive())

    assert [c.id for c in answer.retrieved] == ["c", "a"]
    assert [c.id for c in answer.discarded] == ["b"]


def test_without_a_reranker_nothing_is_discarded():
    assert last_answer(QueryWithContext(query="q", history=[])).discarded == []


def test_chat_only_and_expanded_context_report_no_retrieval():
    """Expanded context sends whole documents the user picked; that is not a search result."""
    assert last_answer(QueryWithContext(query="q", history=[], chat_only=True)).retrieved == []
    assert last_answer(QueryWithContext(query="q", history=[], expand_context=["x.pdf"])).retrieved == []


# ─── saved once, served as cited and as retrieved ─────────────────────────────


class CitingAgent:
    """Stands in for CustomRAG: cites `b` of the three chunks it retrieved."""

    def __init__(
        self, retrieved: list[ChunkMatch], cited: list[str], discarded: list[ChunkMatch] | None = None
    ) -> None:
        self.retrieved = retrieved
        self.cited = cited
        self.discarded = discarded or []

    async def answer_stream(self, query_with_context):
        yield AgentAnswer(
            content=[MessageContent(type="text", text="ok")],
            role=Roles.ASSISTANT,
            sources=[c for c in self.retrieved if c.id in self.cited],
            retrieved=self.retrieved,
            discarded=self.discarded,
        )


def controller(tmp_path, agent) -> ConversationalToolkitController:
    return ConversationalToolkitController(
        conversation_db=InMemoryConversationDatabase(str(tmp_path / "c.json")),
        message_db=InMemoryMessageDatabase(str(tmp_path / "m.json")),
        reaction_db=InMemoryReactionDatabase(str(tmp_path / "r.json")),
        source_db=InMemorySourceDatabase(str(tmp_path / "s.json")),
        user_db=InMemoryUserDatabase(str(tmp_path / "u.json")),
        agent=agent,
    )


def send(ctrl: ConversationalToolkitController):
    async def drive():
        final = None
        async for message in ctrl.process_new_message_stream(MessageInput(content="q"), user_id="u"):
            if not isinstance(message, bytes):
                final = message
        return final

    return run(drive())


RETRIEVED = [match("a", semantic=1), match("b", semantic=2, bm25=1, pre_rerank=4), match("c", bm25=2)]


def test_the_streamed_message_keeps_sources_cited_only(tmp_path):
    final = send(controller(tmp_path, CitingAgent(RETRIEVED, cited=["b"])))

    assert [s.metadata["source_file"] for s in final.sources] == ["b.pdf"]
    assert "_retrieval" not in final.sources[0].metadata


def test_the_streamed_message_lists_everything_retrieved_with_provenance(tmp_path):
    final = send(controller(tmp_path, CitingAgent(RETRIEVED, cited=["b"])))

    info = [s.metadata["_retrieval"] for s in final.retrieved]
    assert [i["chunk_id"] for i in info] == ["a", "b", "c"]
    assert [i["cited"] for i in info] == [False, True, False]
    assert info[1]["ranks"] == {"semantic": 2, "bm25": 1, "pre_rerank": 4}
    assert info[1]["title"] == "title b"


def test_each_chunk_is_saved_once(tmp_path):
    ctrl = controller(tmp_path, CitingAgent(RETRIEVED, cited=["b"]))

    final = send(ctrl)

    stored = run(ctrl.source_db.get_sources_by_message_id(final.id))
    assert len(stored) == 3


def test_reloading_the_conversation_returns_the_same_split(tmp_path):
    ctrl = controller(tmp_path, CitingAgent(RETRIEVED, cited=["b"]))
    final = send(ctrl)

    for messages in (
        run(ctrl.get_conversation_by_id(final.conversation_id)).messages,
        run(ctrl.get_messages_by_conversation_id(final.conversation_id)),
    ):
        reloaded = next(m for m in messages if m.id == final.id)
        assert [s.metadata["source_file"] for s in reloaded.sources] == ["b.pdf"]
        assert [s.metadata["_retrieval"]["chunk_id"] for s in reloaded.retrieved] == ["a", "b", "c"]


def test_discarded_chunks_follow_the_sent_ones_and_are_never_sources(tmp_path):
    ctrl = controller(tmp_path, CitingAgent(RETRIEVED, cited=["b"], discarded=[match("d", semantic=5, pre_rerank=2)]))

    final = send(ctrl)

    info = [s.metadata["_retrieval"] for s in final.retrieved]
    assert [(i["chunk_id"], i["sent"], i["position"]) for i in info] == [
        ("a", True, 1),
        ("b", True, 2),
        ("c", True, 3),
        ("d", False, 4),
    ]
    assert [s.metadata["source_file"] for s in final.sources] == ["b.pdf"]


def stream(ctrl: ConversationalToolkitController) -> list:
    async def drive():
        return [
            m
            async for m in ctrl.process_new_message_stream(MessageInput(content="q"), user_id="u")
            if not isinstance(m, bytes)
        ]

    return run(drive())


class TwoChunkAgent(CitingAgent):
    async def answer_stream(self, query_with_context):
        for text in ("o", "ok"):
            yield AgentAnswer(
                content=[MessageContent(type="text", text=text)],
                role=Roles.ASSISTANT,
                sources=[c for c in self.retrieved if c.id in self.cited],
                retrieved=self.retrieved,
                discarded=self.discarded,
            )


def test_the_first_streamed_chunk_already_lists_what_was_retrieved(tmp_path):
    """The panel can show the documents while the answer is still being written."""
    messages = stream(controller(tmp_path, TwoChunkAgent(RETRIEVED, cited=["b"], discarded=[match("d", semantic=5)])))
    first, second, final = messages

    info = [s.metadata["_retrieval"] for s in first.retrieved]
    assert [(i["chunk_id"], i["sent"]) for i in info] == [("a", True), ("b", True), ("c", True), ("d", False)]
    assert not any(i["cited"] for i in info)  # citations are only known once the answer is complete
    assert second.retrieved == []  # sent once, not with every chunk
    assert [s.metadata["_retrieval"]["cited"] for s in final.retrieved] == [False, True, False, False]


def test_an_agent_without_retrieval_saves_its_sources_as_before(tmp_path):
    """Chat-only, expanded context, and messages saved before provenance existed."""

    class PlainAgent:
        async def answer_stream(self, query_with_context):
            yield AgentAnswer(
                content=[MessageContent(type="text", text="ok")], role=Roles.ASSISTANT, sources=[record("a")]
            )

    final = send(controller(tmp_path, PlainAgent()))

    assert [s.metadata["source_file"] for s in final.sources] == ["a.pdf"]
    assert final.retrieved == []
