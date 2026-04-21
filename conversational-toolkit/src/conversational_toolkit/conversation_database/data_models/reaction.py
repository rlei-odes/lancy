"""
Reaction data model and storage interface.

Reactions represent user feedback (thumbs up / thumbs down) on individual
assistant messages. Each reaction belongs to a user and a message; adding a
new reaction for the same user and message replaces the previous one.

Concrete implementations: 'InMemoryReactionDatabase', 'PostgreSQLReactionDatabase'.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class Reaction(BaseModel):
    """User feedback attached to an assistant message."""

    id: str
    user_id: str
    message_id: str
    content: str
    note: str | None = None


class ReactionDatabase(ABC):
    """Abstract repository for 'Reaction' records."""

    @abstractmethod
    async def create_reaction(self, reaction: Reaction) -> Reaction:
        pass

    @abstractmethod
    async def get_reactions_by_message_id(self, message_id: str) -> list[Reaction]:
        pass

    @abstractmethod
    async def delete_reactions(self, reaction_ids: list[str]) -> bool:
        pass
