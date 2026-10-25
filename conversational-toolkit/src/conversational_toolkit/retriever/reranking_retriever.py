"""
LLM-based reranking retriever.

'RerankingRetriever' is a two-stage retriever: it first fetches a larger candidate pool from a base retriever, then asks an LLM to re-order the candidates by relevance to the query. This is useful when the base retriever (embedding similarity or BM25) retrieves the right documents but ranks them suboptimally.

Design note: configure the base retriever with 'top_k = candidate_pool_size' (e.g. 20) and set 'RerankingRetriever.top_k' to the final number you want returned (e.g. 5). The two 'top_k' values serve different purposes and are intentionally separate.

If the LLM call fails or returns unparseable JSON the retriever falls back to the original ranking from the base retriever, so the pipeline never breaks.
"""

import asyncio
import re
from collections import Counter
from textwrap import dedent
from typing import Any

from loguru import logger

from conversational_toolkit.llms.base import LLM, LLMMessage, MessageContent, Roles
from conversational_toolkit.retriever.base import KeywordQuery, Retriever
from conversational_toolkit.retriever.text_analysis import analyze_query, term_spans
from conversational_toolkit.utils.retriever import reciprocal_rank_fusion
from conversational_toolkit.utils.text import compact_markdown_tables
from conversational_toolkit.vectorstores.base import ChunkMatch, ChunkRecord


def _extract_ranking(text: str) -> list[int]:
    """Pull the ranking out of whatever the model produced.

    Small models — the ones most likely to be pointed at reranking — wrap the
    list in a second array, add markdown fences, append commentary full of
    bracketed indices, or get cut off mid-list by the token cap. Each of those
    still carries a usable order, so scan forward from the "ranking" key and
    collect integers until the list (or the object) closes: nesting flattens,
    a truncated tail is kept, and trailing prose is never reached.

    Raises ValueError when there is no ranking at all — the caller then falls
    back to the base retriever's order, which is honest and reported as such.
    """
    match = re.search(r'"ranking"\s*:\s*\[', text)
    if match is None:
        raise ValueError('no "ranking" list in response')

    depth, digits, found = 0, "", []
    for char in text[match.end() - 1 :]:
        if char == "[":
            depth += 1
            continue
        if char.isdigit():
            digits += char
            continue
        if digits:
            found.append(int(digits))
            digits = ""
        if char == "]":
            depth -= 1
            if depth == 0:
                break
        elif char == "}":
            # The object closed with the list still open (a nested-array slip).
            # Whatever follows is prose, and its [3]-style markers are not ranks.
            break
    if digits:  # truncated before any closing bracket
        found.append(int(digits))

    ranking = list(dict.fromkeys(found))  # a repeat would return one chunk twice
    if not ranking:
        raise ValueError("empty ranking")
    return ranking


def _passage(text: str, query_terms: list[str], width: int) -> str:
    """At most 'width' characters of 'text': the window holding the most query terms.

    Ranked by distinct terms first, then by occurrences, so a span naming every
    term of the question beats one repeating a single term. The window opens a
    little before its first hit for context. Without any hit, the start of the text.
    """
    if len(text) <= width:
        return text
    spans = term_spans(text, query_terms)
    if not spans:
        return text[:width]
    words = [text[s:e].lower() for s, e in spans]
    lead = width // 8
    best, best_score = 0, (0, 0)
    seen: Counter[str] = Counter()
    end = 0
    for first, (start, _) in enumerate(spans):
        # Sliding window over the hits: [start, start + width - lead) holds spans[first:end].
        end = max(end, first)  # a word longer than the window leaves it empty
        while end < len(spans) and spans[end][1] <= start + width - lead:
            seen[words[end]] += 1
            end += 1
        if end == first:
            continue
        score = (len(seen), end - first)
        if score > best_score:
            best, best_score = first, score
        seen[words[first]] -= 1
        if not seen[words[first]]:
            del seen[words[first]]
    begin = min(max(0, spans[best][0] - lead), len(text) - width)
    window = text[begin : begin + width]
    return ("… " if begin > 0 else "") + window + (" …" if begin + width < len(text) else "")


