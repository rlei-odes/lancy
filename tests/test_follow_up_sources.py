"""Intent: a follow-up question is answered with the chunks the previous answer cited.

"When was that?" is about the document the last answer quoted. The rewritten
question ("When did X happen?") goes to a fresh search that need not find that
chunk again, and the history holds only the answer's prose, not the chunk's text.
So a date in the chunk but not in the answer was lost.

The contract:
  - The controller hands the agent the previous answer's cited chunks
    (`QueryWithContext.prior_sources`), with their original chunk ids. Uncited
    retrieved chunks are not carried: the answer judged them irrelevant.
  - The RAG agent puts them first in <sources>, past search and reranking; a
    chunk the new search finds again is sent once.
  - Chat-only and expanded-context questions ignore them: the user chose what to
    answer from.
  - The history sent with a question is capped at MAX_HISTORY_MESSAGES.
  - The query rewriter reads the history as plain text, and falls back to the
    user's own words when it returns nothing.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

from conversational_toolkit.agents.base import AgentAnswer, QueryWithContext
from conversational_toolkit.agents.rag import RAG, carry_prior_sources
from conversational_toolkit.conversation_database.controller import (
    MAX_HISTORY_MESSAGES,
    ConversationalToolkitController,
    MessageInput,
)
from conversational_toolkit.conversation_database.in_memory.conversation import InMemoryConversationDatabase
from conversational_toolkit.conversation_database.in_memory.message import InMemoryMessageDatabase
from conversational_toolkit.conversation_database.in_memory.reactions import InMemoryReactionDatabase
from conversational_toolkit.conversation_database.in_memory.source import InMemorySourceDatabase
from conversational_toolkit.conversation_database.in_memory.user import InMemoryUserDatabase
from conversational_toolkit.llms.base import LLMMessage, MessageContent, Roles
from conversational_toolkit.utils.retriever import make_query_standalone
from conversational_toolkit.vectorstores.base import ChunkMatch, ChunkRecord


def run(coro):
    return asyncio.run(coro)


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


def text_of(message: LLMMessage) -> str:
    return "".join(c.text or "" for c in message.content)


# ─── merging carried and found chunks ─────────────────────────────────────────


def test_carried_chunks_come_first():
    assert [c.id for c in carry_prior_sources([record("p")], [record("a"), record("b")])] == ["p", "a", "b"]


def test_a_chunk_found_again_is_sent_once():
    assert [c.id for c in carry_prior_sources([record("b")], [record("a"), record("b")])] == ["b", "a"]


def test_a_carried_chunk_without_its_id_is_recognised_by_its_text():
    """Answers saved without a search record keep a stored id, not the chunk's."""
    prior = [record("stored-uid", content="content of a")]

    assert [c.id for c in carry_prior_sources(prior, [record("a"), record("b")])] == ["stored-uid", "b"]


def test_without_prior_sources_the_search_result_is_unchanged():
    assert [c.id for c in carry_prior_sources([], [record("a")])] == ["a"]


# ─── the agent sends them to the LLM ──────────────────────────────────────────

ANSWER_JSON = '{"answer":"ok","used_sources_id":[],"follow_up_questions":[]}'


class RecordingLLM:
    """Rewrites to nothing (the agent keeps the user's words) and records the answer prompt."""

    def __init__(self) -> None:
        self.prompts: list[list[LLMMessage]] = []

    async def generate(self, messages, **kwargs):
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text="")])

    async def generate_stream(self, messages, **kwargs):
        self.prompts.append(messages)
        yield LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text=ANSWER_JSON)])


class FixedRetriever:
    def __init__(self, chunks: list[ChunkMatch], top_k: int = 10) -> None:
        self.chunks = chunks
        self.top_k = top_k

    async def retrieve(self, query, filters=None):
        return self.chunks


class ChunkStore:
    async def get_chunks_by_filter(self, filters=None):
        return [record("x")]


HISTORY = [
    LLMMessage(role=Roles.USER, content=[MessageContent(type="text", text="What did the audit find?")]),
    LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text="Two deviations (audit.pdf).")]),
]


def ask(query: QueryWithContext) -> tuple[AgentAnswer, RecordingLLM]:
    llm = RecordingLLM()
    rag = RAG(
        llm=llm,
        utility_llm=llm,
        retrievers=[FixedRetriever([match("a", semantic=1)])],
        system_prompt="sys",
        vector_store=ChunkStore(),
    )

    async def drive():
        answer = None
        async for answer in rag.answer_stream(query):
            pass
        return answer

    return run(drive()), llm


def test_a_follow_up_sends_the_previous_answers_chunks_to_the_llm():
    dated = record("p", content="The audit took place on 12 March 2024.")

    answer, llm = ask(QueryWithContext(query="When was that?", history=HISTORY, prior_sources=[dated]))

    context = text_of(llm.prompts[-1][-1])
    assert "12 March 2024" in context
    assert context.index('id="p"') < context.index('id="a"')
    assert [c.id for c in answer.retrieved] == ["p", "a"]


def test_chat_only_and_expanded_context_ignore_prior_sources():
    dated = record("p", content="The audit took place on 12 March 2024.")

    for mode in ({"chat_only": True}, {"expand_context": ["x.pdf"]}):
        _, llm = ask(QueryWithContext(query="When was that?", history=HISTORY, prior_sources=[dated], **mode))
        assert "12 March 2024" not in text_of(llm.prompts[-1][-1])


