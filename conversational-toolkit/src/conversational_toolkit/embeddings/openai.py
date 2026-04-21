from loguru import logger
import numpy as np
from numpy.typing import NDArray

from conversational_toolkit.embeddings.base import EmbeddingsModel
from openai import AsyncOpenAI


class OpenAIEmbeddings(EmbeddingsModel):
    """
    OpenAI-compatible embeddings model.
    Works with OpenAI, LiteLLM proxy, Voyage, and any OpenAI-compatible endpoint.

    Attributes:
        model_name (str): The name of the embeddings model.
        base_url (str | None): Custom base URL (e.g. LiteLLM proxy). Defaults to OpenAI.
        api_key (str | None): API key. Falls back to OPENAI_API_KEY env var if None.
        dimensions (int | None): Output dimensions. None = model default (Voyage, etc. don't support override).
    """

    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        dimensions: int | None = 1024,
    ):
        self.client = AsyncOpenAI(
            base_url=base_url or None,
            api_key=api_key or None,
        )
        self.model_name = model_name
        self.dimensions = dimensions
        logger.debug(f"OpenAI-compat embeddings loaded: {model_name} (base_url={base_url or 'OpenAI default'})")

    async def get_embeddings(
        self, texts: str | list[str], batch_size: int = 100, max_chars: int = 30_000
    ) -> NDArray[np.float64]:
        """Embed one or more texts, batching requests to stay within limits."""
        if isinstance(texts, str):
            texts = [texts]

        texts = [t[:max_chars] if len(t) > max_chars else t for t in texts]
        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
        all_embeddings: list[NDArray[np.float64]] = []
        for batch in batches:
            kwargs: dict = {"input": batch, "model": self.model_name}
            if self.dimensions is not None:
                kwargs["dimensions"] = self.dimensions
            response = await self.client.embeddings.create(**kwargs)
            all_embeddings.append(np.asarray([d.embedding for d in response.data]))

        embeddings = np.concatenate(all_embeddings, axis=0)
        logger.info(f"OpenAI embeddings shape: {embeddings.shape}")
        return embeddings