class RerankingRetriever(Retriever[ChunkMatch]):
    """
    Two-stage retriever that uses an LLM to rerank a candidate pool.

    The base retriever should be configured with a 'top_k' equal to the desired candidate pool size (typically 3-4x the final 'top_k'). The LLM receives the query and each candidate's whole content, table padding compacted — or, when the pool exceeds the budget, the passage around the query terms — and returns a ranked list of indices as JSON. The score assigned to each result is a linear decay from 1.0 (rank 1) to 0.0 (last rank).

    Attributes:
        retriever: The base retriever that supplies the candidate pool.
        llm: The language model used for reranking. A fast, cheap model is recommended since the reranking prompt is simple.
        max_prompt_chars: Budget for the candidates' text in the ranking prompt. Only when the pool exceeds it is each candidate cut, all to the same share, to the passage holding the most query terms. None means no limit.
    """

    def __init__(self, retriever: Retriever[Any], llm: LLM, top_k: int, max_prompt_chars: int | None = None) -> None:
        super().__init__(top_k)
        self.retriever = retriever
        self.llm = llm
        self.max_prompt_chars = max_prompt_chars
        self.phase_callback: Any = None
        self.last_rerank_stats: dict | None = None
        self._last_rerank_fallback: bool = False

    async def retrieve(self, query: str, filters: dict[str, Any] | None = None) -> list[ChunkMatch]:
        """Fetch candidates from the base retriever and rerank them with the LLM."""
        candidates: list[ChunkRecord] = await self.retriever.retrieve(query, filters=filters)  # type: ignore[assignment]
        kept, _ = await self._rerank(query, candidates)
        return kept

    async def retrieve_multi(
        self, queries: list[str], rank_query: str, filters: dict[str, Any] | None = None
    ) -> list[ChunkMatch]:
        """Like 'rank_pool', returning only the chunks that made the cut."""
        kept, _ = await self.rank_pool(queries, rank_query, filters)
        return kept

    async def rank_pool(
        self, queries: list[str], rank_query: str, filters: dict[str, Any] | None = None
    ) -> tuple[list[ChunkMatch], list[ChunkMatch]]:
        """Retrieve for several query variants, fuse them, then rerank once.

        Returns the top 'top_k' and, separately, the candidates that missed the
        cut, in ranked order. Both carry their 'pre_rerank' position.

        Expanded queries and a HyDE document exist to widen recall, not to be
        ranked against: calling `retrieve` per variant would spend one LLM call
        each and rank every pool in isolation, so a chunk found only by one
        variant never competes with the others. The fused pool is ranked
        against `rank_query` — the user's actual question — in a single call,
        and capped at the base retriever's `top_k` so the prompt does not grow
        with the number of variants.
        """
        per_query = await asyncio.gather(*[self.retriever.retrieve(q, filters=filters) for q in queries])
        candidates = reciprocal_rank_fusion(list(per_query))[: self.retriever.top_k]
        return await self._rerank(rank_query, candidates)

    async def _rerank(self, query: str, candidates: list[ChunkRecord]) -> tuple[list[ChunkMatch], list[ChunkMatch]]:
        if not candidates:
            return [], []

        if self.phase_callback:
            self.phase_callback("reranking")
        ranked_indices = await self._llm_rerank(query, candidates)

        n_candidates = len(candidates)
        final_top_k = set(ranked_indices[: self.top_k])
        original_top_k = set(range(min(self.top_k, n_candidates)))
        self.last_rerank_stats = {
            "candidates": n_candidates,
            "top_k": self.top_k,
            "swaps": len(final_top_k - original_top_k),
            "fallback": self._last_rerank_fallback,
        }

        n = len(ranked_indices)
        results: list[ChunkMatch] = []
        for position, original_idx in enumerate(ranked_indices):
            chunk = candidates[original_idx]
            score = (n - position) / n  # linear decay: 1.0 at rank 1, approaching 0 at rank n
            results.append(
                ChunkMatch(
                    id=chunk.id,
                    title=chunk.title,
                    content=chunk.content,
                    mime_type=chunk.mime_type,
                    metadata=chunk.metadata,
                    embedding=chunk.embedding,
                    score=score,
                    ranks={**getattr(chunk, "ranks", {}), "pre_rerank": original_idx + 1},
                )
            )
        return results[: self.top_k], results[self.top_k :]

    async def _llm_rerank(self, query: str, candidates: list[ChunkRecord]) -> list[int]:
        """Ask the LLM to rank the candidates and return a list of original indices.

        Returns the original order as a fallback if the LLM call fails or produces invalid JSON.
        """
        # Whole candidates: a passage deep in a long chunk is still a reason to rank it.
        # The budget only bites when the pool as a whole is too large, and then cuts
        # every candidate to the same share rather than starving the later ones —
        # each to the passage around the query terms, which is what the ranking judges.
        texts = [compact_markdown_tables(chunk.content) for chunk in candidates]
        if self.max_prompt_chars is not None and sum(len(t) for t in texts) > self.max_prompt_chars:
            share = self.max_prompt_chars // len(texts)
            expansions = query.expansions if isinstance(query, KeywordQuery) else ()
            query_terms = [t for q in (query, *expansions) for t in analyze_query(q)]
            texts = [_passage(t, query_terms, share) for t in texts]
        numbered = "\n\n".join(
            f"[{i}] {chunk.title or '(no title)'}\n{text}" for i, (chunk, text) in enumerate(zip(candidates, texts))
        )
        prompt = dedent(f"""
            Query: {query}

            Rank the following {len(candidates)} document chunks from most to least relevant
            to the query. Output only a JSON object with a single key "ranking" whose value
            is a list of the chunk indices ordered from most to least relevant.

            Chunks:
            {numbered}

            Output format: {{"ranking": [most_relevant_index, second_index, ...]}}

            Rules:
            - Output the JSON object and nothing else: no explanation, no reasoning,
              no self-correction, no markdown fences.
            - "ranking" is a flat list of integers. Do not nest it in another list.
            - Each index from 0 to {len(candidates) - 1} appears exactly once.
        """).strip()

        messages = [
            LLMMessage(
                role=Roles.SYSTEM,
                content=[
                    MessageContent(
                        type="text",
                        text=(
                            "You are an expert at assessing document relevance. "
                            "Reply with a single JSON object and nothing else."
                        ),
                    )
                ],
            ),
            LLMMessage(role=Roles.USER, content=[MessageContent(type="text", text=prompt)]),
        ]

        try:
            response = await self.llm.generate(messages)
            text = response.content[0].text or ""
            ranking: list[int] = [i for i in _extract_ranking(text) if 0 <= i < len(candidates)]
            if not ranking:
                raise ValueError("no valid candidate index in ranking")
            # Append any missing indices at the end (graceful fallback for partial rankings)
            seen = set(ranking)
            ranking += [i for i in range(len(candidates)) if i not in seen]
            self._last_rerank_fallback = False
            logger.info(f"RerankingRetriever: reranked {len(candidates)} candidates → {ranking[: self.top_k]}")
            return ranking
        except Exception as exc:
            self._last_rerank_fallback = True
            logger.warning(f"RerankingRetriever LLM call failed, using original order: {exc}")
            return list(range(len(candidates)))
