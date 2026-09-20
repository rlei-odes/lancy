"""Intent: the reranker LLM is called once per question, over one merged pool.

Query expansion and HyDE widen recall by asking the same question several ways.
`RAG.answer_stream` used to hand each variant to the retriever separately, and
because the reranker *is* a retriever, every variant triggered its own LLM
ranking call — then the already-ranked lists were fused. Three consequences:

  - N LLM calls where one suffices (cost and latency, and N times the chance
    of the malformed-JSON fallback that motivated this work);
  - each variant ranked in isolation, so the model never compares a chunk found
    only by HyDE against one found only by the original query;
  - `last_rerank_stats` is instance state written by every concurrent call, so
    the stats the UI shows come from whichever finished last.

The contract: retrieve per variant, fuse into one candidate pool, rank that
pool once — against the user's actual question, not the HyDE document, which
is an invented answer and a poor relevance target.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

import pytest

from conversational_toolkit.agents.base import QueryWithContext
from conversational_toolkit.agents.rag import RAG
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.retriever.reranking_retriever import RerankingRetriever
from conversational_toolkit.vectorstores.base import ChunkRecord

ANSWER_JSON = '{"answer":"ok","used_sources_id":[],"follow_up_questions":[]}'


def chunk(chunk_id: str) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id, title=chunk_id, content=f"content of {chunk_id}",
        mime_type="text/plain", metadata={"source_file": f"{chunk_id}.pdf"}, embedding=[],
    )


class FakeBaseRetriever:
    """Returns a different chunk set per query, so a merge is observable."""

    def __init__(self, by_query: dict, top_k: int = 10) -> None:
        self.by_query = by_query
        self.top_k = top_k
        self.queries_seen: list[str] = []

    async def retrieve(self, query: str, filters=None):
        self.queries_seen.append(query)
        ids = self.by_query.get(query, self.by_query.get("*", []))
        return [chunk(i) for i in ids]


class CountingLLM:
    """Counts generate() calls and records the prompts it was given."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def generate(self, messages, **kwargs):
        self.prompts.append(
            " ".join(c.text or "" for m in messages for c in m.content if c.type == "text")
        )
        return LLMMessage(
            role=Roles.ASSISTANT, content=[MessageContent(type="text", text=self.reply)]
        )

    async def generate_stream(self, messages, **kwargs):
        self.prompts.append("stream")
        yield LLMMessage(
            role=Roles.ASSISTANT, content=[MessageContent(type="text", text=ANSWER_JSON)]
        )


class UtilityLLM(CountingLLM):
    """Answers whatever preprocessing asks: expansion lines, then a HyDE blurb."""

    def __init__(self) -> None:
        super().__init__("")

    async def generate(self, messages, **kwargs):
        text = " ".join(c.text or "" for m in messages for c in m.content if c.type == "text")
        self.prompts.append(text)
        reply = "expanded query one" if "search queries" in text else "hyde document"
        return LLMMessage(
            role=Roles.ASSISTANT, content=[MessageContent(type="text", text=reply)]
        )


def build(qe: int, hyde: bool, by_query=None, pool: int = 10, top_k: int = 3):
    base = FakeBaseRetriever(
        by_query or {"*": ["a", "b", "c", "d"]}, top_k=pool
    )
    rerank_llm = CountingLLM('{"ranking": [0, 1, 2, 3]}')
    reranker = RerankingRetriever(base, rerank_llm, top_k=top_k)
    agent = RAG(
        llm=CountingLLM(ANSWER_JSON),
        utility_llm=UtilityLLM(),
        retrievers=[reranker],
        system_prompt="sys",
        number_query_expansion=qe,
        enable_hyde=hyde,
    )
    return agent, base, rerank_llm


def run(agent) -> None:
    async def drive():
        async for _ in agent.answer_stream(
            QueryWithContext(query="the original question", history=[])
        ):
            pass

    asyncio.run(drive())


# ─── one call, whatever the preprocessing ─────────────────────────────────────


@pytest.mark.parametrize(
    "qe,hyde,variants",
    [(0, False, 1), (0, True, 2), (1, False, 2), (1, True, 3)],
    ids=["baseline", "hyde", "expansion", "hyde+expansion"],
)
def test_the_reranker_llm_is_called_exactly_once(qe, hyde, variants):
    agent, base, rerank_llm = build(qe, hyde)

    run(agent)

    assert len(base.queries_seen) == variants  # retrieval still runs per variant
    assert len(rerank_llm.prompts) == 1        # ranking does not


@pytest.mark.parametrize(
    "qe,hyde", [(0, False), (0, True), (1, False), (1, True)],
    ids=["baseline", "hyde", "expansion", "hyde+expansion"],
)
def test_the_ranking_is_judged_against_the_users_question(qe, hyde):
    """Ranking against the HyDE document would score chunks on an invented answer."""
    agent, _, rerank_llm = build(qe, hyde)

    run(agent)

    assert "Query: the original question" in rerank_llm.prompts[0]


# ─── the pool the reranker sees is the merged one ─────────────────────────────


def test_chunks_found_only_by_one_variant_reach_the_reranker():
    """The point of expansion: a chunk only HyDE surfaces must still compete."""
    agent, _, rerank_llm = build(
        qe=0, hyde=True,
        by_query={"the original question": ["a", "b"], "hyde document": ["c", "d"]},
    )

    run(agent)

    prompt = rerank_llm.prompts[0]
    assert all(f"content of {c}" in prompt for c in ["a", "b", "c", "d"])


def test_the_merged_pool_is_capped_at_the_candidate_pool_size():
    """Unbounded merging would grow the rerank prompt with every variant."""
    agent, _, rerank_llm = build(
        qe=0, hyde=True, pool=3,
        by_query={"the original question": ["a", "b", "c"], "hyde document": ["d", "e", "f"]},
    )

    run(agent)

    listed = sum(
        f"content of {c}" in rerank_llm.prompts[0] for c in ["a", "b", "c", "d", "e", "f"]
    )
    assert listed == 3


def test_a_duplicate_hit_is_merged_not_listed_twice():
    agent, _, rerank_llm = build(
        qe=0, hyde=True,
        by_query={"the original question": ["a", "b"], "hyde document": ["b", "c"]},
    )

    run(agent)

    assert rerank_llm.prompts[0].count("content of b") == 1


def test_the_stats_describe_the_single_merged_call():
    """With one call there is no last-write-wins ambiguity left to report."""
    agent, _, _ = build(
        qe=0, hyde=True,
        by_query={"the original question": ["a", "b"], "hyde document": ["c", "d"]},
    )
    reranker = agent.retrievers[0]

    run(agent)

    assert reranker.last_rerank_stats["candidates"] == 4
