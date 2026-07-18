"""
Retrieval-Augmented Generation (RAG) agent.

'RAG' combines document retrieval with language model generation. Before calling the LLM it rewrites the query to be history-independent, optionally expands it into multiple search queries, retrieves relevant chunks from all configured retrievers, merges the ranked results via Reciprocal Rank Fusion, and injects the sources into the LLM prompt using XML tags.
"""

import asyncio
from typing import Any, AsyncGenerator, Callable

from conversational_toolkit.agents.base import Agent, AgentAnswer, QueryWithContext, RetrievalStats
from conversational_toolkit.llms.base import LLM, LLMMessage, Roles, MessageContent
from conversational_toolkit.retriever.base import Retriever
from conversational_toolkit.retriever.reranking_retriever import RerankingRetriever
from conversational_toolkit.utils.retriever import (
    make_query_standalone,
    query_expansion,
    reciprocal_rank_fusion,
    hyde_expansion,
)
from conversational_toolkit.vectorstores.base import ChunkRecord, VectorStore

import logging

logger = logging.getLogger(__name__)


class RAG(Agent):
    """
    RAG agent that retrieves document chunks before generating an answer.

    # TODO: LLM response is assumed to be text-only; image output from the model is not handled.

    Attributes:
        utility_llm: A (typically cheaper) LLM used for query rewriting and expansion. Kept separate so a fast model can handle preprocessing while a more capable model handles generation.
        retrievers: One or more retrievers queried in parallel. Their results are merged with Reciprocal Rank Fusion before being passed to the LLM.
        number_query_expansion: Number of additional search queries to generate from the original query. Set to 0 to disable expansion.
    """

    def __init__(
        self,
        llm: LLM,
        utility_llm: LLM,
        retrievers: list[Retriever[Any]],
        system_prompt: str,
        description: str = "",
        number_query_expansion: int = 0,
        enable_hyde: bool = False,
        chat_only_system_prompt: str = "",
        expand_context_system_prompt: str = "",
        vector_store: VectorStore | None = None,
    ):
        super().__init__(system_prompt, llm, description)
        self.description = description
        self.llm = llm
        self.utility_llm = utility_llm
        self.retrievers = retrievers
        self.number_query_expansion = number_query_expansion
        self.enable_hyde = enable_hyde
        # Used only when QueryWithContext.chat_only=True. Empty falls back to the standard system_prompt.
        self.chat_only_system_prompt = chat_only_system_prompt
        # Used only when QueryWithContext.expand_context is set. Empty falls back to the standard system_prompt.
        self.expand_context_system_prompt = expand_context_system_prompt
        # Required for expand_context mode: fetches all chunks matching the picked source_files.
        self.vector_store = vector_store

    async def answer_stream(  # noqa: PLR0912
        self,
        query_with_context: QueryWithContext,
        phase_callback: Callable[[str], None] | None = None,
    ) -> AsyncGenerator[AgentAnswer, None]:
        query = query_with_context.query
        history = query_with_context.history
        filters = query_with_context.filters or None

        # Chat-only mode: skip retrieval and every preprocessing step; answer from history + general knowledge.
        if query_with_context.chat_only:
            async for answer in self._answer_stream_chat_only(query, history):
                yield answer
            return

        # Expand-context mode: skip retrieval and every preprocessing step; fetch all chunks for
        # the user-picked source_files and pass them wholesale to the LLM.
        if query_with_context.expand_context:
            async for answer in self._answer_stream_expand_context(query, history, query_with_context.expand_context):
                yield answer
            return

        has_preprocessing = len(history) > 0 or self.number_query_expansion > 0 or self.enable_hyde
        if has_preprocessing and phase_callback:
            phase_callback("preprocessing")

        if len(history) > 0:
            query = await make_query_standalone(self.utility_llm, history, query)

        queries = [query]

        if self.number_query_expansion > 0:
            queries_expanded = await query_expansion(query, self.utility_llm, self.number_query_expansion)
            queries += queries_expanded

        if self.enable_hyde:
            hyde_expansion_message = await hyde_expansion(query, self.utility_llm)
            queries.append(hyde_expansion_message)

        if phase_callback:
            phase_callback("retrieving")
        for retriever in self.retrievers:
            if isinstance(retriever, RerankingRetriever):
                retriever.phase_callback = phase_callback

        async def _retrieve_one(retriever) -> list[ChunkRecord]:
            results = await asyncio.gather(*[retriever.retrieve(q, filters=filters) for q in queries])
            return reciprocal_rank_fusion(list(results))[: retriever.top_k]

        all_results = await asyncio.gather(*[_retrieve_one(r) for r in self.retrievers])
        sources: list[ChunkRecord] = [chunk for group in all_results for chunk in group]

        # Build retrieval stats from the first reranker (there is at most one in practice).
        reranker = next((r for r in self.retrievers if isinstance(r, RerankingRetriever)), None)
        if reranker and reranker.last_rerank_stats:
            s = reranker.last_rerank_stats
            retrieval_stats = RetrievalStats(
                candidates_retrieved=s["candidates"],
                chunks_to_llm=len(sources),
                reranker_active=True,
                reranker_swaps=s["swaps"],
                reranker_fallback=s["fallback"],
            )
            fallback_note = " [fallback: original order]" if s["fallback"] else ""
            logger.info(
                f"Retrieval: {s['candidates']} candidates → reranker → "
                f"{len(sources)} chunk(s) to LLM, {s['swaps']} swap(s){fallback_note}"
            )
        else:
            retrieval_stats = RetrievalStats(
                candidates_retrieved=len(sources),
                chunks_to_llm=len(sources),
            )
            logger.info(f"Retrieval: {len(sources)} chunk(s) to LLM")

        context_message = LLMMessage(role=Roles.USER, content=[
            MessageContent(type="text", text="<sources>"),
        ])

        for source in sources:
            if "text" in source.mime_type:
                context_message.content.append(
                    MessageContent(
                        type="text",
                        text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}">{source.content}</source>',
                    )
                )
            elif "image" in source.mime_type:
                context_message.content.append(
                    MessageContent(type="text", text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}" type="image">')
                )
                context_message.content.append(
                    MessageContent(type="image", image_url=source.content)
                )
                context_message.content.append(
                    MessageContent(type="text", text="</source>")
                )
            else:
                raise ValueError(f"Unsupported MIME type: {source.mime_type}")

        context_message.content.append(MessageContent(type="text", text=f"</sources>\n<user_question>\n{query}\n</user_question>"))

        response_stream = self.llm.generate_stream(
            [
                LLMMessage(
                    role=Roles.SYSTEM,
                    content=[MessageContent(type="text", text=self.system_prompt)],
                ),
                *history,
                context_message,
            ]
        )

        content = ""
        async for response_chunk in response_stream:
            if response_chunk.content:
                for message_content in response_chunk.content:
                    if message_content.type == "text" and message_content.text:
                        content += message_content.text
                    elif message_content.type == "image" and message_content.image_url:
                        raise NotImplementedError("Image output from LLM is not supported in this version.")
                answer = await self._answer_post_processing(
                    AgentAnswer(
                        content=[MessageContent(type="text", text=content)],
                        role=Roles.ASSISTANT,
                        sources=sources,
                        retrieval_stats=retrieval_stats,
                    )
                )
                if answer:
                    yield answer

    async def _answer_stream_expand_context(
        self,
        query: str,
        history: list[LLMMessage],
        source_files: list[str],
    ) -> AsyncGenerator[AgentAnswer, None]:
        """Answer using ALL chunks of the picked source_files — no retrieval, no reranking.

        Fetches every chunk whose `metadata.source_file` matches one of `source_files`, sorts
        each document's chunks by their sequential id where present (falls back to insertion
        order), then wraps them in the same <sources><source>…</source></sources> XML the
        standard RAG path uses. Uses `expand_context_system_prompt` if provided, otherwise
        falls back to the standard `system_prompt`.
        """
        if self.vector_store is None:
            raise RuntimeError("expand_context requested but RAG agent has no vector_store configured")

        chunks = await self.vector_store.get_chunks_by_filter(filters={"source_file": source_files})

        # Group by source_file so the LLM sees each document's chunks contiguously.
        # Within a document, keep the vector store's natural (insertion) order.
        by_file: dict[str, list[ChunkRecord]] = {}
        for c in chunks:
            by_file.setdefault(c.metadata.get("source_file", ""), []).append(c)
        sources: list[ChunkRecord] = [c for f in source_files for c in by_file.get(f, [])]

        retrieval_stats = RetrievalStats(
            candidates_retrieved=len(sources),
            chunks_to_llm=len(sources),
        )
        logger.info(
            f"Retrieval: expand-context mode — {len(sources)} chunk(s) across {len(source_files)} document(s) to LLM"
        )

        system_prompt = self.expand_context_system_prompt or self.system_prompt

        context_message = LLMMessage(role=Roles.USER, content=[
            MessageContent(type="text", text="<sources>"),
        ])
        for source in sources:
            if "text" in source.mime_type:
                context_message.content.append(
                    MessageContent(
                        type="text",
                        text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}">{source.content}</source>',
                    )
                )
            elif "image" in source.mime_type:
                # Images normally live in a separate vector store, so this branch is unlikely,
                # but if a picked source_file happens to be image-typed, emit it consistently.
                context_message.content.append(
                    MessageContent(type="text", text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}" type="image">')
                )
                context_message.content.append(
                    MessageContent(type="image", image_url=source.content)
                )
                context_message.content.append(
                    MessageContent(type="text", text="</source>")
                )
            else:
                raise ValueError(f"Unsupported MIME type: {source.mime_type}")
        context_message.content.append(MessageContent(type="text", text=f"</sources>\n<user_question>\n{query}\n</user_question>"))

        response_stream = self.llm.generate_stream(
            [
                LLMMessage(role=Roles.SYSTEM, content=[MessageContent(type="text", text=system_prompt)]),
                *history,
                context_message,
            ]
        )

        content = ""
        async for response_chunk in response_stream:
            if not response_chunk.content:
                continue
            for message_content in response_chunk.content:
                if message_content.type == "text" and message_content.text:
                    content += message_content.text
                elif message_content.type == "image" and message_content.image_url:
                    raise NotImplementedError("Image output from LLM is not supported in this version.")
            answer = await self._answer_post_processing(
                AgentAnswer(
                    content=[MessageContent(type="text", text=content)],
                    role=Roles.ASSISTANT,
                    sources=sources,
                    retrieval_stats=retrieval_stats,
                )
            )
            if answer:
                yield answer

    async def _answer_stream_chat_only(
        self,
        query: str,
        history: list[LLMMessage],
    ) -> AsyncGenerator[AgentAnswer, None]:
        """Answer using only conversation history + general knowledge — no retrieval.

        Uses `chat_only_system_prompt` if provided, otherwise falls back to the standard
        `system_prompt`. Sources on the yielded AgentAnswer are always empty.
        """
        system_prompt = self.chat_only_system_prompt or self.system_prompt
        user_message = LLMMessage(
            role=Roles.USER,
            content=[MessageContent(type="text", text=query)],
        )
        response_stream = self.llm.generate_stream(
            [
                LLMMessage(role=Roles.SYSTEM, content=[MessageContent(type="text", text=system_prompt)]),
                *history,
                user_message,
            ]
        )
        retrieval_stats = RetrievalStats(candidates_retrieved=0, chunks_to_llm=0)
        logger.info("Retrieval: chat-only mode — LLM answers from history + general knowledge")

        content = ""
        async for response_chunk in response_stream:
            if not response_chunk.content:
                continue
            for message_content in response_chunk.content:
                if message_content.type == "text" and message_content.text:
                    content += message_content.text
                elif message_content.type == "image" and message_content.image_url:
                    raise NotImplementedError("Image output from LLM is not supported in this version.")
            answer = await self._answer_post_processing(
                AgentAnswer(
                    content=[MessageContent(type="text", text=content)],
                    role=Roles.ASSISTANT,
                    sources=[],
                    retrieval_stats=retrieval_stats,
                )
            )
            if answer:
                yield answer
