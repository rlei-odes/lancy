"""
Embeddings model abstractions.

Concrete implementations: 'OpenAIEmbeddings', 'SentenceTransformerEmbeddings'.
"""

from abc import ABC, abstractmethod

import numpy as np
from numpy.typing import NDArray


class EmbeddingsModel(ABC):
    """
    Abstract base class for text embedding models.

    Attributes:
        model_name: Identifier of the underlying model.
        embedding_size: Dimensionality of the returned embedding vectors.
    """

    @abstractmethod
    async def get_embeddings(self, texts: str | list[str]) -> NDArray[np.float64]:
        """Embed one or more texts and return a float64 array of shape '(n, embedding_size)'."""
        pass
