"""Strict contracts for the isolated RAG-value ablation.

The models in this module carry syntax and integrity only.  They never grant access to a
DatasetRelease, CorpusRelease, model, or provider.  Trusted execution still requires the
production capability gates and separately approved human annotations.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from eve_relation_rag.domain.keys import (
    is_versioned_assembly_accession,
    is_versioned_contig_accession,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    EMBEDDING_REVISION,
    FTS_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    CanonicalText,
    ChunkKey,
    CorpusReleaseKey,
    DocumentKey,
    NonEmptyText,
    QuestionText,
    Rfc3339Utc,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_bytes, canonical_json_sha256
from eve_relation_rag.retrieval.structured.results import QuerySuccess, StructuredResult

type QuestionFamily = Literal["structured", "literature", "hybrid", "unsupported"]
type ReviewStatus = Literal["pending", "approved", "rejected"]
type TrustStatus = Literal["trusted", "test_only", "failed"]
type ClaimType = Literal[
    "structured_fact",
    "literature_fact",
    "interpretation",
    "limitation",
]
type SupportLabel = Literal[
    "fully_supported",
    "partially_supported",
    "unsupported",
    "not_assessable",
]
type SystemKey = Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6"]
type EvidenceMode = Literal[
    "none",
    "raw_context",
    "keyword_literature",
    "hybrid_literature",
    "structured",
    "structured_first_hybrid",
    "oracle",
]
type DependencyKind = Literal[
    "database",
    "structured_retrieval",
    "corpus",
    "fts",
    "embedding_provider",
    "dense_index",
    "summary_index",
    "rrf",
    "raw_context_loader",
    "oracle_loader",
    "llm_provider",
]
type ExecutionStage = Literal[
    "context_construction",
    "structured_planning",
    "release_binding",
    "structured_retrieval",
    "anchor_resolution",
    "fts_retrieval",
    "dense_retrieval",
    "summary_retrieval",
    "rrf_fusion",
    "chunk_hydration",
    "oracle_load",
    "generation",
    "mechanical_validation",
    "deterministic_render",
]

_CLAIM_ID_RE = re.compile(r"^C[1-9][0-9]*$")
_CITATION_ID_RE = re.compile(r"^[DR][1-9][0-9]*$")
_RAW_SEGMENT_ID_RE = re.compile(r"^R[1-9][0-9]*$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _validate_assembly_accession(value: str) -> str:
    if not is_versioned_assembly_accession(value):
        raise ValueError("assembly accession must be an exact GCA_/GCF_ accession.version")
    return value


def _validate_sequence_accession(value: str) -> str:
    if not is_versioned_contig_accession(value):
        raise ValueError("sequence accession must be an exact accession.version")
    return value


AssemblyAccessionVersion = Annotated[
    str,
    Field(min_length=1, max_length=32),
    AfterValidator(_validate_assembly_accession),
]
SequenceAccessionVersion = Annotated[
    str,
    Field(min_length=1, max_length=64),
    AfterValidator(_validate_sequence_accession),
]


class HumanApproval(StrictFrozenSchema):
    """One accountable human approval; no default identity is ever supplied."""

    reviewer_key: StableToken
    reviewed_at: Rfc3339Utc
    attestation: Literal[
        "I independently reviewed this annotation and approve it for this benchmark."
    ]


class CoordinateGold(StrictFrozenSchema):
    """One exact 0-based half-open placement expected by structured scoring."""

    sequence_accession_version: SequenceAccessionVersion
    start0: int = Field(ge=0)
    end0: int = Field(gt=0)
    strand: Literal["+", "-"]
    coordinate_convention: Literal["0-based-half-open"] = "0-based-half-open"

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.start0 >= self.end0:
            raise ValueError("coordinate start0 must be smaller than end0")
        return self

    def sort_key(self) -> tuple[str, int, int, str]:
        return (self.sequence_accession_version, self.start0, self.end0, self.strand)


class StructuredGold(StrictFrozenSchema):
    """Human-approved exact structured values relevant to one question."""

    gold_kind: Literal["structured"] = "structured"
    exact_count: int | None = Field(default=None, ge=0)
    metric_key: StableToken | None = None
    record_keys: tuple[StableToken, ...] | None = None
    assembly_accession_versions: tuple[AssemblyAccessionVersion, ...] | None = None
    sequence_accession_versions: tuple[SequenceAccessionVersion, ...] | None = None
    locus_keys: tuple[StableToken, ...] | None = None
    coordinates: tuple[CoordinateGold, ...] | None = None
    detection_call_keys: tuple[StableToken, ...] | None = None
    release_key: StableToken
    release_manifest_sha256: Sha256
    required_limitation_codes: tuple[StableToken, ...] = ()

    @field_validator(
        "record_keys",
        "assembly_accession_versions",
        "sequence_accession_versions",
        "locus_keys",
        "detection_call_keys",
        "required_limitation_codes",
    )
    @classmethod
    def canonical_tokens(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if values is None:
            return None
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("structured gold collections must be sorted and unique")
        return values

    @field_validator("coordinates")
    @classmethod
    def canonical_coordinates(
        cls, values: tuple[CoordinateGold, ...] | None
    ) -> tuple[CoordinateGold, ...] | None:
        if values is None:
            return None
        keys = tuple(value.sort_key() for value in values)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("structured gold coordinates must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_metric_and_content(self) -> Self:
        if (self.exact_count is None) != (self.metric_key is None):
            raise ValueError("exact_count and metric_key must be supplied together")
        substantive = (
            self.exact_count is not None
            or self.record_keys is not None
            or self.assembly_accession_versions is not None
            or self.sequence_accession_versions is not None
            or self.locus_keys is not None
            or self.coordinates is not None
            or self.detection_call_keys is not None
        )
        if not substantive:
            raise ValueError("structured gold requires at least one exact scored value")
        return self

    @property
    def required_identifiers(self) -> frozenset[str]:
        return frozenset(
            (
                *(self.record_keys or ()),
                *(self.assembly_accession_versions or ()),
                *(self.sequence_accession_versions or ()),
                *(self.locus_keys or ()),
                *(self.detection_call_keys or ()),
                self.release_key,
            )
        )


class EvidenceGroup(StrictFrozenSchema):
    """One required literature need and only its human-approved substitutes."""

    group_id: StableToken
    required_document_key: DocumentKey
    required_chunk_key: ChunkKey
    acceptable_alternative_chunk_keys: tuple[ChunkKey, ...] = ()

    @field_validator("acceptable_alternative_chunk_keys")
    @classmethod
    def canonical_alternatives(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("alternative chunk keys must be sorted and unique")
        return values

    @model_validator(mode="after")
    def required_is_not_alternative(self) -> Self:
        if self.required_chunk_key in self.acceptable_alternative_chunk_keys:
            raise ValueError("required chunk cannot also be an alternative")
        return self

    @property
    def member_chunk_keys(self) -> frozenset[str]:
        return frozenset((self.required_chunk_key, *self.acceptable_alternative_chunk_keys))


class LiteratureGold(StrictFrozenSchema):
    """Human-approved document, passage, concept, and limitation semantics."""

    gold_kind: Literal["literature"] = "literature"
    required_document_keys: tuple[DocumentKey, ...] = Field(min_length=1)
    evidence_groups: tuple[EvidenceGroup, ...] = Field(min_length=1)
    excluded_chunk_keys: tuple[ChunkKey, ...] = ()
    required_concepts: tuple[NonEmptyText, ...] = Field(min_length=1)
    required_limitations: tuple[NonEmptyText, ...] = ()
    forbidden_claims: tuple[NonEmptyText, ...] = ()

    @field_validator(
        "required_document_keys",
        "excluded_chunk_keys",
        "required_concepts",
        "required_limitations",
        "forbidden_claims",
    )
    @classmethod
    def canonical_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("literature gold collections must be sorted and unique")
        return values

    @field_validator("evidence_groups")
    @classmethod
    def canonical_groups(cls, values: tuple[EvidenceGroup, ...]) -> tuple[EvidenceGroup, ...]:
        keys = tuple(value.group_id for value in values)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("evidence groups must be sorted by unique group_id")
        return values

    @model_validator(mode="after")
    def validate_group_membership(self) -> Self:
        seen: set[str] = set()
        for group in self.evidence_groups:
            members = set(group.member_chunk_keys)
            if seen & members:
                raise ValueError("a positive chunk may belong to only one evidence group")
            seen.update(members)
        if seen & set(self.excluded_chunk_keys):
            raise ValueError("positive and excluded chunks must be disjoint")
        group_documents = {group.required_document_key for group in self.evidence_groups}
        if not group_documents <= set(self.required_document_keys):
            raise ValueError("evidence-group documents must be required documents")
        return self


class HybridGold(StrictFrozenSchema):
    """Exact structured and literature gold plus their required interpretation boundary."""

    gold_kind: Literal["hybrid"] = "hybrid"
    structured: StructuredGold
    literature: LiteratureGold
    required_relationships: tuple[NonEmptyText, ...] = Field(min_length=1)
    required_limitations: tuple[NonEmptyText, ...] = ()
    forbidden_claims: tuple[NonEmptyText, ...] = ()

    @field_validator("required_relationships", "required_limitations", "forbidden_claims")
    @classmethod
    def canonical_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("hybrid gold collections must be sorted and unique")
        return values


type RefusalCategory = Literal[
    "insufficient_release_scope",
    "unsupported_biological_inference",
    "biological_absence_not_established",
    "prevalence_not_established",
    "independent_event_not_established",
    "modern_infection_not_established",
    "external_computation_requested",
    "instruction_override_attempt",
]


class UnsupportedGold(StrictFrozenSchema):
    """Human-approved refusal expectation and prohibited downstream behavior."""

    gold_kind: Literal["unsupported"] = "unsupported"
    expected_refusal: Literal[True] = True
    refusal_category: RefusalCategory
    prohibited_downstream_stages: tuple[ExecutionStage, ...] = Field(min_length=1)
    required_explanations: tuple[NonEmptyText, ...] = Field(min_length=1)
    forbidden_claims: tuple[NonEmptyText, ...] = Field(min_length=1)

    @field_validator(
        "prohibited_downstream_stages",
        "required_explanations",
        "forbidden_claims",
    )
    @classmethod
    def canonical_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("unsupported gold collections must be unique")
        return values


type QuestionGold = Annotated[
    StructuredGold | LiteratureGold | HybridGold | UnsupportedGold,
    Field(discriminator="gold_kind"),
]


class EvaluationQuestion(StrictFrozenSchema):
    """One exact question; only a human-approved record may carry real gold."""

    question_schema_version: Literal["rag-value-question-v1"] = "rag-value-question-v1"
    question_id: StableToken
    family: QuestionFamily
    question_text: QuestionText
    question_text_sha256: Sha256
    review_status: ReviewStatus
    approval: HumanApproval | None = None
    gold: QuestionGold | None = None
    authoring_notes: str | None = Field(default=None, max_length=4000)
    record_sha256: Sha256

    @model_validator(mode="after")
    def validate_review_and_hash(self) -> Self:
        if self.question_text_sha256 != hashlib.sha256(
            self.question_text.encode("utf-8")
        ).hexdigest():
            raise ValueError("question_text_sha256 does not match question_text")
        if self.review_status == "approved":
            if self.approval is None or self.gold is None:
                raise ValueError("approved question requires human approval and complete gold")
            if self.family != self.gold.gold_kind:
                raise ValueError("question family does not match gold kind")
        elif self.approval is not None or self.gold is not None:
            raise ValueError("only approved questions may carry approval or real gold")
        if self.record_sha256 != _self_sha256(self, "record_sha256"):
            raise ValueError("question record checksum does not match")
        return self


def build_evaluation_question(
    *,
    question_id: str,
    family: QuestionFamily,
    question_text: str,
    review_status: ReviewStatus,
    approval: HumanApproval | None = None,
    gold: QuestionGold | None = None,
    authoring_notes: str | None = None,
) -> EvaluationQuestion:
    """Build a canonical self-checksummed question without approving anything implicitly."""

    payload: dict[str, object] = {
        "question_schema_version": "rag-value-question-v1",
        "question_id": question_id,
        "family": family,
        "question_text": question_text,
        "question_text_sha256": hashlib.sha256(question_text.encode("utf-8")).hexdigest(),
        "review_status": review_status,
        "approval": approval,
        "gold": gold,
        "authoring_notes": authoring_notes,
    }
    return EvaluationQuestion.model_validate(
        {**payload, "record_sha256": canonical_json_sha256(payload)}
    )


class QuestionManifest(StrictFrozenSchema):
    """Canonical annotation set with a separately visible approved projection."""

    manifest_schema_version: Literal["rag-value-question-manifest-v1"] = (
        "rag-value-question-manifest-v1"
    )
    dataset_release_key: StableToken | None = None
    dataset_manifest_sha256: Sha256 | None = None
    corpus_release_key: CorpusReleaseKey | None = None
    corpus_manifest_sha256: Sha256 | None = None
    question_count: int = Field(ge=1)
    approved_question_count: int = Field(ge=0)
    family_counts: dict[QuestionFamily, int]
    approved_family_counts: dict[QuestionFamily, int]
    gold_sha256: Sha256 | None
    questions: tuple[EvaluationQuestion, ...] = Field(min_length=1)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if (self.dataset_release_key is None) != (self.dataset_manifest_sha256 is None):
            raise ValueError("dataset release key and manifest checksum must be paired")
        if (self.corpus_release_key is None) != (self.corpus_manifest_sha256 is None):
            raise ValueError("corpus release key and manifest checksum must be paired")
        ids = tuple(question.question_id for question in self.questions)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("questions must be sorted by unique question_id")
        if self.question_count != len(self.questions):
            raise ValueError("question_count does not match questions")
        approved = self.approved_questions
        if self.approved_question_count != len(approved):
            raise ValueError("approved_question_count does not match questions")
        if self.family_counts != _family_counts(self.questions):
            raise ValueError("family_counts does not match questions")
        if self.approved_family_counts != _family_counts(approved):
            raise ValueError("approved_family_counts does not match approved questions")
        expected_gold = _gold_sha256(approved)
        if self.gold_sha256 != expected_gold:
            raise ValueError("gold_sha256 does not match approved question gold")
        if self.manifest_sha256 != _self_sha256(self, "manifest_sha256"):
            raise ValueError("question manifest checksum does not match")
        return self

    @property
    def approved_questions(self) -> tuple[EvaluationQuestion, ...]:
        return tuple(
            question for question in self.questions if question.review_status == "approved"
        )


def build_question_manifest(
    questions: Sequence[EvaluationQuestion],
    *,
    dataset_release_key: str | None = None,
    dataset_manifest_sha256: str | None = None,
    corpus_release_key: str | None = None,
    corpus_manifest_sha256: str | None = None,
) -> QuestionManifest:
    """Build a canonical annotation manifest without upgrading review status."""

    ordered = tuple(sorted(questions, key=lambda question: question.question_id))
    approved = tuple(
        question for question in ordered if question.review_status == "approved"
    )
    payload: dict[str, object] = {
        "manifest_schema_version": "rag-value-question-manifest-v1",
        "dataset_release_key": dataset_release_key,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "corpus_release_key": corpus_release_key,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "question_count": len(ordered),
        "approved_question_count": len(approved),
        "family_counts": _family_counts(ordered),
        "approved_family_counts": _family_counts(approved),
        "gold_sha256": _gold_sha256(approved),
        "questions": ordered,
    }
    return QuestionManifest.model_validate(
        {**payload, "manifest_sha256": canonical_json_sha256(payload)}
    )


class OracleEvidenceEntry(StrictFrozenSchema):
    """Separately reviewed oracle facts/chunks; a retriever cannot issue approval."""

    entry_schema_version: Literal["rag-value-oracle-entry-v1"] = (
        "rag-value-oracle-entry-v1"
    )
    question_id: StableToken
    question_text_sha256: Sha256
    review_status: ReviewStatus
    approval: HumanApproval | None = None
    evidence_disposition: Literal[
        "evidence_supplied", "no_supporting_evidence"
    ] | None = None
    structured_facts: StructuredGold | None = None
    literature_chunk_keys: tuple[ChunkKey, ...] = ()
    dataset_release_key: StableToken | None = None
    dataset_manifest_sha256: Sha256 | None = None
    corpus_release_key: CorpusReleaseKey | None = None
    corpus_manifest_sha256: Sha256 | None = None
    source_attestation: Literal[
        "Evidence was selected manually and not generated from model or retriever output."
    ] | None = None
    entry_sha256: Sha256

    @field_validator("literature_chunk_keys")
    @classmethod
    def canonical_chunks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("oracle chunks must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_approval_and_hash(self) -> Self:
        if (self.dataset_release_key is None) != (self.dataset_manifest_sha256 is None):
            raise ValueError("oracle dataset identity must be paired")
        if (self.corpus_release_key is None) != (self.corpus_manifest_sha256 is None):
            raise ValueError("oracle corpus identity must be paired")
        evidence_present = self.structured_facts is not None or bool(self.literature_chunk_keys)
        if self.review_status == "approved":
            if (
                self.approval is None
                or self.source_attestation is None
                or self.evidence_disposition is None
            ):
                raise ValueError("approved oracle entry requires manual approval and disposition")
            if self.evidence_disposition == "evidence_supplied" and not evidence_present:
                raise ValueError("oracle evidence disposition requires supplied evidence")
            if self.evidence_disposition == "no_supporting_evidence" and evidence_present:
                raise ValueError("empty oracle disposition cannot carry supplied evidence")
        elif (
            self.approval is not None
            or self.evidence_disposition is not None
            or self.source_attestation is not None
            or evidence_present
        ):
            raise ValueError("only approved oracle entries may carry evidence or approval")
        if self.entry_sha256 != _self_sha256(self, "entry_sha256"):
            raise ValueError("oracle entry checksum does not match")
        return self


class OracleEvidenceManifest(StrictFrozenSchema):
    """Canonical collection of separately approved S6 oracle evidence."""

    manifest_schema_version: Literal["rag-value-oracle-manifest-v1"] = (
        "rag-value-oracle-manifest-v1"
    )
    entry_count: int = Field(ge=1)
    approved_entry_count: int = Field(ge=0)
    entries: tuple[OracleEvidenceEntry, ...] = Field(min_length=1)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        ids = tuple(entry.question_id for entry in self.entries)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("oracle entries must be sorted by unique question_id")
        if self.entry_count != len(self.entries):
            raise ValueError("oracle entry_count does not match entries")
        expected_approved = sum(entry.review_status == "approved" for entry in self.entries)
        if self.approved_entry_count != expected_approved:
            raise ValueError("oracle approved_entry_count does not match entries")
        if self.manifest_sha256 != _self_sha256(self, "manifest_sha256"):
            raise ValueError("oracle manifest checksum does not match")
        return self


def build_oracle_entry(**values: object) -> OracleEvidenceEntry:
    """Build an oracle entry from explicitly supplied review data without inferring evidence."""

    payload = {
        "entry_schema_version": "rag-value-oracle-entry-v1",
        "approval": None,
        "evidence_disposition": None,
        "structured_facts": None,
        "literature_chunk_keys": (),
        "dataset_release_key": None,
        "dataset_manifest_sha256": None,
        "corpus_release_key": None,
        "corpus_manifest_sha256": None,
        "source_attestation": None,
        **values,
    }
    payload.pop("entry_sha256", None)
    return OracleEvidenceEntry.model_validate(
        {**payload, "entry_sha256": canonical_json_sha256(payload)}
    )


def build_oracle_manifest(
    entries: Sequence[OracleEvidenceEntry],
) -> OracleEvidenceManifest:
    """Build a canonical oracle manifest while preserving every entry's review status."""

    ordered = tuple(sorted(entries, key=lambda entry: entry.question_id))
    payload: dict[str, object] = {
        "manifest_schema_version": "rag-value-oracle-manifest-v1",
        "entry_count": len(ordered),
        "approved_entry_count": sum(
            entry.review_status == "approved" for entry in ordered
        ),
        "entries": ordered,
    }
    return OracleEvidenceManifest.model_validate(
        {**payload, "manifest_sha256": canonical_json_sha256(payload)}
    )