# ─── the controller carries the cited chunks ──────────────────────────────────


class CitingAgent:
    """Turn 1 retrieves a, b, c and cites b; every turn records what it was given."""

    def __init__(self) -> None:
        self.queries: list[QueryWithContext] = []

    async def answer_stream(self, query_with_context):
        self.queries.append(query_with_context)
        retrieved = [match("a", semantic=1), match("b", semantic=2), match("c", bm25=1)]
        yield AgentAnswer(
            content=[MessageContent(type="text", text="ok")],
            role=Roles.ASSISTANT,
            sources=[c for c in retrieved if c.id == "b"],
            retrieved=retrieved,
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


def send(ctrl: ConversationalToolkitController, content: str, after=None):
    async def drive():
        final = None
        message_input = MessageInput(
            content=content,
            conversation_id=after.conversation_id if after else None,
            parent_id=after.id if after else None,
        )
        async for message in ctrl.process_new_message_stream(message_input, user_id="u"):
            if not isinstance(message, bytes):
                final = message
        return final

    return run(drive())


def test_a_first_question_has_no_prior_sources(tmp_path):
    agent = CitingAgent()

    send(controller(tmp_path, agent), "What did the audit find?")

    assert agent.queries[0].prior_sources == []


def test_a_follow_up_gets_the_cited_chunks_only_with_their_ids(tmp_path):
    agent = CitingAgent()
    ctrl = controller(tmp_path, agent)

    first = send(ctrl, "What did the audit find?")
    send(ctrl, "When was that?", after=first)

    prior = agent.queries[1].prior_sources
    assert [c.id for c in prior] == ["b"]
    assert prior[0].content == "content of b"
    assert prior[0].title == "title b"
    assert prior[0].metadata == {"source_file": "b.pdf"}
    assert prior[0].ranks == {"previous_answer": 1}


def test_a_carried_chunk_is_saved_with_the_follow_up_and_carried_again(tmp_path):
    """Cited again, it is the follow-up's source too, so the chain keeps it."""
    agent = CitingAgent()
    ctrl = controller(tmp_path, agent)

    first = send(ctrl, "What did the audit find?")
    second = send(ctrl, "When was that?", after=first)
    send(ctrl, "And who signed it?", after=second)

    assert [c.id for c in agent.queries[2].prior_sources] == ["b"]


def test_an_answer_saved_without_a_search_record_is_carried_by_its_stored_id(tmp_path):
    class PlainAgent(CitingAgent):
        async def answer_stream(self, query_with_context):
            self.queries.append(query_with_context)
            yield AgentAnswer(
                content=[MessageContent(type="text", text="ok")], role=Roles.ASSISTANT, sources=[record("a")]
            )

    agent = PlainAgent()
    ctrl = controller(tmp_path, agent)

    first = send(ctrl, "Summarise a.pdf")
    send(ctrl, "When was that?", after=first)

    prior = agent.queries[1].prior_sources
    stored = run(ctrl.source_db.get_sources_by_message_id(first.id))
    assert [c.id for c in prior] == [stored[0].id]
    assert prior[0].content == "content of a"
    assert prior[0].mime_type == "text/markdown"


def test_the_history_is_capped(tmp_path):
    agent = CitingAgent()
    ctrl = controller(tmp_path, agent)

    last = None
    for i in range(MAX_HISTORY_MESSAGES):
        last = send(ctrl, f"question {i}", after=last)
    send(ctrl, "the last one", after=last)

    history = agent.queries[-1].history
    assert len(history) == MAX_HISTORY_MESSAGES
    assert text_of(history[-1]) == "ok"  # the most recent turns are the ones kept
    assert text_of(history[0]) != "question 0"


def test_the_history_keeps_reply_order_when_timestamps_tie(tmp_path, monkeypatch):
    """A question and its answer can share a millisecond; the parent chain still orders them."""
    import conversational_toolkit.conversation_database.controller as controller_module

    monkeypatch.setattr(controller_module, "get_current_timestamp", lambda: 1_000)
    agent = CitingAgent()
    ctrl = controller(tmp_path, agent)

    last = send(ctrl, "question 0")
    last = send(ctrl, "question 1", after=last)
    send(ctrl, "the last one", after=last)

    history = agent.queries[-1].history
    assert [text_of(m) for m in history] == ["question 0", "ok", "question 1", "ok"]


# ─── the rewriter reads plain text ────────────────────────────────────────────


class EchoLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.seen = ""

    async def generate(self, messages, **kwargs):
        self.seen = text_of(messages[-1])
        return LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text=self.reply)])


def test_the_rewriter_sees_the_history_as_plain_text():
    llm = EchoLLM("When did the audit take place?")

    rewritten = run(make_query_standalone(llm, HISTORY, "When was that?"))

    assert rewritten == "When did the audit take place?"
    assert "- user: What did the audit find?" in llm.seen
    assert "- assistant: Two deviations (audit.pdf)." in llm.seen
    assert "MessageContent" not in llm.seen


def test_an_empty_rewrite_keeps_the_users_question():
    assert run(make_query_standalone(EchoLLM("  "), HISTORY, "When was that?")) == "When was that?"


def test_the_rewriter_cuts_a_long_answer():
    long_answer = LLMMessage(role=Roles.ASSISTANT, content=[MessageContent(type="text", text="word " * 2000)])
    llm = EchoLLM("q")

    run(make_query_standalone(llm, [long_answer], "When was that?"))

    assert len(llm.seen) < 4000
