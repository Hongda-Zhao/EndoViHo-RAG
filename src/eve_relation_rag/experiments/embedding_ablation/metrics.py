"""Deterministic retrieval-quality and latency metrics for the ablation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from pydantic import Field

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AnnotationQuestion,
    QuestionCategory,
)
from eve_relation_rag.literature.contracts import ChunkKey, StableToken, StrictFrozenSchema

_METRIC_QUANTUM = Decimal("0.000000000001")
_METRIC_PATTERN = r"^(?:0|1)\.[0-9]{12}$"


class MetricCalculationError(ValueError):
    """Raised when inputs cannot produce an exact, interpretable metric."""


class QuestionMetrics(StrictFrozenSchema):
    """Exact quality metrics for one approved question and one system."""

    question_id: StableToken
    category: QuestionCategory
    returned_chunk_keys: tuple[ChunkKey, ...]
    recall_at_1: str = Field(pattern=_METRIC_PATTERN)
    recall_at_3: str = Field(pattern=_METRIC_PATTERN)
    recall_at_5: str = Field(pattern=_METRIC_PATTERN)
    recall_at_10: str = Field(pattern=_METRIC_PATTERN)
    mrr_at_10: str = Field(pattern=_METRIC_PATTERN)
    ndcg_at_10: str = Field(pattern=_METRIC_PATTERN)
    excluded_hit_count_at_10: int = Field(ge=0)


class QualitySummary(StrictFrozenSchema):
    """Macro-averaged quality metrics for a non-empty question set."""

    question_count: int = Field(ge=1)
    recall_at_1: str = Field(pattern=_METRIC_PATTERN)
    recall_at_3: str = Field(pattern=_METRIC_PATTERN)
    recall_at_5: str = Field(pattern=_METRIC_PATTERN)
    recall_at_10: str = Field(pattern=_METRIC_PATTERN)
    mrr_at_10: str = Field(pattern=_METRIC_PATTERN)
    ndcg_at_10: str = Field(pattern=_METRIC_PATTERN)


class QualitySummaryByCategory(StrictFrozenSchema):
    """Overall and per-category macro summaries."""

    overall: QualitySummary
    by_category: dict[QuestionCategory, QualitySummary]


class LatencySummary(StrictFrozenSchema):
    """Nearest-rank p50/p95 over positive integer nanosecond samples."""

    sample_count: int = Field(ge=1)
    p50_ns: int = Field(ge=0)
    p95_ns: int = Field(ge=0)


def compute_question_metrics(
    question: AnnotationQuestion,
    returned_chunk_keys: Sequence[str],
) -> QuestionMetrics:
    """Compute group-aware Recall/MRR/nDCG without double-counting alternatives."""

    if question.review_status != "approved" or question.category is None:
        raise MetricCalculationError("only approved categorized questions may be scored")
    if not question.evidence_groups:
        raise MetricCalculationError("approved question has no evidence groups")
    returned = tuple(returned_chunk_keys)
    if len(returned) != len(set(returned)):
        raise MetricCalculationError("returned chunk keys must be unique")

    group_members = tuple(group.member_chunk_keys for group in question.evidence_groups)
    recall_values = {
        cutoff: _recall_at(returned, group_members=group_members, cutoff=cutoff)
        for cutoff in (1, 3, 5, 10)
    }
    first_relevant_rank = next(
        (
            rank
            for rank, chunk_key in enumerate(returned[:10], start=1)
            if any(chunk_key in members for members in group_members)
        ),
        None,
    )
    reciprocal_rank = (
        Decimal(0) if first_relevant_rank is None else Decimal(1) / Decimal(first_relevant_rank)
    )
    ndcg = _ndcg_at_10(returned, group_members)
    excluded = set(question.excluded_chunk_keys)
    return QuestionMetrics(
        question_id=question.question_id,
        category=question.category,
        returned_chunk_keys=returned,
        recall_at_1=_metric(recall_values[1]),
        recall_at_3=_metric(recall_values[3]),
        recall_at_5=_metric(recall_values[5]),
        recall_at_10=_metric(recall_values[10]),
        mrr_at_10=_metric(reciprocal_rank),
        ndcg_at_10=_metric(ndcg),
        excluded_hit_count_at_10=sum(chunk_key in excluded for chunk_key in returned[:10]),
    )


def summarize_quality(results: Sequence[QuestionMetrics]) -> QualitySummaryByCategory:
    """Macro-average quality overall and independently within each category."""

    materialized = tuple(results)
    if not materialized:
        raise MetricCalculationError("quality summary requires at least one question")
    question_ids = tuple(result.question_id for result in materialized)
    if len(question_ids) != len(set(question_ids)):
        raise MetricCalculationError("quality summary contains duplicate question IDs")
    grouped: defaultdict[QuestionCategory, list[QuestionMetrics]] = defaultdict(list)
    for result in materialized:
        grouped[result.category].append(result)
    return QualitySummaryByCategory(
        overall=_quality_summary(materialized),
        by_category={
            category: _quality_summary(tuple(grouped[category]))
            for category in sorted(grouped)
        },
    )


def summarize_latency(samples_ns: Iterable[int]) -> LatencySummary:
    """Use the fixed discrete nearest-rank definition: x[ceil(p*n)-1]."""

    samples = tuple(samples_ns)
    if not samples:
        raise MetricCalculationError("latency summary requires at least one sample")
    if any(type(sample) is not int or sample < 0 for sample in samples):
        raise MetricCalculationError("latency samples must be non-negative integer nanoseconds")
    ordered = tuple(sorted(samples))
    return LatencySummary(
        sample_count=len(ordered),
        p50_ns=_nearest_rank(ordered, numerator=50, denominator=100),
        p95_ns=_nearest_rank(ordered, numerator=95, denominator=100),
    )


def rank_shift(
    before: Sequence[str],
    after: Sequence[str],
) -> Mapping[str, int]:
    """Return pre-rank minus post-rank for an unchanged candidate set."""

    before_keys = tuple(before)
    after_keys = tuple(after)
    if len(before_keys) != len(set(before_keys)) or len(after_keys) != len(set(after_keys)):
        raise MetricCalculationError("rank-shift candidates must be unique")
    if set(before_keys) != set(after_keys):
        raise MetricCalculationError("rank shift requires an unchanged candidate set")
    before_rank = {key: rank for rank, key in enumerate(before_keys, start=1)}
    return {
        key: before_rank[key] - rank
        for rank, key in enumerate(after_keys, start=1)
    }


def _recall_at(
    returned: Sequence[str],
    *,
    group_members: Sequence[frozenset[str]],
    cutoff: int,
) -> Decimal:
    observed = set(returned[:cutoff])
    satisfied = sum(bool(observed & members) for members in group_members)
    return Decimal(satisfied) / Decimal(len(group_members))


def _ndcg_at_10(
    returned: Sequence[str],
    group_members: Sequence[frozenset[str]],
) -> Decimal:
    satisfied_groups: set[int] = set()
    dcg = Decimal(0)
    with localcontext() as context:
        context.prec = 50
        ln_two = Decimal(2).ln()
        for rank, chunk_key in enumerate(returned[:10], start=1):
            group_index = next(
                (
                    index
                    for index, members in enumerate(group_members)
                    if index not in satisfied_groups and chunk_key in members
                ),
                None,
            )
            if group_index is not None:
                satisfied_groups.add(group_index)
                dcg += ln_two / Decimal(rank + 1).ln()
        ideal_count = min(len(group_members), 10)
        idcg = sum(
            (ln_two / Decimal(rank + 1).ln() for rank in range(1, ideal_count + 1)),
            start=Decimal(0),
        )
        return dcg / idcg


def _quality_summary(results: Sequence[QuestionMetrics]) -> QualitySummary:
    return QualitySummary(
        question_count=len(results),
        recall_at_1=_mean(result.recall_at_1 for result in results),
        recall_at_3=_mean(result.recall_at_3 for result in results),
        recall_at_5=_mean(result.recall_at_5 for result in results),
        recall_at_10=_mean(result.recall_at_10 for result in results),
        mrr_at_10=_mean(result.mrr_at_10 for result in results),
        ndcg_at_10=_mean(result.ndcg_at_10 for result in results),
    )


def _nearest_rank(
    ordered: Sequence[int],
    *,
    numerator: int,
    denominator: int,
) -> int:
    rank = (numerator * len(ordered) + denominator - 1) // denominator
    return ordered[rank - 1]


def _mean(values: Iterable[str]) -> str:
    materialized = tuple(Decimal(value) for value in values)
    return _metric(sum(materialized, start=Decimal(0)) / Decimal(len(materialized)))


def _metric(value: Decimal) -> str:
    return f"{value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN):.12f}"