class GenerationIdentity(StrictFrozenSchema):
    """Exact generation identity that every LLM-based system must share."""

    identity_schema_version: Literal["rag-value-generation-identity-v1"] = (
        "rag-value-generation-identity-v1"
    )
    provider_key: StableToken
    provider_kind: Literal["verified_local", "deterministic_fake", "unverified"]
    model_id: str = Field(min_length=1, max_length=255)
    exact_revision: str = Field(min_length=40, max_length=64)
    model_artifact_manifest_sha256: Sha256
    tokenizer_id: str = Field(min_length=1, max_length=255)
    tokenizer_revision: str = Field(min_length=40, max_length=64)
    tokenizer_artifact_manifest_sha256: Sha256
    system_instruction_sha256: Sha256
    request_template_sha256: Sha256
    output_schema_sha256: Sha256
    temperature: Literal[0] = 0
    max_output_tokens: int = Field(ge=1)
    max_output_bytes: int = Field(ge=1)
    context_limit_tokens: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1, le=3600)
    retry_count: Literal[0] = 0
    request_concurrency: Literal[1] = 1
    seed: int | None = None
    tools_enabled: Literal[False] = False
    web_enabled: Literal[False] = False
    conversation_memory_enabled: Literal[False] = False
    identity_sha256: Sha256

    @field_validator("exact_revision", "tokenizer_revision")
    @classmethod
    def immutable_revision(cls, value: str) -> str:
        if _REVISION_RE.fullmatch(value) is None:
            raise ValueError("model/tokenizer revision must be immutable lowercase hex")
        return value

    @model_validator(mode="after")
    def validate_limits_and_hash(self) -> Self:
        if self.max_output_tokens >= self.context_limit_tokens:
            raise ValueError("max_output_tokens must be smaller than context_limit_tokens")
        if self.identity_sha256 != _self_sha256(self, "identity_sha256"):
            raise ValueError("generation identity checksum does not match")
        return self


