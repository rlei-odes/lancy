"""
Conversation data model and storage interface.

The 'ConversationDatabase' ABC is the pluggable storage backend for conversation
records. Concrete implementations ('InMemoryConversationDatabase',
'PostgreSQLConversationDatabase') are interchangeable at construction time,
keeping the controller and API layer free of storage-specific code.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class Conversation(BaseModel):
    """A single conversation session owned by a user."""

    id: str
    user_id: str
    create_timestamp: int
    update_timestamp: int
    title: str
    # Evaluation metadata — which KB and RAG config was active when this chat was created
    kb_id: str | None = None
    kb_name: str | None = None
    rag_config_snapshot: dict | None = None
    session_label: str | None = None


class ConversationDatabase(ABC):
    """Abstract repository for 'Conversation' records."""

    @abstractmethod
    async def create_conversation(self, conversation: Conversation) -> Conversation:
        pass

    @abstractmethod
    async def get_conversations_by_user_id(self, user_id: str) -> list[Conversation]:
        pass

    @abstractmethod
    async def get_conversation_by_id(self, conversation_id: str) -> Conversation:
        pass

    @abstractmethod
    async def update_conversation(self, conversation: Conversation) -> Conversation:
        pass

    @abstractmethod
    async def delete_conversation(self, conversation_id: str) -> bool:
        pass
