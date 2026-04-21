from typing import Any

from conversational_toolkit.llms.base import LLM
from conversational_toolkit.retriever.base import Retriever
from conversational_toolkit.utils.retriever import (
    make_query_standalone,
    query_expansion,
    reciprocal_rank_fusion,
)
from conversational_toolkit.tools.base import Tool
from conversational_toolkit.vectorstores.base import ChunkRecord


class RetrieverTool(Tool):
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        llm: LLM,
        retriever: Retriever[ChunkRecord],
        number_query_expansion: int = 0,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.llm = llm
        self.retriever = retriever
        self.number_query_expansion = number_query_expansion

    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        history = args.get("_history", [])
        query = str(args.get("_query"))

        if len(history) > 0:
            query = await make_query_standalone(self.llm, history, query)
        if self.number_query_expansion > 0:
            queries = await query_expansion(query, self.llm, self.number_query_expansion)
        else:
            queries = [query]

        retrieved = [await self.retriever.retrieve(q) for q in queries]
        sources = reciprocal_rank_fusion(retrieved)[: self.retriever.top_k]

        json_chunks = [
            {
                "id": source.id,
                "title": source.title,
                "content": source.content,
                "mime_type": source.mime_type,
                "metadata": {**(source.metadata if source.metadata else {}), "id": source.id},
            }
            for source in sources
        ]

        return {"_sources": json_chunks}
