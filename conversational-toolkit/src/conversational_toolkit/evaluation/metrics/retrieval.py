"""
Reference-based retrieval quality metrics.

All metrics inherit from '_RetrievalMetric' and require 'relevant_chunk_ids' on each sample. Samples missing this field are skipped with a loguru warning so a partially-labelled dataset does not crash the evaluation run.

Available metrics:
    HitRate: fraction of queries where at least one relevant chunk is in the top k.
    MRR: mean reciprocal rank of the first relevant chunk in the top k.
    PrecisionAtK: mean fraction of top-k results that are relevant.
    RecallAtK: mean fraction of all relevant chunks found in the top k.
    NDCGAtK: normalised discounted cumulative gain with binary relevance.
"""

import math
from typing import Sequence

from loguru import logger

from conversational_toolkit.evaluation.data_models import EvaluationSample, MetricResult
from conversational_toolkit.evaluation.metrics.base import Metric


class _RetrievalMetric(Metric):
    """Shared base for all retrieval metrics. Holds cutoff 'k' and derives the metric name."""

    def __init__(self, k: int) -> None:
        self.k = k

    @property
    def name(self) -> str:
        return f"{self.__class__.__name__}@{self.k}"

    def _top_k_ids(self, sample: EvaluationSample) -> list[str]:
        return [chunk.id for chunk in sample.retrieved_chunks[: self.k]]

    def _usable(self, sample: EvaluationSample) -> bool:
        if not sample.relevant_chunk_ids:
            logger.warning(f"{self.name}: sample '{sample.query[:60]}' has no relevant_chunk_ids, skipping.")
            return False
        return True


class HitRate(_RetrievalMetric):
    """Fraction of queries where at least one relevant chunk appears in the top-k results."""

    async def compute(self, samples: Sequence[EvaluationSample]) -> MetricResult:
        scores: list[float] = []
        for s in samples:
            if not self._usable(s):
                continue
            top_k = self._top_k_ids(s)
            scores.append(1.0 if any(cid in s.relevant_chunk_ids for cid in top_k) else 0.0)
        mean = sum(scores) / len(scores) if scores else 0.0
        return MetricResult(metric_name=self.name, score=mean, per_sample_scores=scores)


class MRR(_RetrievalMetric):
    """Mean reciprocal rank of the first relevant chunk in the top-k results. Evaluates how early in the ranked list the first relevant chunk appears"""

    async def compute(self, samples: Sequence[EvaluationSample]) -> MetricResult:
        scores: list[float] = []
        for s in samples:
            if not self._usable(s):
                continue
            rr = 0.0
            for rank, cid in enumerate(self._top_k_ids(s), start=1):
                if cid in s.relevant_chunk_ids:
                    rr = 1.0 / rank
                    break
            scores.append(rr)
        mean = sum(scores) / len(scores) if scores else 0.0
        return MetricResult(metric_name=self.name, score=mean, per_sample_scores=scores)


class PrecisionAtK(_RetrievalMetric):
    """Mean fraction of the top-k retrieved chunks that are relevant."""

    async def compute(self, samples: Sequence[EvaluationSample]) -> MetricResult:
        scores: list[float] = []
        for s in samples:
            if not self._usable(s):
                continue
            top_k = self._top_k_ids(s)
            hits = sum(1 for cid in top_k if cid in s.relevant_chunk_ids)
            scores.append(hits / self.k)
        mean = sum(scores) / len(scores) if scores else 0.0
        return MetricResult(metric_name=self.name, score=mean, per_sample_scores=scores)


class RecallAtK(_RetrievalMetric):
    """Mean fraction of all relevant chunks that appear in the top-k results."""

    async def compute(self, samples: Sequence[EvaluationSample]) -> MetricResult:
        scores: list[float] = []
        for s in samples:
            if not self._usable(s):
                continue
            top_k = self._top_k_ids(s)
            hits = sum(1 for cid in top_k if cid in s.relevant_chunk_ids)
            scores.append(hits / len(s.relevant_chunk_ids))
        mean = sum(scores) / len(scores) if scores else 0.0
        return MetricResult(metric_name=self.name, score=mean, per_sample_scores=scores)


class NDCGAtK(_RetrievalMetric):
    """Normalised discounted cumulative gain with binary relevance and log2 position discount. Evaluates how well all the relevant chunks are ranked, accounting for the fact that lower positions are less useful."""

    async def compute(self, samples: Sequence[EvaluationSample]) -> MetricResult:
        scores: list[float] = []
        for s in samples:
            if not self._usable(s):
                continue
            top_k = self._top_k_ids(s)
            dcg = sum(
                1.0 / math.log2(rank + 1) for rank, cid in enumerate(top_k, start=1) if cid in s.relevant_chunk_ids
            )
            ideal_hits = min(len(s.relevant_chunk_ids), self.k)
            idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
            scores.append(dcg / idcg if idcg > 0 else 0.0)
        mean = sum(scores) / len(scores) if scores else 0.0
        return MetricResult(metric_name=self.name, score=mean, per_sample_scores=scores)
