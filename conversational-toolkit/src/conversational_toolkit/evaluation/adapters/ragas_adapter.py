"""
RAGAS integration adapter.

Available functions:
    to_ragas_dataset: Convert samples to a RAGAS 'EvaluationDataset' of 'SingleTurnSample'.
    to_ragas_multiturn_dataset: Convert samples to a RAGAS 'EvaluationDataset' of 'MultiTurnSample'.
    evaluate_with_ragas: Run RAGAS metrics and return a toolkit 'EvaluationReport'.
"""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
from typing import Any, Sequence

from ragas import EvaluationDataset  # type: ignore[import-untyped]
from ragas import MultiTurnSample  # type: ignore[import-untyped]
from ragas import SingleTurnSample  # type: ignore[import-untyped]
from ragas.messages import AIMessage as RagasAIMessage  # type: ignore[import-untyped]
from ragas.messages import HumanMessage as RagasHumanMessage  # type: ignore[import-untyped]
import ragas  # type: ignore[import-untyped]

from conversational_toolkit.evaluation.data_models import EvaluationReport, EvaluationSample, MetricResult
from conversational_toolkit.llms.base import Roles


def _to_ragas_messages(sample: EvaluationSample) -> list[Any]:
    """Build a RAGAS message list from a sample's history and current turn.

    Toolkit 'USER' messages become 'RagasHumanMessage', 'ASSISTANT' messages become 'RagasAIMessage'. 'SYSTEM' and 'TOOL' messages are skipped because RAGAS multi-turn format has no direct equivalent for them.

    The current query and answer are appended as the final user/assistant turn.
    """
    messages: list[Any] = []
    for msg in sample.history:
        if msg.role == Roles.USER:
            messages.append(RagasHumanMessage(content=msg.content[0].text or "" if msg.content else ""))
        elif msg.role == Roles.ASSISTANT:
            messages.append(RagasAIMessage(content=msg.content[0].text or "" if msg.content else ""))
        # SYSTEM and TOOL messages are intentionally skipped
    messages.append(RagasHumanMessage(content=sample.query))
    if sample.answer:
        messages.append(RagasAIMessage(content=sample.answer))
    return messages


def to_ragas_dataset(samples: Sequence[EvaluationSample]) -> Any:
    """Convert a sequence of 'EvaluationSample' objects to a RAGAS 'EvaluationDataset' of 'SingleTurnSample'.

    Each sample is mapped to one 'SingleTurnSample'. Use this for single-turn Q&A evaluation. For conversational / multi-turn evaluation use 'to_ragas_multiturn_dataset'.

    Maps fields as follows:
        query               -> user_input
        answer              -> response
        retrieved_chunks    -> retrieved_contexts (chunk content strings)
        ground_truth_answer -> reference

    Args:
        samples: The samples to convert.

    Returns:
        A 'ragas.EvaluationDataset' ready to pass to 'ragas.evaluate()'.
    """
    ragas_samples = [
        SingleTurnSample(
            user_input=s.query,
            response=s.answer,
            retrieved_contexts=[chunk.content for chunk in s.retrieved_chunks],
            reference=s.ground_truth_answer,
        )
        for s in samples
    ]
    return EvaluationDataset(samples=ragas_samples)  # type: ignore[arg-type]


def to_ragas_multiturn_dataset(samples: Sequence[EvaluationSample]) -> Any:
    """Convert a sequence of 'EvaluationSample' objects to a RAGAS 'EvaluationDataset' of 'MultiTurnSample'.

    Each sample's 'history' plus the current 'query' / 'answer' are assembled into a RAGAS message list: toolkit 'USER' messages become 'HumanMessage', 'ASSISTANT' messages become 'AIMessage'. 'SYSTEM' and 'TOOL' messages are skipped.

    Populate 'history' by using 'Evaluator.build_samples_from_agent', which captures the conversation history that was passed to each agent call.

    Maps fields as follows:
        history + query + answer -> user_input (list of HumanMessage / AIMessage)
        ground_truth_answer      -> reference

    Args:
        samples: The samples to convert. Each sample should have a non-empty 'history' for multi-turn evaluation to be meaningful.

    Returns:
        A 'ragas.EvaluationDataset' ready to pass to 'ragas.evaluate()'.
    """
    ragas_samples = [
        MultiTurnSample(
            user_input=_to_ragas_messages(s),
            reference=s.ground_truth_answer,
        )
        for s in samples
    ]
    return EvaluationDataset(samples=ragas_samples)  # type: ignore[arg-type]


def evaluate_with_ragas(
    samples: Sequence[EvaluationSample],
    metrics: list[Any],
    llm: Any | None = None,
    embeddings: Any | None = None,
    multiturn: bool = False,
) -> EvaluationReport:
    """Run RAGAS evaluation and return a 'EvaluationReport'.

    Args:
        samples: The samples to evaluate.
        metrics: RAGAS metric objects, e.g. '[ragas.metrics.Faithfulness()]'.
        llm: Optional RAGAS-compatible LLM wrapper. Uses the RAGAS default if 'None'.
        embeddings: Optional RAGAS-compatible embeddings wrapper.
        multiturn: When 'True', converts samples with 'to_ragas_multiturn_dataset' instead of 'to_ragas_dataset'. Use this together with multi-turn RAGAS metrics such as 'AgentGoalAccuracy'.

    Returns:
        An 'EvaluationReport' with one 'MetricResult' per RAGAS metric column.
    """
    dataset = to_ragas_multiturn_dataset(samples) if multiturn else to_ragas_dataset(samples)
    kwargs: dict[str, Any] = {"dataset": dataset, "metrics": metrics}
    if llm is not None:
        kwargs["llm"] = llm
    if embeddings is not None:
        kwargs["embeddings"] = embeddings

    ragas_result = ragas.evaluate(**kwargs)
    scores_df = ragas_result.to_pandas()  # type: ignore[union-attr]

    # to_pandas() includes input columns (user_input, retrieved_contexts, response) alongside metric score columns. Filter to only the metric columns.
    metric_names = {m.name for m in metrics}
    score_columns = [col for col in scores_df.columns if col in metric_names]

    metric_results: list[MetricResult] = [
        MetricResult(
            metric_name=col,
            score=float(pd.to_numeric(scores_df[col], errors="coerce").mean()),
            per_sample_scores=[float(v) for v in pd.to_numeric(scores_df[col], errors="coerce").tolist()],
        )
        for col in score_columns
    ]
    return EvaluationReport(results=metric_results, num_samples=len(samples))
