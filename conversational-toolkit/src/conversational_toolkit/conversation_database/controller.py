"""
Conversational toolkit controller (Facade).

'ConversationalToolkitController' is the single entry point for all application logic. It coordinates five pluggable database repositories and an agent to handle the full lifecycle of a conversation turn: user registration, conversation and thread management, agent invocation, streaming, and persistence of messages, sources, and reactions.

The two public entry points for message processing are:

    'process_new_message'        - non-streaming, returns the final 'ClientMessage'.
    'process_new_message_stream' - async generator that yields partial content chunks during generation, then the final persisted message with sources.

'ClientMessage' extends 'Message' with the API-response fields ('sources', 'reaction', 'follow_up_questions') that the frontend needs but that are not stored directly on the message record.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from pydantic import BaseModel

from conversational_toolkit.agents.base import Agent, QueryWithContext
from conversational_toolkit.conversation_database.data_models.conversation import Conversation, ConversationDatabase
from conversational_toolkit.conversation_database.data_models.message import Message, MessageDatabase
from conversational_toolkit.conversation_database.data_models.reaction import Reaction, ReactionDatabase
from conversational_toolkit.conversation_database.data_models.source import Source, SourceDatabase
from conversational_toolkit.conversation_database.data_models.user import User, UserDatabase
from conversational_toolkit.llms.base import LLMMessage, Roles, MessageContent
from conversational_toolkit.utils.database import generate_uid
from conversational_toolkit.utils.metadata_provider import MetadataProvider
from conversational_toolkit.utils.time import get_current_timestamp


class MessageInput(BaseModel):
    content: str
    parent_id: str | None = None
    conversation_id: str | None = None
    type: str | None = None
    session_label: str | None = None


class ConversationInput(BaseModel):
    title: str


class ReactionInput(BaseModel):
    content: str = ""
    note: str | None = None


class ClientMessage(Message):
    sources: Sequence[Source]
    reaction: str | None
    follow_up_questions: Sequence[str] | None
    query_duration_ms: int | None = None
    tokens_per_second: float | None = None
    llm_model: str | None = None

    def encode(self, charset: str = "utf-8") -> bytes:
        return json.dumps(self.model_dump()).encode(charset)


class ClientConversation(Conversation):
    messages: list[ClientMessage]


DEFAULT_CONVERSATION_TITLE = "New Conversation"


class ConversationalToolkitController:
    def __init__(
        self,
        conversation_db: ConversationDatabase,
        message_db: MessageDatabase,
        reaction_db: ReactionDatabase,
        source_db: SourceDatabase,
        user_db: UserDatabase,
        agent: Agent,
    ):
        self.conversation_db = conversation_db
        self.message_db = message_db
        self.reaction_db = reaction_db
        self.source_db = source_db
        self.user_db = user_db
        self.agent = agent

    async def get_user_by_id(self, user_id: str) -> User | None:
        return await self.user_db.get_user_by_id(user_id)

    async def register_user(self, user_id: str) -> User:
        return await self.user_db.create_user(User(id=user_id))

    async def process_new_message(
        self, user_input: MessageInput, user_id: str, extra_meta: dict | None = None
    ) -> ClientMessage:
        last_message = None
        async for message in self.process_new_message_stream(user_input, user_id, extra_meta=extra_meta):
            last_message = message
        if last_message is None:
            raise Exception("No message was generated from the stream")
        return last_message

    async def _setup_conversation(
        self,
        user_input: MessageInput,
        user_id: str,
        extra_meta: dict | None = None,
    ) -> tuple[Conversation, list[Message]]:
        if user_input.conversation_id is None:
            create_time = get_current_timestamp()
            meta = extra_meta or {}
            conversation = await self.conversation_db.create_conversation(
                Conversation(
                    id=generate_uid(),
                    user_id=user_id,
                    create_timestamp=create_time,
                    update_timestamp=create_time,
                    title=DEFAULT_CONVERSATION_TITLE,
                    kb_id=meta.get("kb_id"),
                    kb_name=meta.get("kb_name"),
                    rag_config_snapshot=meta.get("rag_config_snapshot"),
                    session_label=user_input.session_label or meta.get("session_label"),
                )
            )
            return conversation, []

        conversation = await self.conversation_db.get_conversation_by_id(user_input.conversation_id)
        conversation_history = await self.message_db.get_messages_by_conversation_id(conversation.id)
        parent_message = await self.message_db.get_message_by_id(user_input.parent_id) if user_input.parent_id else None
        if not parent_message:
            return conversation, []

        current_message = parent_message
        thread: list[Message] = [current_message]
        while current_message.parent_id:
            current_message = next(
                message for message in conversation_history if message.id == current_message.parent_id
            )
            thread.append(current_message)
        return conversation, thread

    async def process_new_message_stream(
        self, user_input: MessageInput, user_id: str, extra_meta: dict | None = None
    ) -> AsyncGenerator[ClientMessage, Any]:
        with MetadataProvider.get_manager():
            user = await self.user_db.get_user_by_id(user_id)

            if not user:
                await self.user_db.create_user(User(id=user_id))

            conversation, thread = await self._setup_conversation(user_input, user_id, extra_meta=extra_meta)

            if user_input.type == "redo":
                if user_input.parent_id is None:
                    raise ValueError("Parent id must be provided for redo type")
                input_message = await self.message_db.get_message_by_id(user_input.parent_id)
            else:
                input_message = await self.message_db.create_message(
                    Message(
                        id=generate_uid(),
                        user_id=user_id,
                        conversation_id=conversation.id,
                        content=user_input.content,
                        role=Roles.USER,
                        create_timestamp=get_current_timestamp(),
                        metadata=None,
                        parent_id=user_input.parent_id,
                    )
                )

            # Rename conversation eagerly so a failed/interrupted stream still leaves a useful title
            if conversation.title == DEFAULT_CONVERSATION_TITLE:
                await self.conversation_db.update_conversation(
                    conversation=Conversation(
                        id=conversation.id,
                        user_id=user_id,
                        create_timestamp=conversation.create_timestamp,
                        update_timestamp=get_current_timestamp(),
                        title=input_message.content[:60],
                        kb_id=conversation.kb_id,
                        kb_name=conversation.kb_name,
                        rag_config_snapshot=conversation.rag_config_snapshot,
                        session_label=conversation.session_label,
                    )
                )

            t_start = time.monotonic()
            stream = self.agent.answer_stream(
                QueryWithContext(
                    query=input_message.content,
                    history=[
                        LLMMessage(role=message.role, content=[MessageContent(type="text", text=message.content)])
                        for message in sorted(thread, key=lambda m: m.create_timestamp)
                    ],
                )
            )

            # Iterate agent stream with keepalive: yield b"\n" every 20s while
            # waiting for the next chunk so intermediate proxies don't time out.
            _KEEPALIVE = 20.0
            _sentinel = object()

            async def _next_chunk(aiter):  # type: ignore[no-untyped-def]
                try:
                    return await aiter.__anext__()
                except StopAsyncIteration:
                    return _sentinel

            _aiter = stream.__aiter__()
            _pending = asyncio.create_task(_next_chunk(_aiter))
            last_chunk = None
            try:
                while True:
                    done, _ = await asyncio.wait({_pending}, timeout=_KEEPALIVE)
                    if not done:
                        yield b"\n"  # keepalive — JSON-parser ignores whitespace
                        continue
                    chunk = _pending.result()
                    if chunk is _sentinel:
                        break
                    last_chunk = chunk
                    if chunk.content:
                        yield ClientMessage(
                            id="",
                            user_id="",
                            conversation_id=conversation.id,
                            content=last_chunk.content[0].text if last_chunk.content else "",
                            role=Roles.ASSISTANT,
                            sources=[],
                            reaction=None,
                            follow_up_questions=chunk.follow_up_questions,
                            parent_id=input_message.id,
                            create_timestamp=get_current_timestamp(),
                        )
                    _pending = asyncio.create_task(_next_chunk(_aiter))
            finally:
                _pending.cancel()

            if last_chunk is None:
                return

            query_duration_ms = int((time.monotonic() - t_start) * 1000)
            _meta = MetadataProvider.get_metadata()
            _tps: float | None = next(
                (m["tokens_per_second"] for m in reversed(_meta) if m.get("tokens_per_second")),
                None,
            )
            _model: str | None = next(
                (m.get("model") for m in reversed(_meta) if m.get("model")),
                None,
            )
            MetadataProvider.add_metadata({"query_duration_ms": query_duration_ms})

            final_message = await self.message_db.create_message(
                Message(
                    id=generate_uid(),
                    user_id=None,
                    conversation_id=conversation.id,
                    content=last_chunk.content[0].text if last_chunk.content else "",
                    role=Roles.ASSISTANT,
                    create_timestamp=get_current_timestamp(),
                    metadata=MetadataProvider.get_metadata(),
                    parent_id=input_message.id,
                )
            )

            sources = [
                await self.source_db.create_source(
                    Source(
                        id=generate_uid(), message_id=final_message.id, content=source.content, metadata=source.metadata
                    )
                )
                for source in last_chunk.sources
            ]

            yield ClientMessage(
                id=final_message.id,
                user_id=final_message.user_id,
                conversation_id=final_message.conversation_id,
                content=final_message.content,
                role=final_message.role,
                sources=sources,
                reaction=None,
                follow_up_questions=last_chunk.follow_up_questions,
                parent_id=input_message.id,
                create_timestamp=final_message.create_timestamp,
                query_duration_ms=query_duration_ms,
                tokens_per_second=_tps,
                llm_model=_model,
            )

    async def get_conversations_data_by_user_id(self, user_id: str) -> list[Conversation]:
        return await self.conversation_db.get_conversations_by_user_id(user_id)

    async def get_conversation_by_id(self, conversation_id: str) -> ClientConversation:
        conversation = await self.conversation_db.get_conversation_by_id(conversation_id)
        messages = await self.message_db.get_messages_by_conversation_id(conversation_id)
        api_messages = []
        for message in sorted(messages, key=lambda m: m.create_timestamp):
            _meta = message.metadata or []
            api_messages.append(
                ClientMessage(
                    id=message.id,
                    user_id=message.user_id,
                    conversation_id=message.conversation_id,
                    content=message.content,
                    role=message.role,
                    sources=await self.source_db.get_sources_by_message_id(message.id),
                    reaction=None,
                    follow_up_questions=[],
                    parent_id=message.parent_id,
                    create_timestamp=message.create_timestamp,
                    query_duration_ms=next(
                        (m.get("query_duration_ms") for m in reversed(_meta) if m.get("query_duration_ms")), None
                    ),
                    tokens_per_second=next(
                        (m.get("tokens_per_second") for m in reversed(_meta) if m.get("tokens_per_second")), None
                    ),
                    llm_model=next((m.get("model") for m in reversed(_meta) if m.get("model")), None),
                )
            )

        return ClientConversation(
            id=conversation.id,
            create_timestamp=conversation.create_timestamp,
            update_timestamp=conversation.update_timestamp,
            title=conversation.title,
            user_id=conversation.user_id,
            messages=api_messages,
            kb_id=conversation.kb_id,
            kb_name=conversation.kb_name,
            rag_config_snapshot=conversation.rag_config_snapshot,
            session_label=conversation.session_label,
        )

    async def update_conversation(self, conversation_id: str, conversation_updates: ConversationInput) -> Conversation:
        conversation = await self.conversation_db.get_conversation_by_id(conversation_id)

        if not conversation:
            raise ValueError(f"Conversation with id {conversation_id} not found")

        return await self.conversation_db.update_conversation(
            conversation=Conversation(
                id=conversation.id,
                user_id=conversation.user_id,
                create_timestamp=conversation.create_timestamp,
                update_timestamp=get_current_timestamp(),
                title=conversation_updates.title,
                kb_id=conversation.kb_id,
                kb_name=conversation.kb_name,
                rag_config_snapshot=conversation.rag_config_snapshot,
                session_label=conversation.session_label,
            )
        )

    async def delete_conversation(self, conversation_id: str) -> bool:
        messages = await self.message_db.get_messages_by_conversation_id(conversation_id)
        for message in messages:
            sources = await self.source_db.get_sources_by_message_id(message_id=message.id)
            await self.source_db.delete_sources([source.id for source in sources])

            reactions = await self.reaction_db.get_reactions_by_message_id(message_id=message.id)
            await self.reaction_db.delete_reactions([reaction.id for reaction in reactions])

            await self.message_db.delete_message(message.id)

        return await self.conversation_db.delete_conversation(conversation_id)

    async def get_messages_by_conversation_id(self, conversation_id: str) -> list[ClientMessage]:
        messages = await self.message_db.get_messages_by_conversation_id(conversation_id)
        api_messages = []
        for message in messages:
            reactions = await self.reaction_db.get_reactions_by_message_id(message.id)
            api_reaction = reactions[0].content if reactions else None
            sources = await self.source_db.get_sources_by_message_id(message.id)
            _meta = message.metadata or []
            api_message = ClientMessage(
                id=message.id,
                user_id=message.user_id,
                conversation_id=message.conversation_id,
                content=message.content,
                role=message.role,
                sources=sources,
                reaction=api_reaction,
                follow_up_questions=[],
                parent_id=message.parent_id,
                create_timestamp=message.create_timestamp,
                query_duration_ms=next(
                    (m.get("query_duration_ms") for m in reversed(_meta) if m.get("query_duration_ms")), None
                ),
                tokens_per_second=next(
                    (m.get("tokens_per_second") for m in reversed(_meta) if m.get("tokens_per_second")), None
                ),
                llm_model=next((m.get("model") for m in reversed(_meta) if m.get("model")), None),
            )

            api_messages.append(api_message)

        return api_messages

    async def add_reaction(self, reaction_input: ReactionInput, message_id: str, user_id: str) -> Reaction:
        message = await self.message_db.get_message_by_id(message_id)
        if not message:
            raise ValueError(f"Message with id {message_id} not found")
        conversation = await self.conversation_db.get_conversation_by_id(message.conversation_id)
        if conversation.user_id != user_id:
            raise ValueError(f"User {user_id} does not have access to conversation {conversation.id}")
        reactions = await self.reaction_db.get_reactions_by_message_id(message_id)
        user_reactions = [reaction for reaction in reactions if reaction.user_id == user_id]
        if user_reactions:
            await self.reaction_db.delete_reactions([reaction.id for reaction in user_reactions])

        return await self.reaction_db.create_reaction(
            Reaction(
                id=generate_uid(),
                user_id=user_id,
                message_id=message_id,
                content=reaction_input.content,
                note=reaction_input.note,
            )
        )
