"""
Retrieval-Augmented Generation (RAG) agent.

'RAG' combines document retrieval with language model generation. Before calling the LLM it rewrites the query to be history-independent, optionally expands it into multiple search queries, retrieves relevant chunks from all configured retrievers, merges the ranked results via Reciprocal Rank Fusion, and injects the sources into the LLM prompt using XML tags.
"""

import asyncio
import json
import re
from typing import Any, AsyncGenerator, Callable, Sequence

from conversational_toolkit.agents.base import Agent, AgentAnswer, QueryWithContext, RetrievalStats
from conversational_toolkit.llms.base import LLM, LLMMessage, Roles, MessageContent
from conversational_toolkit.retriever.base import KeywordQuery, Retriever, SemanticOnlyQuery
from conversational_toolkit.retriever.reranking_retriever import RerankingRetriever
from conversational_toolkit.utils.retriever import (
    keyword_extraction,
    make_query_standalone,
    query_expansion,
    reciprocal_rank_fusion,
    hyde_expansion,
)
from conversational_toolkit.utils.text import compact_markdown_tables
from conversational_toolkit.vectorstores.base import ChunkRecord, VectorStore

import logging

logger = logging.getLogger(__name__)


# A partial answer saying its part of the documents has nothing on the question.
NOTHING_RELEVANT = "NOTHING RELEVANT"


def split_into_passes(lengths: Sequence[int], budget: int) -> list[list[int]]:
    """Indices of consecutive items grouped so each group's total length stays within 'budget'.

    Order is kept, so a pass reads a stretch of the document as written. An item longer
    than the budget on its own forms a pass by itself; the caller cuts it to fit.
    """
    passes: list[list[int]] = []
    current: list[int] = []
    size = 0
    for i, length in enumerate(lengths):
        if current and size + length > budget:
            passes.append(current)
            current, size = [], 0
        current.append(i)
        size += length
    if current:
        passes.append(current)
    return passes


def _answer_field(text: str) -> str:
    """The "answer" of a JSON reply, markdown fences tolerated; the text itself when it is not JSON."""
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        parsed = json.loads(body)
    except ValueError:
        return text.strip()
    if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
        return parsed["answer"].strip()
    return text.strip()


def _source_xml(source: ChunkRecord, text: str) -> str:
    return f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}">{text}</source>'


