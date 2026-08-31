"""Canonical inputs, replay, and identities for structured release receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from eve_relation_rag.activation import contracts as activation_contracts_module
from eve_relation_rag.activation import policy as activation_policy_module
from eve_relation_rag.domain import keys as domain_keys_module
from eve_relation_rag.domain.keys import canonical_json_sha256, is_release_key, stable_key
from eve_relation_rag.importers import audit as importer_audit_module
from eve_relation_rag.importers import data_s1 as importer_data_s1_module
from eve_relation_rag.releases import dependencies as release_dependencies_module
from eve_relation_rag.releases import validator as validator_module
from eve_relation_rag.releases.validator import (
    ReleaseValidationReport,
    ReleaseValidationRequest,
    validate_release,
)
from eve_relation_rag.retrieval.structured.capability import LineageRole

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ROLE_ORDER: tuple[LineageRole, ...] = (
    "assembly_source_taxonomy",
    "formal_viral_taxonomy",
    "study_viral_lineage",
    "extended_viral_lineage",
)
_REQUIRED_COMPLETE_ROLES = frozenset[LineageRole](
    {"assembly_source_taxonomy", "formal_viral_taxonomy"}
)
_REQUEST_ADAPTER: TypeAdapter[ReleaseValidationRequest] = TypeAdapter(ReleaseValidationRequest)


class DatasetReceiptIntegrityError(ValueError):
    """Raised when structured release evidence cannot be replayed exactly."""


class _StrictFrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def structured_activation_policy_code_sha256() -> str:
    """Hash the code that interprets the structured activation manifests.

    The scientific activation policy is intentionally kept separate from the
    release validator bundle.  A receipt binds both identities so changing the
    inclusion/adjudication contract cannot silently retain an older approval.
    """

    files: list[dict[str, str]] = []
    for module_name, module in (
        ("eve_relation_rag.activation.contracts", activation_contracts_module),
        ("eve_relation_rag.activation.policy", activation_policy_module),
    ):
        source_path_text = module.__file__
        if source_path_text is None:  # pragma: no cover - source installs have paths.
            raise DatasetReceiptIntegrityError("activation policy source is unavailable")
        try:
            source_sha256 = hashlib.sha256(Path(source_path_text).read_bytes()).hexdigest()
        except OSError as exc:
            raise DatasetReceiptIntegrityError("activation policy source is unavailable") from exc
        files.append({"module": module_name, "sha256": source_sha256})
    files.sort(key=lambda item: item["module"])
    return canonical_json_sha256(
        {
            "bundle_schema_version": "structured-activation-policy-code-v1",
            "files": files,
        }
    )


class DatasetCandidateActivationEvidence(_StrictFrozenSchema):
    """Structured activation identities that exist before route validation.

    This object deliberately contains no rebuild, benchmark, human-review, or
    receipt identity.  It is therefore an acyclic trust root for the candidate
    capability and for every pre-receipt validation artifact.
    """

    evidence_schema_version: Literal["dataset-candidate-activation-evidence-v1"]
    release_key: str
    structured_activation_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_audit_sha256: str = Field(pattern=_SHA256_PATTERN)
    ncbi_artifact_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    ncbi_snapshot_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    ictv_artifact_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    ictv_snapshot_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    flank_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    inclusion_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    adjudication_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_locus_membership_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_assertion_membership_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    activation_policy_code_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_complete_evidence(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("activation evidence release_key is invalid")
        payload = self.model_dump(mode="json")
        del payload["evidence_sha256"]
        if self.evidence_sha256 != canonical_json_sha256(payload):
            raise ValueError("activation evidence self-checksum is inconsistent")
        return self


def build_dataset_candidate_activation_evidence(
    *,
    release_key: str,
    structured_activation_manifest_sha256: str,
    source_manifest_sha256: str,
    source_audit_sha256: str,
    ncbi_artifact_manifest_sha256: str,
    ncbi_snapshot_manifest_sha256: str,
    ictv_artifact_manifest_sha256: str,
    ictv_snapshot_manifest_sha256: str,
    flank_manifest_sha256: str,
    inclusion_manifest_sha256: str,
    adjudication_manifest_sha256: str,
    public_locus_membership_manifest_sha256: str,
    public_assertion_membership_manifest_sha256: str,
) -> DatasetCandidateActivationEvidence:
    """Build the checksum-frozen activation packet available before validation."""

    payload: dict[str, object] = {
        "evidence_schema_version": "dataset-candidate-activation-evidence-v1",
        "release_key": release_key,
        "structured_activation_manifest_sha256": (structured_activation_manifest_sha256),
        "source_manifest_sha256": source_manifest_sha256,
        "source_audit_sha256": source_audit_sha256,
        "ncbi_artifact_manifest_sha256": ncbi_artifact_manifest_sha256,
        "ncbi_snapshot_manifest_sha256": ncbi_snapshot_manifest_sha256,
        "ictv_artifact_manifest_sha256": ictv_artifact_manifest_sha256,
        "ictv_snapshot_manifest_sha256": ictv_snapshot_manifest_sha256,
        "flank_manifest_sha256": flank_manifest_sha256,
        "inclusion_manifest_sha256": inclusion_manifest_sha256,
        "adjudication_manifest_sha256": adjudication_manifest_sha256,
        "public_locus_membership_manifest_sha256": (public_locus_membership_manifest_sha256),
        "public_assertion_membership_manifest_sha256": (
            public_assertion_membership_manifest_sha256
        ),
        "activation_policy_code_sha256": structured_activation_policy_code_sha256(),
    }
    payload["evidence_sha256"] = canonical_json_sha256(payload)
    return DatasetCandidateActivationEvidence.model_validate(payload)


def load_dataset_candidate_activation_evidence(
    path: Path, *, approved_evidence_sha256: str
) -> DatasetCandidateActivationEvidence:
    """Load candidate activation evidence at an explicit checksum boundary."""

    try:
        evidence = DatasetCandidateActivationEvidence.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise DatasetReceiptIntegrityError(
            "candidate activation evidence is invalid"
        ) from exc
    if evidence.evidence_sha256 != approved_evidence_sha256:
        raise DatasetReceiptIntegrityError(
            "approved candidate activation evidence checksum does not match"
        )
    return evidence


class DatasetActivationEvidence(_StrictFrozenSchema):
    """Post-benchmark ACT-D04 evidence bound one-way to a candidate input.

    Hashes alone never turn a failing report into passing evidence.  The four
    literal pass flags make the approval object unable to bind a failed rebuild,
    benchmark, or human review while still satisfying the receipt schema.
    """

    evidence_schema_version: Literal["dataset-activation-evidence-v2"]
    release_key: str
    candidate_validation_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    clean_rebuild_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    clean_rebuild_passed: Literal[True]
    structured_benchmark_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    structured_benchmark_passed: Literal[True]
    hybrid_benchmark_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    hybrid_benchmark_passed: Literal[True]
    human_review_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    human_review_passed: Literal[True]
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_complete_evidence(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("activation evidence release_key is invalid")
        payload = self.model_dump(mode="json")
        del payload["evidence_sha256"]
        if self.evidence_sha256 != canonical_json_sha256(payload):
            raise ValueError("activation evidence self-checksum is inconsistent")
        return self


def build_dataset_activation_evidence(
    *,
    candidate_validation_input_sha256: str,
    release_key: str,
    clean_rebuild_report_sha256: str,
    structured_benchmark_report_sha256: str,
    hybrid_benchmark_report_sha256: str,
    human_review_report_sha256: str,
) -> DatasetActivationEvidence:
    """Build one report packet after every required pre-receipt gate passes."""

    payload: dict[str, object] = {
        "evidence_schema_version": "dataset-activation-evidence-v2",
        "release_key": release_key,
        "candidate_validation_input_sha256": candidate_validation_input_sha256,
        "clean_rebuild_report_sha256": clean_rebuild_report_sha256,
        "clean_rebuild_passed": True,
        "structured_benchmark_report_sha256": structured_benchmark_report_sha256,
        "structured_benchmark_passed": True,
        "hybrid_benchmark_report_sha256": hybrid_benchmark_report_sha256,
        "hybrid_benchmark_passed": True,
        "human_review_report_sha256": human_review_report_sha256,
        "human_review_passed": True,
    }
    payload["evidence_sha256"] = canonical_json_sha256(payload)
    return DatasetActivationEvidence.model_validate(payload)


def load_dataset_activation_evidence(
    path: Path, *, approved_evidence_sha256: str
) -> DatasetActivationEvidence:
    """Load activation evidence only at its separate checksum approval boundary."""

    try:
        evidence = DatasetActivationEvidence.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DatasetReceiptIntegrityError("activation evidence is invalid") from exc
    if evidence.evidence_sha256 != approved_evidence_sha256:
        raise DatasetReceiptIntegrityError("approved activation evidence checksum does not match")
    return evidence


def _json_compatible(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DatasetReceiptIntegrityError("receipt datetimes must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible(child) for child in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise DatasetReceiptIntegrityError(
        f"receipt evidence contains unsupported value {type(value).__name__}"
    )


def validation_request_payload(request: ReleaseValidationRequest) -> dict[str, object]:
    """Return the complete canonical JSON-compatible validator request."""

    payload = _json_compatible(asdict(request))
    if not isinstance(payload, dict):  # pragma: no cover - dataclass invariant.
        raise DatasetReceiptIntegrityError("validation request did not serialize as an object")
    return cast(dict[str, object], payload)


def validation_request_from_payload(payload: Mapping[str, object]) -> ReleaseValidationRequest:
    """Strictly reconstruct a validator request and reject lossy/coercive payloads."""

    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        request = _REQUEST_ADAPTER.validate_json(encoded)
    except Exception as exc:
        raise DatasetReceiptIntegrityError("validation request payload is invalid") from exc
    if validation_request_payload(request) != dict(payload):
        raise DatasetReceiptIntegrityError("validation request payload is not canonical")
    return request


def release_validator_code_sha256() -> str:
    """Hash the validator and every direct scientific-policy dependency."""

    modules: tuple[tuple[str, ModuleType], ...] = (
        ("eve_relation_rag.domain.keys", domain_keys_module),
        ("eve_relation_rag.importers.audit", importer_audit_module),
        ("eve_relation_rag.importers.data_s1", importer_data_s1_module),
        ("eve_relation_rag.releases.dependencies", release_dependencies_module),
        ("eve_relation_rag.releases.validator", validator_module),
    )
    files: list[dict[str, str]] = []
    for module_name, module in modules:
        source_path_text = module.__file__
        if source_path_text is None:  # pragma: no cover - source installs have paths.
            raise DatasetReceiptIntegrityError("release validator source is unavailable")
        try:
            source_sha256 = hashlib.sha256(Path(source_path_text).read_bytes()).hexdigest()
        except OSError as exc:
            raise DatasetReceiptIntegrityError("release validator source is unavailable") from exc
        files.append({"module": module_name, "sha256": source_sha256})
    files.append(
        {
            "module": "eve_relation_rag.releases.receipt_integrity",
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    files.sort(key=lambda item: item["module"])
    return canonical_json_sha256(
        {
            "bundle_schema_version": "dataset-release-validator-code-v1",
            "files": files,
        }
    )


def _validate_complete_roles(roles: tuple[LineageRole, ...]) -> None:
    canonical_roles = tuple(role for role in _ROLE_ORDER if role in roles)
    if canonical_roles != roles:
        raise ValueError("complete lineage roles must be unique and canonically ordered")
    if not _REQUIRED_COMPLETE_ROLES.issubset(roles):
        raise ValueError("host and formal viral lineage closure roles must be complete")


class DatasetCandidateValidationInput(_StrictFrozenSchema):
    """Acyclic candidate trust root approved before benchmarks or rebuilds run."""

    input_schema_version: Literal["dataset-candidate-validation-input-v1"]
    release_key: str
    release_schema_version: str
    release_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_dependency_graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_activation_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_activation_evidence: DatasetCandidateActivationEvidence
    complete_lineage_closure_roles: tuple[LineageRole, ...]
    validator_code_sha256: str = Field(pattern=_SHA256_PATTERN)
    validation_request_sha256: str = Field(pattern=_SHA256_PATTERN)
    validation_request: dict[str, object]
    input_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_candidate_input(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("release_key is not an exact immutable release key")
        if not self.release_schema_version.strip():
            raise ValueError("release_schema_version is required")
        _validate_complete_roles(self.complete_lineage_closure_roles)
        activation = self.candidate_activation_evidence
        if activation.release_key != self.release_key:
            raise ValueError("candidate activation evidence targets a different release")
        if activation.structured_activation_manifest_sha256 != self.release_manifest_sha256:
            raise ValueError("candidate activation evidence does not bind the release manifest")
        if activation.evidence_sha256 != self.candidate_activation_evidence_sha256:
            raise ValueError("candidate activation evidence checksum is inconsistent")
        request = validation_request_from_payload(self.validation_request)
        if request.release_key != self.release_key:
            raise ValueError("validation request targets a different release")
        if self.validation_request_sha256 != canonical_json_sha256(self.validation_request):
            raise ValueError("validation request checksum is inconsistent")
        payload = self.model_dump(mode="json")
        del payload["input_sha256"]
        if self.input_sha256 != canonical_json_sha256(payload):
            raise ValueError("candidate validation input self-checksum is inconsistent")
        return self


def build_dataset_candidate_validation_input(
    *,
    release_schema_version: str,
    release_manifest_sha256: str,
    expected_dependency_graph_sha256: str,
    candidate_activation_evidence: DatasetCandidateActivationEvidence,
    complete_lineage_closure_roles: tuple[LineageRole, ...],
    request: ReleaseValidationRequest,
) -> DatasetCandidateValidationInput:
    """Build the exact scientific input shared by all pre-receipt reports."""

    request_payload = validation_request_payload(request)
    payload: dict[str, Any] = {
        "input_schema_version": "dataset-candidate-validation-input-v1",
        "release_key": request.release_key,
        "release_schema_version": release_schema_version,
        "release_manifest_sha256": release_manifest_sha256,
        "expected_dependency_graph_sha256": expected_dependency_graph_sha256,
        "candidate_activation_evidence_sha256": (
            candidate_activation_evidence.evidence_sha256
        ),
        "candidate_activation_evidence": candidate_activation_evidence.model_dump(
            mode="json"
        ),
        "complete_lineage_closure_roles": complete_lineage_closure_roles,
        "validator_code_sha256": release_validator_code_sha256(),
        "validation_request_sha256": canonical_json_sha256(request_payload),
        "validation_request": request_payload,
    }
    payload["input_sha256"] = canonical_json_sha256(payload)
    return DatasetCandidateValidationInput.model_validate(payload)


def load_dataset_candidate_validation_input(
    path: Path, *, approved_input_sha256: str
) -> DatasetCandidateValidationInput:
    """Load a candidate input only at its explicit checksum approval boundary."""

    try:
        candidate = DatasetCandidateValidationInput.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise DatasetReceiptIntegrityError("candidate validation input is invalid") from exc
    if candidate.input_sha256 != approved_input_sha256:
        raise DatasetReceiptIntegrityError(
            "approved candidate validation input checksum does not match"
        )
    return candidate


def structured_candidate_capability_sha256(
    candidate: DatasetCandidateValidationInput,
) -> str:
    """Derive the validation-only capability identity from the approved input."""

    candidate = DatasetCandidateValidationInput.model_validate_json(
        candidate.model_dump_json(), strict=True
    )
    return canonical_json_sha256(
        {
            "candidate_capability_schema_version": (
                "v0-structured-candidate-capability-v1"
            ),
            "candidate_validation_input_sha256": candidate.input_sha256,
            "release_key": candidate.release_key,
            "release_manifest_sha256": candidate.release_manifest_sha256,
            "validation_request_sha256": candidate.validation_request_sha256,
            "dependency_graph_sha256": candidate.expected_dependency_graph_sha256,
        }
    )


class ApprovedDatasetValidationInput(_StrictFrozenSchema):
    """Separately checksum-approved scientific evidence submitted to publication."""

    input_schema_version: Literal["dataset-validation-input-v3"]
    release_key: str
    release_schema_version: str
    release_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_dependency_graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_validation_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_validation_input: DatasetCandidateValidationInput
    activation_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    activation_evidence: DatasetActivationEvidence
    complete_lineage_closure_roles: tuple[LineageRole, ...]
    validator_code_sha256: str = Field(pattern=_SHA256_PATTERN)
    validation_request_sha256: str = Field(pattern=_SHA256_PATTERN)
    validation_request: dict[str, object]
    input_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_complete_input(self) -> Self:
        if not is_release_key(self.release_key):
            raise ValueError("release_key is not an exact immutable release key")
        if not self.release_schema_version.strip():
            raise ValueError("release_schema_version is required")
        _validate_complete_roles(self.complete_lineage_closure_roles)
        candidate = self.candidate_validation_input
        candidate_pairs = (
            (candidate.input_sha256, self.candidate_validation_input_sha256),
            (candidate.release_key, self.release_key),
            (candidate.release_schema_version, self.release_schema_version),
            (candidate.release_manifest_sha256, self.release_manifest_sha256),
            (
                candidate.expected_dependency_graph_sha256,
                self.expected_dependency_graph_sha256,
            ),
            (
                candidate.complete_lineage_closure_roles,
                self.complete_lineage_closure_roles,
            ),
            (candidate.validator_code_sha256, self.validator_code_sha256),
            (candidate.validation_request_sha256, self.validation_request_sha256),
            (candidate.validation_request, self.validation_request),
        )
        if any(observed != expected for observed, expected in candidate_pairs):
            raise ValueError("approved input differs from its candidate validation input")
        if self.activation_evidence.release_key != self.release_key:
            raise ValueError("activation evidence targets a different release")
        if (
            self.activation_evidence.candidate_validation_input_sha256
            != self.candidate_validation_input_sha256
        ):
            raise ValueError("activation evidence does not bind the candidate input")
        if self.activation_evidence.evidence_sha256 != self.activation_evidence_sha256:
            raise ValueError("activation evidence checksum is inconsistent")
        request = validation_request_from_payload(self.validation_request)
        if request.release_key != self.release_key:
            raise ValueError("validation request targets a different release")
        expected_request_sha256 = canonical_json_sha256(self.validation_request)
        if self.validation_request_sha256 != expected_request_sha256:
            raise ValueError("validation request checksum is inconsistent")
        payload = self.model_dump(mode="json")
        del payload["input_sha256"]
        if self.input_sha256 != canonical_json_sha256(payload):
            raise ValueError("validation input self-checksum is inconsistent")
        return self


def build_approved_validation_input(
    *,
    candidate_validation_input: DatasetCandidateValidationInput,
    activation_evidence: DatasetActivationEvidence,
) -> ApprovedDatasetValidationInput:
    """Build one canonical input ready for separate checksum approval."""

    candidate = DatasetCandidateValidationInput.model_validate_json(
        candidate_validation_input.model_dump_json(), strict=True
    )
    payload: dict[str, Any] = {
        "input_schema_version": "dataset-validation-input-v3",
        "release_key": candidate.release_key,
        "release_schema_version": candidate.release_schema_version,
        "release_manifest_sha256": candidate.release_manifest_sha256,
        "expected_dependency_graph_sha256": candidate.expected_dependency_graph_sha256,
        "candidate_validation_input_sha256": candidate.input_sha256,
        "candidate_validation_input": candidate.model_dump(mode="json"),
        "activation_evidence_sha256": activation_evidence.evidence_sha256,
        "activation_evidence": activation_evidence.model_dump(mode="json"),
        "complete_lineage_closure_roles": candidate.complete_lineage_closure_roles,
        "validator_code_sha256": candidate.validator_code_sha256,
        "validation_request_sha256": candidate.validation_request_sha256,
        "validation_request": candidate.validation_request,
    }
    payload["input_sha256"] = canonical_json_sha256(payload)
    return ApprovedDatasetValidationInput.model_validate_json(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        strict=True,
    )


def load_approved_validation_input(
    path: Path, *, approved_input_sha256: str
) -> ApprovedDatasetValidationInput:
    """Load one strict local input only when its self-hash was separately approved."""

    try:
        approved = ApprovedDatasetValidationInput.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise DatasetReceiptIntegrityError("approved validation input is invalid") from exc
    if approved.input_sha256 != approved_input_sha256:
        raise DatasetReceiptIntegrityError("approved validation input checksum does not match")
    return approved


def load_validation_request(path: Path) -> ReleaseValidationRequest:
    """Load a complete canonical validator request without coercion."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DatasetReceiptIntegrityError("validation request JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise DatasetReceiptIntegrityError("validation request must be a JSON object")
    return validation_request_from_payload(payload)


