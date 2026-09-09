"""Offline, fail-closed readiness gate for real Phase 3 retrieval.

The preflight consumes only explicit, checksum-bound evidence. It deliberately
does not read application settings, open a database connection, construct a
retriever, or load a model. A positive offline report is diagnostic only.
Dependency construction additionally
requires a short-lived authority from execution_gate, which replays real role and
published-release checks. Neither handle grants trusted scoring authority.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import Field, field_validator, model_validator

from eve_relation_rag.experiments.rag_value_ablation.associations import ASSOCIATION_DIMENSIONS
from eve_relation_rag.experiments.rag_value_ablation.contracts import QuestionManifest
from eve_relation_rag.experiments.rag_value_ablation.scoped_admission import (
    Core53QuestionScope,
    validate_core53_question_scope,
)
from eve_relation_rag.literature.contracts import (
    EMBEDDING_MODEL_KEY,
    EMBEDDING_REVISION,
    FTS_POLICY_KEY,
    RETRIEVAL_POLICY_KEY,
    NonEmptyText,
    Sha256,
    StableToken,
    StrictFrozenSchema,
)
from eve_relation_rag.literature.hashing import canonical_json_sha256

if TYPE_CHECKING:
    from eve_relation_rag.experiments.rag_value_ablation.execution_gate import (
        Phase3ExecutionAuthority,
    )

type ApprovalStatus = Literal["pending", "approved", "rejected"]
type IntegrityStatus = Literal["not_checked", "passed", "failed"]
type ReleaseStatus = Literal[
    "missing", "candidate", "validated", "published", "retired"
]
type ReceiptStatus = Literal["missing", "passed", "failed"]
type Phase3SystemKey = Literal["S1", "S2", "S3", "S4"]

PHASE3_SYSTEM_KEYS: tuple[Phase3SystemKey, ...] = ("S1", "S2", "S3", "S4")
REQUIRED_ASSOCIATION_DIMENSIONS = ASSOCIATION_DIMENSIONS
REQUIRED_FAMILY_COUNTS: Mapping[str, tuple[int, int]] = {
    "structured": (15, 20),
    "literature": (15, 20),
    "hybrid": (15, 20),
    "unsupported": (15, 20),
}


class ApprovedArtifactEvidence(StrictFrozenSchema):
    """Explicit observed identity and the independently approved identity."""

    approval_status: ApprovalStatus
    observed_sha256: Sha256 | None
    approved_sha256: Sha256 | None
    integrity_status: IntegrityStatus


class ReleaseEvidence(StrictFrozenSchema):
    """One exact release identity; only ``published`` can pass this preflight."""

    release_key: StableToken
    status: ReleaseStatus
    manifest: ApprovedArtifactEvidence
    validation_receipt: ApprovedArtifactEvidence
    receipt_status: ReceiptStatus
    receipt_trusted: bool
    snapshot_fingerprint: ApprovedArtifactEvidence


class QuestionEvidence(StrictFrozenSchema):
    """Approved question/Gold projection and its exact release bindings."""

    scope_revision: Literal["legacy-v1", "single-source-v2"] = "legacy-v1"
    core53_scope: Core53QuestionScope | None = None
    bound_question_manifest: QuestionManifest | None = None
    question_manifest: ApprovedArtifactEvidence
    gold_manifest: ApprovedArtifactEvidence
    entity_binding_manifest: ApprovedArtifactEvidence
    approved_question_count: int = Field(ge=0)
    approved_family_counts: dict[StableToken, int]
    dataset_release_key: StableToken | None
    dataset_manifest_sha256: Sha256 | None
    corpus_release_key: StableToken | None
    corpus_manifest_sha256: Sha256 | None

    @field_validator("approved_family_counts")
    @classmethod
    def nonnegative_family_counts(cls, values: dict[str, int]) -> dict[str, int]:
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise ValueError("approved family counts must be non-negative integers")
        return values


class AssociationEvidence(StrictFrozenSchema):
    """Approved v1 association contract, provenance, and diversity evidence."""

    association_contract: ApprovedArtifactEvidence
    structured_association_manifest: ApprovedArtifactEvidence
    association_dimensions: tuple[NonEmptyText, ...]
    represented_source_taxon_count: int = Field(ge=0)
    represented_assembly_count: int = Field(ge=0)
    represented_eve_locus_count: int = Field(ge=0)
    reported_viral_region_count: int = Field(ge=0)
    role_qualified_viral_lineage_count: int = Field(ge=0)
    evidence_source_count: int = Field(ge=0)

    @field_validator("association_dimensions")
    @classmethod
    def canonical_association_dimensions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != REQUIRED_ASSOCIATION_DIMENSIONS:
            raise ValueError("association dimensions must use the canonical v1 chain order")
        return values


class DatabaseRoleEvidence(StrictFrozenSchema):
    """A separately approved database-role audit; this module never performs it."""

    audit: ApprovedArtifactEvidence
    role_default_transaction_read_only: bool
    runtime_transaction_read_only: bool
    schema_create_denied: bool
    table_create_denied: bool
    dml_denied: bool


class RawContextEvidence(StrictFrozenSchema):
    """Approved S1 materials, tokenizer, and deterministic construction policy."""

    material_manifest: ApprovedArtifactEvidence
    construction_policy: ApprovedArtifactEvidence
    tokenizer_artifact: ApprovedArtifactEvidence
    dataset_release_key: StableToken
    dataset_manifest_sha256: Sha256
    corpus_release_key: StableToken
    corpus_manifest_sha256: Sha256
    tokenizer_id: NonEmptyText
    tokenizer_revision: str = Field(min_length=40, max_length=64)
    tokenizer_artifact_manifest_sha256: Sha256
    model_context_limit_tokens: int = Field(ge=1)
    reserved_output_tokens: int = Field(ge=1)
    truncation_policy_explicit: bool
    omission_policy_explicit: bool

    @field_validator("tokenizer_revision")
    @classmethod
    def immutable_tokenizer_revision(cls, value: str) -> str:
        if not 40 <= len(value) <= 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("tokenizer revision must be immutable lowercase hex")
        return value


class RetrievalEvidence(StrictFrozenSchema):
    """Exact S2/S3 retrieval and BGE artifact identities."""

    policy_manifest: ApprovedArtifactEvidence
    fts_policy_key: StableToken
    hybrid_retrieval_policy_key: StableToken
    embedding_model_key: StableToken
    embedding_revision: StableToken
    branch_candidate_depth: int = Field(ge=1)
    rrf_k: int = Field(ge=1)
    summary_branch_enabled: bool
    bge_artifact: ApprovedArtifactEvidence
    bge_complete_file_set_verified: bool
    offline_model_policy_enforced: bool


class StructuredQueryCapabilityEvidence(StrictFrozenSchema):
    """Approved coverage receipt for every S4 structured or Hybrid question."""

    validation_receipt: ApprovedArtifactEvidence
    question_manifest_sha256: Sha256
    applicable_question_count: int = Field(ge=0)
    route_accepted_question_count: int = Field(ge=0)
    query_plan_validated_question_count: int = Field(ge=0)
    association_projection_validated_question_count: int = Field(ge=0)


class RequestValidationEvidence(StrictFrozenSchema):
    """Approved replay receipt for shared scope and refusal validation."""

    validation_receipt: ApprovedArtifactEvidence
    question_manifest_sha256: Sha256
    replayed_question_count: int = Field(ge=0)
    scope_admitted_question_count: int = Field(ge=0)
    scope_refused_question_count: int = Field(ge=0)
    validated_scope_refusal_question_count: int = Field(ge=0)
    downstream_calls_after_scope_refusal: int = Field(ge=0)


class AnchorEvidence(StrictFrozenSchema):
    """Exact, corpus-bound structured target anchors required by S5."""

    manifest: ApprovedArtifactEvidence
    corpus_release_key: StableToken
    corpus_manifest_sha256: Sha256
    structured_target_anchor_count: int = Field(ge=0)
    required_target_coverage_complete: bool


class BindingEvidence(StrictFrozenSchema):
    """One approved exact DatasetRelease/CorpusRelease pair for S5."""

    manifest: ApprovedArtifactEvidence
    dataset_release_key: StableToken
    dataset_manifest_sha256: Sha256
    corpus_release_key: StableToken
    corpus_manifest_sha256: Sha256


class Phase3PreflightInput(StrictFrozenSchema):
    """Complete explicit Phase 3 evidence; construction performs no discovery."""

    input_schema_version: Literal["rag-value-phase3-preflight-input-v1"] = (
        "rag-value-phase3-preflight-input-v1"
    )
    questions: QuestionEvidence
    associations: AssociationEvidence
    database_role: DatabaseRoleEvidence
    dataset_release: ReleaseEvidence
    corpus_release: ReleaseEvidence
    raw_context: RawContextEvidence
    retrieval: RetrievalEvidence
    structured_query_capability: StructuredQueryCapabilityEvidence
    request_validation: RequestValidationEvidence
    # S5 consumes these identities in Phase 4. They may be recorded early for
    # diagnostics, but they never block or authorize the S1-S4 Phase 3 run.
    anchors: AnchorEvidence | None = None
    binding: BindingEvidence | None = None
    input_sha256: Sha256

    @model_validator(mode="after")
    def validate_checksum(self) -> Phase3PreflightInput:
        if self.input_sha256 != _self_sha256(self, "input_sha256"):
            raise ValueError("Phase 3 preflight input checksum does not match")
        return self


def build_phase3_preflight_input(**values: object) -> Phase3PreflightInput:
    """Build one self-checksummed input without reading paths or settings."""

    payload = {
        "input_schema_version": "rag-value-phase3-preflight-input-v1",
        **values,
    }
    payload.pop("input_sha256", None)
    return Phase3PreflightInput.model_validate(
        {**payload, "input_sha256": canonical_json_sha256(payload)}
    )


class Phase3SystemReadiness(StrictFrozenSchema):
    """Canonical readiness and blocker codes for one system."""

    system_key: Phase3SystemKey
    ready: bool
    blocker_codes: tuple[StableToken, ...]

    @model_validator(mode="after")
    def validate_readiness(self) -> Phase3SystemReadiness:
        if self.blocker_codes != tuple(sorted(set(self.blocker_codes))):
            raise ValueError("preflight blocker codes must be sorted and unique")
        if self.ready != (not self.blocker_codes):
            raise ValueError("preflight readiness does not match blocker codes")
        return self


class Phase3PreflightReport(StrictFrozenSchema):
    """Checksum-bound S1-S4 retrieval-only readiness report."""

    report_schema_version: Literal["rag-value-phase3-preflight-report-v1"] = (
        "rag-value-phase3-preflight-report-v1"
    )
    input_sha256: Sha256
    systems: tuple[Phase3SystemReadiness, ...]
    ready: bool
    report_sha256: Sha256

    @model_validator(mode="after")
    def validate_report(self) -> Phase3PreflightReport:
        if tuple(item.system_key for item in self.systems) != PHASE3_SYSTEM_KEYS:
            raise ValueError("Phase 3 system readiness must be canonical S1-S4 order")
        if self.ready != all(item.ready for item in self.systems):
            raise ValueError("Phase 3 overall readiness does not match systems")
        if self.report_sha256 != _self_sha256(self, "report_sha256"):
            raise ValueError("Phase 3 preflight report checksum does not match")
        return self


_PREFLIGHT_ISSUER = object()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Phase3PreflightDecision:
    """Issuer-only diagnostic handle wrapping the serializable report."""

    report: Phase3PreflightReport
    _issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _PREFLIGHT_ISSUER:
            raise TypeError("Phase3PreflightDecision may only be issued by the preflight gate")


_ISSUED_PREFLIGHT_DECISIONS: dict[
    int, weakref.ReferenceType[Phase3PreflightDecision]
] = {}


class Phase3PreflightBlocked(RuntimeError):
    """Raised before dependency construction when any system has a blocker."""

    def __init__(self, blocker_codes: tuple[str, ...]) -> None:
        self.blocker_codes = blocker_codes
        super().__init__("Phase 3 dependencies are blocked by offline preflight")


def run_phase3_preflight(value: Phase3PreflightInput) -> Phase3PreflightDecision:
    """Evaluate explicit evidence and issue a non-forgeable readiness decision."""

    if type(value) is not Phase3PreflightInput:
        raise TypeError("preflight requires an exact Phase3PreflightInput")
    evidence = Phase3PreflightInput.model_validate_json(value.model_dump_json())
    common = _common_blockers(evidence)
    common |= _request_validation_blockers(evidence)
    by_system: dict[Phase3SystemKey, set[str]] = {
        system_key: set(common) for system_key in PHASE3_SYSTEM_KEYS
    }

    _add(by_system, ("S1", "S4"), _release_blockers("dataset", evidence.dataset_release))
    _add(
        by_system,
        ("S1", "S2", "S3"),
        _release_blockers("corpus", evidence.corpus_release),
    )
    _add(by_system, ("S2", "S3", "S4"), _database_blockers(evidence))
    _add(by_system, ("S1",), _raw_context_blockers(evidence))
    _add(by_system, ("S2",), _fts_policy_blockers(evidence))
    _add(by_system, ("S3",), _hybrid_retrieval_blockers(evidence))
    _add(by_system, ("S1", "S4"), _structured_association_blockers(evidence))
    _add(by_system, ("S4",), _structured_query_capability_blockers(evidence))

    systems = tuple(
        Phase3SystemReadiness(
            system_key=system_key,
            ready=not by_system[system_key],
            blocker_codes=tuple(sorted(by_system[system_key])),
        )
        for system_key in PHASE3_SYSTEM_KEYS
    )
    payload: dict[str, object] = {
        "report_schema_version": "rag-value-phase3-preflight-report-v1",
        "input_sha256": evidence.input_sha256,
        "systems": systems,
        "ready": all(item.ready for item in systems),
    }
    report = Phase3PreflightReport.model_validate(
        {**payload, "report_sha256": canonical_json_sha256(payload)}
    )
    decision = Phase3PreflightDecision(report=report, _issuer=_PREFLIGHT_ISSUER)
    _register_issued_preflight_decision(decision)
    return decision


def is_issued_phase3_preflight_decision(value: object) -> bool:
    """Return whether ``value`` is the exact instance issued by this module."""

    if not isinstance(value, Phase3PreflightDecision) or value._issuer is not _PREFLIGHT_ISSUER:
        return False
    registered = _ISSUED_PREFLIGHT_DECISIONS.get(id(value))
    return registered is not None and registered() is value


def _register_issued_preflight_decision(decision: Phase3PreflightDecision) -> None:
    identity = id(decision)

    def discard(reference: weakref.ReferenceType[Phase3PreflightDecision]) -> None:
        if _ISSUED_PREFLIGHT_DECISIONS.get(identity) is reference:
            _ISSUED_PREFLIGHT_DECISIONS.pop(identity, None)

    _ISSUED_PREFLIGHT_DECISIONS[identity] = weakref.ref(decision, discard)


def construct_phase3_dependencies[T](
    decision: Phase3PreflightDecision,
    dependency_factory: Callable[[], T],
    *, execution_authority: Phase3ExecutionAuthority | None = None,
) -> T:
    """Require a live single-use authority before invoking the dependency factory."""

    if not is_issued_phase3_preflight_decision(decision):
        raise TypeError("an issued Phase 3 preflight decision is required")
    if execution_authority is not None:
        from eve_relation_rag.experiments.rag_value_ablation.execution_gate import (
            consume_phase3_execution_authority,
        )

        return consume_phase3_execution_authority(
            execution_authority, decision, dependency_factory,
        )
    blockers = {
        code
        for system in decision.report.systems
        for code in system.blocker_codes
    }
    blockers.add("phase3_live_execution_authority_required")
    raise Phase3PreflightBlocked(tuple(sorted(blockers)))


def _common_blockers(value: Phase3PreflightInput) -> set[str]:
    blockers = _artifact_blockers("question_manifest", value.questions.question_manifest)
    blockers |= _artifact_blockers("gold_manifest", value.questions.gold_manifest)
    blockers |= _artifact_blockers(
        "entity_binding_manifest", value.questions.entity_binding_manifest
    )
    blockers |= _artifact_blockers(
        "association_contract", value.associations.association_contract
    )
    families = REQUIRED_FAMILY_COUNTS
    minimum_total, maximum_total = 60, 80
    if value.questions.scope_revision == "single-source-v2":
        minimum_total = maximum_total = 53
        families = {"structured": (16, 16), "literature": (16, 16),
                    "hybrid": (9, 9), "unsupported": (12, 12)}
        questions = value.questions
        if questions.core53_scope is None or questions.bound_question_manifest is None:
            blockers.add("core53_bound_scope_missing")
        else:
            try:
                validate_core53_question_scope(
                    questions.core53_scope, questions.bound_question_manifest,
                    revision="single-source-v2",
                )
                if (
                    questions.bound_question_manifest.manifest_sha256
                    != questions.question_manifest.observed_sha256
                    or questions.core53_scope.entities.manifest_sha256
                    != questions.entity_binding_manifest.observed_sha256
                ):
                    blockers.add("core53_bound_identity_mismatch")
            except ValueError:
                blockers.add("core53_bound_scope_invalid")
    elif value.questions.core53_scope is not None:
        blockers.add("core53_revision_mismatch")
    if not minimum_total <= value.questions.approved_question_count <= maximum_total:
        blockers.add("approved_question_count_out_of_range")
    if set(value.questions.approved_family_counts) != set(families):
        blockers.add("approved_question_family_set_invalid")
    else:
        for family, (minimum, maximum) in families.items():
            count = value.questions.approved_family_counts[family]
            if not minimum <= count <= maximum:
                blockers.add(f"approved_{family}_question_count_out_of_range")
    if sum(value.questions.approved_family_counts.values()) != (
        value.questions.approved_question_count
    ):
        blockers.add("approved_question_count_mismatch")
    if value.questions.dataset_release_key != value.dataset_release.release_key or (
        value.questions.dataset_manifest_sha256
        != value.dataset_release.manifest.observed_sha256
    ):
        blockers.add("question_dataset_identity_mismatch")
    if value.questions.corpus_release_key != value.corpus_release.release_key or (
        value.questions.corpus_manifest_sha256
        != value.corpus_release.manifest.observed_sha256
    ):
        blockers.add("question_corpus_identity_mismatch")
    if value.associations.association_dimensions != REQUIRED_ASSOCIATION_DIMENSIONS:
        blockers.add("association_dimension_contract_invalid")
    if value.associations.represented_source_taxon_count < 2:
        blockers.add("source_taxon_diversity_insufficient")
    if value.associations.represented_assembly_count < 2:
        blockers.add("assembly_diversity_insufficient")
    if value.associations.represented_eve_locus_count < 1:
        blockers.add("eve_locus_coverage_missing")
    if value.associations.reported_viral_region_count < 1:
        blockers.add("reported_viral_region_coverage_missing")
    if value.associations.role_qualified_viral_lineage_count < 2:
        blockers.add("viral_lineage_diversity_insufficient")
    if value.associations.evidence_source_count < 2:
        blockers.add("evidence_source_diversity_insufficient")
    return blockers


def _artifact_blockers(prefix: str, value: ApprovedArtifactEvidence) -> set[str]:
    blockers: set[str] = set()
    if value.observed_sha256 is None:
        blockers.add(f"{prefix}_missing")
    if value.approval_status != "approved" or value.approved_sha256 is None:
        blockers.add(f"{prefix}_not_approved")
    if (
        value.observed_sha256 is not None
        and value.approved_sha256 is not None
        and value.observed_sha256 != value.approved_sha256
    ):
        blockers.add(f"{prefix}_hash_mismatch")
    if value.integrity_status != "passed":
        blockers.add(f"{prefix}_integrity_not_verified")
    return blockers


def _release_blockers(prefix: str, value: ReleaseEvidence) -> set[str]:
    blockers = _artifact_blockers(f"{prefix}_manifest", value.manifest)
    blockers |= _artifact_blockers(f"{prefix}_receipt", value.validation_receipt)
    blockers |= _artifact_blockers(f"{prefix}_snapshot", value.snapshot_fingerprint)
    if value.status != "published":
        blockers.add(f"{prefix}_release_not_published")
    if value.receipt_status != "passed":
        blockers.add(f"{prefix}_receipt_not_passed")
    if not value.receipt_trusted:
        blockers.add(f"{prefix}_receipt_not_trusted")
    return blockers


def _database_blockers(value: Phase3PreflightInput) -> set[str]:
    role = value.database_role
    blockers = _artifact_blockers("database_role_audit", role.audit)
    if not all(
        (
            role.role_default_transaction_read_only,
            role.runtime_transaction_read_only,
            role.schema_create_denied,
            role.table_create_denied,
            role.dml_denied,
        )
    ):
        blockers.add("database_role_not_strictly_read_only")
    return blockers


def _raw_context_blockers(value: Phase3PreflightInput) -> set[str]:
    raw = value.raw_context
    blockers = _artifact_blockers("raw_material_manifest", raw.material_manifest)
    blockers |= _artifact_blockers("raw_context_policy", raw.construction_policy)
    blockers |= _artifact_blockers("raw_tokenizer_artifact", raw.tokenizer_artifact)
    if (
        raw.tokenizer_artifact.observed_sha256
        != raw.tokenizer_artifact_manifest_sha256
    ):
        blockers.add("raw_tokenizer_artifact_identity_mismatch")
    if (
        raw.dataset_release_key != value.dataset_release.release_key
        or raw.dataset_manifest_sha256 != value.dataset_release.manifest.observed_sha256
        or raw.corpus_release_key != value.corpus_release.release_key
        or raw.corpus_manifest_sha256 != value.corpus_release.manifest.observed_sha256
    ):
        blockers.add("raw_context_release_identity_mismatch")
    if raw.reserved_output_tokens >= raw.model_context_limit_tokens:
        blockers.add("raw_context_token_budget_invalid")
    if not raw.truncation_policy_explicit:
        blockers.add("raw_context_truncation_policy_missing")
    if not raw.omission_policy_explicit:
        blockers.add("raw_context_omission_policy_missing")
    return blockers


def _fts_policy_blockers(value: Phase3PreflightInput) -> set[str]:
    blockers = _artifact_blockers("retrieval_policy", value.retrieval.policy_manifest)
    if value.retrieval.fts_policy_key != FTS_POLICY_KEY:
        blockers.add("fts_policy_identity_mismatch")
    return blockers


def _hybrid_retrieval_blockers(value: Phase3PreflightInput) -> set[str]:
    retrieval = value.retrieval
    blockers = _fts_policy_blockers(value)
    blockers |= _artifact_blockers("bge_artifact", retrieval.bge_artifact)
    if (
        retrieval.hybrid_retrieval_policy_key != RETRIEVAL_POLICY_KEY
        or retrieval.embedding_model_key != EMBEDDING_MODEL_KEY
        or retrieval.embedding_revision != EMBEDDING_REVISION
        or retrieval.branch_candidate_depth != 100
        or retrieval.rrf_k != 60
        or not retrieval.summary_branch_enabled
    ):
        blockers.add("hybrid_retrieval_identity_mismatch")
    if not retrieval.bge_complete_file_set_verified:
        blockers.add("bge_complete_file_set_not_verified")
    if not retrieval.offline_model_policy_enforced:
        blockers.add("offline_model_policy_not_enforced")
    return blockers


def _structured_association_blockers(value: Phase3PreflightInput) -> set[str]:
    return _artifact_blockers(
        "structured_association_manifest",
        value.associations.structured_association_manifest,
    )


def _structured_query_capability_blockers(value: Phase3PreflightInput) -> set[str]:
    capability = value.structured_query_capability
    blockers = _artifact_blockers(
        "structured_query_capability_receipt",
        capability.validation_receipt,
    )
    question_manifest_sha256 = value.questions.question_manifest.observed_sha256
    if capability.question_manifest_sha256 != question_manifest_sha256:
        blockers.add("structured_query_capability_question_identity_mismatch")
    expected = (
        value.questions.approved_family_counts.get("structured", 0)
        + value.questions.approved_family_counts.get("hybrid", 0)
    )
    if capability.applicable_question_count != expected:
        blockers.add("structured_query_applicable_count_mismatch")
    if capability.route_accepted_question_count != capability.applicable_question_count:
        blockers.add("structured_query_route_coverage_incomplete")
    if capability.query_plan_validated_question_count != capability.applicable_question_count:
        blockers.add("structured_query_plan_coverage_incomplete")
    if (
        capability.association_projection_validated_question_count
        != capability.applicable_question_count
    ):
        blockers.add("structured_association_projection_coverage_incomplete")
    return blockers


def _request_validation_blockers(value: Phase3PreflightInput) -> set[str]:
    request_validation = value.request_validation
    blockers = _artifact_blockers(
        "request_validation_receipt",
        request_validation.validation_receipt,
    )
    if (
        request_validation.question_manifest_sha256
        != value.questions.question_manifest.observed_sha256
    ):
        blockers.add("request_validation_question_identity_mismatch")
    if request_validation.replayed_question_count != value.questions.approved_question_count:
        blockers.add("request_validation_replay_coverage_incomplete")
    if (
        request_validation.scope_admitted_question_count
        + request_validation.scope_refused_question_count
        != request_validation.replayed_question_count
    ):
        blockers.add("request_validation_outcome_count_mismatch")
    if (
        request_validation.validated_scope_refusal_question_count
        != request_validation.scope_refused_question_count
    ):
        blockers.add("request_validation_refusal_coverage_incomplete")
    if request_validation.downstream_calls_after_scope_refusal:
        blockers.add("request_validation_downstream_after_refusal")
    return blockers


def _add(
    target: dict[Phase3SystemKey, set[str]],
    systems: tuple[Phase3SystemKey, ...],
    blockers: set[str],
) -> None:
    for system_key in systems:
        target[system_key].update(blockers)


def _self_sha256(value: StrictFrozenSchema, field_name: str) -> str:
    payload = value.model_dump(mode="python")
    payload.pop(field_name)
    return canonical_json_sha256(payload)


__all__ = [
    "AnchorEvidence",
    "ApprovedArtifactEvidence",
    "BindingEvidence",
    "DatabaseRoleEvidence",
    "PHASE3_SYSTEM_KEYS",
    "Phase3PreflightBlocked",
    "Phase3PreflightDecision",
    "Phase3PreflightInput",
    "Phase3PreflightReport",
    "Phase3SystemReadiness",
    "QuestionEvidence",
    "RawContextEvidence",
    "RequestValidationEvidence",
    "AssociationEvidence",
    "ReleaseEvidence",
    "RetrievalEvidence",
    "StructuredQueryCapabilityEvidence",
    "build_phase3_preflight_input",
    "construct_phase3_dependencies",
    "is_issued_phase3_preflight_decision",
    "run_phase3_preflight",
]
