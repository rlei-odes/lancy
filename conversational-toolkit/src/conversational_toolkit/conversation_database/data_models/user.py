"""
User data model and storage interface.

User records are created on first message if the user does not already exist,
so applications do not need an explicit registration flow. The controller
performs this check automatically in 'process_new_message_stream'.

Concrete implementations: 'InMemoryUserDatabase', 'PostgreSQLUserDatabase'.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class User(BaseModel):
    """A registered user, identified solely by their ID."""

    id: str


class UserDatabase(ABC):
    """Abstract repository for 'User' records."""

    @abstractmethod
    async def create_user(self, user: User) -> User:
        pass

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> User | None:
        pass
