"""
Evaluation and validation module for the conversational toolkit.

Generation quality (faithfulness, answer correctness, context precision) is evaluated via RAGAS -> use the adapter:

    from conversational_toolkit.evaluation.adapters import evaluate_with_ragas

Retrieval metrics that do not require a judge LLM (HitRate, MRR, NDCG, ...) are available directly:

    from conversational_toolkit.evaluation import (
        Evaluator, EvaluationSample,
        HitRate, MRR, NDCGAtK,
    )
"""

from conversational_toolkit.evaluation.data_models import EvaluationReport, EvaluationSample, MetricResult
from conversational_toolkit.evaluation.evaluator import Evaluator
from conversational_toolkit.evaluation.metrics.base import Metric
from conversational_toolkit.evaluation.metrics.retrieval import HitRate, MRR, NDCGAtK, PrecisionAtK, RecallAtK

__all__ = [
    "MRR",
    "EvaluationReport",
    "EvaluationSample",
    "Evaluator",
    "HitRate",
    "Metric",
    "MetricResult",
    "NDCGAtK",
    "PrecisionAtK",
    "RecallAtK",
]