class RAG(Agent):
    """
    RAG agent that retrieves document chunks before generating an answer.

    # TODO: LLM response is assumed to be text-only; image output from the model is not handled.

    Attributes:
        utility_llm: A (typically cheaper) LLM used for query rewriting and expansion. Kept separate so a fast model can handle preprocessing while a more capable model handles generation.
        retrievers: One or more retrievers queried in parallel. Their results are merged with Reciprocal Rank Fusion before being passed to the LLM.
        number_query_expansion: Number of additional search queries to generate from the original query. Set to 0 to disable expansion.
        expand_context_budget_chars: Characters of document text one LLM call may read in expand-context mode. Picked documents over it are read in several passes: a partial answer per pass, merged into the final answer. None reads everything in one call.
        keyword_extraction: Ask the utility LLM for the question's key terms, synonyms and translations, and give them to keyword search at a lower weight. Keyword search then takes the question and these terms only; expanded queries go to semantic search.
        corpus_languages: Returns the names of the corpus languages, most common first, read on every question. HyDE, query expansion and keyword extraction write in them. None or empty means unknown.
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
        keyword_extraction: bool = False,
        corpus_languages: Callable[[], Sequence[str]] | None = None,
        expand_context_budget_chars: int | None = None,
        expand_context_map_prompt: str = "",
        expand_context_merge_prompt: str = "",
        expand_context_reduce_prompt: str = "",
    ):
        super().__init__(system_prompt, llm, description)
        self.description = description
        self.llm = llm
        self.utility_llm = utility_llm
        self.retrievers = retrievers
        self.number_query_expansion = number_query_expansion
        self.enable_hyde = enable_hyde
        self.keyword_extraction = keyword_extraction
        self.corpus_languages = corpus_languages
        if expand_context_budget_chars is not None and not (
            expand_context_map_prompt and expand_context_merge_prompt and expand_context_reduce_prompt
        ):
            raise ValueError("Multi-pass expand context needs its map, merge and reduce prompts.")
        self.expand_context_budget_chars = expand_context_budget_chars
        self.expand_context_map_prompt = expand_context_map_prompt
        self.expand_context_merge_prompt = expand_context_merge_prompt
        self.expand_context_reduce_prompt = expand_context_reduce_prompt
        # Told (pass, passes) while a multi-pass expand-context answer reads its parts; 0 passes = merging.
        self.progress_callback: Callable[[int, int], None] | None = None
        # Used only when QueryWithContext.chat_only=True. Empty falls back to the standard system_prompt.
        self.chat_only_system_prompt = chat_only_system_prompt
        # Used only when QueryWithContext.expand_context is set. Empty falls back to the standard system_prompt.
        self.expand_context_system_prompt = expand_context_system_prompt
        # Required for expand_context mode: fetches all chunks matching the picked source_files.
        self.vector_store = vector_store

    async def answer_stream(
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
            async for answer in self._answer_stream_expand_context(
                query, history, query_with_context.expand_context, phase_callback
            ):
                yield answer
            return

        has_preprocessing = (
            len(history) > 0 or self.number_query_expansion > 0 or self.enable_hyde or self.keyword_extraction
        )
        if has_preprocessing and phase_callback:
            phase_callback("preprocessing")

        if len(history) > 0:
            query = await make_query_standalone(self.utility_llm, history, query)

        languages = list(self.corpus_languages()) if self.corpus_languages else []
        search_query: str = query
        if self.keyword_extraction:
            search_query = KeywordQuery(query, await keyword_extraction(query, self.utility_llm, languages))

        queries: list[str] = [search_query]

        if self.number_query_expansion > 0:
            queries_expanded = await query_expansion(
                query, self.utility_llm, self.number_query_expansion, languages=languages
            )
            # With keyword extraction, keyword search takes the question and its weighted
            # terms; an expanded query would add its own words at full weight.
            queries += [SemanticOnlyQuery(q) for q in queries_expanded] if self.keyword_extraction else queries_expanded

        if self.enable_hyde:
            hyde_expansion_message = await hyde_expansion(query, self.utility_llm, languages=languages)
            # A hypothetical answer widens semantic recall; as keywords it is noise.
            queries.append(SemanticOnlyQuery(hyde_expansion_message))

        if phase_callback:
            phase_callback("retrieving")
        for retriever in self.retrievers:
            if isinstance(retriever, RerankingRetriever):
                retriever.phase_callback = phase_callback

        async def _retrieve_one(retriever) -> tuple[list[ChunkRecord], list[ChunkRecord]]:
            # A reranking retriever fuses the variants itself and ranks the merged
            # pool in one LLM call, against the user's question rather than the
            # HyDE document. Ranking per variant would cost one call each and
            # never compare a chunk found by one variant against another's.
            if isinstance(retriever, RerankingRetriever):
                kept, dropped = await retriever.rank_pool(queries, rank_query=search_query, filters=filters)
                return list(kept), list(dropped)
            results = await asyncio.gather(*[retriever.retrieve(q, filters=filters) for q in queries])
            return reciprocal_rank_fusion(list(results))[: retriever.top_k], []

        all_results = await asyncio.gather(*[_retrieve_one(r) for r in self.retrievers])
        sources: list[ChunkRecord] = [chunk for kept, _ in all_results for chunk in kept]
        discarded: list[ChunkRecord] = [chunk for _, dropped in all_results for chunk in dropped]

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

        context_message = LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(type="text", text="<sources>"),
            ],
        )

        for source in sources:
            if "text" in source.mime_type:
                context_message.content.append(
                    MessageContent(
                        type="text",
                        text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}">{compact_markdown_tables(source.content)}</source>',
                    )
                )
            elif "image" in source.mime_type:
                context_message.content.append(
                    MessageContent(
                        type="text",
                        text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}" type="image">',
                    )
                )
                context_message.content.append(MessageContent(type="image", image_url=source.content))
                context_message.content.append(MessageContent(type="text", text="</source>"))
            else:
                raise ValueError(f"Unsupported MIME type: {source.mime_type}")

        context_message.content.append(
            MessageContent(type="text", text=f"</sources>\n<user_question>\n{query}\n</user_question>")
        )

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
                        retrieved=sources,
                        discarded=discarded,
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
        phase_callback: Callable[[str], None] | None = None,
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

        texts = [compact_markdown_tables(s.content) for s in sources if "text" in s.mime_type]
        budget = self.expand_context_budget_chars
        if budget is not None and sum(len(t) for t in texts) > budget:
            async for answer in self._answer_stream_multi_pass(query, history, sources, budget, phase_callback):
                yield answer
            return

        system_prompt = self.expand_context_system_prompt or self.system_prompt

        context_message = LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(type="text", text="<sources>"),
            ],
        )
        for source in sources:
            if "text" in source.mime_type:
                context_message.content.append(
                    MessageContent(
                        type="text",
                        text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}">{compact_markdown_tables(source.content)}</source>',
                    )
                )
            elif "image" in source.mime_type:
                # Images normally live in a separate vector store, so this branch is unlikely,
                # but if a picked source_file happens to be image-typed, emit it consistently.
                context_message.content.append(
                    MessageContent(
                        type="text",
                        text=f'<source id="{source.id}" file="{source.metadata.get("source_file", "")}" type="image">',
                    )
                )
                context_message.content.append(MessageContent(type="image", image_url=source.content))
                context_message.content.append(MessageContent(type="text", text="</source>"))
            else:
                raise ValueError(f"Unsupported MIME type: {source.mime_type}")
        context_message.content.append(
            MessageContent(type="text", text=f"</sources>\n<user_question>\n{query}\n</user_question>")
        )

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

    async def _answer_stream_multi_pass(
        self,
        query: str,
        history: list[LLMMessage],
        sources: list[ChunkRecord],
        budget: int,
        phase_callback: Callable[[str], None] | None,
    ) -> AsyncGenerator[AgentAnswer, None]:
        """Answer from documents too large for one call: map, merge, reduce.

        Map: the chunks, in document order, are split into passes that each fit the
        budget, and every pass yields a partial answer from its part alone, citing its
        chunks as [id] — or NOTHING RELEVANT. Merge: while the partial answers together
        exceed the budget, groups of them are combined, ids kept. Reduce: one streamed
        answer from the partial answers, in the usual JSON shape, its [id] markers and
        used_sources_id pointing at the chunks the passes cited.
        """
        text_sources = [s for s in sources if "text" in s.mime_type]
        texts = [compact_markdown_tables(s.content)[:budget] for s in text_sources]
        passes = split_into_passes([len(t) for t in texts], budget)
        logger.info(f"Expand context: {len(text_sources)} chunk(s) over budget — reading in {len(passes)} pass(es)")

        if phase_callback:
            phase_callback("reading")
        partials: list[str] = []
        for number, indices in enumerate(passes, start=1):
            if self.progress_callback:
                self.progress_callback(number, len(passes))
            part = "".join(_source_xml(text_sources[i], texts[i]) for i in indices)
            reply = await self._generate_text(
                self.expand_context_map_prompt,
                history,
                f'<part number="{number}" of="{len(passes)}"><sources>{part}</sources></part>\n'
                f"<user_question>\n{query}\n</user_question>",
            )
            if reply and NOTHING_RELEVANT not in reply.upper():
                partials.append(reply)
        logger.info(f"Expand context: {len(partials)} of {len(passes)} pass(es) found something relevant")

        # Merge rounds: a long document read at a small context leaves more partial
        # answers than one call can read.
        while sum(len(p) for p in partials) > budget and len(partials) > 1:
            if phase_callback:
                phase_callback("merging")
            if self.progress_callback:
                self.progress_callback(0, 0)
            groups = split_into_passes([len(p) for p in partials], budget)
            if len(groups) == len(partials):  # every partial alone fills the budget: merging cannot shrink them
                partials = [p[: budget // len(partials)] for p in partials]
                break
            partials = [
                await self._generate_text(
                    self.expand_context_merge_prompt,
                    history,
                    "<partial_answers>"
                    + "".join(f"<partial>{partials[i]}</partial>" for i in group)
                    + f"</partial_answers>\n<user_question>\n{query}\n</user_question>",
                )
                for group in groups
            ]

        if phase_callback:
            phase_callback("generating")
        body = "".join(f"<partial>{p}</partial>" for p in partials) or f"<partial>{NOTHING_RELEVANT}</partial>"
        context_message = LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(
                    type="text",
                    text=f'<partial_answers passes="{len(passes)}">{body}</partial_answers>\n'
                    f"<user_question>\n{query}\n</user_question>",
                )
            ],
        )
        response_stream = self.llm.generate_stream(
            [
                LLMMessage(
                    role=Roles.SYSTEM, content=[MessageContent(type="text", text=self.expand_context_reduce_prompt)]
                ),
                *history,
                context_message,
            ]
        )
        retrieval_stats = RetrievalStats(candidates_retrieved=len(sources), chunks_to_llm=len(sources))
        content = ""
        async for response_chunk in response_stream:
            if not response_chunk.content:
                continue
            for message_content in response_chunk.content:
                if message_content.type == "text" and message_content.text:
                    content += message_content.text
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

    async def _generate_text(self, system_prompt: str, history: list[LLMMessage], user_text: str) -> str:
        """One non-streamed call of the answer model; the text of its answer.

        The answer model may be bound to the answer's JSON shape (a structured-output
        backend enforces it), so the map and merge prompts ask for that shape too and
        the partial answer is its "answer" field. A reply that is not JSON is taken whole.
        """
        reply = await self.llm.generate(
            [
                LLMMessage(role=Roles.SYSTEM, content=[MessageContent(type="text", text=system_prompt)]),
                *history,
                LLMMessage(role=Roles.USER, content=[MessageContent(type="text", text=user_text)]),
            ]
        )
        text = (reply.content[0].text or "").strip() if reply.content else ""
        return _answer_field(text)

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