class TrustedDatasetReceiptEvidence(_StrictFrozenSchema):
    """Complete receipt evidence replayed by publication and every production gate."""

    evidence_schema_version: Literal["dataset-validation-evidence-v1"]
    validation_input: ApprovedDatasetValidationInput
    dependency_graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    validation_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    validation_report: dict[str, object]

    @model_validator(mode="after")
    def replay_scientific_validation(self) -> Self:
        request = validation_request_from_payload(self.validation_input.validation_request)
        report = validate_release(request)
        expected_report = report.to_dict()
        if not report.valid:
            raise ValueError("trusted dataset receipt requires a passing validation report")
        if self.validation_report != expected_report:
            raise ValueError("persisted validation report does not replay exactly")
        if self.validation_report_sha256 != canonical_json_sha256(expected_report):
            raise ValueError("validation report checksum is inconsistent")
        return self


def build_trusted_receipt_evidence(
    approved: ApprovedDatasetValidationInput,
    *,
    dependency_graph_sha256: str,
) -> tuple[TrustedDatasetReceiptEvidence, ReleaseValidationRequest, ReleaseValidationReport]:
    """Replay an approved request and build evidence only for a passing result."""

    if approved.validator_code_sha256 != release_validator_code_sha256():
        raise DatasetReceiptIntegrityError(
            "approved validator code checksum does not match runtime"
        )
    if (
        approved.candidate_validation_input.candidate_activation_evidence.activation_policy_code_sha256
        != structured_activation_policy_code_sha256()
    ):
        raise DatasetReceiptIntegrityError(
            "approved activation policy checksum does not match runtime"
        )
    if dependency_graph_sha256 != approved.expected_dependency_graph_sha256:
        raise DatasetReceiptIntegrityError(
            "live dependency graph checksum does not match approved input"
        )
    request = validation_request_from_payload(approved.validation_request)
    report = validate_release(request)
    if not report.valid:
        raise DatasetReceiptIntegrityError("approved structured validation did not pass")
    report_payload = report.to_dict()
    evidence = TrustedDatasetReceiptEvidence(
        evidence_schema_version="dataset-validation-evidence-v1",
        validation_input=approved,
        dependency_graph_sha256=dependency_graph_sha256,
        validation_report_sha256=canonical_json_sha256(report_payload),
        validation_report=report_payload,
    )
    return evidence, request, report


