from __future__ import annotations

import pytest

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AnnotationQuestion,
    EvidenceGroup,
)
from eve_relation_rag.experiments.embedding_ablation.metrics import (
    MetricCalculationError,
    compute_question_metrics,
    rank_shift,
    summarize_latency,
    summarize_quality,
)

CHUNK_A = f"chunk:sha256:{'a' * 64}"
CHUNK_B = f"chunk:sha256:{'b' * 64}"
CHUNK_C = f"chunk:sha256:{'c' * 64}"
CHUNK_D = f"chunk:sha256:{'d' * 64}"
CHUNK_E = f"chunk:sha256:{'e' * 64}"
CHUNK_F = f"chunk:sha256:{'f' * 64}"


def test_group_aware_metrics_are_exact_and_do_not_double_count_alternatives() -> None:
    question = _question()

    result = compute_question_metrics(
        question,
        (CHUNK_D, CHUNK_B, CHUNK_E, CHUNK_F, CHUNK_C),
    )

    assert result.recall_at_1 == "0.000000000000"
    assert result.recall_at_3 == "0.500000000000"
    assert result.recall_at_5 == result.recall_at_10 == "1.000000000000"
    assert result.mrr_at_10 == "0.500000000000"
    assert result.ndcg_at_10 == "0.624050520004"
    assert result.excluded_hit_count_at_10 == 1

    duplicate_same_group = compute_question_metrics(
        question,
        (CHUNK_A, CHUNK_B, CHUNK_C),
    )
    assert duplicate_same_group.recall_at_3 == "1.000000000000"
    assert duplicate_same_group.ndcg_at_10 == "0.919720789148"


def test_metrics_reject_pending_questions_and_duplicate_retrieval_keys() -> None:
    pending = AnnotationQuestion(
        question_id="pending-q",
        question="What remains pending?",
        category=None,
        review_status="pending",
    )
    with pytest.raises(MetricCalculationError, match="approved"):
        compute_question_metrics(pending, ())
    with pytest.raises(MetricCalculationError, match="unique"):
        compute_question_metrics(_question(), (CHUNK_A, CHUNK_A))


def test_macro_summary_latency_quantiles_and_rank_shift_are_exact() -> None:
    first = compute_question_metrics(_question(), (CHUNK_A, CHUNK_C))
    second_question = _question().model_copy(
        update={"question_id": "q-2", "category": "method"}
    )
    second = compute_question_metrics(second_question, (CHUNK_D,))

    summary = summarize_quality((first, second))

    assert summary.overall.question_count == 2
    assert summary.overall.recall_at_5 == "0.500000000000"
    assert summary.overall.mrr_at_10 == "0.500000000000"
    assert set(summary.by_category) == {"definition", "method"}
    latency = summarize_latency(range(1, 21))
    assert latency.p50_ns == 10
    assert latency.p95_ns == 19
    assert rank_shift((CHUNK_A, CHUNK_B, CHUNK_C), (CHUNK_C, CHUNK_A, CHUNK_B)) == {
        CHUNK_C: 2,
        CHUNK_A: -1,
        CHUNK_B: -1,
    }


def _question() -> AnnotationQuestion:
    return AnnotationQuestion(
        question_id="q-1",
        question="Which two evidence groups answer the definition question?",
        category="definition",
        required_chunk_keys=(CHUNK_A, CHUNK_C),
        acceptable_alternative_chunk_keys=(CHUNK_B,),
        excluded_chunk_keys=(CHUNK_D,),
        evidence_groups=(
            EvidenceGroup(
                group_id="e1",
                required_chunk_key=CHUNK_A,
                acceptable_alternative_chunk_keys=(CHUNK_B,),
            ),
            EvidenceGroup(group_id="e2", required_chunk_key=CHUNK_C),
        ),
        review_status="approved",
        reviewer_id="expert-1",
        reviewed_at="2099-01-01T00:00:00Z",
    )
