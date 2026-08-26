"""Fail-closed validation for proposed Milestone 1 release memberships.

The validator operates on immutable data-transfer objects rather than ORM
models.  A repository layer can project database rows into these objects
without allowing validation to mutate truth-layer state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from eve_relation_rag.domain.keys import (
    LocusIdentity,
    StableKeyError,
    is_release_key,
    is_versioned_assembly_accession,
    is_versioned_contig_accession,
)
from eve_relation_rag.importers.audit import (
    APPROVED_DATA_S1_EXPECTED_COUNTS,
    APPROVED_DATA_S1_KEY_DIGESTS,
)
from eve_relation_rag.importers.data_s1 import (
    DATA_S1_ARTIFACT_SHA256,
    DATA_S1_SOURCE_SNAPSHOT_KEY,
)

Severity = Literal["error", "warning"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_COORDINATE_SYSTEM = "0-based-half-open"
_PASSING_FLANK_VERDICT = "supported"
_PILOT_FLANK_WINDOW_BP = 20_000
_SOURCE_ASSESSMENTS = frozenset({"source_high", "source_low"})
_M1_MANIFEST_SHA256: Final = (
    "afa5982542c592aaec6ec1033e0ac9ebbd3786e881baed0d81a1a602a30adf0d"
)
_M1_ARTIFACT_KEY: Final = (
    "source-artifact:biorxiv-data-s1:sha256:"
    "4b9090d9f3e651179680361af19097e1b5d2ab267da4f221caa6838a6b240150"
)
_M1_ARTIFACT_URI: Final = (
    "https://www.biorxiv.org/content/biorxiv/early/2026/05/21/"
    "2025.04.19.649669/DC6/embed/media-6.xlsx?download=true"
)
_M1_LICENSE_KEY: Final = "CC-BY-NC-ND-4.0"
_M1_AUDIT_SCHEMA: Final = "endoviho-milestone1-source-audit-v1"
_M1_SOURCE_RECORD_COUNT: Final = APPROVED_DATA_S1_EXPECTED_COUNTS["source_records"]
_M1_ACCOUNTED_QUARANTINE_COUNT: Final = APPROVED_DATA_S1_EXPECTED_COUNTS[
    "vr_type_viral_contig"
]
_M1_CALL_KEYS_SHA256: Final = APPROVED_DATA_S1_KEY_DIGESTS[
    "sorted_call_keys_sha256"
]
_M1_LOCUS_KEYS_SHA256: Final = APPROVED_DATA_S1_KEY_DIGESTS[
    "sorted_locus_keys_sha256"
]
_NCBI_TAXONOMY_AUTHORITY: Final = "NCBI Taxonomy"
_NCBI_USAGE_BASIS: Final = "NCBI-MOLECULAR-DATA-USAGE-POLICY"
_ICTV_MSL_VERSION: Final = "MSL41 v1"


@dataclass(frozen=True, slots=True)
class SourceManifestEvidence:
    """Frozen source metadata and independently verified values."""

    source_snapshot_key: str | None
    manifest_sha256: str | None
    verified_manifest_sha256: str | None
    artifact_key: str | None
    artifact_sha256: str | None
    verified_artifact_sha256: str | None
    license_key: str | None
    verified_license_key: str | None
    provenance_uri: str | None
    remote_artifact_verified: bool
    remote_artifact_uri: str | None
    remote_retrieved_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlacementEvidence:
    """One normalized placement proposed for public membership."""

    contig_accession_version: str | None
    start0: int | None
    end0: int | None
    precision: str | None
    coordinate_system: str | None
    provenance_key: str | None


@dataclass(frozen=True, slots=True)
class FlankEvidence:
    """An independently recorded left or right host-flank assessment."""

    side: str
    verdict: str | None
    policy_key: str | None
    evidence_key: str | None
    inspection_window_bp: int | None
    available_bp: int | None
    inspected_bp: int | None
    method_or_curator_key: str | None
    evidence_sha256: str | None


@dataclass(frozen=True, slots=True)
class SourceAuditEvidence:
    """Frozen whole-ledger evidence required before membership validation."""

    audit_schema: str | None
    audit_artifact_sha256: str | None
    verified_audit_artifact_sha256: str | None
    passed: bool
    expected_source_record_count: int | None
    observed_source_record_count: int | None
    expected_accounted_quarantine_count: int | None
    expected_call_keys_sha256: str | None
    observed_call_keys_sha256: str | None
    expected_locus_keys_sha256: str | None
    observed_locus_keys_sha256: str | None


@dataclass(frozen=True, slots=True)
class NcbiTaxonomyEvidence:
    """Complete, frozen NCBI Taxonomy evidence bound to the proposed release."""

    snapshot_key: str | None
    authority: str | None
    version: str | None
    artifact_key: str | None
    artifact_sha256: str | None
    verified_artifact_sha256: str | None
    provenance_uri: str | None
    usage_basis_key: str | None
    retrieved_at: datetime | None
    release_bound: bool
    merged_history_included: bool
    deleted_history_included: bool


@dataclass(frozen=True, slots=True)
class IctvReleaseEvidence:
    """Frozen ICTV MSL41 and corrected VMR artifacts bound to the release."""

    msl_snapshot_key: str | None
    msl_version: str | None
    msl_artifact_key: str | None
    msl_artifact_sha256: str | None
    verified_msl_artifact_sha256: str | None
    vmr_artifact_key: str | None
    vmr_artifact_sha256: str | None
    verified_vmr_artifact_sha256: str | None
    provenance_uri: str | None
    license_key: str | None
    retrieved_at: datetime | None
    msl_release_bound: bool
    vmr_release_bound: bool
    vmr_corrected: bool


@dataclass(frozen=True, slots=True)
class InclusionEvidence:
    """The explicit decision authorizing a proposed membership."""

    decision: str
    policy_key: str | None
    authorized_by: str | None


@dataclass(frozen=True, slots=True)
class ReleaseMembershipCandidate:
    """All gates required to evaluate one proposed locus membership."""

    locus_key: str
    identity: LocusIdentity
    assembly_accession_version: str
    assembly_resolution: str
    contig_accession_version: str
    contig_resolution: str
    contig_length: int | None
    source_record_key: str | None
    method_key: str | None
    import_run_key: str | None
    source_assessment: str | None
    placements: tuple[PlacementEvidence, ...] = ()
    flank_assessments: tuple[FlankEvidence, ...] = ()
    inclusion: InclusionEvidence | None = None
    unresolved_issues: tuple[str, ...] = ()
    quarantine_issues: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReleaseValidationRequest:
    """A frozen source manifest plus the memberships proposed for one release.

    ``unresolved_quarantine_issues`` means quarantine ledger rows that lack a
    reconciled terminal outcome or otherwise fail accounting.  Properly
    retained terminal quarantine rows belong in ``accounted_quarantine_count``
    and do not block publication merely because they exist.
    """

    release_key: str
    source: SourceManifestEvidence
    source_audit: SourceAuditEvidence | None
    ncbi_taxonomy: NcbiTaxonomyEvidence | None
    ictv: IctvReleaseEvidence | None
    candidates: tuple[ReleaseMembershipCandidate, ...]
    unresolved_issues: tuple[str, ...] = ()
    unresolved_quarantine_issues: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    accounted_quarantine_count: int = 0


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One stable, machine-readable release validation finding."""

    severity: Severity
    code: str
    message: str
    field: str | None = None
    candidate_index: int | None = None
    locus_key: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "candidate_index": self.candidate_index,
            "locus_key": self.locus_key,
        }


