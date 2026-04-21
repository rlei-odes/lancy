"""
Data models for the evaluation module.

'EvaluationSample' bundles a single query-answer pair with the retrieved context and optional ground-truth labels needed by the metrics. 'MetricResult' holds the aggregated score and per-sample breakdown for one metric. 'EvaluationReport' collects all metric results for a batch run and exposes a convenience 'summary' method for quick comparison.
"""

from typing import Any, Sequence

from pydantic import BaseModel, Field

from conversational_toolkit.llms.base import LLMMessage
from conversational_toolkit.vectorstores.base import ChunkRecord


class EvaluationSample(BaseModel):
    """A single query-answer pair with associated context and optional ground-truth labels.

    Attributes:
        query: The user question posed to the system.
        answer: The system-generated answer to evaluate.
        retrieved_chunks: The document chunks retrieved and passed to the LLM.
        history: The conversation turns that preceded this query, in chronological order.
            Populated by 'Evaluator.build_samples_from_agent'. Required for multi-turn
            RAGAS evaluation via 'to_ragas_multiturn_dataset'.
        ground_truth_answer: Optional reference answer for correctness metrics.
        relevant_chunk_ids: Set of chunk IDs considered relevant for retrieval metrics.
            Required by 'HitRate', 'MRR', 'PrecisionAtK', 'RecallAtK', 'NDCGAtK'.
        metadata: Arbitrary key-value annotations for traceability.
    """

    query: str
    answer: str
    retrieved_chunks: Sequence[ChunkRecord] = Field(default_factory=list)
    history: list[LLMMessage] = Field(default_factory=list)
    ground_truth_answer: str | None = None
    relevant_chunk_ids: set[str] = Field(default_factory=set)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetricResult(BaseModel):
    """The output of a single metric computed over a batch of samples.

    Attributes:
        metric_name: Identifier matching the metric's 'name' property.
        score: Mean score across all samples, normalised to [0, 1].
        per_sample_scores: Individual scores in the same order as the input samples.
            'None' when per-sample breakdown is not available.
        details: Free-form extra data, e.g. LLM judge rationale strings.
    """

    metric_name: str
    score: float
    per_sample_scores: list[float] | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    """Aggregated results for a full evaluation run.

    Attributes:
        results: One 'MetricResult' per metric passed to 'Evaluator'.
        num_samples: Number of 'EvaluationSample' objects evaluated.
        metadata: Arbitrary annotations attached at evaluation time.
    """

    results: list[MetricResult]
    num_samples: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> dict[str, float]:
        """Return a flat mapping of metric name to mean score for quick inspection."""
        return {r.metric_name: r.score for r in self.results}