def receipt_payload(evidence: TrustedDatasetReceiptEvidence) -> dict[str, object]:
    """Return the small canonical preimage for one immutable receipt identity."""

    approved = evidence.validation_input
    return {
        "receipt_schema_version": "dataset-validation-receipt-v1",
        "release_key": approved.release_key,
        "release_schema_version": approved.release_schema_version,
        "manifest_sha256": approved.release_manifest_sha256,
        "expected_dependency_graph_sha256": (approved.expected_dependency_graph_sha256),
        "candidate_validation_input_sha256": (
            approved.candidate_validation_input_sha256
        ),
        "activation_evidence_sha256": approved.activation_evidence_sha256,
        "input_sha256": approved.input_sha256,
        "validation_request_sha256": approved.validation_request_sha256,
        "validation_report_sha256": evidence.validation_report_sha256,
        "dependency_graph_sha256": evidence.dependency_graph_sha256,
        "validator_code_sha256": approved.validator_code_sha256,
        "complete_lineage_closure_roles": list(approved.complete_lineage_closure_roles),
    }


def receipt_identity(evidence: TrustedDatasetReceiptEvidence) -> tuple[str, str]:
    """Derive the stable receipt key and checksum from replayable evidence."""

    payload = receipt_payload(evidence)
    receipt_key = stable_key("dataset-receipt", payload)
    receipt_sha256 = canonical_json_sha256(
        {"receipt_key": receipt_key, "status": "passed", "trusted": True, **payload}
    )
    return receipt_key, receipt_sha256


