"""
BM25 lexical retriever backed by 'rank-bm25'.

'rank-bm25' provides a well-tested BM25 Okapi implementation and several variants (BM25L, BM25Plus). The corpus is tokenised and indexed at construction time; retrieval is a pure in-memory operation with no I/O cost per query.

Typical usage: initialise from a list of 'ChunkRecord' objects already stored in a vector store, then combine with a 'VectorStoreRetriever' inside a 'HybridRetriever' for lexical + semantic search.
"""

import asyncio
import logging
from typing import Any, Callable

import numpy as np
from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

from conversational_toolkit.retriever.base import KeywordQuery, Retriever, SemanticOnlyQuery
from conversational_toolkit.retriever.text_analysis import detect_language, index_document, query_words, tokenize
from conversational_toolkit.vectorstores.base import ChunkMatch, VectorStore

logger = logging.getLogger(__name__)

# Weight of a 'KeywordQuery''s extra terms against the query's own words.
EXPANSION_WEIGHT = 0.5

# BM25 length normalisation: 0 ignores chunk length, 1 scales a term's weight fully by it.
# Below the usual 0.75, because chunks here range from a slide's few lines to 8,000
# characters of a glossary, and one mention in a long chunk still answers the question.
DEFAULT_B = 0.6


class BM25Retriever(Retriever[ChunkMatch]):
    """
    In-memory BM25 retriever over a fixed corpus of 'ChunkRecord' objects.

    Uses 'rank_bm25.BM25Okapi' under the hood. Chunks and queries go through 'text_analysis': stop words dropped, words stemmed — chunks in their own language (plus English stems in other-language chunks), queries in every supported language. Each query word scores once, by its best-matching form, so a word with many distinct stems weighs no more than one with a single stem. A 'SemanticOnlyQuery' returns no results. A 'KeywordQuery''s extra terms add to the score at 'EXPANSION_WEIGHT'.

    Attributes:
        corpus: The indexed document chunks.
        b: BM25 length normalisation, see 'DEFAULT_B'. Fixed when the index is built.
        search_listener: Called after every search with its query, terms, detected language and every hit it returns. For debugging tools; None means nobody listens.
    """

    def __init__(self, vector_store: VectorStore, top_k: int, b: float = DEFAULT_B) -> None:
        super().__init__(top_k)
        self.vs = vector_store
        self.b = b
        self.corpus = []
        self.tokenized = []
        self._bm25 = None
        self.search_listener: Callable[[dict[str, Any]], None] | None = None

    @staticmethod
    def _searchable_text(chunk: Any) -> str:
        """Chunk content plus filename/title, so lexical search also matches
        document names — e.g. "find X" over a filename that never appears in
        the body text. Metadata/title are cheap to include here since BM25
        tokenises this in memory at query time; embeddings are unaffected.

        Underscores are replaced with spaces before tokenising: the word
        pattern treats "_" as a word character, so an underscore-joined filename
        segment (e.g. "Komponenten_000014468_B_001") would otherwise become
        one glued token instead of separately matchable words.
        """
        source_file = (chunk.metadata or {}).get("source_file", "").replace("_", " ")
        return " ".join(part for part in (source_file, chunk.title, chunk.content) if part)

    async def retrieve(self, query: str, filters: dict[str, Any] | None = None) -> list[ChunkMatch]:
        """Score the corpus against 'query' using BM25 and return the top 'top_k' matches.

        'filters' post-filters scored results by chunk metadata (equality match). The
        BM25 index is built over the full corpus and cached; filtering happens after
        scoring so the cache remains valid across queries with different filters.
        """
        if isinstance(query, SemanticOnlyQuery):
            return []
        if self._bm25 is None:
            loop = asyncio.get_running_loop()
            self.corpus = await self.vs.get_chunks_by_filter()

            # Guard: if the vector store has no chunks yet, return early.
            # BM25Okapi raises ZeroDivisionError on an empty corpus.
            if not self.corpus:
                return []

            corpus = self.corpus

            # Analysing and indexing the corpus is CPU-bound — run in a thread to keep the event loop free.
            def _build() -> BM25Okapi:
                indexed = [index_document(self._searchable_text(chunk)) for chunk in corpus]
                bm25 = BM25Okapi([terms for terms, _ in indexed], b=self.b)
                # rank-bm25 takes a chunk's length from its term list, which here includes
                # the English stems of other-language chunks. Normalise by the word count.
                bm25.doc_len = [length for _, length in indexed]
                bm25.avgdl = max(sum(bm25.doc_len) / len(bm25.doc_len), 1.0)
                return bm25

            self._bm25 = await loop.run_in_executor(None, _build)

        words = query_words(query)
        query_terms = [t for forms in words for t in forms]
        logger.info(f"BM25 terms for {query[:80]!r}: {' '.join(dict.fromkeys(query_terms))}")
        score_array = self._score(words)
        expansion_terms: list[str] = []
        if isinstance(query, KeywordQuery) and query.expansions:
            # Each expansion is analysed on its own: they mix languages, and the
            # stop words of one would be detected and dropped from the others.
            # A word the question already has is not an expansion.
            own = set(query_terms)
            extra = [forms for e in query.expansions for forms in query_words(e) if own.isdisjoint(forms)]
            extra = list({tuple(forms): forms for forms in extra}.values())
            expansion_terms = list(dict.fromkeys(t for forms in extra for t in forms))
            logger.info(f"BM25 expansion terms for {query[:80]!r}: {' '.join(expansion_terms)}")
            if extra:
                score_array = score_array + EXPANSION_WEIGHT * self._score(extra)
        scores: list[float] = score_array.tolist()
        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        if filters:

            def _matches(i: int) -> bool:
                m = self.corpus[i].metadata or {}
                return all(str(m.get(k, "")) == str(v) for k, v in filters.items())

            ranked_indices = [i for i in ranked_indices if _matches(i)]
        top_indices = ranked_indices[: self.top_k]
        if self.search_listener is not None:
            self._notify(query, query_terms, expansion_terms, top_indices, scores)
        # A zero score means no query term occurs in the chunk: it only fills
        # top_k, so it is not reported as a keyword match.
        return [
            ChunkMatch(
                id=self.corpus[i].id,
                title=self.corpus[i].title,
                content=self.corpus[i].content,
                mime_type=self.corpus[i].mime_type,
                metadata=self.corpus[i].metadata,
                embedding=self.corpus[i].embedding,
                score=scores[i],
                ranks={"bm25": rank} if scores[i] > 0 else {},
            )
            for rank, i in enumerate(top_indices, start=1)
        ]

    def _score(self, words: list[list[str]]) -> np.ndarray:
        """BM25 scores of the corpus: each word adds the score of its best-matching form."""
        assert self._bm25 is not None
        by_term = {t: self._bm25.get_scores([t]) for t in dict.fromkeys(t for forms in words for t in forms)}
        total = np.zeros(len(self.corpus))
        for forms in words:
            total += np.max([by_term[t] for t in forms], axis=0)
        return total

    def _notify(
        self,
        query: str,
        query_terms: list[str],
        expansion_terms: list[str],
        top_indices: list[int],
        scores: list[float],
    ) -> None:
        assert self._bm25 is not None and self.search_listener is not None
        all_terms = list(dict.fromkeys([*query_terms, *expansion_terms]))
        hits = []
        # Every hit this search hands on, not a preview: the debug view has to show
        # where a chunk ranked, and whether it was in the pool at all.
        for i in top_indices:
            if scores[i] <= 0:
                break
            doc_terms = self._bm25.doc_freqs[i]
            chunk = self.corpus[i]
            hits.append(
                {
                    "id": chunk.id,
                    "source_file": (chunk.metadata or {}).get("source_file", ""),
                    "title": chunk.title,
                    "score": scores[i],
                    "matched_terms": [t for t in all_terms if t in doc_terms],
                }
            )
        self.search_listener(
            {
                "query": str(query),
                "terms": list(dict.fromkeys(query_terms)),
                "expansion_terms": expansion_terms,
                "language": detect_language(tokenize(query)),
                "hits": hits,
            }
        )

    def invalidate_cache(self) -> None:
        """
        Reset the BM25 index so it is rebuilt on the next retrieve() call.

        Call this whenever documents are added to or removed from the vector
        store, so the BM25 corpus stays in sync.
        """
        self._bm25 = None
        self.corpus = []
        self.tokenized = []