def build_generation_identity(**values: object) -> GenerationIdentity:
    """Build the self-checksummed common generation identity."""

    payload = {
        "identity_schema_version": "rag-value-generation-identity-v1",
        **values,
    }
    payload.pop("identity_sha256", None)
    return GenerationIdentity.model_validate(
        {**payload, "identity_sha256": canonical_json_sha256(payload)}
    )


class RetrievalPolicyIdentity(StrictFrozenSchema):
    """Current approved S2/S3/S5 retrieval identity and evaluation depths."""

    policy_schema_version: Literal["rag-value-retrieval-policy-v1"] = (
        "rag-value-retrieval-policy-v1"
    )
    fts_policy_key: Literal["fts:postgres16:english-weighted-v2"] = FTS_POLICY_KEY
    hybrid_retrieval_policy_key: Literal[
        "retrieval:postgres16-english-bge-hnsw-summary-rrf60-v2"
    ] = RETRIEVAL_POLICY_KEY
    embedding_model_key: Literal[
        "embedding:hf:BAAI-bge-small-en-v1.5@"
        "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a:cls-l2norm-v1"
    ] = EMBEDDING_MODEL_KEY
    embedding_revision: Literal[
        "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    ] = EMBEDDING_REVISION
    embedding_artifact_manifest_sha256: Sha256 | None
    dense_branches: tuple[Literal["full"], Literal["title_abstract"]] = (
        "full",
        "title_abstract",
    )
    summary_branch_enabled: Literal[True] = True
    branch_candidate_depth: Literal[100] = 100
    rrf_k: Literal[60] = 60
    retrieval_metric_depth: Literal[10] = 10
    generation_context_chunk_limit: Literal[8] = 8
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.policy_sha256 != _self_sha256(self, "policy_sha256"):
            raise ValueError("retrieval policy checksum does not match")
        return self


def build_retrieval_policy_identity(
    *,
    embedding_artifact_manifest_sha256: str | None,
) -> RetrievalPolicyIdentity:
    """Freeze the current BGE hybrid baseline without loading its model."""

    payload: dict[str, object] = {
        "policy_schema_version": "rag-value-retrieval-policy-v1",
        "fts_policy_key": FTS_POLICY_KEY,
        "hybrid_retrieval_policy_key": RETRIEVAL_POLICY_KEY,
        "embedding_model_key": EMBEDDING_MODEL_KEY,
        "embedding_revision": EMBEDDING_REVISION,
        "embedding_artifact_manifest_sha256": embedding_artifact_manifest_sha256,
        "dense_branches": ("full", "title_abstract"),
        "summary_branch_enabled": True,
        "branch_candidate_depth": 100,
        "rrf_k": 60,
        "retrieval_metric_depth": 10,
        "generation_context_chunk_limit": 8,
    }
    return RetrievalPolicyIdentity.model_validate(
        {**payload, "policy_sha256": canonical_json_sha256(payload)}
    )


class RawContextPolicy(StrictFrozenSchema):
    """Frozen S1 source identity, ordering, token budget, and truncation policy."""

    policy_schema_version: Literal["rag-value-raw-context-policy-v1"] = (
        "rag-value-raw-context-policy-v1"
    )
    source_manifest_sha256: Sha256
    structured_export_sha256: Sha256
    document_manifest_sha256: Sha256
    ordering: Literal["structured_then_corpus_manifest_then_source_order"]
    whole_segment_preferred: Literal[True] = True
    final_partial_segment_allowed: bool
    separator_sha256: Sha256
    tokenizer_key: StableToken
    model_context_limit_tokens: int = Field(ge=1)
    reserved_output_tokens: int = Field(ge=1)
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.reserved_output_tokens >= self.model_context_limit_tokens:
            raise ValueError("raw-context reserved output must fit the context limit")
        if self.policy_sha256 != _self_sha256(self, "policy_sha256"):
            raise ValueError("raw-context policy checksum does not match")
        return self


def build_raw_context_policy(**values: object) -> RawContextPolicy:
    """Build an exact S1 construction policy without reading source document bytes."""

    payload = {
        "policy_schema_version": "rag-value-raw-context-policy-v1",
        "ordering": "structured_then_corpus_manifest_then_source_order",
        "whole_segment_preferred": True,
        **values,
    }
    payload.pop("policy_sha256", None)
    return RawContextPolicy.model_validate(
        {**payload, "policy_sha256": canonical_json_sha256(payload)}
    )


class EvaluationSystem(StrictFrozenSchema):
    """Frozen evidence and dependency policy for one S0-S6 condition."""

    system_schema_version: Literal["rag-value-system-v1"] = "rag-value-system-v1"
    system_key: SystemKey
    display_name: NonEmptyText
    evidence_mode: EvidenceMode
    uses_llm: bool
    generation_identity_sha256: Sha256 | None
    allowed_dependencies: tuple[DependencyKind, ...]
    required_success_stages: tuple[ExecutionStage, ...]
    allowed_stages: tuple[ExecutionStage, ...]
    system_sha256: Sha256

    @field_validator("allowed_dependencies", "required_success_stages", "allowed_stages")
    @classmethod
    def unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("system dependency/stage collections must be unique")
        return values

    @model_validator(mode="after")
    def validate_system(self) -> Self:
        if not self.uses_llm and self.generation_identity_sha256 is not None:
            raise ValueError("non-LLM systems cannot carry a generation identity")
        if not set(self.required_success_stages) <= set(self.allowed_stages):
            raise ValueError("required stages must be a subset of allowed stages")
        if self.system_sha256 != _self_sha256(self, "system_sha256"):
            raise ValueError("system checksum does not match")
        return self


class ContextConstructionRecord(StrictFrozenSchema):
    """Exact context accounting, including any truncation or omission."""

    policy_sha256: Sha256
    tokenizer_key: StableToken
    model_context_limit_tokens: int = Field(ge=1)
    reserved_output_tokens: int = Field(ge=1)
    input_token_count: int = Field(ge=0)
    context_token_count: int = Field(ge=0)
    context_byte_count: int = Field(ge=0)
    truncated: bool
    omitted_source_keys: tuple[StableToken, ...] = ()
    omitted_segment_count: int = Field(ge=0)

    @field_validator("omitted_source_keys")
    @classmethod
    def canonical_omissions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("omitted source keys must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        if self.input_token_count + self.reserved_output_tokens > self.model_context_limit_tokens:
            raise ValueError("input plus reserved output exceeds the approved context limit")
        if self.context_token_count > self.input_token_count:
            raise ValueError("context tokens cannot exceed total input tokens")
        if not self.truncated and (self.omitted_source_keys or self.omitted_segment_count):
            raise ValueError("omissions require truncated=true")
        return self


class EvidenceCitation(StrictFrozenSchema):
    """One checksum-bound literature passage visible to the model."""

    citation_id: str = Field(pattern=_CITATION_ID_RE.pattern)
    document_key: DocumentKey
    chunk_key: ChunkKey
    locator_text: NonEmptyText
    text: CanonicalText
    text_sha256: Sha256

    @model_validator(mode="after")
    def validate_text_hash(self) -> Self:
        if self.text_sha256 != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("evidence citation text checksum does not match")
        return self


class RawContextSegment(StrictFrozenSchema):
    """One exact segment of the approved S1 raw-context construction."""

    segment_id: str = Field(pattern=_RAW_SEGMENT_ID_RE.pattern)
    source_kind: Literal["structured_export", "document"]
    source_key: StableToken
    source_sha256: Sha256
    byte_start: int = Field(ge=0)
    byte_end: int = Field(gt=0)
    text: CanonicalText
    text_sha256: Sha256

    @model_validator(mode="after")
    def validate_segment(self) -> Self:
        if self.byte_start >= self.byte_end:
            raise ValueError("raw segment byte range is invalid")
        if len(self.text.encode("utf-8")) != self.byte_end - self.byte_start:
            raise ValueError("raw segment byte range does not match text bytes")
        if self.text_sha256 != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("raw segment text checksum does not match")
        return self


class EvaluationEvidencePack(StrictFrozenSchema):
    """Common model evidence envelope; it intentionally contains no system or gold label."""

    pack_schema_version: Literal["rag-value-evidence-pack-v1"] = (
        "rag-value-evidence-pack-v1"
    )
    question_id: StableToken
    question_text: QuestionText
    question_text_sha256: Sha256
    structured_success: QuerySuccess | None = None
    citations: tuple[EvidenceCitation, ...] = ()
    raw_context_segments: tuple[RawContextSegment, ...] = ()
    construction: ContextConstructionRecord
    production_context_pack_sha256: Sha256 | None = None
    oracle_entry_sha256: Sha256 | None = None
    pack_sha256: Sha256

    @model_validator(mode="after")
    def validate_pack(self) -> Self:
        if self.question_text_sha256 != hashlib.sha256(
            self.question_text.encode("utf-8")
        ).hexdigest():
            raise ValueError("evidence question checksum does not match")
        citation_ids = tuple(item.citation_id for item in self.citations)
        expected_citations = tuple(f"D{index}" for index in range(1, len(self.citations) + 1))
        if citation_ids != expected_citations:
            raise ValueError("evidence citation IDs must be contiguous and ordered")
        chunk_keys = tuple(item.chunk_key for item in self.citations)
        if len(chunk_keys) != len(set(chunk_keys)):
            raise ValueError("evidence chunks must be unique")
        segment_ids = tuple(item.segment_id for item in self.raw_context_segments)
        expected_segments = tuple(
            f"R{index}" for index in range(1, len(self.raw_context_segments) + 1)
        )
        if segment_ids != expected_segments:
            raise ValueError("raw segment IDs must be contiguous and ordered")
        visible_bytes = canonical_json_bytes(model_visible_evidence(self))
        if self.construction.context_byte_count != len(visible_bytes):
            raise ValueError("context_byte_count does not match model-visible evidence bytes")
        if self.pack_sha256 != _self_sha256(self, "pack_sha256"):
            raise ValueError("evidence pack checksum does not match")
        return self


def model_visible_evidence(pack: EvaluationEvidencePack) -> dict[str, object]:
    """Project only the identical model-visible fields used by every LLM condition."""

    return _model_visible_payload(
        question_text=pack.question_text,
        structured_success=pack.structured_success,
        citations=pack.citations,
        raw_context_segments=pack.raw_context_segments,
    )


def build_evidence_pack(
    *,
    question_id: str,
    question_text: str,
    structured_success: QuerySuccess | None = None,
    citations: Sequence[EvidenceCitation] = (),
    raw_context_segments: Sequence[RawContextSegment] = (),
    policy_sha256: str,
    tokenizer_key: str,
    model_context_limit_tokens: int,
    reserved_output_tokens: int,
    input_token_count: int,
    context_token_count: int,
    truncated: bool = False,
    omitted_source_keys: Sequence[str] = (),
    omitted_segment_count: int = 0,
    production_context_pack_sha256: str | None = None,
    oracle_entry_sha256: str | None = None,
) -> EvaluationEvidencePack:
    """Build a checksum-bound evidence pack with exact model-visible byte accounting."""

    citation_tuple = tuple(citations)
    raw_tuple = tuple(raw_context_segments)
    visible = _model_visible_payload(
        question_text=question_text,
        structured_success=structured_success,
        citations=citation_tuple,
        raw_context_segments=raw_tuple,
    )
    construction = ContextConstructionRecord(
        policy_sha256=policy_sha256,
        tokenizer_key=tokenizer_key,
        model_context_limit_tokens=model_context_limit_tokens,
        reserved_output_tokens=reserved_output_tokens,
        input_token_count=input_token_count,
        context_token_count=context_token_count,
        context_byte_count=len(canonical_json_bytes(visible)),
        truncated=truncated,
        omitted_source_keys=tuple(omitted_source_keys),
        omitted_segment_count=omitted_segment_count,
    )
    payload: dict[str, object] = {
        "pack_schema_version": "rag-value-evidence-pack-v1",
        "question_id": question_id,
        "question_text": question_text,
        "question_text_sha256": hashlib.sha256(question_text.encode("utf-8")).hexdigest(),
        "structured_success": structured_success,
        "citations": citation_tuple,
        "raw_context_segments": raw_tuple,
        "construction": construction,
        "production_context_pack_sha256": production_context_pack_sha256,
        "oracle_entry_sha256": oracle_entry_sha256,
    }
    return EvaluationEvidencePack.model_validate(
        {**payload, "pack_sha256": canonical_json_sha256(payload)}
    )


def _model_visible_payload(
    *,
    question_text: str,
    structured_success: QuerySuccess | None,
    citations: Sequence[EvidenceCitation],
    raw_context_segments: Sequence[RawContextSegment],
) -> dict[str, object]:
    return {
        "question": question_text,
        "structured_result": (
            None
            if structured_success is None
            else structured_success.structured_result.model_dump(mode="json")
        ),
        "literature_evidence": [
            citation.model_dump(mode="json") for citation in citations
        ],
        "raw_context": [
            segment.model_dump(mode="json") for segment in raw_context_segments
        ],
    }


class EvaluationClaim(StrictFrozenSchema):
    """One model-authored atomic claim in the common answer schema."""

    claim_id: str = Field(pattern=_CLAIM_ID_RE.pattern)
    text: NonEmptyText
    claim_type: ClaimType
    citation_ids: tuple[str, ...] = ()

    @field_validator("citation_ids")
    @classmethod
    def canonical_citations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(_CITATION_ID_RE.fullmatch(value) is None for value in values):
            raise ValueError("claim citation ID is invalid")
        if len(values) != len(set(values)):
            raise ValueError("claim citation IDs must be unique")
        if values != tuple(sorted(values, key=lambda value: (value[0], int(value[1:])))):
            raise ValueError("claim citation IDs must be canonically ordered")
        return values

    @model_validator(mode="after")
    def literature_claim_is_cited(self) -> Self:
        if self.claim_type == "literature_fact" and not self.citation_ids:
            raise ValueError("literature factual claims require at least one citation")
        return self


class EvaluationAnswer(StrictFrozenSchema):
    """Common model-visible output contract for S0/S1/S2/S3/S5/S6."""

    answer_text: NonEmptyText
    abstained: bool
    claims: tuple[EvaluationClaim, ...] = ()
    limitations: tuple[NonEmptyText, ...] = ()
    cited_chunk_ids: tuple[ChunkKey, ...] = ()

    @field_validator("limitations", "cited_chunk_ids")
    @classmethod
    def unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("answer limitations/chunk IDs must be unique")
        return values

    @model_validator(mode="after")
    def validate_answer_shape(self) -> Self:
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        expected = tuple(f"C{index}" for index in range(1, len(self.claims) + 1))
        if claim_ids != expected:
            raise ValueError("claim IDs must be contiguous and ordered")
        if self.abstained and (self.claims or self.cited_chunk_ids):
            raise ValueError("abstained answer must not assert claims or citations")
        if not self.abstained and not self.claims:
            raise ValueError("non-abstained answer requires at least one atomic claim")
        return self


class MechanicalValidation(StrictFrozenSchema):
    """Mechanical provenance checks; this is not a semantic entailment judgment."""

    passed: bool
    issue_codes: tuple[StableToken, ...]

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.issue_codes != tuple(sorted(set(self.issue_codes))):
            raise ValueError("mechanical validation issue codes must be sorted and unique")
        if self.passed != (not self.issue_codes):
            raise ValueError("mechanical validation status does not match issue codes")
        return self


def mechanically_validate_answer(
    answer: EvaluationAnswer,
    evidence: EvaluationEvidencePack,
) -> MechanicalValidation:
    """Check only exact evidence references and impossible provenance combinations."""

    citation_by_id = {citation.citation_id: citation for citation in evidence.citations}
    raw_by_id = {segment.segment_id: segment for segment in evidence.raw_context_segments}
    issues: set[str] = set()
    referenced_ids = {
        citation_id for claim in answer.claims for citation_id in claim.citation_ids
    }
    unknown = referenced_ids - set(citation_by_id) - set(raw_by_id)
    if unknown:
        issues.add("unknown_citation_id")
    expected_chunks = {
        citation_by_id[citation_id].chunk_key
        for citation_id in referenced_ids
        if citation_id in citation_by_id
    }
    if expected_chunks != set(answer.cited_chunk_ids):
        issues.add("cited_chunk_set_mismatch")
    has_raw_structured_export = any(
        segment.source_kind == "structured_export"
        for segment in evidence.raw_context_segments
    )
    if (
        any(claim.claim_type == "structured_fact" for claim in answer.claims)
        and evidence.structured_success is None
        and not has_raw_structured_export
    ):
        issues.add("structured_claim_without_structured_result")
    return MechanicalValidation(passed=not issues, issue_codes=tuple(sorted(issues)))


class StructuredPreservationProof(StrictFrozenSchema):
    """Mechanical proof that S5 returned the exact structured object supplied upstream."""

    input_structured_result_sha256: Sha256
    output_structured_result_sha256: Sha256
    preserved: bool

    @model_validator(mode="after")
    def validate_proof(self) -> Self:
        expected = self.input_structured_result_sha256 == self.output_structured_result_sha256
        if self.preserved != expected:
            raise ValueError("structured preservation status does not match checksums")
        return self


def prove_structured_result_preserved(
    input_result: StructuredResult,
    output_result: StructuredResult,
) -> StructuredPreservationProof:
    """Hash typed structured values before generation and after deterministic merge."""

    input_sha256 = canonical_json_sha256(input_result)
    output_sha256 = canonical_json_sha256(output_result)
    return StructuredPreservationProof(
        input_structured_result_sha256=input_sha256,
        output_structured_result_sha256=output_sha256,
        preserved=input_sha256 == output_sha256,
    )


class ExecutionTrace(StrictFrozenSchema):
    """Auditable proof of constructed capabilities and actually called stages."""

    system_key: SystemKey
    question_id: StableToken
    status: Literal[
        "completed", "refused", "retrieval_only", "not_applicable", "failed"
    ]
    constructed_dependencies: tuple[DependencyKind, ...] = ()
    called_stages: tuple[ExecutionStage, ...] = ()
    refusal_stage: ExecutionStage | None = None
    generation_call_count: int = Field(ge=0, le=1)

    @field_validator("constructed_dependencies", "called_stages")
    @classmethod
    def unique_trace_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("execution trace values must be unique")
        return values

    @model_validator(mode="after")
    def validate_trace_shape(self) -> Self:
        if self.status == "not_applicable" and (
            self.constructed_dependencies
            or self.called_stages
            or self.refusal_stage is not None
            or self.generation_call_count
        ):
            raise ValueError("not-applicable trace must contain no execution")
        if self.status == "retrieval_only" and self.generation_call_count:
            raise ValueError("retrieval-only trace cannot call generation")
        if self.status == "refused":
            if self.refusal_stage is None or self.refusal_stage not in self.called_stages:
                raise ValueError("refused trace requires its exact called refusal stage")
            if self.called_stages[-1] != self.refusal_stage:
                raise ValueError("no downstream stage may run after refusal")
        elif self.refusal_stage is not None:
            raise ValueError("only refused traces may carry refusal_stage")
        return self


class RuntimeIdentity(StrictFrozenSchema):
    """Portable hardware and dependency identity recorded with a run."""

    operating_system: NonEmptyText
    machine_architecture: StableToken
    cpu: NonEmptyText
    ram_bytes: int = Field(ge=1)
    accelerator: NonEmptyText
    python_version: StableToken
    uv_version: StableToken
    postgresql_version: NonEmptyText
    pgvector_version: StableToken
    dependency_lock_sha256: Sha256
    dependency_versions: dict[StableToken, NonEmptyText] = Field(min_length=1)
    thread_settings: dict[StableToken, str]

    @field_validator("dependency_versions", "thread_settings")
    @classmethod
    def canonical_runtime_maps(cls, values: dict[str, str]) -> dict[str, str]:
        if tuple(values) != tuple(sorted(values)):
            raise ValueError("runtime maps must use canonical key order")
        return values


class ExperimentManifest(StrictFrozenSchema):
    """Frozen inputs and trust status shared by one future evaluation run."""

    manifest_schema_version: Literal["rag-value-experiment-v1"] = (
        "rag-value-experiment-v1"
    )
    experiment_key: StableToken
    phase: Literal[
        "phase2_synthetic",
        "phase3_retrieval",
        "phase4_llm",
        "phase5_human",
        "phase6_analysis",
    ]
    trust_status: TrustStatus
    trust_reasons: tuple[NonEmptyText, ...]
    source_commit: str = Field(min_length=40, max_length=40)
    source_tree_clean: bool
    production_source_fingerprint_sha256: Sha256
    question_manifest_sha256: Sha256
    oracle_manifest_sha256: Sha256 | None = None
    dataset_release_key: StableToken | None = None
    dataset_manifest_sha256: Sha256 | None = None
    corpus_release_key: CorpusReleaseKey | None = None
    corpus_manifest_sha256: Sha256 | None = None
    binding_manifest_sha256: Sha256 | None = None
    generation_identity: GenerationIdentity | None = None
    retrieval_policy: RetrievalPolicyIdentity
    raw_context_policy: RawContextPolicy
    pricing_manifest_sha256: Sha256 | None = None
    runtime_identity: RuntimeIdentity
    systems: tuple[EvaluationSystem, ...] = Field(min_length=7, max_length=7)
    manifest_sha256: Sha256

    @field_validator("source_commit")
    @classmethod
    def exact_commit(cls, value: str) -> str:
        if _COMMIT_RE.fullmatch(value) is None:
            raise ValueError("source commit must be exact lowercase 40-hex")
        return value

    @field_validator("trust_reasons")
    @classmethod
    def canonical_reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("trust reasons must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if (self.dataset_release_key is None) != (self.dataset_manifest_sha256 is None):
            raise ValueError("dataset identity must be paired")
        if (self.corpus_release_key is None) != (self.corpus_manifest_sha256 is None):
            raise ValueError("corpus identity must be paired")
        system_keys = tuple(system.system_key for system in self.systems)
        if system_keys != ("S0", "S1", "S2", "S3", "S4", "S5", "S6"):
            raise ValueError("manifest must contain S0-S6 in canonical order")
        llm_hashes = {
            system.generation_identity_sha256 for system in self.systems if system.uses_llm
        }
        expected_hash = (
            None
            if self.generation_identity is None
            else self.generation_identity.identity_sha256
        )
        if llm_hashes != {expected_hash}:
            raise ValueError("all LLM systems must share the manifest generation identity")
        if self.systems[4].generation_identity_sha256 is not None:
            raise ValueError("S4 must not carry a generation identity")
        if self.phase == "phase3_retrieval" and self.generation_identity is not None:
            raise ValueError("retrieval-only Phase 3 must not bind or activate an LLM provider")
        if self.phase != "phase3_retrieval" and self.generation_identity is None:
            raise ValueError("generation phases require an exact generation identity")
        if self.generation_identity is not None and (
            self.raw_context_policy.model_context_limit_tokens
            != self.generation_identity.context_limit_tokens
            or self.raw_context_policy.reserved_output_tokens
            != self.generation_identity.max_output_tokens
        ):
            raise ValueError("S1 raw-context budget differs from the common generation identity")
        if self.phase == "phase2_synthetic" and self.trust_status == "trusted":
            raise ValueError("synthetic Phase 2 runs can never be trusted")
        if self.phase == "phase2_synthetic" and (
            self.generation_identity is None
            or self.generation_identity.provider_kind != "deterministic_fake"
        ):
            raise ValueError("synthetic Phase 2 requires a deterministic fake provider")
        if self.phase in {"phase4_llm", "phase5_human", "phase6_analysis"} and (
            self.generation_identity is None
            or self.generation_identity.provider_kind != "verified_local"
        ):
            raise ValueError("real generation phases require a verified local provider")
        if self.trust_status == "trusted" and (
            not self.source_tree_clean
            or self.dataset_release_key is None
            or self.corpus_release_key is None
            or self.retrieval_policy.embedding_artifact_manifest_sha256 is None
        ):
            raise ValueError("trusted run requires real frozen data and a clean source tree")
        if self.trust_status == "trusted" and self.phase in {
            "phase4_llm",
            "phase5_human",
            "phase6_analysis",
        } and (
            self.binding_manifest_sha256 is None
            or self.oracle_manifest_sha256 is None
            or self.generation_identity is None
            or self.generation_identity.provider_kind != "verified_local"
        ):
            raise ValueError("trusted generation requires bound oracle and hybrid artifacts")
        if self.manifest_sha256 != _self_sha256(self, "manifest_sha256"):
            raise ValueError("experiment manifest checksum does not match")
        return self


def build_experiment_manifest(**values: object) -> ExperimentManifest:
    """Build a self-checksummed run manifest from explicit frozen identities."""

    payload = {
        "manifest_schema_version": "rag-value-experiment-v1",
        "oracle_manifest_sha256": None,
        "dataset_release_key": None,
        "dataset_manifest_sha256": None,
        "corpus_release_key": None,
        "corpus_manifest_sha256": None,
        "binding_manifest_sha256": None,
        "generation_identity": None,
        "pricing_manifest_sha256": None,
        **values,
    }
    payload.pop("manifest_sha256", None)
    return ExperimentManifest.model_validate(
        {**payload, "manifest_sha256": canonical_json_sha256(payload)}
    )


class FailureRecord(StrictFrozenSchema):
    """Sanitized failure without credentials, question text, or document bytes."""

    system_key: SystemKey | None = None
    question_id: StableToken | None = None
    stage: StableToken
    error_code: StableToken
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def single_line_message(cls, value: str) -> str:
        if any(character in value for character in ("\n", "\r", "\x00")):
            raise ValueError("failure message must be one sanitized line")
        return value


def _family_counts(questions: Sequence[EvaluationQuestion]) -> dict[QuestionFamily, int]:
    families: tuple[QuestionFamily, ...] = (
        "structured",
        "literature",
        "hybrid",
        "unsupported",
    )
    return {
        family: sum(question.family == family for question in questions)
        for family in families
    }


def _gold_sha256(approved: Sequence[EvaluationQuestion]) -> str | None:
    if not approved:
        return None
    return canonical_json_sha256(
        tuple(
            {"question_id": question.question_id, "gold": question.gold}
            for question in approved
        )
    )


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="python")
    del payload[field_name]
    return canonical_json_sha256(payload)
