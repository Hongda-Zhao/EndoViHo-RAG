"""Strict, immutable contracts for the isolated retrieval ablation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.literature.contracts import (
    ANCHOR_POLICY_KEY,
    FTS_POLICY_KEY,
    AnchorKey,
    ChunkKey,
    CorpusReleaseKey,
    QuestionText,
    RetrievalAnchor,
    Rfc3339Utc,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

type QuestionCategory = Literal[
    "definition",
    "method",
    "classification",
    "evidence",
    "limitation",
    "taxonomy",
]
type ReviewStatus = Literal["pending", "approved", "rejected"]
type ModelTaskKind = Literal["embedding", "reranker"]
type NormalizationKind = Literal["l2", "none"]
type SimilarityKind = Literal["cosine", "dot_product", "not_applicable"]
type TrustStatus = Literal["trusted", "test_only", "failed"]
type RetrievalTier = Literal["anchored", "corpus_fill"]

_SYSTEM_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")


class EvidenceGroup(StrictFrozenSchema):
    """One required evidence unit and its expert-approved substitutes."""

    group_id: StableToken
    required_chunk_key: ChunkKey
    acceptable_alternative_chunk_keys: tuple[ChunkKey, ...] = ()

    @field_validator("acceptable_alternative_chunk_keys")
    @classmethod
    def canonical_alternatives(cls, keys: tuple[str, ...]) -> tuple[str, ...]:
        if len(keys) != len(set(keys)):
            raise ValueError("evidence-group alternatives must be unique")
        return tuple(sorted(keys))

    @model_validator(mode="after")
    def required_is_not_an_alternative(self) -> Self:
        if self.required_chunk_key in self.acceptable_alternative_chunk_keys:
            raise ValueError("required chunk cannot also be an alternative")
        return self

    @property
    def member_chunk_keys(self) -> frozenset[str]:
        return frozenset((self.required_chunk_key, *self.acceptable_alternative_chunk_keys))


class AnnotationQuestion(StrictFrozenSchema):
    """One human-reviewed retrieval question with explicit evidence semantics."""

    question_id: StableToken
    question: QuestionText
    category: QuestionCategory | None
    anchors: tuple[RetrievalAnchor, ...] = ()
    required_chunk_keys: tuple[ChunkKey, ...] = ()
    acceptable_alternative_chunk_keys: tuple[ChunkKey, ...] = ()
    excluded_chunk_keys: tuple[ChunkKey, ...] = ()
    evidence_groups: tuple[EvidenceGroup, ...] = ()
    review_status: ReviewStatus
    reviewer_id: StableToken | None = None
    reviewed_at: Rfc3339Utc | None = None
    annotation_notes: str | None = Field(default=None, max_length=4000)

    @field_validator(
        "required_chunk_keys",
        "acceptable_alternative_chunk_keys",
        "excluded_chunk_keys",
    )
    @classmethod
    def canonical_chunk_keys(cls, keys: tuple[str, ...]) -> tuple[str, ...]:
        if len(keys) != len(set(keys)):
            raise ValueError("annotation chunk-key collections must not contain duplicates")
        return tuple(sorted(keys))

    @field_validator("anchors")
    @classmethod
    def canonical_anchors(
        cls, anchors: tuple[RetrievalAnchor, ...]
    ) -> tuple[RetrievalAnchor, ...]:
        keys = tuple(anchor.anchor_key for anchor in anchors)
        if len(keys) != len(set(keys)):
            raise ValueError("annotation anchors must not contain duplicate anchor keys")
        return tuple(sorted(anchors, key=lambda anchor: anchor.anchor_key))

    @field_validator("evidence_groups")
    @classmethod
    def canonical_groups(cls, groups: tuple[EvidenceGroup, ...]) -> tuple[EvidenceGroup, ...]:
        group_ids = tuple(group.group_id for group in groups)
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("evidence group IDs must be unique")
        return tuple(sorted(groups, key=lambda group: group.group_id))

    @model_validator(mode="after")
    def validate_review_and_evidence(self) -> Self:
        required = set(self.required_chunk_keys)
        alternatives = set(self.acceptable_alternative_chunk_keys)
        excluded = set(self.excluded_chunk_keys)
        if required & alternatives or excluded & (required | alternatives):
            raise ValueError("required, alternative, and excluded chunk keys must be disjoint")

        seen_members: set[str] = set()
        projected_required: set[str] = set()
        projected_alternatives: set[str] = set()
        for group in self.evidence_groups:
            members = set(group.member_chunk_keys)
            if seen_members & members:
                raise ValueError("a positive chunk key may belong to only one evidence group")
            seen_members.update(members)
            projected_required.add(group.required_chunk_key)
            projected_alternatives.update(group.acceptable_alternative_chunk_keys)
        if self.evidence_groups and (
            required != projected_required or alternatives != projected_alternatives
        ):
            raise ValueError("top-level positive chunk keys must equal evidence-group projections")

        if self.review_status == "approved":
            if self.category is None:
                raise ValueError("approved question requires a category")
            if not self.evidence_groups:
                raise ValueError("approved question requires at least one evidence group")
            if self.reviewer_id is None or self.reviewed_at is None:
                raise ValueError("approved question requires reviewer identity and review time")
        elif self.reviewer_id is not None or self.reviewed_at is not None:
            raise ValueError("only approved questions may carry completed review metadata")
        return self

    @property
    def positive_chunk_keys(self) -> frozenset[str]:
        return frozenset((*self.required_chunk_keys, *self.acceptable_alternative_chunk_keys))


class AnnotationManifest(StrictFrozenSchema):
    """Self-checksummed annotation input; only approved questions are benchmarkable."""

    annotation_schema_version: Literal["embedding-ablation-annotations-v1"]
    corpus_release_key: CorpusReleaseKey
    corpus_manifest_sha256: Sha256
    question_count: int = Field(ge=1)
    approved_question_count: int = Field(ge=0)
    gold_sha256: Sha256
    annotation_manifest_sha256: Sha256
    questions: tuple[AnnotationQuestion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts_and_hashes(self) -> Self:
        if self.question_count != len(self.questions):
            raise ValueError("question_count does not match questions")
        expected_approved = sum(
            question.review_status == "approved" for question in self.questions
        )
        if self.approved_question_count != expected_approved:
            raise ValueError("approved_question_count does not match questions")
        question_ids = tuple(question.question_id for question in self.questions)
        if question_ids != tuple(sorted(question_ids)):
            raise ValueError("annotation questions must be ordered by question_id")
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("annotation question IDs must be unique")
        if self.gold_sha256 != canonical_json_sha256(self.questions):
            raise ValueError("gold_sha256 does not match annotation questions")
        payload = self.model_dump(mode="python")
        del payload["annotation_manifest_sha256"]
        if self.annotation_manifest_sha256 != canonical_json_sha256(payload):
            raise ValueError("annotation_manifest_sha256 does not match manifest")
        return self

    @property
    def approved_questions(self) -> tuple[AnnotationQuestion, ...]:
        return tuple(
            question for question in self.questions if question.review_status == "approved"
        )


def build_annotation_manifest(
    *,
    corpus_release_key: str,
    corpus_manifest_sha256: str,
    questions: Sequence[AnnotationQuestion],
) -> AnnotationManifest:
    """Build a canonical, self-checksummed annotation manifest."""

    ordered = tuple(sorted(questions, key=lambda question: question.question_id))
    payload: dict[str, object] = {
        "annotation_schema_version": "embedding-ablation-annotations-v1",
        "corpus_release_key": corpus_release_key,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "question_count": len(ordered),
        "approved_question_count": sum(
            question.review_status == "approved" for question in ordered
        ),
        "gold_sha256": canonical_json_sha256(ordered),
        "questions": ordered,
    }
    return AnnotationManifest.model_validate(
        {
            **payload,
            "annotation_manifest_sha256": canonical_json_sha256(payload),
        }
    )


class ArtifactFileRecord(StrictFrozenSchema):
    """One exact regular file in a local model artifact directory."""

    relative_path: str = Field(min_length=1, max_length=2000)
    byte_size: int = Field(ge=0)
    sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("artifact path must be canonical, relative, and contained")
        return value


class ModelRepresentationContract(StrictFrozenSchema):
    """Exact serialization and vector/score semantics for one local model."""

    task_kind: ModelTaskKind
    dimension: int | None = Field(default=None, ge=1, le=65536)
    pooling: str = Field(min_length=1, max_length=128)
    normalization: NormalizationKind
    similarity: SimilarityKind
    query_format: str = Field(min_length=1, max_length=4000)
    passage_format: str = Field(min_length=1, max_length=4000)
    max_sequence_length: int = Field(ge=1, le=1_000_000)
    truncation_policy: Literal["reject", "truncate_tail", "truncate_head", "model_native"]
    truncation_side: Literal["none", "left", "right"]
    output_dtype: Literal["float32"]

    @model_validator(mode="after")
    def validate_task_semantics(self) -> Self:
        if self.task_kind == "embedding":
            if self.dimension is None:
                raise ValueError("embedding representation requires a dimension")
            if self.similarity == "not_applicable":
                raise ValueError("embedding representation requires a similarity")
        elif self.dimension is not None or self.similarity != "not_applicable":
            raise ValueError("reranker representation must not declare vector semantics")
        if self.truncation_policy == "reject" and self.truncation_side != "none":
            raise ValueError("reject truncation policy requires truncation_side=none")
        if self.truncation_policy != "reject" and self.truncation_side == "none":
            raise ValueError("truncating policy requires an explicit truncation side")
        return self


class ModelArtifactManifest(StrictFrozenSchema):
    """Complete local model identity; its exact file bytes are approved out of band."""

    manifest_schema_version: Literal["embedding-ablation-model-artifact-v1"]
    model_key: StableToken
    model_id: str = Field(min_length=1, max_length=255)
    exact_revision: str = Field(min_length=40, max_length=64)
    license: str = Field(min_length=1, max_length=255)
    license_review_status: Literal["approved", "pending", "rejected"]
    representation: ModelRepresentationContract
    runtime_key: StableToken
    local_files_only: Literal[True]
    trust_remote_code: Literal[False]
    files: tuple[ArtifactFileRecord, ...] = Field(min_length=1)

    @field_validator("exact_revision")
    @classmethod
    def immutable_revision(cls, value: str) -> str:
        if _REVISION_RE.fullmatch(value) is None:
            raise ValueError("exact_revision must be a lowercase immutable commit hash")
        return value

    @field_validator("files")
    @classmethod
    def canonical_file_order(
        cls, files: tuple[ArtifactFileRecord, ...]
    ) -> tuple[ArtifactFileRecord, ...]:
        paths = tuple(item.relative_path for item in files)
        if paths != tuple(sorted(paths)):
            raise ValueError("artifact files must be ordered by relative_path")
        if len(paths) != len(set(paths)):
            raise ValueError("artifact file paths must be unique")
        return files


class RecordedModelIdentity(StrictFrozenSchema):
    """Portable model identity embedded directly in every machine-result manifest."""

    artifact_manifest_schema_version: StableToken
    artifact_manifest_sha256: Sha256
    model_key: StableToken
    model_id: str = Field(min_length=1, max_length=255)
    exact_revision: str = Field(min_length=40, max_length=64)
    license: str = Field(min_length=1, max_length=255)
    representation: ModelRepresentationContract
    runtime_key: StableToken

    @field_validator("exact_revision")
    @classmethod
    def immutable_revision(cls, value: str) -> str:
        if _REVISION_RE.fullmatch(value) is None:
            raise ValueError("exact_revision must be a lowercase immutable commit hash")
        return value


class AblationSystem(StrictFrozenSchema):
    """One exact candidate-generation and optional reranking configuration."""

    system_key: str = Field(min_length=1, max_length=128)
    embedding_model_key: StableToken
    embedding_artifact_manifest_sha256: Sha256
    embedding_dimension: int = Field(ge=1, le=65536)
    query_encoder_model_key: StableToken | None = None
    query_encoder_artifact_manifest_sha256: Sha256 | None = None
    encoder_bundle_manifest_sha256: Sha256 | None = None
    reranker_model_key: StableToken | None = None
    reranker_artifact_manifest_sha256: Sha256 | None = None
    rerank_candidate_depth: Literal[20, 50] | None = None
    reranker_batch_size: int | None = Field(default=None, ge=1, le=512)
    fts_policy_key: Literal["fts:postgres16:english-weighted-v2"] = FTS_POLICY_KEY
    anchor_policy_key: Literal["anchor:endoviho-curated-retrieval-v2"] = ANCHOR_POLICY_KEY
    dense_branches: tuple[Literal["full", "title_abstract"], ...] = (
        "full",
        "title_abstract",
    )
    branch_candidate_depth: Literal[100] = 100
    rrf_k: Literal[60] = 60
    top_k: Literal[10] = 10

    @field_validator("system_key")
    @classmethod
    def safe_system_key(cls, value: str) -> str:
        if _SYSTEM_KEY_RE.fullmatch(value) is None:
            raise ValueError("system_key contains unsafe characters")
        return value

    @model_validator(mode="after")
    def validate_reranker_tuple(self) -> Self:
        query_fields = (
            self.query_encoder_model_key,
            self.query_encoder_artifact_manifest_sha256,
            self.encoder_bundle_manifest_sha256,
        )
        if any(value is None for value in query_fields) and any(
            value is not None for value in query_fields
        ):
            raise ValueError(
                "separate query encoder identity and bundle checksum must be supplied together"
            )
        reranker_fields = (
            self.reranker_model_key,
            self.reranker_artifact_manifest_sha256,
            self.rerank_candidate_depth,
            self.reranker_batch_size,
        )
        if any(value is None for value in reranker_fields) and any(
            value is not None for value in reranker_fields
        ):
            raise ValueError("reranker identity and candidate depth must be supplied together")
        if self.dense_branches != ("full", "title_abstract"):
            raise ValueError("all systems must keep the approved dense branches in exact order")
        return self

    @property
    def effective_query_encoder_model_key(self) -> str:
        return self.query_encoder_model_key or self.embedding_model_key

    @property
    def effective_query_encoder_artifact_manifest_sha256(self) -> str:
        return (
            self.query_encoder_artifact_manifest_sha256
            or self.embedding_artifact_manifest_sha256
        )


class HardwareRecord(StrictFrozenSchema):
    """Hardware/runtime identity shared by every system in one experiment."""

    hardware_schema_version: Literal["embedding-ablation-hardware-v1"]
    cpu_model: str = Field(min_length=1, max_length=512)
    physical_core_count: int = Field(ge=1)
    logical_core_count: int = Field(ge=1)
    ram_bytes: int = Field(ge=1)
    operating_system: str = Field(min_length=1, max_length=512)
    kernel_release: str = Field(min_length=1, max_length=512)
    machine_architecture: str = Field(min_length=1, max_length=128)
    accelerator: str = Field(min_length=1, max_length=512)
    accelerator_runtime: str = Field(min_length=1, max_length=512)
    numerical_backend: str = Field(min_length=1, max_length=512)
    python_version: str = Field(min_length=1, max_length=128)
    uv_lock_sha256: Sha256
    postgresql_version: str = Field(min_length=1, max_length=512)
    pgvector_version: str = Field(min_length=1, max_length=128)
    thread_settings: dict[str, str]

    @model_validator(mode="after")
    def validate_core_counts(self) -> Self:
        if self.physical_core_count > self.logical_core_count:
            raise ValueError("physical_core_count cannot exceed logical_core_count")
        return self


class RankedCandidate(StrictFrozenSchema):
    """A retrieval candidate whose text is intentionally kept out of result contracts."""

    chunk_key: ChunkKey
    retrieval_tier: RetrievalTier
    pre_rerank_rank: int = Field(ge=1)
    fts_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    summary_vector_rank: int | None = Field(default=None, ge=1)
    rrf_score: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{12}$")
    reranker_score: float | None = None
    final_rank: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def at_least_one_branch(self) -> Self:
        if self.fts_rank is None and self.vector_rank is None and self.summary_vector_rank is None:
            raise ValueError("ranked candidate requires at least one retrieval branch")
        return self


def anchor_keys(anchors: Sequence[RetrievalAnchor]) -> tuple[AnchorKey, ...]:
    """Return canonical anchor keys for manifest and snapshot comparisons."""

    keys = tuple(sorted(anchor.anchor_key for anchor in anchors))
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate anchor keys")
    return keys
