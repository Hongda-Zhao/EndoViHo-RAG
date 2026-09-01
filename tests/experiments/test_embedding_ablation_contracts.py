from __future__ import annotations

import pytest
from pydantic import ValidationError

from eve_relation_rag.experiments.embedding_ablation.contracts import (
    AblationSystem,
    AnnotationQuestion,
    EvidenceGroup,
    ModelRepresentationContract,
    build_annotation_manifest,
)

CORPUS_KEY = "corpus:endoviho-rag:v0:20990101:001"
CHUNK_A = f"chunk:sha256:{'a' * 64}"
CHUNK_B = f"chunk:sha256:{'b' * 64}"
CHUNK_C = f"chunk:sha256:{'c' * 64}"


def test_approved_annotation_is_grouped_reviewed_and_checksum_bound() -> None:
    question = _approved_question()

    manifest = build_annotation_manifest(
        corpus_release_key=CORPUS_KEY,
        corpus_manifest_sha256="d" * 64,
        questions=(question,),
    )

    assert manifest.question_count == manifest.approved_question_count == 1
    assert manifest.approved_questions == (question,)
    assert len(manifest.gold_sha256) == 64
    assert len(manifest.annotation_manifest_sha256) == 64
    assert question.positive_chunk_keys == frozenset({CHUNK_A, CHUNK_B})


def test_pending_annotation_may_be_empty_but_cannot_claim_completed_review() -> None:
    pending = AnnotationQuestion(
        question_id="eve-q-pending",
        question="Which evidence should an expert review?",
        category=None,
        review_status="pending",
    )

    assert pending.required_chunk_keys == ()
    assert pending.evidence_groups == ()

    with pytest.raises(ValidationError, match="only approved"):
        pending.model_copy(update={"reviewer_id": "expert-1"}).model_validate(
            {**pending.model_dump(), "reviewer_id": "expert-1"}
        )


def test_approved_annotation_rejects_missing_review_and_ambiguous_alternatives() -> None:
    with pytest.raises(ValidationError, match="reviewer"):
        AnnotationQuestion(
            question_id="eve-q-1",
            question="What is the definition?",
            category="definition",
            required_chunk_keys=(CHUNK_A,),
            evidence_groups=(
                EvidenceGroup(group_id="e1", required_chunk_key=CHUNK_A),
            ),
            review_status="approved",
        )

    with pytest.raises(ValidationError, match="projections"):
        AnnotationQuestion(
            question_id="eve-q-1",
            question="What is the definition?",
            category="definition",
            required_chunk_keys=(CHUNK_A,),
            acceptable_alternative_chunk_keys=(CHUNK_B,),
            evidence_groups=(
                EvidenceGroup(group_id="e1", required_chunk_key=CHUNK_A),
            ),
            review_status="approved",
            reviewer_id="expert-1",
            reviewed_at="2099-01-01T00:00:00Z",
        )


def test_system_contract_requires_complete_reranker_identity_and_fixed_branches() -> None:
    baseline = AblationSystem(
        system_key="bge_small__fts_dense_summary__rrf60",
        embedding_model_key="embedding:test:bge",
        embedding_artifact_manifest_sha256="a" * 64,
        embedding_dimension=384,
    )
    assert baseline.rerank_candidate_depth is None
    assert baseline.top_k == 10

    with pytest.raises(ValidationError, match="supplied together"):
        baseline.model_copy(update={"rerank_candidate_depth": 20}).model_validate(
            {**baseline.model_dump(), "rerank_candidate_depth": 20}
        )
    with pytest.raises(ValidationError, match="query encoder identity"):
        AblationSystem.model_validate(
            {
                **baseline.model_dump(mode="python"),
                "query_encoder_model_key": "embedding:test:query",
            }
        )
    with pytest.raises(ValidationError, match="dense branches"):
        AblationSystem(
            system_key="unsafe",
            embedding_model_key="embedding:test:bge",
            embedding_artifact_manifest_sha256="a" * 64,
            embedding_dimension=384,
            dense_branches=("full",),
        )


def test_representation_contract_rejects_dimensionless_embedding_and_reranker_vectors() -> None:
    with pytest.raises(ValidationError, match="requires a dimension"):
        _representation(dimension=None)
    with pytest.raises(ValidationError, match="must not declare vector semantics"):
        ModelRepresentationContract(
            task_kind="reranker",
            dimension=384,
            pooling="not_applicable",
            normalization="none",
            similarity="not_applicable",
            query_format="{query}",
            passage_format="{passage}",
            max_sequence_length=512,
            truncation_policy="reject",
            truncation_side="none",
            output_dtype="float32",
        )


def _approved_question() -> AnnotationQuestion:
    return AnnotationQuestion(
        question_id="eve-q-1",
        question="What is the approved definition?",
        category="definition",
        required_chunk_keys=(CHUNK_A,),
        acceptable_alternative_chunk_keys=(CHUNK_B,),
        excluded_chunk_keys=(CHUNK_C,),
        evidence_groups=(
            EvidenceGroup(
                group_id="e1",
                required_chunk_key=CHUNK_A,
                acceptable_alternative_chunk_keys=(CHUNK_B,),
            ),
        ),
        review_status="approved",
        reviewer_id="expert-1",
        reviewed_at="2099-01-01T00:00:00Z",
    )


def _representation(*, dimension: int | None) -> ModelRepresentationContract:
    return ModelRepresentationContract(
        task_kind="embedding",
        dimension=dimension,
        pooling="cls",
        normalization="l2",
        similarity="cosine",
        query_format="query: {query}",
        passage_format="{passage}",
        max_sequence_length=512,
        truncation_policy="reject",
        truncation_side="none",
        output_dtype="float32",
    )
