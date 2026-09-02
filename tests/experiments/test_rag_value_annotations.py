from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from eve_relation_rag.experiments.rag_value_ablation.annotations import (
    AnnotationError,
    load_oracle_manifest,
    load_question_manifest,
    require_approved_questions,
    require_trusted_question_set,
    validate_oracle_coverage,
    write_new_canonical_json,
)
from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    HumanApproval,
    StructuredGold,
    build_evaluation_question,
    build_oracle_entry,
    build_oracle_manifest,
    build_question_manifest,
)


def test_approved_question_and_separately_approved_oracle_round_trip(tmp_path: Path) -> None:
    gold = _gold()
    approval = _approval("question-reviewer")
    question = build_evaluation_question(
        question_id="structured-001",
        family="structured",
        question_text="Count included loci.",
        review_status="approved",
        approval=approval,
        gold=gold,
    )
    questions = build_question_manifest(
        (question,),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
    )
    oracle_entry = build_oracle_entry(
        question_id=question.question_id,
        question_text_sha256=question.question_text_sha256,
        review_status="approved",
        approval=_approval("oracle-reviewer"),
        evidence_disposition="evidence_supplied",
        structured_facts=gold,
        literature_chunk_keys=(),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
        source_attestation=(
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    )
    oracle = build_oracle_manifest((oracle_entry,))
    question_path = tmp_path / "questions.json"
    oracle_path = tmp_path / "oracle.json"
    question_file_sha = write_new_canonical_json(question_path, questions)
    oracle_file_sha = write_new_canonical_json(oracle_path, oracle)

    loaded_questions = load_question_manifest(question_path, question_file_sha)
    loaded_oracle = load_oracle_manifest(oracle_path, oracle_file_sha)
    assert require_approved_questions(loaded_questions) == (question,)
    validate_oracle_coverage(loaded_questions, loaded_oracle)

    with pytest.raises(AnnotationError, match="already exists"):
        write_new_canonical_json(question_path, questions)
    with pytest.raises(AnnotationError, match="not approved"):
        load_question_manifest(question_path, "0" * 64)


def test_pending_questions_and_oracle_entries_cannot_enter_trusted_benchmark() -> None:
    pending = build_evaluation_question(
        question_id="pending-001",
        family="structured",
        question_text="Which value still needs review?",
        review_status="pending",
    )
    questions = build_question_manifest((pending,))

    with pytest.raises(AnnotationError, match="at least one approved"):
        require_approved_questions(questions)


def test_trusted_question_set_enforces_preregistered_family_counts() -> None:
    gold = _gold()
    question = build_evaluation_question(
        question_id="structured-001",
        family="structured",
        question_text="Count included loci.",
        review_status="approved",
        approval=_approval("question-reviewer"),
        gold=gold,
    )
    manifest = build_question_manifest(
        (question,),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
        corpus_release_key="corpus:endoviho-rag:v0:20990101:001",
        corpus_manifest_sha256="e" * 64,
    )

    with pytest.raises(AnnotationError, match="60-80 approved"):
        require_trusted_question_set(manifest)


def test_approved_oracle_can_attest_that_unsupported_question_has_no_evidence() -> None:
    entry = build_oracle_entry(
        question_id="unsupported-001",
        question_text_sha256="a" * 64,
        review_status="approved",
        approval=_approval("oracle-reviewer"),
        evidence_disposition="no_supporting_evidence",
        source_attestation=(
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    )

    assert entry.structured_facts is None
    assert entry.literature_chunk_keys == ()


def test_oracle_question_and_release_checksums_must_match_approved_questions() -> None:
    gold = _gold()
    question = build_evaluation_question(
        question_id="structured-001",
        family="structured",
        question_text="Count included loci.",
        review_status="approved",
        approval=_approval("question-reviewer"),
        gold=gold,
    )
    questions = build_question_manifest(
        (question,),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
    )
    mismatched = build_oracle_entry(
        question_id=question.question_id,
        question_text_sha256=hashlib.sha256(b"different question").hexdigest(),
        review_status="approved",
        approval=_approval("oracle-reviewer"),
        evidence_disposition="evidence_supplied",
        structured_facts=gold,
        literature_chunk_keys=(),
        dataset_release_key=gold.release_key,
        dataset_manifest_sha256=gold.release_manifest_sha256,
        source_attestation=(
            "Evidence was selected manually and not generated from model or retriever output."
        ),
    )

    with pytest.raises(AnnotationError, match="question checksum"):
        validate_oracle_coverage(questions, build_oracle_manifest((mismatched,)))


def _approval(reviewer_key: str) -> HumanApproval:
    return HumanApproval(
        reviewer_key=reviewer_key,
        reviewed_at="2099-01-01T00:00:00Z",
        attestation=(
            "I independently reviewed this annotation and approve it for this benchmark."
        ),
    )


def _gold() -> StructuredGold:
    return StructuredGold(
        exact_count=1,
        metric_key="distinct_included_locus_count",
        release_key="release:test:v0:20990101:001",
        release_manifest_sha256="d" * 64,
    )
