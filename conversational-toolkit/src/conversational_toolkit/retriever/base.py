"""
Retriever abstractions.

A retriever accepts a natural-language query and returns a ranked list of document chunks. 'Retriever' is generic over 'T_co' (covariant, bounded by 'Chunk') so that a 'Retriever[ChunkMatch]' can be assigned where a 'Retriever[ChunkRecord]' is expected without unsafe casts.

Concrete implementations: 'VectorStoreRetriever', 'BM25Retriever', 'HybridRetriever',
'RerankingRetriever'.
"""

from abc import ABC, abstractmethod
from typing import Any, TypeVar, Generic

from conversational_toolkit.chunking.base import Chunk

T_co = TypeVar("T_co", bound=Chunk, covariant=True)


class SemanticOnlyQuery(str):
    """A query meant for semantic search only; keyword retrievers return nothing for it.

    Used for HyDE text: a hypothetical answer widens semantic recall, but as a
    keyword query its many words only add noise. A 'str' subclass so it passes
    unchanged through fusion and reranking down to the retrievers.
    """


class KeywordQuery(str):
    """A query carrying extra search terms for keyword search: synonyms and translations of its key terms.

    Keyword retrievers score the query's own words in full and the extra terms at a
    lower weight, so a synonym cannot outrank the word the user typed. Other
    retrievers see the plain query. A 'str' subclass for the same reason as
    'SemanticOnlyQuery'.
    """

    expansions: tuple[str, ...]

    def __new__(cls, text: str, expansions: list[str] | tuple[str, ...] = ()) -> "KeywordQuery":
        query = super().__new__(cls, text)
        query.expansions = tuple(expansions)
        return query


class Retriever(ABC, Generic[T_co]):
    """
    Abstract base class for document retrievers.

    'T_co' is covariant because the retriever only produces values of that type (in the return position of 'retrieve') and never consumes them, which makes the covariant subtyping relationship safe and useful in practice.

    Attributes:
        top_k: Maximum number of chunks to return per query.
    """

    def __init__(self, top_k: int):
        self.top_k = top_k

    @abstractmethod
    async def retrieve(self, query: str, filters: dict[str, Any] | None = None) -> list[T_co]:
        """Return up to 'top_k' chunks most relevant to 'query'.

        'filters' is a neutral flat {field: value} dict passed to the underlying
        vector store to restrict the searched corpus (metadata pre-filtering).
        None or empty means no restriction.
        """
        pass
