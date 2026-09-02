from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.rag_value_ablation.contracts import (
    EvaluationAnswer,
    EvaluationClaim,
    EvidenceCitation,
    HumanApproval,
    MechanicalValidation,
    RawContextSegment,
    StructuredGold,
    UnsupportedGold,
    build_evaluation_question,
    build_evidence_pack,
    build_question_manifest,
    mechanically_validate_answer,
)

DOCUMENT = f"document:sha256:{'a' * 64}"
CHUNK = f"chunk:sha256:{'b' * 64}"
CORPUS = "corpus:endoviho-rag:v0:20990101:001"


def test_pending_questions_carry_no_real_gold_and_manifest_has_no_gold_hash() -> None:
    question = build_evaluation_question(
        question_id="structured-pending-001",
        family="structured",
        question_text="How many included loci are in the selected release?",
        review_status="pending",
        authoring_notes="Candidate wording only; expert review required.",
    )
    manifest = build_question_manifest((question,))

    assert manifest.approved_question_count == 0
    assert manifest.gold_sha256 is None
    assert manifest.family_counts == {
        "structured": 1,
        "literature": 0,
        "hybrid": 0,
        "unsupported": 0,
    }
    with pytest.raises(ValidationError, match="only approved"):
        build_evaluation_question(
            question_id="structured-pending-002",
            family="structured",
            question_text="List included loci.",
            review_status="pending",
            gold=_structured_gold(),
        )


def test_approved_question_requires_human_approval_matching_gold_and_checksums() -> None:
    question = build_evaluation_question(
        question_id="structured-approved-001",
        family="structured",
        question_text="How many included loci are in the selected release?",
        review_status="approved",
        approval=_approval(),
        gold=_structured_gold(),
    )
    manifest = build_question_manifest(
        (question,),
        dataset_release_key="release:test:v0:20990101:001",
        dataset_manifest_sha256="d" * 64,
    )

    assert manifest.approved_questions == (question,)
    assert manifest.gold_sha256 is not None
    assert question.question_text_sha256 == hashlib.sha256(
        question.question_text.encode()
    ).hexdigest()
    with pytest.raises(ValidationError, match="family does not match"):
        build_evaluation_question(
            question_id="bad-family",
            family="literature",
            question_text="How many included loci are in the selected release?",
            review_status="approved",
            approval=_approval(),
            gold=_structured_gold(),
        )


def test_gold_contracts_reject_empty_structured_truth_and_weak_refusal_labels() -> None:
    with pytest.raises(ValidationError, match="at least one exact"):
        StructuredGold(
            release_key="release:test:v0:20990101:001",
            release_manifest_sha256="d" * 64,
        )
    with pytest.raises(ValidationError):
        UnsupportedGold(
            refusal_category="prevalence_not_established",
            prohibited_downstream_stages=(),
            required_explanations=("The release does not define prevalence.",),
            forbidden_claims=("A lineage has the highest prevalence.",),
        )


def test_common_answer_and_mechanical_validation_are_evidence_bound() -> None:
    evidence = _literature_evidence_pack()
    answer = EvaluationAnswer(
        answer_text="The cited method was used.",
        abstained=False,
        claims=(
            EvaluationClaim(
                claim_id="C1",
                text="The cited method was used.",
                claim_type="literature_fact",
                citation_ids=("D1",),
            ),
        ),
        cited_chunk_ids=(CHUNK,),
    )

    assert mechanically_validate_answer(answer, evidence) == MechanicalValidation(
        passed=True,
        issue_codes=(),
    )
    bad = answer.model_copy(update={"cited_chunk_ids": ()})
    validation = mechanically_validate_answer(bad, evidence)
    assert validation.passed is False
    assert validation.issue_codes == ("cited_chunk_set_mismatch",)

    with pytest.raises(ValidationError, match="literature factual claims"):
        EvaluationClaim(
            claim_id="C1",
            text="An uncited literature assertion.",
            claim_type="literature_fact",
        )
    with pytest.raises(ValidationError, match="abstained answer"):
        EvaluationAnswer(
            answer_text="Insufficient evidence.",
            abstained=True,
            claims=(
                EvaluationClaim(
                    claim_id="C1",
                    text="A claim despite abstention.",
                    claim_type="interpretation",
                ),
            ),
        )


