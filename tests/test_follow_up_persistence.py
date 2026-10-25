"""Intent: an answer's suggested follow-up questions survive reloading the conversation.

They arrived only with the streamed answer and were never saved, so opening
the conversation again showed none. They are now stored with the message.

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


class SuggestingAgent:
    def __init__(self, follow_ups: list[str]) -> None:
        self.follow_ups = follow_ups

    async def answer_stream(self, query_with_context):
        yield AgentAnswer(
            content=[MessageContent(type="text", text="ok")], role=Roles.ASSISTANT, follow_up_questions=self.follow_ups
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


def answer_and_reload(tmp_path, follow_ups: list[str]):
    ctrl = controller(tmp_path, SuggestingAgent(follow_ups))

    async def drive():
        final = None
        async for message in ctrl.process_new_message_stream(MessageInput(content="q"), user_id="u"):
            if not isinstance(message, bytes):
                final = message
        loaded = [
            next(m for m in (await ctrl.get_conversation_by_id(final.conversation_id)).messages if m.id == final.id),
            next(m for m in await ctrl.get_messages_by_conversation_id(final.conversation_id) if m.id == final.id),
        ]
        return loaded

    return asyncio.run(drive())


def test_follow_up_questions_come_back_when_the_conversation_is_reloaded(tmp_path):
    for message in answer_and_reload(tmp_path, ["Why?", "How?"]):
        assert message.follow_up_questions == ["Why?", "How?"]


def test_an_answer_without_follow_ups_reloads_with_none(tmp_path):
    for message in answer_and_reload(tmp_path, []):
        assert message.follow_up_questions == []
