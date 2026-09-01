"""Exact metrics for explicitly preliminary runs over the legacy 13-question gold."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from eve_relation_rag.literature.benchmarking import BenchmarkQuestion

_METRIC_QUANTUM = Decimal("0.000000000001")


class PreliminaryMetricError(ValueError):
    """Raised when legacy-gold metrics would be ambiguous or inexact."""


def compute_legacy_question_metrics(
    question: BenchmarkQuestion,
    returned_chunk_keys: Sequence[str],
) -> dict[str, object]:
    """Score one legacy question without assigning category or review approval."""

    returned = tuple(returned_chunk_keys)
    relevant = frozenset(question.relevant_chunk_keys)
    if len(returned) != len(set(returned)):
        raise PreliminaryMetricError("returned chunk keys must be unique")
    if not relevant:
        raise PreliminaryMetricError("legacy question has no relevant chunk keys")
    first_relevant_rank = next(
        (
            rank
            for rank, chunk_key in enumerate(returned[:10], start=1)
            if chunk_key in relevant
        ),
        None,
    )
    reciprocal_rank = (
        Decimal(0)
        if first_relevant_rank is None
        else Decimal(1) / Decimal(first_relevant_rank)
    )
    return {
        "question_key": question.question_key,
        "recall_at_1": _metric(_recall(returned, relevant, 1)),
        "recall_at_3": _metric(_recall(returned, relevant, 3)),
        "recall_at_5": _metric(_recall(returned, relevant, 5)),
        "recall_at_10": _metric(_recall(returned, relevant, 10)),
        "mrr_at_10": _metric(reciprocal_rank),
        "ndcg_at_10": _metric(_ndcg(returned, relevant)),
    }


def summarize_legacy_quality(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    """Macro-average exact decimal metric strings for a non-empty legacy result set."""

    materialized = tuple(rows)
    if not materialized:
        raise PreliminaryMetricError("legacy quality summary requires questions")
    keys = (
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10",
    )
    return {
        "question_count": len(materialized),
        **{
            key: _mean(str(row[key]) for row in materialized)
            for key in keys
        },
    }


def _recall(returned: Sequence[str], relevant: frozenset[str], cutoff: int) -> Decimal:
    observed = set(returned[:cutoff])
    return Decimal(len(observed & relevant)) / Decimal(len(relevant))


def _ndcg(returned: Sequence[str], relevant: frozenset[str]) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        ln_two = Decimal(2).ln()
        dcg = sum(
            (
                ln_two / Decimal(rank + 1).ln()
                for rank, chunk_key in enumerate(returned[:10], start=1)
                if chunk_key in relevant
            ),
            start=Decimal(0),
        )
        ideal_count = min(len(relevant), 10)
        idcg = sum(
            (ln_two / Decimal(rank + 1).ln() for rank in range(1, ideal_count + 1)),
            start=Decimal(0),
        )
        return dcg / idcg


def _mean(values: Iterable[str]) -> str:
    materialized = tuple(Decimal(value) for value in values)
    if not materialized:
        raise PreliminaryMetricError("metric mean requires values")
    return _metric(sum(materialized, start=Decimal(0)) / Decimal(len(materialized)))


def _metric(value: Decimal) -> str:
    return f"{value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN):.12f}"
