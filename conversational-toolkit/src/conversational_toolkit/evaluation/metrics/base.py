"""
Abstract base class for evaluation metrics.

Every metric, whether reference-based retrieval metric or LLM-as-judge generation metric, implements this interface.
"""

from abc import ABC, abstractmethod
from typing import Sequence

from conversational_toolkit.evaluation.data_models import EvaluationSample, MetricResult


class Metric(ABC):
    """
    Abstract base for all evaluation metrics.

    Retrieval metrics override 'compute' with pure arithmetic (no I/O). Generation metrics call a judge LLM inside 'compute'. Both share the same async interface so 'Evaluator' treats them identically.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique, human-readable metric identifier (e.g. 'HitRate@5', 'Faithfulness')."""

    @abstractmethod
    async def compute(self, samples: Sequence[EvaluationSample]) -> MetricResult:
        """Compute the metric over 'samples' and return a 'MetricResult'."""