def test_evidence_pack_records_exact_model_visible_bytes_and_has_no_system_label() -> None:
    evidence = _literature_evidence_pack()

    assert evidence.construction.context_byte_count > 0
    assert evidence.pack_sha256
    payload = evidence.model_dump_json()
    assert "system_key" not in payload
    assert "review_status" not in payload
    assert "gold" not in payload


def test_raw_context_supports_structured_and_document_claim_references() -> None:
    structured_text = "release_key: release:test:v0:20990101:001"
    document_text = "The source describes a synthetic detection method."
    segments = (
        RawContextSegment(
            segment_id="R1",
            source_kind="structured_export",
            source_key="raw-source:structured",
            source_sha256="1" * 64,
            byte_start=0,
            byte_end=len(structured_text.encode()),
            text=structured_text,
            text_sha256=hashlib.sha256(structured_text.encode()).hexdigest(),
        ),
        RawContextSegment(
            segment_id="R2",
            source_kind="document",
            source_key="raw-source:document",
            source_sha256="2" * 64,
            byte_start=0,
            byte_end=len(document_text.encode()),
            text=document_text,
            text_sha256=hashlib.sha256(document_text.encode()).hexdigest(),
        ),
    )
    evidence = build_evidence_pack(
        question_id="hybrid-raw-001",
        question_text="What does the raw context establish?",
        raw_context_segments=segments,
        policy_sha256="e" * 64,
        tokenizer_key="tokenizer:synthetic",
        model_context_limit_tokens=4096,
        reserved_output_tokens=512,
        input_token_count=100,
        context_token_count=50,
    )
    answer = EvaluationAnswer(
        answer_text="The export names a release and the document describes a method.",
        abstained=False,
        claims=(
            EvaluationClaim(
                claim_id="C1",
                text="The export names the selected release.",
                claim_type="structured_fact",
                citation_ids=("R1",),
            ),
            EvaluationClaim(
                claim_id="C2",
                text="The document describes a method.",
                claim_type="literature_fact",
                citation_ids=("R2",),
            ),
        ),
        cited_chunk_ids=(),
    )

    assert mechanically_validate_answer(answer, evidence).passed is True


def _approval() -> HumanApproval:
    return HumanApproval(
        reviewer_key="expert-001",
        reviewed_at="2099-01-01T00:00:00Z",
        attestation=(
            "I independently reviewed this annotation and approve it for this benchmark."
        ),
    )


def _structured_gold() -> StructuredGold:
    return StructuredGold(
        exact_count=1,
        metric_key="distinct_included_locus_count",
        record_keys=("locus:eve:v1:sha256:" + "c" * 64,),
        assembly_accession_versions=("GCA_000001.1",),
        sequence_accession_versions=("NC_000001.1",),
        locus_keys=("locus:eve:v1:sha256:" + "c" * 64,),
        coordinates=(),
        detection_call_keys=(),
        release_key="release:test:v0:20990101:001",
        release_manifest_sha256="d" * 64,
        required_limitation_codes=(
            "assembly_local_locus_is_not_independent_integration_event",
        ),
    )


def _literature_evidence_pack():
    text = "The synthetic fixture describes a detection method."
    citation = EvidenceCitation(
        citation_id="D1",
        document_key=DOCUMENT,
        chunk_key=CHUNK,
        locator_text="Synthetic methods, paragraph 1",
        text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    return build_evidence_pack(
        question_id="literature-001",
        question_text="How were regions detected?",
        citations=(citation,),
        policy_sha256="e" * 64,
        tokenizer_key="tokenizer:synthetic",
        model_context_limit_tokens=4096,
        reserved_output_tokens=512,
        input_token_count=100,
        context_token_count=50,
    )