@dataclass(frozen=True, slots=True)
class ValidationCounts:
    """Deterministic release-validation counters."""

    candidate_count: int
    eligible_membership_count: int
    blocked_membership_count: int
    explicit_include_count: int
    source_high_count: int
    source_low_count: int
    audited_source_record_count: int
    accounted_quarantine_count: int
    error_count: int
    warning_count: int

    def to_dict(self) -> dict[str, int]:
        return {
            "candidate_count": self.candidate_count,
            "eligible_membership_count": self.eligible_membership_count,
            "blocked_membership_count": self.blocked_membership_count,
            "explicit_include_count": self.explicit_include_count,
            "source_high_count": self.source_high_count,
            "source_low_count": self.source_low_count,
            "audited_source_record_count": self.audited_source_record_count,
            "accounted_quarantine_count": self.accounted_quarantine_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
        }


@dataclass(frozen=True, slots=True)
class ReleaseValidationReport:
    """Complete validator output; ``valid`` is false whenever errors exist."""

    release_key: str
    valid: bool
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    counts: ValidationCounts

    def to_dict(self) -> dict[str, object]:
        return {
            "release_key": self.release_key,
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "counts": self.counts.to_dict(),
        }


def _present(value: str | None) -> bool:
    return value is not None and bool(value) and value == value.strip()


def _valid_sha256(value: str | None) -> bool:
    return value is not None and _SHA256_RE.fullmatch(value) is not None


def _timezone_aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