def validate_persisted_dataset_receipt(
    *,
    release_key: str,
    release_schema_version: str,
    release_manifest_sha256: str,
    current_dependency_graph_sha256: str,
    receipt_key: str,
    receipt_status: str,
    receipt_trusted: bool,
    receipt_manifest_sha256: str,
    receipt_dependency_graph_sha256: str,
    receipt_validation_request_sha256: str,
    receipt_activation_evidence_sha256: str,
    receipt_candidate_validation_input_sha256: str,
    receipt_validation_input_sha256: str,
    receipt_validation_report_sha256: str,
    receipt_validator_code_sha256: str,
    receipt_sha256: str,
    receipt_complete_lineage_closure_roles: list[str],
    validation_evidence: Mapping[str, object],
) -> TrustedDatasetReceiptEvidence:
    """Reload, replay, and bind one persisted receipt to its live release graph."""

    try:
        encoded = json.dumps(
            dict(validation_evidence),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        evidence = TrustedDatasetReceiptEvidence.model_validate_json(encoded)
        expected_key, expected_sha256 = receipt_identity(evidence)
    except Exception as exc:
        raise DatasetReceiptIntegrityError("trusted dataset receipt evidence is invalid") from exc

    approved = evidence.validation_input
    expected_roles = list(approved.complete_lineage_closure_roles)
    expected_pairs = (
        (receipt_status, "passed"),
        (receipt_trusted, True),
        (release_key, approved.release_key),
        (release_schema_version, approved.release_schema_version),
        (release_manifest_sha256, approved.release_manifest_sha256),
        (current_dependency_graph_sha256, evidence.dependency_graph_sha256),
        (receipt_manifest_sha256, approved.release_manifest_sha256),
        (receipt_dependency_graph_sha256, evidence.dependency_graph_sha256),
        (receipt_validation_request_sha256, approved.validation_request_sha256),
        (receipt_activation_evidence_sha256, approved.activation_evidence_sha256),
        (
            receipt_candidate_validation_input_sha256,
            approved.candidate_validation_input_sha256,
        ),
        (receipt_validation_input_sha256, approved.input_sha256),
        (receipt_validation_report_sha256, evidence.validation_report_sha256),
        (receipt_validator_code_sha256, approved.validator_code_sha256),
        (receipt_validator_code_sha256, release_validator_code_sha256()),
        (
            approved.candidate_validation_input.candidate_activation_evidence.activation_policy_code_sha256,
            structured_activation_policy_code_sha256(),
        ),
        (receipt_complete_lineage_closure_roles, expected_roles),
        (receipt_key, expected_key),
        (receipt_sha256, expected_sha256),
    )
    if any(observed != expected for observed, expected in expected_pairs):
        raise DatasetReceiptIntegrityError(
            "trusted dataset receipt does not match its release, graph, or evidence"
        )
    return evidence


__all__ = [
    "ApprovedDatasetValidationInput",
    "DatasetActivationEvidence",
    "DatasetCandidateActivationEvidence",
    "DatasetCandidateValidationInput",
    "DatasetReceiptIntegrityError",
    "TrustedDatasetReceiptEvidence",
    "build_approved_validation_input",
    "build_dataset_activation_evidence",
    "build_dataset_candidate_activation_evidence",
    "build_dataset_candidate_validation_input",
    "build_trusted_receipt_evidence",
    "load_dataset_activation_evidence",
    "load_dataset_candidate_activation_evidence",
    "load_dataset_candidate_validation_input",
    "load_approved_validation_input",
    "load_validation_request",
    "receipt_identity",
    "release_validator_code_sha256",
    "structured_activation_policy_code_sha256",
    "structured_candidate_capability_sha256",
    "validate_persisted_dataset_receipt",
    "validation_request_from_payload",
    "validation_request_payload",
]
