"""
Message data model and storage interface.

Messages form a tree within a conversation via 'parent_id'. This branching
structure supports redo / branching flows: multiple assistant responses can
exist as siblings of the same parent user message. 'metadata' stores
LLM usage statistics and model information captured by 'MetadataProvider'.

The 'MessageDatabase' ABC is the pluggable storage backend. Concrete
implementations: 'InMemoryMessageDatabase', 'PostgreSQLMessageDatabase'.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from conversational_toolkit.llms.base import Roles


class Message(BaseModel):
    """
    A single message within a conversation.

    'user_id' is None for assistant messages (the LLM has no user identity).
    'parent_id' links the message to the previous turn, enabling the controller
    to reconstruct the conversation thread from any leaf message upward.
    """

    id: str
    user_id: str | None
    conversation_id: str
    content: str
    role: Roles
    create_timestamp: int
    parent_id: str | None = None
    metadata: list[dict[str, Any]] | None = None


class MessageDatabase(ABC):
    """Abstract repository for 'Message' records."""

    @abstractmethod
    async def create_message(
        self,
        message: Message,
    ) -> Message:
        pass

    @abstractmethod
    async def get_messages_by_conversation_id(self, conversation_id: str) -> list[Message]:
        pass

    @abstractmethod
    async def get_message_by_id(self, message_id: str) -> Message:
        pass

    @abstractmethod
    async def delete_message(self, message_id: str) -> bool:
        pass
