"""Typed loading and local verification for the approved source manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eve_relation_rag.domain.keys import is_versioned_assembly_accession

Sha256 = str


class ContractModel(BaseModel):
    """Immutable base for versioned manifest fragments."""

    model_config = ConfigDict(extra="allow", frozen=True)


class ArtifactSpec(ContractModel):
    """Frozen identity and local observations for Data S1."""

    source_label: str
    native_filename: str
    accepted_local_filename: str
    media_url: str | None
    byte_size: int = Field(gt=0)
    sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    license_key: str = Field(min_length=1)
    license_basis: str = Field(min_length=1)
    worksheet: str
    used_range: str
    populated_columns: str
    remote_checksum_verified: bool
    retrieved_at: str | None
    http_metadata: dict[str, Any] | None


class SelectionSpec(ContractModel):
    """Exact amended all-VR selection boundary."""

    assembly_allowlist: tuple[str, ...]
    viral_major_taxon: Literal["Orthopolintovirales"]
    host_class: Literal["Bivalvia"]
    include_all_vr_values: Literal[True]

    @model_validator(mode="after")
    def validate_allowlist(self) -> SelectionSpec:
        if len(self.assembly_allowlist) != 10 or len(set(self.assembly_allowlist)) != 10:
            raise ValueError("assembly_allowlist must contain ten unique accessions")
        if not all(is_versioned_assembly_accession(value) for value in self.assembly_allowlist):
            raise ValueError("assembly_allowlist values must be exact accession.version")
        return self


class SourceConfidencePolicy(ContractModel):
    """Source-relative HCVR confidence mapping."""

    scheme: Literal["zhao-biorxiv-v4-hcvr-status-v1"]
    source_high_when: Literal["HCVR == Yes"]
    source_low_when: Literal["otherwise"]
    creates_release_membership: Literal[False]


class CoordinatePolicy(ContractModel):
    """Approved coordinate normalization and identity boundary."""

    canonical_system: Literal["0-based-half-open"]
    validation: str
    coordinate_in_locus_identity: Literal[False]


class IdentityPolicy(ContractModel):
    """Approved coordinate-free source-occurrence identity policy."""

    key: Literal["zhao-v4-contig-source-occurrence-v1"]
    preimage_fields: tuple[str, ...]

    @model_validator(mode="after")
    def validate_preimage(self) -> IdentityPolicy:
        required = {
            "source_snapshot_key",
            "assembly_accession_version",
            "sequence_accession_version",
            "native_vr_token",
            "identity_policy_key",
        }
        if set(self.preimage_fields) != required:
            raise ValueError("identity preimage fields differ from the approved D02 contract")
        return self


class CallIdentityPolicy(ContractModel):
    """Approved D08 method-qualified detection-call identity."""

    key_schema: Literal["zhao-data-s1-detection-call-v2"]
    method_run_identity: Literal["zhao-data-s1-import-v2"]
    preimage_fields: tuple[str, ...]

    @model_validator(mode="after")
    def validate_preimage(self) -> CallIdentityPolicy:
        required = {
            "artifact_sha256",
            "source_snapshot_key",
            "worksheet",
            "assembly_accession_version",
            "sequence_accession_version",
            "native_vr_token",
            "method_run_identity",
            "key_schema",
        }
        if set(self.preimage_fields) != required:
            raise ValueError("call-key preimage fields differ from approved D08")
        return self


class SourceRecordIdentityPolicy(ContractModel):
    """Physical source-row identity kept independent of the importing method."""

    key_schema: Literal["zhao-data-s1-source-record-v1"]
    preimage_fields: tuple[str, ...]

    @model_validator(mode="after")
    def validate_preimage(self) -> SourceRecordIdentityPolicy:
        required = {
            "artifact_sha256",
            "source_snapshot_key",
            "worksheet",
            "excel_row",
            "key_schema",
        }
        if set(self.preimage_fields) != required:
            raise ValueError("source-record preimage fields differ from approved D08")
        return self


class ExpectedCounts(ContractModel):
    """Reproducibility counters for the frozen staging import."""

    source_records: int = Field(ge=0)
    source_high: int = Field(ge=0)
    source_low: int = Field(ge=0)
    assemblies: int = Field(ge=0)
    source_organism_names: int = Field(ge=0)
    contigs: int = Field(ge=0)
    unique_source_occurrence_keys: int = Field(ge=0)
    vr_type_integration: int = Field(ge=0)
    vr_type_viral_contig: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_totals(self) -> ExpectedCounts:
        if self.source_high + self.source_low != self.source_records:
            raise ValueError("source_high + source_low must equal source_records")
        if self.unique_source_occurrence_keys != self.source_records:
            raise ValueError("every source record must have one unique occurrence key")
        if self.vr_type_integration + self.vr_type_viral_contig != self.source_records:
            raise ValueError("VR type counts must equal source_records")
        return self


class ResolutionResult(ContractModel):
    """Observed exact NCBI resolution totals."""

    assemblies_resolved_exact: int = Field(ge=0)
    assembly_status_current: int = Field(ge=0)
    assembly_status_previous: int = Field(ge=0)
    assembly_status_previous_accession: str | None
    selected_contigs_resolved_exact: int = Field(ge=0)
    selected_contig_length_mismatches: int = Field(ge=0)


class ResolutionReportSpec(ContractModel):
    """Frozen byte identity and record count for one NCBI JSONL report."""

    local_audit_path: str = Field(min_length=1)
    sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0)
    media_type: str = Field(min_length=1)
    records: int = Field(gt=0)


class UsageBasisSpec(ContractModel):
    """Versioned license or usage-policy basis for an external authority."""

    key: str = Field(min_length=1)
    url: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class AssemblyResolution(ContractModel):
    """Frozen NCBI Datasets tool and response provenance."""

    authority: Literal["NCBI Datasets v2"]
    source_snapshot_key: str = Field(min_length=1)
    datasets_cli_version: str = Field(min_length=1)
    datasets_cli_binary_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    dataformat_used: bool
    retrieved_at: str = Field(min_length=1)
    commands: tuple[str, ...] = Field(min_length=1)
    license_or_usage_basis: UsageBasisSpec
    assembly_report: ResolutionReportSpec
    sequence_report: ResolutionReportSpec
    resolution_result: ResolutionResult

    @model_validator(mode="after")
    def validate_report_counts(self) -> AssemblyResolution:
        if self.assembly_report.records < self.resolution_result.assemblies_resolved_exact:
            raise ValueError("assembly report records cannot be fewer than resolved assemblies")
        if self.sequence_report.records < self.resolution_result.selected_contigs_resolved_exact:
            raise ValueError("sequence report records cannot be fewer than resolved contigs")
        if any(not command or command != command.strip() for command in self.commands):
            raise ValueError("assembly resolution commands must be non-empty exact strings")
        return self


class Milestone1SourceManifest(ContractModel):
    """Typed subset of the complete approved Milestone 1 manifest."""

    manifest_schema: Literal["endoviho-source-manifest-v1"]
    manifest_status: str
    source_snapshot_key: str
    artifact: ArtifactSpec
    selection: SelectionSpec
    source_confidence_policy: SourceConfidencePolicy
    coordinate_policy: CoordinatePolicy
    identity_policy: IdentityPolicy
    call_identity_policy: CallIdentityPolicy
    source_record_identity_policy: SourceRecordIdentityPolicy
    assembly_resolution: AssemblyResolution
    expected_counts: ExpectedCounts

    @model_validator(mode="after")
    def validate_resolution_counts(self) -> Milestone1SourceManifest:
        result = self.assembly_resolution.resolution_result
        if result.assemblies_resolved_exact != self.expected_counts.assemblies:
            raise ValueError("resolved assembly count differs from expected_counts")
        if result.selected_contigs_resolved_exact != self.expected_counts.contigs:
            raise ValueError("resolved contig count differs from expected_counts")
        return self


class ArtifactVerification(BaseModel):
    """Read-only checksum/size comparison for one local source file."""

    model_config = ConfigDict(frozen=True)

    path: Path
    expected_byte_size: int
    actual_byte_size: int
    expected_sha256: Sha256
    actual_sha256: Sha256
    valid: bool
    errors: tuple[str, ...]


def load_source_manifest(path: str | Path) -> Milestone1SourceManifest:
    """Load and validate one JSON source manifest without mutating it."""

    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return Milestone1SourceManifest.model_validate(payload)


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_local_artifact(
    artifact: ArtifactSpec, path: str | Path
) -> ArtifactVerification:
    """Recompute file size and SHA-256; remote verification remains a separate gate."""

    artifact_path = Path(path)
    actual_size = artifact_path.stat().st_size
    actual_sha256 = _sha256_file(artifact_path)
    errors: list[str] = []
    if actual_size != artifact.byte_size:
        errors.append("artifact_byte_size_mismatch")
    if actual_sha256 != artifact.sha256:
        errors.append("artifact_sha256_mismatch")
    return ArtifactVerification(
        path=artifact_path,
        expected_byte_size=artifact.byte_size,
        actual_byte_size=actual_size,
        expected_sha256=artifact.sha256,
        actual_sha256=actual_sha256,
        valid=not errors,
        errors=tuple(errors),
    )