def validate_release(request: ReleaseValidationRequest) -> ReleaseValidationReport:
    """Validate every proposed membership and return all fail-closed findings."""

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    blocked: set[int] = set()
    release_blocked = False

    def add_error(
        code: str,
        message: str,
        *,
        field: str | None = None,
        candidate_index: int | None = None,
        locus_key: str | None = None,
    ) -> None:
        nonlocal release_blocked
        errors.append(
            ValidationIssue(
                severity="error",
                code=code,
                message=message,
                field=field,
                candidate_index=candidate_index,
                locus_key=locus_key,
            )
        )
        if candidate_index is None:
            release_blocked = True
        else:
            blocked.add(candidate_index)

    def add_warning(
        code: str,
        message: str,
        *,
        field: str | None = None,
        candidate_index: int | None = None,
        locus_key: str | None = None,
    ) -> None:
        warnings.append(
            ValidationIssue(
                severity="warning",
                code=code,
                message=message,
                field=field,
                candidate_index=candidate_index,
                locus_key=locus_key,
            )
        )

    if not _present(request.release_key):
        add_error("release_key_missing", "A non-empty immutable release key is required.")
    elif not is_release_key(request.release_key):
        add_error(
            "release_key_invalid",
            "Release key does not follow release:<dataset>:v<version>:YYYYMMDD:NNN.",
            field="release_key",
        )

    source = request.source
    required_source_text = (
        ("source_snapshot_key", source.source_snapshot_key),
        ("artifact_key", source.artifact_key),
        ("license_key", source.license_key),
        ("verified_license_key", source.verified_license_key),
        ("provenance_uri", source.provenance_uri),
        ("remote_artifact_uri", source.remote_artifact_uri),
    )
    for field, value in required_source_text:
        if not _present(value):
            add_error(
                "source_metadata_missing",
                f"Required source metadata is missing: {field}.",
                field=f"source.{field}",
            )

    if source.remote_artifact_verified is not True:
        add_error(
            "remote_artifact_not_verified",
            "The frozen local artifact must be verified against the canonical remote artifact.",
            field="source.remote_artifact_verified",
        )
    if not _timezone_aware(source.remote_retrieved_at):
        add_error(
            "remote_retrieval_timestamp_invalid",
            "Remote artifact verification requires a timezone-aware retrieval timestamp.",
            field="source.remote_retrieved_at",
        )

    checksum_pairs = (
        (
            "manifest",
            source.manifest_sha256,
            source.verified_manifest_sha256,
            "source.manifest_sha256",
            "source.verified_manifest_sha256",
        ),
        (
            "artifact",
            source.artifact_sha256,
            source.verified_artifact_sha256,
            "source.artifact_sha256",
            "source.verified_artifact_sha256",
        ),
    )
    for label, declared, verified, declared_field, verified_field in checksum_pairs:
        if not _valid_sha256(declared):
            add_error(
                "source_checksum_invalid",
                f"The declared {label} checksum must be 64 lowercase SHA-256 hex characters.",
                field=declared_field,
            )
        if not _valid_sha256(verified):
            add_error(
                "source_checksum_invalid",
                f"The verified {label} checksum must be 64 lowercase SHA-256 hex characters.",
                field=verified_field,
            )
        if _valid_sha256(declared) and _valid_sha256(verified) and declared != verified:
            add_error(
                "source_checksum_mismatch",
                f"The declared and verified {label} checksums differ.",
                field=declared_field,
            )

    if (
        _present(source.license_key)
        and _present(source.verified_license_key)
        and source.license_key != source.verified_license_key
    ):
        add_error(
            "source_license_mismatch",
            "The declared and verified source license keys differ.",
            field="source.license_key",
        )

    canonical_source_values = (
        (
            "source_snapshot_not_canonical",
            "source.source_snapshot_key",
            source.source_snapshot_key,
            DATA_S1_SOURCE_SNAPSHOT_KEY,
            "The source snapshot is not the approved Milestone 1 Data S1 snapshot.",
        ),
        (
            "source_manifest_not_canonical",
            "source.manifest_sha256",
            source.manifest_sha256,
            _M1_MANIFEST_SHA256,
            "The declared manifest digest is not the approved Milestone 1 manifest.",
        ),
        (
            "source_manifest_not_canonical",
            "source.verified_manifest_sha256",
            source.verified_manifest_sha256,
            _M1_MANIFEST_SHA256,
            "The verified manifest digest is not the approved Milestone 1 manifest.",
        ),
        (
            "source_artifact_not_canonical",
            "source.artifact_key",
            source.artifact_key,
            _M1_ARTIFACT_KEY,
            "The source artifact key is not the approved canonical Data S1 artifact.",
        ),
        (
            "source_artifact_not_canonical",
            "source.artifact_sha256",
            source.artifact_sha256,
            DATA_S1_ARTIFACT_SHA256,
            "The declared source digest is not the approved canonical Data S1 digest.",
        ),
        (
            "source_artifact_not_canonical",
            "source.verified_artifact_sha256",
            source.verified_artifact_sha256,
            DATA_S1_ARTIFACT_SHA256,
            "The verified source digest is not the approved canonical Data S1 digest.",
        ),
        (
            "source_license_not_canonical",
            "source.license_key",
            source.license_key,
            _M1_LICENSE_KEY,
            "The declared license is not the conservative Milestone 1 source license.",
        ),
        (
            "source_license_not_canonical",
            "source.verified_license_key",
            source.verified_license_key,
            _M1_LICENSE_KEY,
            "The verified license is not the conservative Milestone 1 source license.",
        ),
        (
            "source_uri_not_canonical",
            "source.provenance_uri",
            source.provenance_uri,
            _M1_ARTIFACT_URI,
            "The source provenance URI is not the approved canonical Data S1 URI.",
        ),
        (
            "source_uri_not_canonical",
            "source.remote_artifact_uri",
            source.remote_artifact_uri,
            _M1_ARTIFACT_URI,
            "The remote verification URI is not the approved canonical Data S1 URI.",
        ),
    )
    for code, field, observed, expected, message in canonical_source_values:
        if observed != expected:
            add_error(code, message, field=field)

    audited_source_record_count = 0
    source_audit = request.source_audit
    if source_audit is None:
        add_error(
            "source_audit_missing",
            "A frozen, passing whole-ledger source audit is required before publication.",
            field="source_audit",
        )
    else:
        if not _present(source_audit.audit_schema):
            add_error(
                "source_audit_schema_missing",
                "The source audit schema identifier is required.",
                field="source_audit.audit_schema",
            )
        if source_audit.passed is not True:
            add_error(
                "source_audit_not_passed",
                "The complete source ledger audit must pass before publication.",
                field="source_audit.passed",
            )
        for label, declared, verified in (
            (
                "audit artifact",
                source_audit.audit_artifact_sha256,
                source_audit.verified_audit_artifact_sha256,
            ),
            (
                "call-key set",
                source_audit.expected_call_keys_sha256,
                source_audit.observed_call_keys_sha256,
            ),
            (
                "locus-key set",
                source_audit.expected_locus_keys_sha256,
                source_audit.observed_locus_keys_sha256,
            ),
        ):
            if not _valid_sha256(declared) or not _valid_sha256(verified):
                add_error(
                    "source_audit_checksum_invalid",
                    f"The declared and observed {label} digests must be full SHA-256 values.",
                    field="source_audit",
                )
            elif declared != verified:
                add_error(
                    "source_audit_checksum_mismatch",
                    f"The declared and observed {label} digests differ.",
                    field="source_audit",
                )

        expected_records = source_audit.expected_source_record_count
        observed_records = source_audit.observed_source_record_count
        if (
            type(expected_records) is not int
            or expected_records <= 0
            or type(observed_records) is not int
            or observed_records < 0
        ):
            add_error(
                "source_audit_record_count_invalid",
                "Source audit record counts must be integers and the expected "
                "count must be positive.",
                field="source_audit.expected_source_record_count",
            )
        elif expected_records != observed_records:
            add_error(
                "source_audit_record_count_mismatch",
                "The observed source ledger count differs from the frozen expectation.",
                field="source_audit.observed_source_record_count",
            )
        else:
            audited_source_record_count = observed_records

        expected_quarantine = source_audit.expected_accounted_quarantine_count
        if type(expected_quarantine) is not int or expected_quarantine < 0:
            add_error(
                "source_audit_quarantine_count_invalid",
                "The expected accounted quarantine count must be a non-negative integer.",
                field="source_audit.expected_accounted_quarantine_count",
            )
        elif request.accounted_quarantine_count != expected_quarantine:
            add_error(
                "source_audit_quarantine_count_mismatch",
                "The accounted quarantine ledger count differs from the frozen expectation.",
                field="accounted_quarantine_count",
            )

        canonical_audit_values = (
            (
                "source_audit_schema_not_canonical",
                "source_audit.audit_schema",
                source_audit.audit_schema,
                _M1_AUDIT_SCHEMA,
                "The source audit schema is not the approved Milestone 1 audit schema.",
            ),
            (
                "source_audit_record_count_not_canonical",
                "source_audit.expected_source_record_count",
                source_audit.expected_source_record_count,
                _M1_SOURCE_RECORD_COUNT,
                "The expected source-record count is not the approved Milestone 1 count.",
            ),
            (
                "source_audit_record_count_not_canonical",
                "source_audit.observed_source_record_count",
                source_audit.observed_source_record_count,
                _M1_SOURCE_RECORD_COUNT,
                "The observed source-record count is not the approved Milestone 1 count.",
            ),
            (
                "source_audit_quarantine_count_not_canonical",
                "source_audit.expected_accounted_quarantine_count",
                source_audit.expected_accounted_quarantine_count,
                _M1_ACCOUNTED_QUARANTINE_COUNT,
                "The expected quarantine count is not the approved Milestone 1 count.",
            ),
            (
                "source_audit_call_digest_not_canonical",
                "source_audit.expected_call_keys_sha256",
                source_audit.expected_call_keys_sha256,
                _M1_CALL_KEYS_SHA256,
                "The expected call-key digest is not the approved Milestone 1 digest.",
            ),
            (
                "source_audit_call_digest_not_canonical",
                "source_audit.observed_call_keys_sha256",
                source_audit.observed_call_keys_sha256,
                _M1_CALL_KEYS_SHA256,
                "The observed call-key digest is not the approved Milestone 1 digest.",
            ),
            (
                "source_audit_locus_digest_not_canonical",
                "source_audit.expected_locus_keys_sha256",
                source_audit.expected_locus_keys_sha256,
                _M1_LOCUS_KEYS_SHA256,
                "The expected locus-key digest is not the approved Milestone 1 digest.",
            ),
            (
                "source_audit_locus_digest_not_canonical",
                "source_audit.observed_locus_keys_sha256",
                source_audit.observed_locus_keys_sha256,
                _M1_LOCUS_KEYS_SHA256,
                "The observed locus-key digest is not the approved Milestone 1 digest.",
            ),
        )
        for audit_code, audit_field, audit_observed, audit_expected, audit_message in (
            canonical_audit_values
        ):
            if audit_observed != audit_expected:
                add_error(audit_code, audit_message, field=audit_field)

    ncbi_taxonomy = request.ncbi_taxonomy
    if ncbi_taxonomy is None:
        add_error(
            "ncbi_taxonomy_evidence_missing",
            "A complete frozen NCBI Taxonomy snapshot is required before publication.",
            field="ncbi_taxonomy",
        )
    else:
        for field, value in (
            ("snapshot_key", ncbi_taxonomy.snapshot_key),
            ("version", ncbi_taxonomy.version),
            ("artifact_key", ncbi_taxonomy.artifact_key),
            ("provenance_uri", ncbi_taxonomy.provenance_uri),
            ("usage_basis_key", ncbi_taxonomy.usage_basis_key),
        ):
            if not _present(value):
                add_error(
                    "ncbi_taxonomy_metadata_missing",
                    f"Required NCBI Taxonomy evidence is missing: {field}.",
                    field=f"ncbi_taxonomy.{field}",
                )
        if ncbi_taxonomy.authority != _NCBI_TAXONOMY_AUTHORITY:
            add_error(
                "ncbi_taxonomy_authority_invalid",
                "The formal host taxonomy authority must be NCBI Taxonomy.",
                field="ncbi_taxonomy.authority",
            )
        if ncbi_taxonomy.usage_basis_key != _NCBI_USAGE_BASIS:
            add_error(
                "ncbi_taxonomy_usage_basis_invalid",
                "The NCBI molecular-data usage basis must be explicitly frozen.",
                field="ncbi_taxonomy.usage_basis_key",
            )
        if not _valid_sha256(ncbi_taxonomy.artifact_sha256) or not _valid_sha256(
            ncbi_taxonomy.verified_artifact_sha256
        ):
            add_error(
                "ncbi_taxonomy_checksum_invalid",
                "Declared and verified NCBI Taxonomy artifact digests must be full SHA-256 values.",
                field="ncbi_taxonomy.artifact_sha256",
            )
        elif (
            ncbi_taxonomy.artifact_sha256
            != ncbi_taxonomy.verified_artifact_sha256
        ):
            add_error(
                "ncbi_taxonomy_checksum_mismatch",
                "Declared and verified NCBI Taxonomy artifact digests differ.",
                field="ncbi_taxonomy.artifact_sha256",
            )
        if not _timezone_aware(ncbi_taxonomy.retrieved_at):
            add_error(
                "ncbi_taxonomy_retrieval_timestamp_invalid",
                "NCBI Taxonomy evidence requires a timezone-aware retrieval timestamp.",
                field="ncbi_taxonomy.retrieved_at",
            )
        if ncbi_taxonomy.release_bound is not True:
            add_error(
                "ncbi_taxonomy_not_release_bound",
                "The frozen NCBI Taxonomy snapshot must be bound to the proposed release.",
                field="ncbi_taxonomy.release_bound",
            )
        if (
            ncbi_taxonomy.merged_history_included is not True
            or ncbi_taxonomy.deleted_history_included is not True
        ):
            add_error(
                "ncbi_taxonomy_history_incomplete",
                "The NCBI Taxonomy snapshot must include merged and deleted TaxId history.",
                field="ncbi_taxonomy",
            )

    ictv = request.ictv
    if ictv is None:
        add_error(
            "ictv_release_evidence_missing",
            "Frozen ICTV MSL41 and corrected VMR evidence is required before publication.",
            field="ictv",
        )
    else:
        for field, value in (
            ("msl_snapshot_key", ictv.msl_snapshot_key),
            ("msl_artifact_key", ictv.msl_artifact_key),
            ("vmr_artifact_key", ictv.vmr_artifact_key),
            ("provenance_uri", ictv.provenance_uri),
            ("license_key", ictv.license_key),
        ):
            if not _present(value):
                add_error(
                    "ictv_release_metadata_missing",
                    f"Required ICTV release evidence is missing: {field}.",
                    field=f"ictv.{field}",
                )
        if ictv.msl_version != _ICTV_MSL_VERSION:
            add_error(
                "ictv_msl_version_invalid",
                "Formal viral taxonomy must be pinned to ICTV MSL41 v1.",
                field="ictv.msl_version",
            )
        for label, declared, verified in (
            ("MSL", ictv.msl_artifact_sha256, ictv.verified_msl_artifact_sha256),
            ("VMR", ictv.vmr_artifact_sha256, ictv.verified_vmr_artifact_sha256),
        ):
            if not _valid_sha256(declared) or not _valid_sha256(verified):
                add_error(
                    "ictv_release_checksum_invalid",
                    f"Declared and verified ICTV {label} digests must be full SHA-256 values.",
                    field="ictv",
                )
            elif declared != verified:
                add_error(
                    "ictv_release_checksum_mismatch",
                    f"Declared and verified ICTV {label} digests differ.",
                    field="ictv",
                )
        if not _timezone_aware(ictv.retrieved_at):
            add_error(
                "ictv_retrieval_timestamp_invalid",
                "ICTV release evidence requires a timezone-aware retrieval timestamp.",
                field="ictv.retrieved_at",
            )
        if ictv.msl_release_bound is not True or ictv.vmr_release_bound is not True:
            add_error(
                "ictv_artifact_not_release_bound",
                "Both MSL41 and corrected VMR artifacts must be bound to the release.",
                field="ictv",
            )
        if ictv.vmr_corrected is not True:
            add_error(
                "ictv_vmr_not_corrected",
                "The release must use the corrected ICTV VMR MSL41 artifact.",
                field="ictv.vmr_corrected",
            )

    if not request.candidates:
        add_error(
            "release_candidates_empty",
            "A public release must propose at least one fully evidenced locus membership.",
            field="candidates",
        )
    elif audited_source_record_count and len(request.candidates) > audited_source_record_count:
        add_error(
            "release_candidate_count_exceeds_source_ledger",
            "Proposed memberships cannot outnumber audited source records.",
            field="candidates",
        )

    for issue_name, values, field in (
        ("unresolved", request.unresolved_issues, "unresolved_issues"),
        (
            "unresolved_quarantine",
            request.unresolved_quarantine_issues,
            "unresolved_quarantine_issues",
        ),
        ("conflict", request.conflicts, "conflicts"),
    ):
        if values:
            add_error(
                f"release_{issue_name}_issues_present",
                f"Release-level {issue_name} issues must be empty before publication.",
                field=field,
            )

    if (
        type(request.accounted_quarantine_count) is not int
        or request.accounted_quarantine_count < 0
    ):
        add_error(
            "accounted_quarantine_count_invalid",
            "Accounted quarantine count must be a non-negative integer.",
            field="accounted_quarantine_count",
        )
    elif request.accounted_quarantine_count != _M1_ACCOUNTED_QUARANTINE_COUNT:
        add_error(
            "accounted_quarantine_count_not_canonical",
            "The accounted quarantine count is not the approved Milestone 1 count.",
            field="accounted_quarantine_count",
        )
    elif request.accounted_quarantine_count:
        add_warning(
            "accounted_quarantine_rows_retained",
            "Terminal, auditable quarantine rows are counted but do not block publication.",
            field="accounted_quarantine_count",
        )

    first_index_by_locus_key: dict[str, int] = {}

    for index, candidate in enumerate(request.candidates):
        locus_key = candidate.locus_key or None

        if not _present(candidate.locus_key):
            add_error(
                "locus_key_missing",
                "A proposed membership must have a deterministic locus key.",
                field="locus_key",
                candidate_index=index,
                locus_key=locus_key,
            )
        else:
            previous_index = first_index_by_locus_key.get(candidate.locus_key)
            if previous_index is not None:
                add_error(
                    "duplicate_locus_key",
                    "A release cannot contain the same locus key more than once.",
                    field="locus_key",
                    candidate_index=previous_index,
                    locus_key=candidate.locus_key,
                )
                add_error(
                    "duplicate_locus_key",
                    "A release cannot contain the same locus key more than once.",
                    field="locus_key",
                    candidate_index=index,
                    locus_key=candidate.locus_key,
                )
            else:
                first_index_by_locus_key[candidate.locus_key] = index

        try:
            expected_locus_key = candidate.identity.key()
        except StableKeyError as exc:
            add_error(
                "locus_identity_invalid",
                f"The locus identity preimage is invalid: {exc}",
                field="identity",
                candidate_index=index,
                locus_key=locus_key,
            )
        else:
            if candidate.locus_key != expected_locus_key:
                add_error(
                    "locus_key_mismatch",
                    "The locus key does not match the canonical identity preimage.",
                    field="locus_key",
                    candidate_index=index,
                    locus_key=locus_key,
                )

        if candidate.assembly_resolution != "exact":
            add_error(
                "assembly_resolution_not_exact",
                "Assembly resolution must be exact for public membership.",
                field="assembly_resolution",
                candidate_index=index,
                locus_key=locus_key,
            )
        if not is_versioned_assembly_accession(candidate.assembly_accession_version):
            add_error(
                "assembly_accession_not_versioned",
                "Assembly identity must be an exact GCA_/GCF_ accession.version.",
                field="assembly_accession_version",
                candidate_index=index,
                locus_key=locus_key,
            )
        if candidate.assembly_accession_version != candidate.identity.assembly_accession_version:
            add_error(
                "assembly_identity_mismatch",
                "Resolved assembly identity differs from the stable-key preimage.",
                field="assembly_accession_version",
                candidate_index=index,
                locus_key=locus_key,
            )

        if candidate.contig_resolution != "exact":
            add_error(
                "contig_resolution_not_exact",
                "Contig resolution must be exact for public membership.",
                field="contig_resolution",
                candidate_index=index,
                locus_key=locus_key,
            )
        if not is_versioned_contig_accession(candidate.contig_accession_version):
            add_error(
                "contig_accession_not_versioned",
                "Contig identity must be an exact INSDC accession.version.",
                field="contig_accession_version",
                candidate_index=index,
                locus_key=locus_key,
            )
        if candidate.contig_accession_version != candidate.identity.contig_accession_version:
            add_error(
                "contig_identity_mismatch",
                "Resolved contig identity differs from the stable-key preimage.",
                field="contig_accession_version",
                candidate_index=index,
                locus_key=locus_key,
            )
        if source.source_snapshot_key != candidate.identity.source_snapshot_key:
            add_error(
                "source_snapshot_identity_mismatch",
                "Source snapshot differs from the stable-key preimage.",
                field="identity.source_snapshot_key",
                candidate_index=index,
                locus_key=locus_key,
            )

        if type(candidate.contig_length) is not int or candidate.contig_length <= 0:
            add_error(
                "contig_length_invalid",
                "A positive exact contig length is required to validate an interval.",
                field="contig_length",
                candidate_index=index,
                locus_key=locus_key,
            )

        for field, value in (
            ("source_record_key", candidate.source_record_key),
            ("method_key", candidate.method_key),
            ("import_run_key", candidate.import_run_key),
        ):
            if not _present(value):
                add_error(
                    "candidate_provenance_missing",
                    f"Required candidate provenance is missing: {field}.",
                    field=field,
                    candidate_index=index,
                    locus_key=locus_key,
                )

        if candidate.source_assessment not in _SOURCE_ASSESSMENTS:
            add_error(
                "source_assessment_invalid",
                "Source assessment must be exactly source_high or source_low.",
                field="source_assessment",
                candidate_index=index,
                locus_key=locus_key,
            )
        else:
            add_warning(
                "source_assessment_non_authoritative",
                "Source assessment is provenance only and never authorizes membership.",
                field="source_assessment",
                candidate_index=index,
                locus_key=locus_key,
            )

        if len(candidate.placements) != 1:
            add_error(
                "placement_count_not_one",
                "Public membership requires exactly one normalized placement.",
                field="placements",
                candidate_index=index,
                locus_key=locus_key,
            )

        for placement_index, placement in enumerate(candidate.placements):
            field_prefix = f"placements[{placement_index}]"
            if placement.precision != "exact":
                add_error(
                    "placement_not_exact",
                    "Public membership requires an exact placement.",
                    field=f"{field_prefix}.precision",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if placement.coordinate_system != _CANONICAL_COORDINATE_SYSTEM:
                add_error(
                    "placement_coordinate_system_invalid",
                    "Placement coordinates must use 0-based-half-open convention.",
                    field=f"{field_prefix}.coordinate_system",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if placement.contig_accession_version != candidate.contig_accession_version:
                add_error(
                    "placement_contig_mismatch",
                    "Placement contig differs from the resolved candidate contig.",
                    field=f"{field_prefix}.contig_accession_version",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if not _present(placement.provenance_key):
                add_error(
                    "placement_provenance_missing",
                    "Placement provenance is required.",
                    field=f"{field_prefix}.provenance_key",
                    candidate_index=index,
                    locus_key=locus_key,
                )

            interval_is_integer = type(placement.start0) is int and type(placement.end0) is int
            if not interval_is_integer:
                add_error(
                    "placement_interval_invalid",
                    "Placement start0 and end0 must be integers.",
                    field=field_prefix,
                    candidate_index=index,
                    locus_key=locus_key,
                )
            else:
                assert placement.start0 is not None
                assert placement.end0 is not None
                if placement.start0 < 0 or placement.start0 >= placement.end0:
                    add_error(
                        "placement_interval_invalid",
                        "Placement must satisfy 0 <= start0 < end0.",
                        field=field_prefix,
                        candidate_index=index,
                        locus_key=locus_key,
                    )
                elif (
                    type(candidate.contig_length) is int
                    and placement.end0 > candidate.contig_length
                ):
                    add_error(
                        "placement_interval_out_of_bounds",
                        "Placement end0 exceeds the resolved contig length.",
                        field=field_prefix,
                        candidate_index=index,
                        locus_key=locus_key,
                    )

        assessments_by_side: dict[str, list[FlankEvidence]] = {"left": [], "right": []}
        for flank_index, assessment in enumerate(candidate.flank_assessments):
            if assessment.side not in assessments_by_side:
                add_error(
                    "flank_side_invalid",
                    "Flank side must be exactly left or right.",
                    field=f"flank_assessments[{flank_index}].side",
                    candidate_index=index,
                    locus_key=locus_key,
                )
                continue
            assessments_by_side[assessment.side].append(assessment)

        passing_flanks: dict[str, FlankEvidence] = {}
        for side in ("left", "right"):
            assessments = assessments_by_side[side]
            if len(assessments) != 1:
                add_error(
                    "flank_assessment_count_not_one",
                    f"Public membership requires exactly one {side} flank assessment.",
                    field="flank_assessments",
                    candidate_index=index,
                    locus_key=locus_key,
                )
                continue
            assessment = assessments[0]
            if assessment.verdict != _PASSING_FLANK_VERDICT:
                add_error(
                    "flank_not_supported",
                    f"The {side} flank must have verdict supported.",
                    field=f"flank_assessments.{side}.verdict",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            else:
                passing_flanks[side] = assessment
            if not _present(assessment.policy_key):
                add_error(
                    "flank_policy_missing",
                    f"The {side} flank requires a versioned assessment policy.",
                    field=f"flank_assessments.{side}.policy_key",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if not _present(assessment.evidence_key):
                add_error(
                    "flank_evidence_missing",
                    f"The {side} flank requires evidence provenance.",
                    field=f"flank_assessments.{side}.evidence_key",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if assessment.inspection_window_bp != _PILOT_FLANK_WINDOW_BP:
                add_error(
                    "flank_window_invalid",
                    f"The {side} flank must use the approved 20,000 bp inspection window.",
                    field=f"flank_assessments.{side}.inspection_window_bp",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            flank_lengths_are_valid = (
                type(assessment.available_bp) is int
                and assessment.available_bp >= 0
                and type(assessment.inspected_bp) is int
                and 0 <= assessment.inspected_bp <= assessment.available_bp
            )
            if not flank_lengths_are_valid:
                add_error(
                    "flank_length_invalid",
                    f"The {side} flank requires 0 <= inspected_bp <= available_bp.",
                    field=f"flank_assessments.{side}",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            elif assessment.verdict == _PASSING_FLANK_VERDICT and assessment.inspected_bp == 0:
                add_error(
                    "supported_flank_not_inspected",
                    f"The supported {side} flank must include inspected sequence.",
                    field=f"flank_assessments.{side}.inspected_bp",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if not _present(assessment.method_or_curator_key):
                add_error(
                    "flank_method_missing",
                    f"The {side} flank requires a versioned method or curator key.",
                    field=f"flank_assessments.{side}.method_or_curator_key",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if not _valid_sha256(assessment.evidence_sha256):
                add_error(
                    "flank_evidence_checksum_invalid",
                    f"The {side} flank evidence requires a full SHA-256 digest.",
                    field=f"flank_assessments.{side}.evidence_sha256",
                    candidate_index=index,
                    locus_key=locus_key,
                )

        if set(passing_flanks) == {"left", "right"}:
            left_policy = passing_flanks["left"].policy_key
            right_policy = passing_flanks["right"].policy_key
            if _present(left_policy) and _present(right_policy) and left_policy != right_policy:
                add_error(
                    "flank_policy_mismatch",
                    "Left and right flank verdicts must use the same approved policy.",
                    field="flank_assessments",
                    candidate_index=index,
                    locus_key=locus_key,
                )

        if candidate.inclusion is None:
            add_error(
                "inclusion_decision_missing",
                "Source assessments never replace an explicit inclusion decision.",
                field="inclusion",
                candidate_index=index,
                locus_key=locus_key,
            )
        else:
            if candidate.inclusion.decision != "include":
                add_error(
                    "inclusion_decision_not_include",
                    "Only an explicit include decision authorizes public membership.",
                    field="inclusion.decision",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if not _present(candidate.inclusion.policy_key):
                add_error(
                    "inclusion_policy_missing",
                    "An include decision requires a versioned policy key.",
                    field="inclusion.policy_key",
                    candidate_index=index,
                    locus_key=locus_key,
                )
            if not _present(candidate.inclusion.authorized_by):
                add_error(
                    "inclusion_authority_missing",
                    "An include decision requires an explicit authorizer.",
                    field="inclusion.authorized_by",
                    candidate_index=index,
                    locus_key=locus_key,
                )

        for issue_name, values in (
            ("unresolved", candidate.unresolved_issues),
            ("quarantine", candidate.quarantine_issues),
            ("conflict", candidate.conflicts),
        ):
            if values:
                add_error(
                    f"candidate_{issue_name}_issues_present",
                    f"Candidate {issue_name} issues must be empty before membership.",
                    field=f"{issue_name}_issues",
                    candidate_index=index,
                    locus_key=locus_key,
                )

    if release_blocked:
        blocked.update(range(len(request.candidates)))

    candidate_count = len(request.candidates)
    eligible_count = candidate_count - len(blocked)
    explicit_include_count = sum(
        candidate.inclusion is not None and candidate.inclusion.decision == "include"
        for candidate in request.candidates
    )
    source_high_count = sum(
        candidate.source_assessment == "source_high" for candidate in request.candidates
    )
    source_low_count = sum(
        candidate.source_assessment == "source_low" for candidate in request.candidates
    )
    counts = ValidationCounts(
        candidate_count=candidate_count,
        eligible_membership_count=eligible_count,
        blocked_membership_count=len(blocked),
        explicit_include_count=explicit_include_count,
        source_high_count=source_high_count,
        source_low_count=source_low_count,
        audited_source_record_count=audited_source_record_count,
        accounted_quarantine_count=request.accounted_quarantine_count,
        error_count=len(errors),
        warning_count=len(warnings),
    )
    return ReleaseValidationReport(
        release_key=request.release_key,
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        counts=counts,
    )
