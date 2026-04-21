"""
Source data model and storage interface.

Sources are the document chunks that were retrieved and used to generate a
specific assistant message. They are persisted alongside the message so that
the API can return them to the client, enabling citation display in the UI.

Concrete implementations: 'InMemorySourceDatabase', 'PostgreSQLSourceDatabase'.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class Source(BaseModel):
    """A retrieved document chunk linked to an assistant message."""

    id: str
    message_id: str
    content: str
    metadata: dict[str, float | int | str | None]


class SourceDatabase(ABC):
    """Abstract repository for 'Source' records."""

    @abstractmethod
    async def create_source(self, source: Source) -> Source:
        pass

    @abstractmethod
    async def get_sources_by_message_id(self, message_id: str) -> list[Source]:
        pass

    @abstractmethod
    async def delete_sources(self, source_ids: list[str]) -> bool:
        pass
