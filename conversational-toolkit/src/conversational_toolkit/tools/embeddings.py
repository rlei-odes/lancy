from typing import Any

from loguru import logger
from conversational_toolkit.embeddings.base import EmbeddingsModel
from conversational_toolkit.tools.base import Tool


class EmbeddingsTool(Tool):
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        embedding_model: EmbeddingsModel,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.embedding_model = embedding_model
        logger.debug(f"Embeddings tool loaded: {name}; {description}; {parameters}; {embedding_model}")

    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        embedding = await self.embedding_model.get_embeddings(args["text"])
        return {"embedding": embedding}
