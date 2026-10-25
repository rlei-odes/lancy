"""Intent: a question asked about picked documents says so when the conversation is reopened.

"Ask using only these" answers from the picked documents in full, instead of a
search. The chat marks such a question with a document icon naming them — which
only works if the question remembers it was sent that way.

pytest-asyncio is not a dependency; the suite is sync.
"""

import asyncio

from conversational_toolkit.agents.base import AgentAnswer
from conversational_toolkit.conversation_database.controller import ConversationalToolkitController, MessageInput
from conversational_toolkit.conversation_database.in_memory.conversation import InMemoryConversationDatabase
from conversational_toolkit.conversation_database.in_memory.message import InMemoryMessageDatabase
from conversational_toolkit.conversation_database.in_memory.reactions import InMemoryReactionDatabase
from conversational_toolkit.conversation_database.in_memory.source import InMemorySourceDatabase
from conversational_toolkit.conversation_database.in_memory.user import InMemoryUserDatabase
from conversational_toolkit.llms.base import MessageContent, Roles


class Agent:
    async def answer_stream(self, query_with_context):
        yield AgentAnswer(content=[MessageContent(type="text", text="ok")], role=Roles.ASSISTANT)


def ask(tmp_path, **message) -> list:
    ctrl = ConversationalToolkitController(
        conversation_db=InMemoryConversationDatabase(str(tmp_path / "c.json")),
        message_db=InMemoryMessageDatabase(str(tmp_path / "m.json")),
        reaction_db=InMemoryReactionDatabase(str(tmp_path / "r.json")),
        source_db=InMemorySourceDatabase(str(tmp_path / "s.json")),
        user_db=InMemoryUserDatabase(str(tmp_path / "u.json")),
        agent=Agent(),
    )

    async def drive():
        final = None
        async for chunk in ctrl.process_new_message_stream(MessageInput(content="q", **message), user_id="u"):
            if not isinstance(chunk, bytes):
                final = chunk
        return await ctrl.get_messages_by_conversation_id(final.conversation_id)

    return asyncio.run(drive())


def test_the_question_remembers_the_documents_it_was_answered_from(tmp_path):
    question, answer = ask(tmp_path, expand_context=["linux.pdf"])

    assert question.role == Roles.USER and question.expand_context == ["linux.pdf"]
    assert answer.expand_context is None


def test_a_searched_question_carries_no_documents(tmp_path):
    question, _ = ask(tmp_path)

    assert question.expand_context is None
