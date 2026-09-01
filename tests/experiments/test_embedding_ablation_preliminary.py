from __future__ import annotations

from eve_relation_rag.experiments.embedding_ablation.preliminary import (
    compute_legacy_question_metrics,
    summarize_legacy_quality,
)
from eve_relation_rag.literature.benchmarking import BenchmarkQuestion


def test_legacy_preliminary_metrics_are_exact_without_review_or_category_fields() -> None:
    question = BenchmarkQuestion(
        question_key="q1",
        question="Where is the evidence?",
        relevant_chunk_keys=("chunk:sha256:" + "a" * 64, "chunk:sha256:" + "b" * 64),
    )
    metrics = compute_legacy_question_metrics(
        question,
        (
            "chunk:sha256:" + "c" * 64,
            "chunk:sha256:" + "a" * 64,
            "chunk:sha256:" + "d" * 64,
            "chunk:sha256:" + "b" * 64,
        ),
    )

    assert metrics["recall_at_1"] == "0.000000000000"
    assert metrics["recall_at_3"] == "0.500000000000"
    assert metrics["recall_at_5"] == "1.000000000000"
    assert metrics["mrr_at_10"] == "0.500000000000"
    assert metrics["ndcg_at_10"] == "0.650920929807"
    assert "category" not in metrics
    assert "review_status" not in metrics

    summary = summarize_legacy_quality((metrics, metrics))
    assert summary["question_count"] == 2
    assert summary["recall_at_5"] == "1.000000000000"
