from __future__ import annotations

from pathlib import Path

import pytest

from eve_relation_rag.experiments.rag_value_ablation.candidates import (
    CandidateSetError,
    build_candidate_questions,
    build_pending_oracle_template,
    candidate_audit_bytes,
    oracle_schema_bytes,
    oracle_template_bytes,
    questions_template_bytes,
    validate_candidate_questions,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    CandidateQuestionMetadata,
    EvaluationQuestion,
    OracleEvidenceEntry,
    build_evaluation_question,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes


def test_candidate_set_is_balanced_pending_unique_and_parser_checked() -> None:
    questions = build_candidate_questions()
    audit = validate_candidate_questions(questions)

    assert audit.artifact_status == "authoring_only_not_gold"
    assert audit.question_count == 64
    assert audit.family_counts == {
        "hybrid": 16,
        "literature": 16,
        "structured": 16,
        "unsupported": 16,
    }
    assert audit.route_counts == audit.family_counts
    assert audit.pending_count == 64
    assert audit.parser_applicable_count == 32
    assert audit.parser_accepted_count == 32
    assert audit.parser_rejection_count == 0
    assert audit.normalized_duplicate_count == 0
    assert audit.evaluation_focus_duplicate_count == 0
    assert audit.semantic_boundary_violation_count == 0
    assert audit.route_mismatch_count == 0
    assert audit.gold_annotation_count == 0
    assert audit.oracle_annotation_count == 0
    assert all(question.review_status == "pending" for question in questions)
    assert all(question.approval is None and question.gold is None for question in questions)
    assert all(question.candidate_metadata is not None for question in questions)
    for question in questions:
        metadata = question.candidate_metadata
        assert metadata is not None
        assert "README.md" in metadata.wording_sources
        assert any(source.startswith("tests/") for source in metadata.wording_sources)
        if question.family in {"structured", "hybrid"}:
            assert (
                "src/eve_relation_rag/planning/query_plans.py"
                in metadata.wording_sources
            )


def test_oracle_template_is_blank_pending_and_question_bound() -> None:
    questions = build_candidate_questions()
    entries = build_pending_oracle_template(questions)

    assert len(entries) == len(questions) == 64
    for question, entry in zip(questions, entries, strict=True):
        assert entry.question_id == question.question_id
        assert entry.question_text_sha256 == question.question_text_sha256
        assert entry.review_status == "pending"
        assert entry.approval is None
        assert entry.evidence_disposition is None
        assert entry.structured_facts is None
        assert entry.literature_chunk_keys == ()
        assert entry.dataset_release_key is None
        assert entry.corpus_release_key is None
        assert entry.source_attestation is None

    rejected = build_evaluation_question(
        question_id=questions[0].question_id,
        family=questions[0].family,
        question_text=questions[0].question_text,
        review_status="rejected",
        candidate_metadata=questions[0].candidate_metadata,
        authoring_notes=questions[0].authoring_notes,
    )
    with pytest.raises(CandidateSetError, match="require pending questions"):
        build_pending_oracle_template((rejected,))


def test_committed_candidate_and_annotation_templates_are_canonical() -> None:
    directory = Path(__file__).parents[2] / "benchmark" / "rag_value_ablation"

    assert (directory / "questions_template.jsonl").read_bytes() == questions_template_bytes()
    assert (
        directory / "oracle_annotations_template.jsonl"
    ).read_bytes() == oracle_template_bytes()
    assert (
        directory / "oracle_annotation_schema.json"
    ).read_bytes() == oracle_schema_bytes()
    assert (
        directory / "candidate_question_audit.json"
    ).read_bytes() == candidate_audit_bytes()
    assert (directory / "question_schema.json").read_bytes() == (
        canonical_json_bytes(EvaluationQuestion.model_json_schema()) + b"\n"
    )

    question_lines = (directory / "questions_template.jsonl").read_text().splitlines()
    oracle_lines = (
        directory / "oracle_annotations_template.jsonl"
    ).read_text().splitlines()
    assert len(question_lines) == len(oracle_lines) == 64
    assert all(EvaluationQuestion.model_validate_json(line) for line in question_lines)
    assert all(OracleEvidenceEntry.model_validate_json(line) for line in oracle_lines)


def test_candidate_audit_rejects_duplicate_wording() -> None:
    questions = list(build_candidate_questions())
    original = questions[0]
    questions[0] = build_evaluation_question(
        question_id=original.question_id,
        family=original.family,
        question_text=questions[1].question_text,
        review_status="pending",
        candidate_metadata=original.candidate_metadata,
        authoring_notes=original.authoring_notes,
    )

    with pytest.raises(CandidateSetError, match="question text must be unique"):
        validate_candidate_questions(questions)


def test_candidate_audit_rejects_data_semantics_drift() -> None:
    questions = list(build_candidate_questions())
    index = next(
        index for index, question in enumerate(questions) if question.family == "structured"
    )
    original = questions[index]
    metadata = original.candidate_metadata
    assert metadata is not None
    questions[index] = build_evaluation_question(
        question_id=original.question_id,
        family=original.family,
        question_text=original.question_text,
        review_status="pending",
        candidate_metadata=CandidateQuestionMetadata(
            wording_sources=metadata.wording_sources,
            evaluation_focus=metadata.evaluation_focus,
            expected_route=metadata.expected_route,
            expected_structured_intent=metadata.expected_structured_intent,
            expected_refusal_code=metadata.expected_refusal_code,
            semantic_boundary_codes=("prevalence_not_established",),
            parser_fixture_profile=metadata.parser_fixture_profile,
            uses_fixture_entities=metadata.uses_fixture_entities,
        ),
        authoring_notes=original.authoring_notes,
    )

    with pytest.raises(CandidateSetError, match="semantic code exceeds"):
        validate_candidate_questions(questions)
