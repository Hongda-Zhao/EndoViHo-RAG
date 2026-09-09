"""Synthetic boundary labels are not real benchmark annotations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    AnnotationError,
    _validate_oracle_entry_against_gold,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    ConditionalAnswerGold,
    LiteratureGold,
    build_evaluation_question,
    build_oracle_entry,
)
from eve_relation_rag.experiments.rag_value_ablation.metrics import (
    refusal_observation_from_question,
    summarize_refusal,
)
from tests.experiments.test_rag_value_scoped_admission import (
    CHUNK,
    CORPUS,
    DATASET,
    _approval,
    _gold,
)


def _conditional() -> ConditionalAnswerGold:
    evidence = _gold("literature")
    assert isinstance(evidence, LiteratureGold)
    return ConditionalAnswerGold(
        conditions=("Synthetic source directly reports the requested association.",),
        answer_evidence=evidence,
        required_explanations=("Preserve the synthetic source qualifier.",),
        forbidden_claims=("Do not infer biological absence.",),
    )


def test_conditional_label_preserves_family_but_changes_refusal_denominator() -> None:
    question = build_evaluation_question(
        question_id="tests-only:boundary",
        family="unsupported",
        question_text="Synthetic?",
        review_status="approved",
        approval=_approval(),
        gold=_conditional(),
    )
    assert question.family == "unsupported"
    observation = refusal_observation_from_question(
        question,
        abstained=True,
        refusal_origin="model_abstention",
    )
    assert observation is not None
    assert not observation.expected_refusal
    metrics = summarize_refusal([observation])
    assert metrics.false_refusal_rate.numerator == metrics.false_refusal_rate.denominator == 1
    assert metrics.correct_refusal_rate.value is None
    accepted = refusal_observation_from_question(question, abstained=False, refusal_origin="none")
    assert accepted is not None and not accepted.unsafe_acceptance


def test_conditional_gold_requires_evidence_conditions_and_real_approval() -> None:
    with pytest.raises(ValidationError):
        ConditionalAnswerGold.model_validate({"gold_kind": "conditional_answer"})
    with pytest.raises(ValidationError):
        build_evaluation_question(
            question_id="tests-only:boundary",
            family="unsupported",
            question_text="Synthetic?",
            review_status="pending",
            gold=_conditional(),
        )
    pending = build_evaluation_question(
        question_id="tests-only:pending",
        family="unsupported",
        question_text="Synthetic?",
        review_status="pending",
    )
    assert (
        refusal_observation_from_question(pending, abstained=False, refusal_origin="none") is None
    )


def test_conditional_oracle_requires_the_approved_evidence_not_empty_refusal_evidence() -> None:
    question = build_evaluation_question(
        question_id="tests-only:conditional",
        family="unsupported",
        question_text="Synthetic?",
        review_status="approved",
        approval=_approval(),
        gold=_conditional(),
    )
    fields = {
        "question_id": question.question_id,
        "question_text_sha256": question.question_text_sha256,
        "review_status": "approved",
        "approval": _approval(),
        "structured_facts": None,
        "dataset_release_key": DATASET,
        "dataset_manifest_sha256": "d" * 64,
        "corpus_release_key": CORPUS,
        "corpus_manifest_sha256": "e" * 64,
        "source_attestation": (
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    }
    supplied = build_oracle_entry(
        **fields,
        evidence_disposition="evidence_supplied",
        literature_chunk_keys=(CHUNK,),
    )
    _validate_oracle_entry_against_gold(question, supplied)
    empty = build_oracle_entry(
        **fields,
        evidence_disposition="no_supporting_evidence",
        literature_chunk_keys=(),
    )
    with pytest.raises(AnnotationError, match="supplied oracle evidence"):
        _validate_oracle_entry_against_gold(question, empty)
