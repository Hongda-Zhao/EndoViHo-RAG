"""Atomic PostgreSQL staging persistence for the frozen Data S1 import.

The service deliberately stops at the auditable candidate layer.  It records
source calls, source-qualified assertions, and terminal import outcomes, but it
never creates flank assessments, inclusion decisions, or public memberships.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.orm import Session

from eve_relation_rag.db.base import Base
from eve_relation_rag.db.models import (
    AssemblySequence,
    AssemblyTaxonAssignment,
    AssertionEvidence,
    Dataset,
    DatasetRelease,
    DetectionCall,
    EVELocus,
    EVELocusPlacement,
    EvidenceItem,
    FlankAssessment,
    GenomeAssembly,
    ImportLedger,
    ImportRun,
    InclusionDecision,
    LineageClosure,
    LineageSnapshot,
    LineageTerm,
    MethodDefinition,
    ProcessRun,
    QuarantineIssue,
    ReleaseAssemblyMembership,
    ReleaseAssertionMembership,
    ReleaseLineageSnapshot,
    ReleaseLocusMembership,
    ReleaseMethodDefinition,
    ReleaseSourceSnapshot,
    ScientificAssertion,
    SourceArtifact,
    SourceAssessment,
    SourceRecord,
    SourceSnapshot,
)
from eve_relation_rag.domain.keys import (
    StableKeyError,
    canonical_json_sha256,
    is_release_key,
    locus_key,
    stable_key,
)
from eve_relation_rag.importers.data_s1 import (
    DATA_S1_ARTIFACT_SHA256,
    DATA_S1_ASSEMBLY_ALLOWLIST,
    DATA_S1_COORDINATE_SYSTEM,
    DATA_S1_IDENTITY_POLICY_KEY,
    DATA_S1_METHOD_RUN_IDENTITY,
    DATA_S1_SOURCE_ASSESSMENT_SCHEME,
    DATA_S1_SOURCE_COLUMNS,
    DATA_S1_SOURCE_SNAPSHOT_KEY,
    DataS1ImportOutcome,
    ImportedDataS1Record,
    NcbiResolutionIndex,
    QuarantinedDataS1Record,
    data_s1_record_key,
    data_s1_source_record_key,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VIRAL_MAJOR_TAXON = "Orthopolintovirales"
_HOST_CLASS = "Bivalvia"
_NORMALIZED = "normalized_candidate"
_QUARANTINE = "quarantine"
_METHOD_ROLE = "data_s1_source_import"
_SOURCE_ROLE = "data_s1_input"
_RESOLUTION_SOURCE_ROLE = "ncbi_resolution_package"
_STUDY_LINEAGE_ROLE = "study_viral_lineage"
_CANONICAL_DATA_S1_BYTE_SIZE = 83_851_778
_EXPANDING_QUERY_BATCH_SIZE = 10_000


class StagingPersistenceError(RuntimeError):
    """Base class for fail-closed staging errors with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StagingInputError(StagingPersistenceError, ValueError):
    """Raised before writes when supplied provenance or DTOs are inconsistent."""


class StagingConflictError(StagingPersistenceError):
    """Raised when an existing immutable row disagrees with the requested row."""


@dataclass(frozen=True, slots=True)
class DatasetReleaseSpec:
    """Identity and manifest pin for the candidate release."""

    dataset_key: str
    dataset_title: str
    release_key: str
    schema_version: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class SourceSnapshotSpec:
    """Frozen source-snapshot provenance required by staging."""

    snapshot_key: str
    source_name: str
    source_version: str
    source_uri: str
    retrieved_at: datetime
    declared_manifest_sha256: str
    verified_manifest_sha256: str
    declared_license_key: str
    verified_license_key: str


@dataclass(frozen=True, slots=True)
class SourceArtifactSpec:
    """Immutable artifact metadata, including optional remote verification."""

    artifact_key: str
    filename: str
    media_type: str
    byte_size: int
    declared_sha256: str
    verified_sha256: str
    source_uri: str
    retrieved_at: datetime
    declared_license_key: str
    verified_license_key: str
    remote_checksum_verified: bool
    remote_verification_at: datetime | None = None
    remote_verification_uri: str | None = None


@dataclass(frozen=True, slots=True)
class AssemblySpec:
    """One exact allow-listed assembly and its authority organism label."""

    accession_version: str
    source_organism_name: str
    source_tax_id: int


@dataclass(frozen=True, slots=True)
class ImportExecutionSpec:
    """Versioned importer execution metadata used to derive process provenance."""

    run_key: str
    importer_name: str
    importer_version: str
    code_sha256: str
    software_agent_key: str
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class StagingExpectation:
    """Frozen full-run summary and identity digests required before writes."""

    source_records: int
    source_high: int
    source_low: int
    normalized_candidates: int
    quarantined_rows: int
    loci: int
    placements: int
    quarantine_issues: int
    call_key_set_sha256: str
    locus_key_set_sha256: str


@dataclass(frozen=True, slots=True)
class DataS1StagingRequest:
    """All frozen dependencies needed to persist one Data S1 staging run."""

    release: DatasetReleaseSpec
    source_snapshot: SourceSnapshotSpec
    data_artifact: SourceArtifactSpec
    resolution_snapshot: SourceSnapshotSpec
    assembly_report_artifact: SourceArtifactSpec
    sequence_report_artifact: SourceArtifactSpec
    assemblies: tuple[AssemblySpec, ...]
    resolution_index: NcbiResolutionIndex
    execution: ImportExecutionSpec
    expectation: StagingExpectation
    worksheet: str = "S3"


@dataclass(frozen=True, slots=True)
class StagingPersistenceResult:
    """Machine-readable persistence outcome."""

    run_key: str
    release_key: str
    replayed: bool
    input_rows: int
    normalized_candidates: int
    quarantined_rows: int
    accounted_policy_quarantines: int
    open_quarantine_issues: int
    created_counts: Mapping[str, int]
    reused_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _PreparedIssue:
    code: str
    field: str
    message: str
    raw_value: str
    severity: str
    status: str


@dataclass(frozen=True, slots=True)
class _PreparedRow:
    outcome: DataS1ImportOutcome
    raw_row: dict[str, str]
    source_label: str
    viral_major_taxon: str
    vr_type: str
    terminal_outcome: str
    issues: tuple[_PreparedIssue, ...]
    persist_locus: bool
    persist_placement: bool
    authority_length: int | None


def deterministic_key_set_sha256(keys: Iterable[str]) -> str:
    """Hash a key set as canonical JSON after exact lexical sorting.

    Duplicate keys are rejected so a multiset can never masquerade as the
    expected set.  This is the digest algorithm used by ``StagingExpectation``.
    """

    values = list(keys)
    if len(values) != len(set(values)):
        raise StagingInputError("duplicate_digest_key", "key-set digests require unique exact keys")
    return canonical_json_sha256(sorted(values))


def persist_data_s1_staging(
    session: Session,
    request: DataS1StagingRequest,
    outcomes: Iterable[DataS1ImportOutcome],
    *,
    batch_size: int = 1_000,
) -> StagingPersistenceResult:
    """Persist a complete candidate staging run in one PostgreSQL transaction.

    Input provenance and every NCBI resolution status are validated before the
    transaction starts.  A successful replay of the same ``run_key`` compares
    the complete record/outcome ledger and performs no inserts.
    """

    if batch_size <= 0:
        raise StagingInputError("invalid_batch_size", "batch_size must be positive")
    if session.in_transaction():
        raise StagingInputError(
            "active_transaction",
            "persist_data_s1_staging requires a Session without an active transaction",
        )

    prepared = _prepare_request(request, outcomes)
    parameters = _run_parameters(request)
    parameters_sha256 = canonical_json_sha256(parameters)
    created: Counter[str] = Counter()
    reused: Counter[str] = Counter()

    with session.begin():
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"data-s1-staging:{request.execution.run_key}"},
        )

        existing_run = session.scalar(
            select(ImportRun).where(ImportRun.run_key == request.execution.run_key)
        )
        if existing_run is not None:
            _validate_replayed_run(
                session,
                existing_run,
                request,
                parameters_sha256,
                prepared,
            )
            reused["import_run"] = 1
            reused["source_record"] = len(prepared)
            reused["import_ledger"] = len(prepared)
            return _result(request, prepared, True, created, reused)

        dataset, _ = _get_or_create(
            session,
            Dataset,
            Dataset.dataset_key == request.release.dataset_key,
            {
                "dataset_key": request.release.dataset_key,
                "title": request.release.dataset_title,
            },
            ("dataset_key", "title"),
            "dataset",
            created,
            reused,
        )
        release, _ = _get_or_create(
            session,
            DatasetRelease,
            DatasetRelease.release_key == request.release.release_key,
            {
                "dataset_id": dataset.id,
                "release_key": request.release.release_key,
                "schema_version": request.release.schema_version,
                "status": "candidate",
                "manifest_sha256": request.release.manifest_sha256,
                "published_at": None,
                "supersedes_release_id": None,
            },
            (
                "dataset_id",
                "release_key",
                "schema_version",
                "status",
                "manifest_sha256",
                "published_at",
                "supersedes_release_id",
            ),
            "dataset_release",
            created,
            reused,
        )
        _require_empty_release(session, release.id)

        snapshot = _persist_source_snapshot(session, request.source_snapshot, created, reused)
        resolution_snapshot = _persist_source_snapshot(
            session, request.resolution_snapshot, created, reused
        )
        artifacts: dict[str, SourceArtifact] = {}
        artifact_bindings = (
            (request.data_artifact, snapshot.id),
            (request.assembly_report_artifact, resolution_snapshot.id),
            (request.sequence_report_artifact, resolution_snapshot.id),
        )
        for artifact_spec, artifact_snapshot_id in artifact_bindings:
            artifact, _ = _get_or_create(
                session,
                SourceArtifact,
                SourceArtifact.artifact_key == artifact_spec.artifact_key,
                _artifact_values(artifact_snapshot_id, artifact_spec),
                (
                    "snapshot_id",
                    "artifact_key",
                    "filename",
                    "media_type",
                    "byte_size",
                    "declared_sha256",
                    "verified_sha256",
                    "source_uri",
                    "retrieved_at",
                    "declared_license_key",
                    "verified_license_key",
                    "remote_checksum_verified",
                    "remote_verification_at",
                    "remote_verification_uri",
                ),
                "source_artifact",
                created,
                reused,
            )
            artifacts[artifact_spec.artifact_key] = artifact

        data_artifact = artifacts[request.data_artifact.artifact_key]
        assembly_artifact = artifacts[request.assembly_report_artifact.artifact_key]
        sequence_artifact = artifacts[request.sequence_report_artifact.artifact_key]
        _get_or_create(
            session,
            ReleaseSourceSnapshot,
            (ReleaseSourceSnapshot.release_id == release.id)
            & (ReleaseSourceSnapshot.source_snapshot_id == snapshot.id),
            {
                "release_id": release.id,
                "source_snapshot_id": snapshot.id,
                "role": _SOURCE_ROLE,
            },
            ("release_id", "source_snapshot_id", "role"),
            "release_source_snapshot",
            created,
            reused,
        )
        _get_or_create(
            session,
            ReleaseSourceSnapshot,
            (ReleaseSourceSnapshot.release_id == release.id)
            & (ReleaseSourceSnapshot.source_snapshot_id == resolution_snapshot.id),
            {
                "release_id": release.id,
                "source_snapshot_id": resolution_snapshot.id,
                "role": _RESOLUTION_SOURCE_ROLE,
            },
            ("release_id", "source_snapshot_id", "role"),
            "release_source_snapshot",
            created,
            reused,
        )

        method, process_run, import_run = _persist_run_provenance(
            session,
            request,
            release,
            snapshot,
            data_artifact,
            parameters,
            parameters_sha256,
            created,
            reused,
        )
        lineage_snapshot, lineage_term = _persist_study_lineage(
            session,
            request,
            release,
            data_artifact,
            created,
            reused,
        )
        assemblies = _persist_assemblies(
            session,
            request,
            release,
            assembly_artifact,
            created,
            reused,
        )
        _persist_assembly_source_taxonomy(
            session,
            request,
            release,
            assembly_artifact,
            assemblies,
            created,
            reused,
        )
        sequences = _persist_sequences(
            session,
            prepared,
            assemblies,
            sequence_artifact,
            created,
            reused,
        )
        source_records = _load_source_records(session, snapshot.id, data_artifact.id)

        for offset in range(0, len(prepared), batch_size):
            chunk = prepared[offset : offset + batch_size]
            _persist_row_chunk(
                session=session,
                request=request,
                rows=chunk,
                release=release,
                snapshot=snapshot,
                data_artifact=data_artifact,
                import_run=import_run,
                process_run=process_run,
                method=method,
                lineage_snapshot=lineage_snapshot,
                lineage_term=lineage_term,
                assemblies=assemblies,
                sequences=sequences,
                source_records=source_records,
                created=created,
                reused=reused,
            )

    return _result(request, prepared, False, created, reused)


def _prepare_request(
    request: DataS1StagingRequest,
    outcomes: Iterable[DataS1ImportOutcome],
) -> tuple[_PreparedRow, ...]:
    _validate_request_metadata(request)
    prepared: list[_PreparedRow] = []
    record_keys: set[str] = set()
    source_record_keys: set[str] = set()
    row_locators: set[tuple[str, int]] = set()
    occurrences: set[tuple[str, str, str]] = set()
    persisted_locus_keys: set[str] = set()

    for outcome in outcomes:
        row = _prepare_row(request, outcome)
        record_key = outcome.record_key
        locator = (outcome.locator.worksheet, outcome.locator.excel_row)
        occurrence = (
            outcome.assembly_accession_version,
            outcome.sequence_accession_version,
            outcome.native_vr_token,
        )
        if record_key in record_keys:
            raise StagingInputError(
                "duplicate_record_key", f"duplicate source record key: {record_key}"
            )
        if outcome.source_record_key in source_record_keys:
            raise StagingInputError(
                "duplicate_source_record_key",
                f"duplicate physical source-record key: {outcome.source_record_key}",
            )
        if locator in row_locators:
            raise StagingInputError(
                "duplicate_source_locator",
                f"duplicate source row locator: {locator[0]}!{locator[1]}",
            )
        if occurrence in occurrences:
            raise StagingInputError(
                "duplicate_source_occurrence",
                "duplicate source occurrence for " + "/".join(occurrence),
            )
        if row.persist_locus:
            assert outcome.locus_key is not None
            if outcome.locus_key in persisted_locus_keys:
                raise StagingInputError(
                    "duplicate_locus_key", f"duplicate locus key: {outcome.locus_key}"
                )
            persisted_locus_keys.add(outcome.locus_key)
        record_keys.add(record_key)
        source_record_keys.add(outcome.source_record_key)
        row_locators.add(locator)
        occurrences.add(occurrence)
        prepared.append(row)

    result = tuple(prepared)
    _validate_prepared_expectation(request.expectation, result)
    return result


def _validate_request_metadata(request: DataS1StagingRequest) -> None:
    _require_token("dataset_key", request.release.dataset_key)
    _require_token("dataset_title", request.release.dataset_title)
    _require_token("release_key", request.release.release_key)
    if not is_release_key(request.release.release_key):
        raise StagingInputError(
            "invalid_release_key",
            "candidate release key does not follow the approved immutable grammar",
        )
    _require_token("schema_version", request.release.schema_version)
    _require_sha256("release manifest", request.release.manifest_sha256)

    _validate_snapshot(request.source_snapshot, "Data S1")
    _validate_snapshot(request.resolution_snapshot, "NCBI resolution")
    if request.source_snapshot.snapshot_key == request.resolution_snapshot.snapshot_key:
        raise StagingInputError(
            "source_snapshot_role_collision",
            "Data S1 and NCBI resolution artifacts require distinct source snapshots",
        )
    _validate_request_artifacts_and_scope(request)


def _validate_snapshot(snapshot: SourceSnapshotSpec, label: str) -> None:
    for name, value in (
        ("snapshot_key", snapshot.snapshot_key),
        ("source_name", snapshot.source_name),
        ("source_version", snapshot.source_version),
        ("source_uri", snapshot.source_uri),
        ("declared_license_key", snapshot.declared_license_key),
        ("verified_license_key", snapshot.verified_license_key),
    ):
        _require_token(name, value)
    _require_aware_datetime(f"{label} source snapshot retrieved_at", snapshot.retrieved_at)
    _require_sha256(f"declared {label} source manifest", snapshot.declared_manifest_sha256)
    _require_sha256(f"verified {label} source manifest", snapshot.verified_manifest_sha256)
    if snapshot.declared_manifest_sha256 != snapshot.verified_manifest_sha256:
        raise StagingInputError(
            "source_manifest_checksum_mismatch",
            f"declared and verified {label} source manifest checksums differ",
        )
    if snapshot.declared_license_key != snapshot.verified_license_key:
        raise StagingInputError(
            "source_license_mismatch",
            f"declared and verified {label} source licenses differ",
        )


def _validate_request_artifacts_and_scope(request: DataS1StagingRequest) -> None:
    artifact_keys: set[str] = set()
    for artifact in _artifact_specs(request):
        _validate_artifact(artifact)
        if artifact.artifact_key in artifact_keys:
            raise StagingInputError(
                "duplicate_artifact_key",
                f"artifact roles must use distinct keys: {artifact.artifact_key}",
            )
        artifact_keys.add(artifact.artifact_key)
    if not request.data_artifact.remote_checksum_verified:
        raise StagingInputError(
            "canonical_artifact_not_remote_verified",
            "the canonical Data S1 artifact must have verified remote provenance",
        )
    if (
        request.data_artifact.verified_sha256 != DATA_S1_ARTIFACT_SHA256
        or request.data_artifact.byte_size != _CANONICAL_DATA_S1_BYTE_SIZE
    ):
        raise StagingInputError(
            "noncanonical_data_s1_artifact",
            "Data S1 must match the frozen official bioRxiv artifact checksum and size",
        )
    if request.source_snapshot.snapshot_key != DATA_S1_SOURCE_SNAPSHOT_KEY:
        raise StagingInputError(
            "noncanonical_data_s1_snapshot",
            "Data S1 must use the approved frozen source snapshot key",
        )

    if request.worksheet != "S3":
        raise StagingInputError(
            "noncanonical_worksheet",
            "canonical Data S1 staging requires the physical worksheet name 'S3'",
        )
    assembly_accessions = [item.accession_version for item in request.assemblies]
    if len(assembly_accessions) != len(set(assembly_accessions)):
        raise StagingInputError("duplicate_assembly", "assembly specifications are not unique")
    if set(assembly_accessions) != set(DATA_S1_ASSEMBLY_ALLOWLIST):
        raise StagingInputError(
            "assembly_allowlist_mismatch",
            "assembly specifications must equal the fixed ten-assembly allow-list",
        )
    taxon_names: dict[int, str] = {}
    for assembly in request.assemblies:
        _require_token("source_organism_name", assembly.source_organism_name)
        if (
            isinstance(assembly.source_tax_id, bool)
            or not isinstance(assembly.source_tax_id, int)
            or assembly.source_tax_id <= 0
        ):
            raise StagingInputError(
                "invalid_assembly_tax_id",
                f"invalid exact NCBI TaxId for {assembly.accession_version}",
            )
        existing_name = taxon_names.setdefault(
            assembly.source_tax_id, assembly.source_organism_name
        )
        if existing_name != assembly.source_organism_name:
            raise StagingInputError(
                "assembly_taxon_name_conflict",
                f"TaxId {assembly.source_tax_id} has conflicting organism names",
            )
    if len(taxon_names) != 9:
        raise StagingInputError(
            "assembly_taxon_count_mismatch",
            "the ten pilot assemblies must resolve to the frozen nine exact NCBI TaxIds",
        )
    if set(request.resolution_index.assemblies) != set(DATA_S1_ASSEMBLY_ALLOWLIST):
        raise StagingInputError(
            "ncbi_assembly_index_mismatch",
            "the frozen NCBI index must resolve every and only allow-listed assembly",
        )
    _validate_resolution_report_observation(
        "assembly",
        request.resolution_index.assembly_report_sha256,
        request.resolution_index.assembly_report_byte_size,
        request.assembly_report_artifact,
    )
    _validate_resolution_report_observation(
        "sequence",
        request.resolution_index.sequence_report_sha256,
        request.resolution_index.sequence_report_byte_size,
        request.sequence_report_artifact,
    )
    if not request.resolution_index.byte_bound:
        raise StagingInputError(
            "ncbi_index_not_byte_bound",
            "the NCBI resolution index was not parsed from its exact observed report bytes",
        )
    expected_assembly_organisms = {
        assembly.accession_version: (
            assembly.source_organism_name,
            assembly.source_tax_id,
        )
        for assembly in request.assemblies
    }
    if dict(request.resolution_index.assembly_organisms) != expected_assembly_organisms:
        raise StagingInputError(
            "ncbi_assembly_taxon_index_mismatch",
            "assembly names/TaxIds must come from the same byte-bound NCBI report index",
        )

    execution = request.execution
    for name, value in (
        ("run_key", execution.run_key),
        ("importer_name", execution.importer_name),
        ("importer_version", execution.importer_version),
        ("software_agent_key", execution.software_agent_key),
    ):
        _require_token(name, value)
    _require_sha256("importer code", execution.code_sha256)
    _require_aware_datetime("import started_at", execution.started_at)
    _require_aware_datetime("import finished_at", execution.finished_at)
    if execution.finished_at < execution.started_at:
        raise StagingInputError(
            "invalid_execution_interval", "finished_at cannot precede started_at"
        )
    _validate_expectation_spec(request.expectation)


def _validate_resolution_report_observation(
    label: str,
    observed_sha256: str | None,
    observed_byte_size: int | None,
    artifact: SourceArtifactSpec,
) -> None:
    if observed_sha256 is None or observed_byte_size is None:
        raise StagingInputError(
            "ncbi_report_observation_missing",
            f"the {label} resolution index lacks its verified report checksum or size",
        )
    if observed_sha256 != artifact.verified_sha256 or observed_byte_size != artifact.byte_size:
        raise StagingInputError(
            "ncbi_report_artifact_mismatch",
            f"the {label} resolution index does not match its staged source artifact",
        )


def _validate_expectation_spec(expectation: StagingExpectation) -> None:
    counts = {
        "source_records": expectation.source_records,
        "source_high": expectation.source_high,
        "source_low": expectation.source_low,
        "normalized_candidates": expectation.normalized_candidates,
        "quarantined_rows": expectation.quarantined_rows,
        "loci": expectation.loci,
        "placements": expectation.placements,
        "quarantine_issues": expectation.quarantine_issues,
    }
    invalid = [
        name
        for name, value in counts.items()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0
    ]
    if invalid:
        raise StagingInputError(
            "invalid_staging_expectation",
            "expectation counts must be nonnegative integers: " + ", ".join(invalid),
        )
    if expectation.source_records <= 0:
        raise StagingInputError(
            "empty_staging_expectation",
            "a succeeded import run must expect at least one source record",
        )
    if expectation.source_high + expectation.source_low != expectation.source_records:
        raise StagingInputError(
            "invalid_staging_expectation",
            "source_high + source_low must equal expected source_records",
        )
    if (
        expectation.normalized_candidates + expectation.quarantined_rows
        != expectation.source_records
    ):
        raise StagingInputError(
            "invalid_staging_expectation",
            "normalized + quarantined must equal expected source_records",
        )
    if expectation.loci > expectation.source_records:
        raise StagingInputError(
            "invalid_staging_expectation", "expected loci cannot exceed source records"
        )
    if expectation.placements > expectation.loci:
        raise StagingInputError(
            "invalid_staging_expectation", "expected placements cannot exceed loci"
        )
    if expectation.quarantine_issues < expectation.quarantined_rows:
        raise StagingInputError(
            "invalid_staging_expectation",
            "every expected quarantine row requires at least one issue",
        )
    _require_sha256("expected call key-set", expectation.call_key_set_sha256)
    _require_sha256("expected locus key-set", expectation.locus_key_set_sha256)


def _validate_prepared_expectation(
    expectation: StagingExpectation, rows: Sequence[_PreparedRow]
) -> None:
    actual: dict[str, int | str] = {
        "source_records": len(rows),
        "source_high": sum(row.outcome.source_assessment == "source_high" for row in rows),
        "source_low": sum(row.outcome.source_assessment == "source_low" for row in rows),
        "normalized_candidates": sum(row.terminal_outcome == _NORMALIZED for row in rows),
        "quarantined_rows": sum(row.terminal_outcome == _QUARANTINE for row in rows),
        "loci": sum(row.persist_locus for row in rows),
        "placements": sum(row.persist_placement for row in rows),
        "quarantine_issues": sum(len(row.issues) for row in rows),
        "call_key_set_sha256": deterministic_key_set_sha256(row.outcome.record_key for row in rows),
        "locus_key_set_sha256": deterministic_key_set_sha256(
            row.outcome.locus_key
            for row in rows
            if row.persist_locus and row.outcome.locus_key is not None
        ),
    }
    expected: dict[str, int | str] = {
        "source_records": expectation.source_records,
        "source_high": expectation.source_high,
        "source_low": expectation.source_low,
        "normalized_candidates": expectation.normalized_candidates,
        "quarantined_rows": expectation.quarantined_rows,
        "loci": expectation.loci,
        "placements": expectation.placements,
        "quarantine_issues": expectation.quarantine_issues,
        "call_key_set_sha256": expectation.call_key_set_sha256,
        "locus_key_set_sha256": expectation.locus_key_set_sha256,
    }
    mismatches = [
        f"{name}: expected {expected[name]!r}, observed {actual[name]!r}"
        for name in expected
        if actual[name] != expected[name]
    ]
    if mismatches:
        raise StagingInputError(
            "staging_expectation_mismatch",
            "full-run staging expectation mismatch; " + "; ".join(mismatches),
        )


def _validate_artifact(artifact: SourceArtifactSpec) -> None:
    for name, value in (
        ("artifact_key", artifact.artifact_key),
        ("filename", artifact.filename),
        ("media_type", artifact.media_type),
        ("source_uri", artifact.source_uri),
        ("declared_license_key", artifact.declared_license_key),
        ("verified_license_key", artifact.verified_license_key),
    ):
        _require_token(name, value)
    if artifact.byte_size <= 0:
        raise StagingInputError("invalid_artifact_size", "artifact byte_size must be positive")
    _require_sha256("declared artifact", artifact.declared_sha256)
    _require_sha256("verified artifact", artifact.verified_sha256)
    if artifact.declared_sha256 != artifact.verified_sha256:
        raise StagingInputError(
            "source_artifact_checksum_mismatch",
            f"declared and verified checksums differ for {artifact.artifact_key}",
        )
    if artifact.declared_license_key != artifact.verified_license_key:
        raise StagingInputError(
            "artifact_license_mismatch",
            f"declared and verified licenses differ for {artifact.artifact_key}",
        )
    _require_aware_datetime("artifact retrieved_at", artifact.retrieved_at)
    if artifact.remote_checksum_verified:
        if artifact.remote_verification_at is None or not artifact.remote_verification_uri:
            raise StagingInputError(
                "remote_verification_provenance_missing",
                f"remote verification provenance is missing for {artifact.artifact_key}",
            )
        _require_aware_datetime("artifact remote_verification_at", artifact.remote_verification_at)
    elif (
        artifact.remote_verification_at is not None or artifact.remote_verification_uri is not None
    ):
        raise StagingInputError(
            "unexpected_remote_verification_provenance",
            f"unverified artifact carries remote verification provenance: {artifact.artifact_key}",
        )


def _prepare_row(request: DataS1StagingRequest, outcome: DataS1ImportOutcome) -> _PreparedRow:
    if not isinstance(outcome, (ImportedDataS1Record, QuarantinedDataS1Record)):
        raise StagingInputError(
            "unsupported_import_dto", f"unsupported import DTO: {type(outcome).__name__}"
        )
    if outcome.artifact_sha256 != request.data_artifact.verified_sha256:
        raise StagingInputError(
            "row_artifact_checksum_mismatch",
            f"{outcome.record_key} is not bound to the canonical Data S1 artifact",
        )
    if outcome.source_snapshot_key != request.source_snapshot.snapshot_key:
        raise StagingInputError(
            "row_snapshot_mismatch",
            f"{outcome.record_key} is bound to a different source snapshot",
        )
    if (
        outcome.method_run_identity != DATA_S1_METHOD_RUN_IDENTITY
        or outcome.method_run_identity != request.execution.importer_version
    ):
        raise StagingInputError(
            "method_run_identity_mismatch",
            f"{outcome.record_key} is bound to a different importer method/run identity",
        )
    if outcome.locator.worksheet != request.worksheet or outcome.locator.excel_row <= 1:
        raise StagingInputError(
            "row_locator_mismatch",
            f"invalid canonical worksheet locator for {outcome.record_key}",
        )
    expected_record_key = data_s1_record_key(
        outcome.artifact_sha256,
        outcome.source_snapshot_key,
        outcome.locator,
        assembly_accession_version=outcome.assembly_accession_version,
        sequence_accession_version=outcome.sequence_accession_version,
        native_vr_token=outcome.native_vr_token,
        method_run_identity=outcome.method_run_identity,
    )
    if outcome.record_key != expected_record_key:
        raise StagingInputError(
            "record_key_mismatch",
            f"canonical source-record/call key mismatch for {outcome.locator.label}",
        )
    expected_source_record_key = data_s1_source_record_key(
        outcome.artifact_sha256,
        outcome.source_snapshot_key,
        outcome.locator,
    )
    if outcome.source_record_key != expected_source_record_key:
        raise StagingInputError(
            "source_record_key_mismatch",
            f"canonical physical source-record key mismatch for {outcome.locator.label}",
        )
    if outcome.identity_policy_key != DATA_S1_IDENTITY_POLICY_KEY:
        raise StagingInputError(
            "identity_policy_mismatch",
            f"unexpected locus identity policy for {outcome.record_key}",
        )
    if outcome.source_assessment_scheme != DATA_S1_SOURCE_ASSESSMENT_SCHEME:
        raise StagingInputError(
            "source_assessment_scheme_mismatch",
            f"unexpected HCVR scheme for {outcome.record_key}",
        )

    raw_row = dict(outcome.raw_row)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw_row.items()):
        raise StagingInputError(
            "invalid_raw_row", f"raw row must be a string mapping: {outcome.record_key}"
        )
    expected_columns = {header for _, header in DATA_S1_SOURCE_COLUMNS}
    missing = sorted(expected_columns.difference(raw_row))
    unexpected = sorted(set(raw_row).difference(expected_columns))
    if missing or unexpected:
        differences: list[str] = []
        if missing:
            differences.append("missing=" + ",".join(missing))
        if unexpected:
            differences.append("unexpected=" + ",".join(unexpected))
        raise StagingInputError(
            "raw_row_schema_mismatch",
            f"{outcome.record_key} raw columns differ: {'; '.join(differences)}",
        )
    if (
        raw_row["Assembly"] != outcome.assembly_accession_version
        or raw_row["Contig"] != outcome.sequence_accession_version
        or raw_row["VR"] != outcome.native_vr_token
    ):
        raise StagingInputError(
            "raw_identity_mismatch",
            f"raw identity differs from DTO identity for {outcome.record_key}",
        )
    if raw_row["Viral Major Taxon"] != _VIRAL_MAJOR_TAXON or raw_row["Class"] != _HOST_CLASS:
        raise StagingInputError(
            "pilot_scope_mismatch", f"row escaped the approved pilot scope: {outcome.record_key}"
        )

    source_label = raw_row["HCVR"]
    expected_confidence = "source_high" if source_label == "Yes" else "source_low"
    if outcome.source_assessment != expected_confidence:
        raise StagingInputError(
            "source_assessment_mismatch",
            f"HCVR confidence does not match the source label for {outcome.record_key}",
        )
    if isinstance(outcome, ImportedDataS1Record):
        if (
            outcome.source_hcvr != source_label
            or outcome.viral_major_taxon != raw_row["Viral Major Taxon"]
            or outcome.host_class != raw_row["Class"]
            or outcome.vr_type != raw_row["VR Type"]
        ):
            raise StagingInputError(
                "normalized_field_mismatch",
                f"normalized fields disagree with raw source row {outcome.record_key}",
            )

    expected_assembly, expected_contig, authority_length = _expected_resolution(
        request.resolution_index,
        outcome.assembly_accession_version,
        outcome.sequence_accession_version,
        raw_row["Contig Length"],
    )
    if (
        outcome.assembly_resolution != expected_assembly
        or outcome.contig_resolution != expected_contig
        or outcome.authority_contig_length != authority_length
    ):
        raise StagingInputError(
            "ncbi_resolution_mismatch",
            "DTO resolution does not match the supplied frozen NCBI index for "
            f"{outcome.record_key}",
        )

    expected_locus_key: str | None
    try:
        expected_locus_key = locus_key(
            source_snapshot_key=outcome.source_snapshot_key,
            assembly_accession_version=outcome.assembly_accession_version,
            contig_accession_version=outcome.sequence_accession_version,
            native_vr_token=outcome.native_vr_token,
            identity_policy_version=outcome.identity_policy_key,
        )
    except StableKeyError:
        expected_locus_key = None
    if outcome.locus_key != expected_locus_key:
        raise StagingInputError(
            "locus_key_mismatch",
            f"coordinate-free locus key mismatch for {outcome.record_key}",
        )

    issues = _prepared_issues(outcome)
    if not source_label or source_label != source_label.strip():
        issues = _append_issue(
            issues,
            _PreparedIssue(
                code="missing_or_malformed_source_hcvr",
                field="HCVR",
                message="HCVR must be an explicit non-empty source value",
                raw_value=source_label,
                severity="error",
                status="open",
            ),
        )

    if isinstance(outcome, ImportedDataS1Record):
        _validate_candidate_interval(outcome, raw_row, authority_length)
        if raw_row["VR Type"] != "Integration":
            raise StagingInputError(
                "candidate_vr_type_mismatch",
                f"normalized candidate is not Integration: {outcome.record_key}",
            )
        if expected_assembly != "exact" or expected_contig != "exact":
            raise StagingInputError(
                "candidate_resolution_not_exact",
                f"normalized candidate lacks exact authority resolution: {outcome.record_key}",
            )
    elif not issues:
        raise StagingInputError(
            "empty_quarantine",
            f"quarantine DTO has no structured issue: {outcome.record_key}",
        )

    terminal = (
        _NORMALIZED if isinstance(outcome, ImportedDataS1Record) and not issues else _QUARANTINE
    )
    persist_locus = (
        expected_assembly == "exact"
        and expected_contig == "exact"
        and expected_locus_key is not None
    )
    persist_placement = terminal == _NORMALIZED
    return _PreparedRow(
        outcome=outcome,
        raw_row=raw_row,
        source_label=source_label,
        viral_major_taxon=raw_row["Viral Major Taxon"],
        vr_type=raw_row["VR Type"],
        terminal_outcome=terminal,
        issues=issues,
        persist_locus=persist_locus,
        persist_placement=persist_placement,
        authority_length=authority_length,
    )


def _prepared_issues(outcome: DataS1ImportOutcome) -> tuple[_PreparedIssue, ...]:
    if not isinstance(outcome, QuarantinedDataS1Record):
        return ()
    prepared: list[_PreparedIssue] = []
    for issue in outcome.issues:
        accounted = issue.code == "viral_contig_policy_quarantine"
        prepared.append(
            _PreparedIssue(
                code=issue.code,
                field=issue.field,
                message=issue.message,
                raw_value=issue.raw_value,
                severity="warning" if accounted else "error",
                status="resolved" if accounted else "open",
            )
        )
    return tuple(prepared)


def _append_issue(
    issues: tuple[_PreparedIssue, ...], issue: _PreparedIssue
) -> tuple[_PreparedIssue, ...]:
    identity = (issue.code, issue.field, issue.message, issue.raw_value)
    if any((item.code, item.field, item.message, item.raw_value) == identity for item in issues):
        return issues
    return (*issues, issue)


def _expected_resolution(
    index: NcbiResolutionIndex,
    assembly_accession: str,
    sequence_accession: str,
    raw_source_length: str,
) -> tuple[str, str, int | None]:
    if assembly_accession not in index.assemblies:
        return "unresolved", "unresolved", None
    authority_length = index.sequence_length(assembly_accession, sequence_accession)
    if authority_length is None:
        return "exact", "unresolved", None
    source_length = _positive_integer_or_none(raw_source_length)
    if source_length is None:
        return "exact", "length_unverified", authority_length
    if source_length != authority_length:
        return "exact", "length_mismatch", authority_length
    return "exact", "exact", authority_length


def _validate_candidate_interval(
    outcome: ImportedDataS1Record,
    raw_row: Mapping[str, str],
    authority_length: int | None,
) -> None:
    if outcome.coordinate_system != DATA_S1_COORDINATE_SYSTEM:
        raise StagingInputError(
            "coordinate_system_mismatch",
            f"noncanonical coordinate system for {outcome.record_key}",
        )
    raw_values = tuple(
        _nonnegative_integer_or_none(raw_row[name])
        for name in ("Contig Length", "Start", "End", "Length")
    )
    if raw_values != (outcome.contig_length, outcome.start0, outcome.end0, outcome.length):
        raise StagingInputError(
            "interval_raw_value_mismatch",
            f"normalized interval differs from raw row {outcome.record_key}",
        )
    if (
        authority_length is None
        or outcome.contig_length != authority_length
        or not 0 <= outcome.start0 < outcome.end0 <= authority_length
        or outcome.length != outcome.end0 - outcome.start0
    ):
        raise StagingInputError(
            "invalid_exact_interval",
            "candidate interval is not an exact in-bounds half-open interval: "
            f"{outcome.record_key}",
        )


def _persist_run_provenance(
    session: Session,
    request: DataS1StagingRequest,
    release: DatasetRelease,
    snapshot: SourceSnapshot,
    data_artifact: SourceArtifact,
    parameters: dict[str, Any],
    parameters_sha256: str,
    created: Counter[str],
    reused: Counter[str],
) -> tuple[MethodDefinition, ProcessRun, ImportRun]:
    method_key = f"{request.execution.importer_name}:data-s1"
    method_definition_key = stable_key(
        "method-definition:data-s1",
        {
            "code_sha256": request.execution.code_sha256,
            "importer_name": request.execution.importer_name,
            "importer_version": request.execution.importer_version,
        },
    )
    parameter_schema = {
        "type": "object",
        "required": ["artifact_sha256", "assembly_allowlist", "worksheet"],
    }
    output_schema = {
        "type": "object",
        "terminal_outcomes": [_NORMALIZED, _QUARANTINE],
        "source_assertions": ["hcvr", "viral_major_taxon", "vr_type"],
    }
    method, _ = _get_or_create(
        session,
        MethodDefinition,
        MethodDefinition.method_definition_key == method_definition_key,
        {
            "method_definition_key": method_definition_key,
            "method_key": method_key,
            "version": request.execution.importer_version,
            "method_kind": "source_import",
            "definition_artifact_id": None,
            "definition_sha256": request.execution.code_sha256,
            "parameter_schema": parameter_schema,
            "output_schema": output_schema,
        },
        (
            "method_definition_key",
            "method_key",
            "version",
            "method_kind",
            "definition_artifact_id",
            "definition_sha256",
            "parameter_schema",
            "output_schema",
        ),
        "method_definition",
        created,
        reused,
    )
    _get_or_create(
        session,
        ReleaseMethodDefinition,
        (ReleaseMethodDefinition.release_id == release.id)
        & (ReleaseMethodDefinition.method_definition_id == method.id)
        & (ReleaseMethodDefinition.role == _METHOD_ROLE),
        {
            "release_id": release.id,
            "method_definition_id": method.id,
            "role": _METHOD_ROLE,
        },
        ("release_id", "method_definition_id", "role"),
        "release_method_definition",
        created,
        reused,
    )
    import_run, was_created = _get_or_create(
        session,
        ImportRun,
        ImportRun.run_key == request.execution.run_key,
        {
            "run_key": request.execution.run_key,
            "release_id": release.id,
            "source_snapshot_id": snapshot.id,
            "source_artifact_id": data_artifact.id,
            "importer_name": request.execution.importer_name,
            "importer_version": request.execution.importer_version,
            "code_sha256": request.execution.code_sha256,
            "parameters": parameters,
            "parameters_sha256": parameters_sha256,
            "status": "succeeded",
            "started_at": request.execution.started_at,
            "finished_at": request.execution.finished_at,
        },
        (
            "run_key",
            "release_id",
            "source_snapshot_id",
            "source_artifact_id",
            "importer_name",
            "importer_version",
            "code_sha256",
            "parameters",
            "parameters_sha256",
            "status",
            "started_at",
            "finished_at",
        ),
        "import_run",
        created,
        reused,
    )
    if not was_created:
        raise StagingConflictError(
            "unexpected_existing_run", "run appeared after replay check while lock was held"
        )
    process_run_key = _process_run_key(request)
    process_run, _ = _get_or_create(
        session,
        ProcessRun,
        ProcessRun.process_run_key == process_run_key,
        {
            "process_run_key": process_run_key,
            "release_id": release.id,
            "method_definition_id": method.id,
            "method_role": _METHOD_ROLE,
            "import_run_id": import_run.id,
            "execution_status": "succeeded",
            "software_agent_key": request.execution.software_agent_key,
            "parameters": parameters,
            "parameters_sha256": parameters_sha256,
            "started_at": request.execution.started_at,
            "finished_at": request.execution.finished_at,
        },
        (
            "process_run_key",
            "release_id",
            "method_definition_id",
            "method_role",
            "import_run_id",
            "execution_status",
            "software_agent_key",
            "parameters",
            "parameters_sha256",
            "started_at",
            "finished_at",
        ),
        "process_run",
        created,
        reused,
    )
    return method, process_run, import_run


def _persist_study_lineage(
    session: Session,
    request: DataS1StagingRequest,
    release: DatasetRelease,
    data_artifact: SourceArtifact,
    created: Counter[str],
    reused: Counter[str],
) -> tuple[LineageSnapshot, LineageTerm]:
    lineage_payload = _study_lineage_payload(request)
    snapshot_key = _study_lineage_snapshot_key(request)
    snapshot_sha256 = canonical_json_sha256(lineage_payload)
    lineage_snapshot, _ = _get_or_create(
        session,
        LineageSnapshot,
        LineageSnapshot.snapshot_key == snapshot_key,
        {
            "snapshot_key": snapshot_key,
            "domain": "viral",
            "scheme_kind": "study_defined",
            "authority_namespace": "zhao-biorxiv-v4-viral-major-taxon",
            "version": request.source_snapshot.source_version,
            "source_artifact_id": data_artifact.id,
            "snapshot_sha256": snapshot_sha256,
        },
        (
            "snapshot_key",
            "domain",
            "scheme_kind",
            "authority_namespace",
            "version",
            "source_artifact_id",
            "snapshot_sha256",
        ),
        "lineage_snapshot",
        created,
        reused,
    )
    _get_or_create(
        session,
        ReleaseLineageSnapshot,
        (ReleaseLineageSnapshot.release_id == release.id)
        & (ReleaseLineageSnapshot.snapshot_id == lineage_snapshot.id)
        & (ReleaseLineageSnapshot.role == _STUDY_LINEAGE_ROLE),
        {
            "release_id": release.id,
            "snapshot_id": lineage_snapshot.id,
            "role": _STUDY_LINEAGE_ROLE,
            "domain": "viral",
            "scheme_kind": "study_defined",
        },
        ("release_id", "snapshot_id", "role", "domain", "scheme_kind"),
        "release_lineage_snapshot",
        created,
        reused,
    )
    term_key = "study-viral-major-taxon:orthopolintovirales"
    lineage_term, _ = _get_or_create(
        session,
        LineageTerm,
        (LineageTerm.snapshot_id == lineage_snapshot.id) & (LineageTerm.term_key == term_key),
        {
            "snapshot_id": lineage_snapshot.id,
            "term_key": term_key,
            "canonical_name": _VIRAL_MAJOR_TAXON,
            "rank": "source_major_taxon",
            "authority_local_id": _VIRAL_MAJOR_TAXON,
            "source_locator": {"worksheet": request.worksheet, "column": "J"},
        },
        (
            "snapshot_id",
            "term_key",
            "canonical_name",
            "rank",
            "authority_local_id",
            "source_locator",
        ),
        "lineage_term",
        created,
        reused,
    )
    _get_or_create(
        session,
        LineageClosure,
        (LineageClosure.snapshot_id == lineage_snapshot.id)
        & (LineageClosure.ancestor_term_id == lineage_term.id)
        & (LineageClosure.descendant_term_id == lineage_term.id),
        {
            "snapshot_id": lineage_snapshot.id,
            "ancestor_term_id": lineage_term.id,
            "descendant_term_id": lineage_term.id,
            "depth": 0,
        },
        ("snapshot_id", "ancestor_term_id", "descendant_term_id", "depth"),
        "lineage_closure",
        created,
        reused,
    )
    return lineage_snapshot, lineage_term


def _persist_assemblies(
    session: Session,
    request: DataS1StagingRequest,
    release: DatasetRelease,
    assembly_artifact: SourceArtifact,
    created: Counter[str],
    reused: Counter[str],
) -> dict[str, GenomeAssembly]:
    result: dict[str, GenomeAssembly] = {}
    for spec in sorted(request.assemblies, key=lambda item: item.accession_version):
        assembly_key = f"assembly:ncbi:{spec.accession_version}"
        assembly, _ = _get_or_create(
            session,
            GenomeAssembly,
            (GenomeAssembly.namespace == "ncbi")
            & (GenomeAssembly.accession_version == spec.accession_version),
            {
                "assembly_key": assembly_key,
                "namespace": "ncbi",
                "accession_version": spec.accession_version,
                "source_organism_name": spec.source_organism_name,
                "source_artifact_id": assembly_artifact.id,
            },
            (
                "assembly_key",
                "namespace",
                "accession_version",
                "source_organism_name",
                "source_artifact_id",
            ),
            "genome_assembly",
            created,
            reused,
        )
        _get_or_create(
            session,
            ReleaseAssemblyMembership,
            (ReleaseAssemblyMembership.release_id == release.id)
            & (ReleaseAssemblyMembership.assembly_id == assembly.id),
            {
                "release_id": release.id,
                "assembly_id": assembly.id,
                "membership_role": "pilot_scope",
            },
            ("release_id", "assembly_id", "membership_role"),
            "release_assembly_membership",
            created,
            reused,
        )
        result[spec.accession_version] = assembly
    return result


def _persist_assembly_source_taxonomy(
    session: Session,
    request: DataS1StagingRequest,
    release: DatasetRelease,
    assembly_artifact: SourceArtifact,
    assemblies: Mapping[str, GenomeAssembly],
    created: Counter[str],
    reused: Counter[str],
) -> None:
    taxon_names = {spec.source_tax_id: spec.source_organism_name for spec in request.assemblies}
    lineage_payload = {
        "authority_namespace": "ncbi-taxonomy",
        "coverage": "assembly-report-leaves-only",
        "source_artifact_sha256": request.assembly_report_artifact.verified_sha256,
        "taxa": [
            {"organism_name": taxon_names[tax_id], "tax_id": tax_id}
            for tax_id in sorted(taxon_names)
        ],
        "version": request.resolution_snapshot.source_version,
    }
    snapshot_key = stable_key("lineage-snapshot:ncbi-taxonomy-assembly-leaves", lineage_payload)
    lineage_snapshot, _ = _get_or_create(
        session,
        LineageSnapshot,
        LineageSnapshot.snapshot_key == snapshot_key,
        {
            "snapshot_key": snapshot_key,
            "domain": "host",
            "scheme_kind": "formal_taxonomy",
            "authority_namespace": "ncbi-taxonomy",
            "version": (request.resolution_snapshot.source_version + ":assembly-report-leaves"),
            "source_artifact_id": assembly_artifact.id,
            "snapshot_sha256": canonical_json_sha256(lineage_payload),
        },
        (
            "snapshot_key",
            "domain",
            "scheme_kind",
            "authority_namespace",
            "version",
            "source_artifact_id",
            "snapshot_sha256",
        ),
        "lineage_snapshot",
        created,
        reused,
    )
    _get_or_create(
        session,
        ReleaseLineageSnapshot,
        (ReleaseLineageSnapshot.release_id == release.id)
        & (ReleaseLineageSnapshot.snapshot_id == lineage_snapshot.id)
        & (ReleaseLineageSnapshot.role == "assembly_source_taxonomy"),
        {
            "release_id": release.id,
            "snapshot_id": lineage_snapshot.id,
            "role": "assembly_source_taxonomy",
            "domain": "host",
            "scheme_kind": "formal_taxonomy",
        },
        ("release_id", "snapshot_id", "role", "domain", "scheme_kind"),
        "release_lineage_snapshot",
        created,
        reused,
    )

    public_release_blocker = (
        "full NCBI Taxonomy taxdump with lineage and merged/deleted TaxId history is not frozen"
    )
    terms: dict[int, LineageTerm] = {}
    for tax_id, organism_name in sorted(taxon_names.items()):
        term_key = f"ncbi-taxonomy:taxid:{tax_id}"
        term, _ = _get_or_create(
            session,
            LineageTerm,
            (LineageTerm.snapshot_id == lineage_snapshot.id) & (LineageTerm.term_key == term_key),
            {
                "snapshot_id": lineage_snapshot.id,
                "term_key": term_key,
                "canonical_name": organism_name,
                "rank": None,
                "authority_local_id": str(tax_id),
                "source_locator": {
                    "artifact_key": request.assembly_report_artifact.artifact_key,
                    "coverage": "assembly-report-leaf-only",
                    "json_path": "$.organism.tax_id",
                    "public_release_blocker": public_release_blocker,
                },
            },
            (
                "snapshot_id",
                "term_key",
                "canonical_name",
                "rank",
                "authority_local_id",
                "source_locator",
            ),
            "lineage_term",
            created,
            reused,
        )
        _get_or_create(
            session,
            LineageClosure,
            (LineageClosure.snapshot_id == lineage_snapshot.id)
            & (LineageClosure.ancestor_term_id == term.id)
            & (LineageClosure.descendant_term_id == term.id),
            {
                "snapshot_id": lineage_snapshot.id,
                "ancestor_term_id": term.id,
                "descendant_term_id": term.id,
                "depth": 0,
            },
            ("snapshot_id", "ancestor_term_id", "descendant_term_id", "depth"),
            "lineage_closure",
            created,
            reused,
        )
        terms[tax_id] = term

    assignment_policy_key = "ncbi-datasets-v2-assembly-organism-taxid-v1"
    for spec in request.assemblies:
        assembly = assemblies[spec.accession_version]
        term = terms[spec.source_tax_id]
        assignment_key = stable_key(
            "assembly-taxon-assignment:ncbi",
            {
                "assembly_key": assembly.assembly_key,
                "assignment_policy_key": assignment_policy_key,
                "lineage_snapshot_key": lineage_snapshot.snapshot_key,
                "release_key": request.release.release_key,
                "tax_id": spec.source_tax_id,
            },
        )
        _get_or_create(
            session,
            AssemblyTaxonAssignment,
            AssemblyTaxonAssignment.assignment_key == assignment_key,
            {
                "assignment_key": assignment_key,
                "release_id": release.id,
                "assembly_id": assembly.id,
                "snapshot_id": lineage_snapshot.id,
                "snapshot_role": "assembly_source_taxonomy",
                "term_id": term.id,
                "assignment_policy_key": assignment_policy_key,
                "source_artifact_id": assembly_artifact.id,
                "source_locator": {
                    "artifact_key": request.assembly_report_artifact.artifact_key,
                    "assembly_accession_version": spec.accession_version,
                    "json_path": "$.organism.tax_id",
                    "public_release_blocker": public_release_blocker,
                    "tax_id": spec.source_tax_id,
                },
            },
            (
                "assignment_key",
                "release_id",
                "assembly_id",
                "snapshot_id",
                "snapshot_role",
                "term_id",
                "assignment_policy_key",
                "source_artifact_id",
                "source_locator",
            ),
            "assembly_taxon_assignment",
            created,
            reused,
        )


def _persist_sequences(
    session: Session,
    prepared: Sequence[_PreparedRow],
    assemblies: Mapping[str, GenomeAssembly],
    sequence_artifact: SourceArtifact,
    created: Counter[str],
    reused: Counter[str],
) -> dict[tuple[str, str], AssemblySequence]:
    required: dict[tuple[str, str], int] = {}
    for row in prepared:
        if row.outcome.assembly_resolution != "exact" or row.authority_length is None:
            continue
        key = (
            row.outcome.assembly_accession_version,
            row.outcome.sequence_accession_version,
        )
        existing_length = required.setdefault(key, row.authority_length)
        if existing_length != row.authority_length:
            raise StagingInputError(
                "conflicting_authority_lengths", f"conflicting authority lengths for {key}"
            )

    result: dict[tuple[str, str], AssemblySequence] = {}
    required_accessions = sorted({sequence_accession for _, sequence_accession in required})
    existing_by_accession: dict[str, AssemblySequence] = {}
    for offset in range(0, len(required_accessions), _EXPANDING_QUERY_BATCH_SIZE):
        accession_batch = required_accessions[offset : offset + _EXPANDING_QUERY_BATCH_SIZE]
        for existing_sequence in session.scalars(
            select(AssemblySequence).where(AssemblySequence.accession_version.in_(accession_batch))
        ).all():
            existing_by_accession[existing_sequence.accession_version] = existing_sequence
    for (assembly_accession, sequence_accession), length in sorted(required.items()):
        assembly = assemblies[assembly_accession]
        sequence_key = f"sequence:insdc:{sequence_accession}"
        values = {
            "assembly_id": assembly.id,
            "sequence_key": sequence_key,
            "namespace": "insdc",
            "accession_version": sequence_accession,
            "sequence_length": length,
            "sequence_sha256": None,
            "source_artifact_id": sequence_artifact.id,
        }
        sequence = existing_by_accession.get(sequence_accession)
        if sequence is None:
            sequence = AssemblySequence(**values)
            session.add(sequence)
            created["assembly_sequence"] += 1
            existing_by_accession[sequence_accession] = sequence
        else:
            _assert_same(
                sequence,
                values,
                (
                    "assembly_id",
                    "sequence_key",
                    "namespace",
                    "accession_version",
                    "sequence_length",
                    "sequence_sha256",
                    "source_artifact_id",
                ),
                "assembly_sequence",
            )
            reused["assembly_sequence"] += 1
        result[(assembly_accession, sequence_accession)] = sequence
    session.flush()
    return result


def _persist_row_chunk(
    *,
    session: Session,
    request: DataS1StagingRequest,
    rows: Sequence[_PreparedRow],
    release: DatasetRelease,
    snapshot: SourceSnapshot,
    data_artifact: SourceArtifact,
    import_run: ImportRun,
    process_run: ProcessRun,
    method: MethodDefinition,
    lineage_snapshot: LineageSnapshot,
    lineage_term: LineageTerm,
    assemblies: Mapping[str, GenomeAssembly],
    sequences: Mapping[tuple[str, str], AssemblySequence],
    source_records: dict[str, SourceRecord],
    created: Counter[str],
    reused: Counter[str],
) -> None:
    chunk_records: dict[str, SourceRecord] = {}
    for row in rows:
        outcome = row.outcome
        locator = _source_locator(outcome)
        raw_sha256 = canonical_json_sha256(row.raw_row)
        values = {
            "source_record_key": outcome.source_record_key,
            "snapshot_id": snapshot.id,
            "artifact_id": data_artifact.id,
            "worksheet": outcome.locator.worksheet,
            "row_number": outcome.locator.excel_row,
            "native_vr_token": outcome.native_vr_token,
            "assembly_accession_version": outcome.assembly_accession_version,
            "sequence_accession_version": outcome.sequence_accession_version,
            "source_locator": locator,
            "raw_payload": row.raw_row,
            "raw_payload_sha256": raw_sha256,
        }
        source_record = source_records.get(outcome.source_record_key)
        if source_record is None:
            source_record = SourceRecord(**values)
            session.add(source_record)
            created["source_record"] += 1
            source_records[outcome.source_record_key] = source_record
        else:
            _assert_same(source_record, values, tuple(values), "source_record")
            reused["source_record"] += 1
        chunk_records[outcome.record_key] = source_record
    session.flush()

    loci: dict[str, EVELocus | None] = {}
    for row in rows:
        outcome = row.outcome
        locus: EVELocus | None = None
        if row.persist_locus:
            assert outcome.locus_key is not None
            assembly = assemblies[outcome.assembly_accession_version]
            sequence = sequences[
                (outcome.assembly_accession_version, outcome.sequence_accession_version)
            ]
            source_record = chunk_records[outcome.record_key]
            locus = EVELocus(
                locus_key=outcome.locus_key,
                release_id=release.id,
                assembly_id=assembly.id,
                sequence_id=sequence.id,
                source_snapshot_id=snapshot.id,
                source_record_id=source_record.id,
                native_vr_token=outcome.native_vr_token,
                identity_policy_key=outcome.identity_policy_key,
            )
            session.add(locus)
            created["eve_locus"] += 1
        loci[outcome.record_key] = locus
    session.flush()

    calls: dict[str, DetectionCall] = {}
    for row in rows:
        outcome = row.outcome
        source_record = chunk_records[outcome.record_key]
        locus = loci[outcome.record_key]
        call = DetectionCall(
            call_key=outcome.record_key,
            release_id=release.id,
            source_snapshot_id=snapshot.id,
            source_record_id=source_record.id,
            locus_id=locus.id if locus is not None else None,
            process_run_id=process_run.id,
            process_run_status="succeeded",
            source_method_key=method.method_key,
            source_locator=_source_locator(outcome),
            raw_result={
                "assembly_resolution": outcome.assembly_resolution,
                "contig_resolution": outcome.contig_resolution,
                "import_status": outcome.status,
                "terminal_outcome": row.terminal_outcome,
            },
        )
        session.add(call)
        calls[outcome.record_key] = call
        created["detection_call"] += 1
    session.flush()

    assessments: dict[str, SourceAssessment] = {}
    evidence_items: dict[str, EvidenceItem] = {}
    for row in rows:
        outcome = row.outcome
        call = calls[outcome.record_key]
        locator = _source_locator(outcome)
        assessment = SourceAssessment(
            assessment_key=stable_key(
                "source-assessment:hcvr",
                {
                    "call_key": outcome.record_key,
                    "scheme": outcome.source_assessment_scheme,
                },
            ),
            release_id=release.id,
            call_id=call.id,
            process_run_id=process_run.id,
            assessment_type="hcvr",
            source_label=row.source_label,
            confidence=outcome.source_assessment,
            source_artifact_id=data_artifact.id,
            source_locator={
                **locator,
                "column": "D",
                "scheme": outcome.source_assessment_scheme,
            },
        )
        evidence = EvidenceItem(
            evidence_key=_evidence_key(request, outcome.record_key),
            release_id=release.id,
            source_snapshot_id=snapshot.id,
            source_artifact_id=data_artifact.id,
            evidence_type="source_row",
            source_locator=locator,
            evidence_sha256=canonical_json_sha256(row.raw_row),
            summary=f"Frozen Data S1 source row {outcome.locator.label}",
        )
        session.add_all((assessment, evidence))
        assessments[outcome.record_key] = assessment
        evidence_items[outcome.record_key] = evidence
        created["source_assessment"] += 1
        created["evidence_item"] += 1
    session.flush()

    assertions_by_record: dict[str, tuple[ScientificAssertion, ...]] = {}
    for row in rows:
        outcome = row.outcome
        call = calls[outcome.record_key]
        locus = loci[outcome.record_key]
        assessment = assessments[outcome.record_key]
        common = {
            "release_id": release.id,
            "call_id": call.id,
            "locus_id": locus.id if locus is not None else None,
            "process_run_id": process_run.id,
            "process_run_status": "succeeded",
        }
        payload = {
            "record_key": outcome.record_key,
            "source_locator": _source_locator(outcome),
            "terminal_outcome": row.terminal_outcome,
        }
        hcvr = ScientificAssertion(
            assertion_key=_assertion_key(request, row, "hcvr"),
            **common,
            assertion_type="hcvr",
            predicate_key="source:hcvr",
            asserted_value=row.source_label,
            source_assessment_id=assessment.id,
            source_label=row.source_label,
            source_confidence=outcome.source_assessment,
            lineage_snapshot_id=None,
            lineage_snapshot_role=None,
            lineage_term_id=None,
            result_payload={**payload, "scheme": outcome.source_assessment_scheme},
        )
        viral_taxon = ScientificAssertion(
            assertion_key=_assertion_key(request, row, "viral_major_taxon"),
            **common,
            assertion_type="viral_major_taxon",
            predicate_key="source:viral-major-taxon",
            asserted_value=row.viral_major_taxon,
            source_assessment_id=None,
            source_label=None,
            source_confidence=None,
            lineage_snapshot_id=lineage_snapshot.id,
            lineage_snapshot_role=_STUDY_LINEAGE_ROLE,
            lineage_term_id=lineage_term.id,
            result_payload=payload,
        )
        vr_type = ScientificAssertion(
            assertion_key=_assertion_key(request, row, "vr_type"),
            **common,
            assertion_type="vr_type",
            predicate_key="source:vr-type",
            asserted_value=row.vr_type,
            source_assessment_id=None,
            source_label=None,
            source_confidence=None,
            lineage_snapshot_id=None,
            lineage_snapshot_role=None,
            lineage_term_id=None,
            result_payload=payload,
        )
        assertion_tuple = (hcvr, viral_taxon, vr_type)
        session.add_all(assertion_tuple)
        assertions_by_record[outcome.record_key] = assertion_tuple
        created["scientific_assertion"] += 3
    session.flush()

    ledgers: dict[str, ImportLedger] = {}
    for row in rows:
        outcome = row.outcome
        call = calls[outcome.record_key]
        locus = loci[outcome.record_key]
        evidence = evidence_items[outcome.record_key]
        for assertion in assertions_by_record[outcome.record_key]:
            session.add(
                AssertionEvidence(
                    release_id=release.id,
                    assertion_id=assertion.id,
                    evidence_id=evidence.id,
                    relation="supports",
                )
            )
            created["assertion_evidence"] += 1

        result_payload = {
            "assembly_resolution": outcome.assembly_resolution,
            "contig_resolution": outcome.contig_resolution,
            "issue_codes": [issue.code for issue in row.issues],
            "record_key": outcome.record_key,
            "terminal_outcome": row.terminal_outcome,
        }
        ledger = ImportLedger(
            run_id=import_run.id,
            release_id=release.id,
            source_record_id=chunk_records[outcome.record_key].id,
            call_id=call.id,
            locus_id=locus.id if locus is not None else None,
            outcome=row.terminal_outcome,
            result_payload=result_payload,
            result_sha256=canonical_json_sha256(result_payload),
        )
        session.add(ledger)
        ledgers[outcome.record_key] = ledger
        created["import_ledger"] += 1
    session.flush()

    for row in rows:
        outcome = row.outcome
        ledger = ledgers[outcome.record_key]
        for index, issue in enumerate(row.issues):
            issue_key = _quarantine_issue_key(request, row, index)
            accounted = issue.code == "viral_contig_policy_quarantine"
            session.add(
                QuarantineIssue(
                    issue_key=issue_key,
                    ledger_id=ledger.id,
                    issue_code=issue.code,
                    severity=issue.severity,
                    status=issue.status,
                    field_name=issue.field,
                    message=issue.message,
                    raw_value=issue.raw_value,
                    details={
                        "accounting_state": (
                            "accounted_policy_quarantine" if accounted else "unresolved"
                        ),
                        "source_locator": _source_locator(outcome),
                    },
                )
            )
            created["quarantine_issue"] += 1
    session.flush()

    for row in rows:
        if not row.persist_placement:
            continue
        outcome = row.outcome
        assert isinstance(outcome, ImportedDataS1Record)
        locus = loci[outcome.record_key]
        assert locus is not None
        assembly = assemblies[outcome.assembly_accession_version]
        sequence = sequences[
            (outcome.assembly_accession_version, outcome.sequence_accession_version)
        ]
        placement_payload = {
            "coordinate_system": outcome.coordinate_system,
            "end0": outcome.end0,
            "locus_key": outcome.locus_key,
            "source_artifact_sha256": outcome.artifact_sha256,
            "source_locator": _source_locator(outcome),
            "start0": outcome.start0,
            "strand": "unknown",
        }
        session.add(
            EVELocusPlacement(
                placement_key=stable_key("placement:eve:v1", placement_payload),
                release_id=release.id,
                locus_id=locus.id,
                assembly_id=assembly.id,
                sequence_id=sequence.id,
                start0=outcome.start0,
                end0=outcome.end0,
                strand="unknown",
                precision="exact",
                coordinate_system=outcome.coordinate_system,
                raw_location=f"{row.raw_row['Start']}:{row.raw_row['End']}",
                raw_coordinate_system=outcome.coordinate_system,
                source_artifact_id=data_artifact.id,
                source_locator=_source_locator(outcome),
                placement_sha256=canonical_json_sha256(placement_payload),
            )
        )
        created["eve_locus_placement"] += 1
    session.flush()


def _validate_replayed_run(
    session: Session,
    existing_run: ImportRun,
    request: DataS1StagingRequest,
    parameters_sha256: str,
    rows: Sequence[_PreparedRow],
) -> None:
    artifact = session.get(SourceArtifact, existing_run.source_artifact_id)
    snapshot = session.get(SourceSnapshot, existing_run.source_snapshot_id)
    release = session.get(DatasetRelease, existing_run.release_id)
    if artifact is None or snapshot is None or release is None:
        raise StagingConflictError(
            "replay_provenance_missing", "existing import run has broken provenance"
        )
    expected = {
        "run_key": request.execution.run_key,
        "importer_name": request.execution.importer_name,
        "importer_version": request.execution.importer_version,
        "code_sha256": request.execution.code_sha256,
        "parameters_sha256": parameters_sha256,
        "status": "succeeded",
        "started_at": request.execution.started_at,
        "finished_at": request.execution.finished_at,
    }
    _assert_same(existing_run, expected, tuple(expected), "import_run")
    if (
        artifact.artifact_key != request.data_artifact.artifact_key
        or artifact.verified_sha256 != request.data_artifact.verified_sha256
        or snapshot.snapshot_key != request.source_snapshot.snapshot_key
        or release.release_key != request.release.release_key
    ):
        raise StagingConflictError(
            "replay_provenance_conflict",
            "existing run is bound to different frozen provenance",
        )
    expected_terminal = {row.outcome.record_key: row.terminal_outcome for row in rows}
    actual_rows = session.execute(
        select(DetectionCall.call_key, ImportLedger.outcome)
        .join(ImportLedger, ImportLedger.call_id == DetectionCall.id)
        .where(ImportLedger.run_id == existing_run.id)
    ).all()
    actual_terminal = {record_key: outcome for record_key, outcome in actual_rows}
    if len(actual_rows) != len(actual_terminal) or actual_terminal != dict(expected_terminal):
        _raise_replay_conflict("ledger rows/outcomes")

    expected_source_record_keys = {row.outcome.source_record_key for row in rows}
    actual_source_record_keys = set(
        session.scalars(
            select(SourceRecord.source_record_key)
            .join(ImportLedger, ImportLedger.source_record_id == SourceRecord.id)
            .where(ImportLedger.run_id == existing_run.id)
        ).all()
    )
    if actual_source_record_keys != expected_source_record_keys:
        _raise_replay_conflict("physical source records")

    process_runs = session.scalars(
        select(ProcessRun).where(ProcessRun.import_run_id == existing_run.id)
    ).all()
    if len(process_runs) != 1:
        _raise_replay_conflict("process run")
    process_run = process_runs[0]
    expected_record_keys = set(expected_terminal)
    actual_call_keys = set(
        session.scalars(
            select(DetectionCall.call_key).where(
                (DetectionCall.release_id == release.id)
                & (DetectionCall.process_run_id == process_run.id)
            )
        ).all()
    )
    if actual_call_keys != expected_record_keys:
        _raise_replay_conflict("detection calls")

    expected_assessment_keys = {
        stable_key(
            "source-assessment:hcvr",
            {
                "call_key": row.outcome.record_key,
                "scheme": row.outcome.source_assessment_scheme,
            },
        )
        for row in rows
    }
    actual_assessment_keys = set(
        session.scalars(
            select(SourceAssessment.assessment_key).where(
                (SourceAssessment.release_id == release.id)
                & (SourceAssessment.process_run_id == process_run.id)
            )
        ).all()
    )
    if actual_assessment_keys != expected_assessment_keys:
        _raise_replay_conflict("source assessments")

    expected_evidence_keys = {_evidence_key(request, row.outcome.record_key) for row in rows}
    actual_evidence_keys = set(
        session.scalars(
            select(EvidenceItem.evidence_key).where(EvidenceItem.release_id == release.id)
        ).all()
    )
    if actual_evidence_keys != expected_evidence_keys:
        _raise_replay_conflict("evidence items")

    expected_assertion_keys = {
        _assertion_key(request, row, assertion_type)
        for row in rows
        for assertion_type in ("hcvr", "viral_major_taxon", "vr_type")
    }
    actual_assertion_keys = set(
        session.scalars(
            select(ScientificAssertion.assertion_key).where(
                (ScientificAssertion.release_id == release.id)
                & (ScientificAssertion.process_run_id == process_run.id)
            )
        ).all()
    )
    if actual_assertion_keys != expected_assertion_keys:
        _raise_replay_conflict("scientific assertions")

    expected_assertion_evidence = {
        (
            _assertion_key(request, row, assertion_type),
            _evidence_key(request, row.outcome.record_key),
            "supports",
        )
        for row in rows
        for assertion_type in ("hcvr", "viral_major_taxon", "vr_type")
    }
    actual_assertion_evidence_rows = session.execute(
        select(
            ScientificAssertion.assertion_key,
            EvidenceItem.evidence_key,
            AssertionEvidence.relation,
        )
        .select_from(AssertionEvidence)
        .join(
            ScientificAssertion,
            (ScientificAssertion.release_id == AssertionEvidence.release_id)
            & (ScientificAssertion.id == AssertionEvidence.assertion_id),
        )
        .join(
            EvidenceItem,
            (EvidenceItem.release_id == AssertionEvidence.release_id)
            & (EvidenceItem.id == AssertionEvidence.evidence_id),
        )
        .where(
            (ScientificAssertion.release_id == release.id)
            & (ScientificAssertion.process_run_id == process_run.id)
        )
    ).all()
    actual_assertion_evidence = {
        (assertion_key, evidence_key, relation)
        for assertion_key, evidence_key, relation in actual_assertion_evidence_rows
    }
    if (
        len(actual_assertion_evidence_rows) != len(actual_assertion_evidence)
        or actual_assertion_evidence != expected_assertion_evidence
    ):
        _raise_replay_conflict("assertion evidence edges")

    expected_locus_keys = {row.outcome.locus_key for row in rows if row.persist_locus}
    actual_locus_keys = set(
        session.scalars(select(EVELocus.locus_key).where(EVELocus.release_id == release.id)).all()
    )
    if actual_locus_keys != expected_locus_keys:
        _raise_replay_conflict("EVE loci")
    placement_count = session.scalar(
        select(func.count())
        .select_from(EVELocusPlacement)
        .where(EVELocusPlacement.release_id == release.id)
    )
    if (placement_count or 0) != sum(row.persist_placement for row in rows):
        _raise_replay_conflict("locus placements")

    expected_issue_keys = {
        _quarantine_issue_key(request, row, index)
        for row in rows
        for index, _ in enumerate(row.issues)
    }
    actual_issue_keys = set(
        session.scalars(
            select(QuarantineIssue.issue_key)
            .join(ImportLedger, QuarantineIssue.ledger_id == ImportLedger.id)
            .where(ImportLedger.run_id == existing_run.id)
        ).all()
    )
    if actual_issue_keys != expected_issue_keys:
        _raise_replay_conflict("quarantine issues")

    assembly_membership_count = session.scalar(
        select(func.count())
        .select_from(ReleaseAssemblyMembership)
        .where(ReleaseAssemblyMembership.release_id == release.id)
    )
    if (assembly_membership_count or 0) != len(DATA_S1_ASSEMBLY_ALLOWLIST):
        _raise_replay_conflict("assembly memberships")
    taxon_assignment_count = session.scalar(
        select(func.count())
        .select_from(AssemblyTaxonAssignment)
        .where(AssemblyTaxonAssignment.release_id == release.id)
    )
    if (taxon_assignment_count or 0) != len(DATA_S1_ASSEMBLY_ALLOWLIST):
        _raise_replay_conflict("assembly source TaxId assignments")
    lineage_pin_count = session.scalar(
        select(func.count())
        .select_from(ReleaseLineageSnapshot)
        .where(ReleaseLineageSnapshot.release_id == release.id)
    )
    if (lineage_pin_count or 0) != 2:
        _raise_replay_conflict("release lineage snapshots")

    forbidden_counts = {
        "flank assessments": session.scalar(
            select(func.count())
            .select_from(FlankAssessment)
            .where(FlankAssessment.release_id == release.id)
        ),
        "inclusion decisions": session.scalar(
            select(func.count())
            .select_from(InclusionDecision)
            .where(InclusionDecision.release_id == release.id)
        ),
        "public locus memberships": session.scalar(
            select(func.count())
            .select_from(ReleaseLocusMembership)
            .where(ReleaseLocusMembership.release_id == release.id)
        ),
        "public assertion memberships": session.scalar(
            select(func.count())
            .select_from(ReleaseAssertionMembership)
            .where(ReleaseAssertionMembership.release_id == release.id)
        ),
    }
    present_forbidden = [label for label, count in forbidden_counts.items() if (count or 0) != 0]
    if present_forbidden:
        raise StagingConflictError(
            "replay_publication_boundary_violated",
            "staging replay found forbidden release rows: " + ", ".join(present_forbidden),
        )


def _raise_replay_conflict(component: str) -> None:
    raise StagingConflictError(
        "replay_ledger_mismatch",
        f"existing run does not reproduce the expected {component}",
    )


def _require_empty_release(session: Session, release_id: int) -> None:
    existing_ledgers = session.scalar(
        select(func.count()).select_from(ImportLedger).where(ImportLedger.release_id == release_id)
    )
    existing_calls = session.scalar(
        select(func.count())
        .select_from(DetectionCall)
        .where(DetectionCall.release_id == release_id)
    )
    if (existing_ledgers or 0) > 0 or (existing_calls or 0) > 0:
        raise StagingConflictError(
            "release_already_staged",
            "candidate release already contains a different staging run",
        )


def _get_or_create[ModelT: Base](
    session: Session,
    model: type[ModelT],
    predicate: ColumnElement[bool],
    values: Mapping[str, Any],
    compare_fields: Sequence[str],
    count_name: str,
    created: Counter[str],
    reused: Counter[str],
) -> tuple[ModelT, bool]:
    existing = session.scalar(select(model).where(predicate))
    if existing is not None:
        _assert_same(existing, values, compare_fields, count_name)
        reused[count_name] += 1
        return existing, False
    entity = model()
    for field_name, value in values.items():
        setattr(entity, field_name, value)
    session.add(entity)
    session.flush()
    created[count_name] += 1
    return entity, True


def _assert_same(
    entity: object,
    expected: Mapping[str, Any],
    fields: Sequence[str],
    entity_name: str,
) -> None:
    mismatches = [
        field_name for field_name in fields if getattr(entity, field_name) != expected[field_name]
    ]
    if mismatches:
        raise StagingConflictError(
            "immutable_row_conflict",
            f"existing {entity_name} differs in: {', '.join(mismatches)}",
        )


def _load_source_records(
    session: Session, snapshot_id: int, artifact_id: int
) -> dict[str, SourceRecord]:
    records = session.scalars(
        select(SourceRecord).where(
            (SourceRecord.snapshot_id == snapshot_id) & (SourceRecord.artifact_id == artifact_id)
        )
    ).all()
    return {record.source_record_key: record for record in records}


def _persist_source_snapshot(
    session: Session,
    spec: SourceSnapshotSpec,
    created: Counter[str],
    reused: Counter[str],
) -> SourceSnapshot:
    snapshot, _ = _get_or_create(
        session,
        SourceSnapshot,
        SourceSnapshot.snapshot_key == spec.snapshot_key,
        {
            "snapshot_key": spec.snapshot_key,
            "source_name": spec.source_name,
            "source_version": spec.source_version,
            "source_uri": spec.source_uri,
            "retrieved_at": spec.retrieved_at,
            "declared_manifest_sha256": spec.declared_manifest_sha256,
            "verified_manifest_sha256": spec.verified_manifest_sha256,
            "declared_license_key": spec.declared_license_key,
            "verified_license_key": spec.verified_license_key,
        },
        (
            "snapshot_key",
            "source_name",
            "source_version",
            "source_uri",
            "retrieved_at",
            "declared_manifest_sha256",
            "verified_manifest_sha256",
            "declared_license_key",
            "verified_license_key",
        ),
        "source_snapshot",
        created,
        reused,
    )
    return snapshot


def _artifact_specs(request: DataS1StagingRequest) -> tuple[SourceArtifactSpec, ...]:
    return (
        request.data_artifact,
        request.assembly_report_artifact,
        request.sequence_report_artifact,
    )


def _artifact_values(snapshot_id: int, artifact: SourceArtifactSpec) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "artifact_key": artifact.artifact_key,
        "filename": artifact.filename,
        "media_type": artifact.media_type,
        "byte_size": artifact.byte_size,
        "declared_sha256": artifact.declared_sha256,
        "verified_sha256": artifact.verified_sha256,
        "source_uri": artifact.source_uri,
        "retrieved_at": artifact.retrieved_at,
        "declared_license_key": artifact.declared_license_key,
        "verified_license_key": artifact.verified_license_key,
        "remote_checksum_verified": artifact.remote_checksum_verified,
        "remote_verification_at": artifact.remote_verification_at,
        "remote_verification_uri": artifact.remote_verification_uri,
    }


def _run_parameters(request: DataS1StagingRequest) -> dict[str, Any]:
    return {
        "artifact_sha256": request.data_artifact.verified_sha256,
        "assembly_allowlist": sorted(DATA_S1_ASSEMBLY_ALLOWLIST),
        "assembly_report_sha256": request.assembly_report_artifact.verified_sha256,
        "expected_summary": {
            "call_key_set_sha256": request.expectation.call_key_set_sha256,
            "locus_key_set_sha256": request.expectation.locus_key_set_sha256,
            "loci": request.expectation.loci,
            "normalized_candidates": request.expectation.normalized_candidates,
            "placements": request.expectation.placements,
            "quarantine_issues": request.expectation.quarantine_issues,
            "quarantined_rows": request.expectation.quarantined_rows,
            "source_high": request.expectation.source_high,
            "source_low": request.expectation.source_low,
            "source_records": request.expectation.source_records,
        },
        "identity_policy": DATA_S1_IDENTITY_POLICY_KEY,
        "resolution_snapshot_key": request.resolution_snapshot.snapshot_key,
        "resolution_assembly_records": request.resolution_index.assembly_report_records,
        "resolution_sequence_records": request.resolution_index.sequence_report_records,
        "sequence_report_sha256": request.sequence_report_artifact.verified_sha256,
        "source_assessment_scheme": DATA_S1_SOURCE_ASSESSMENT_SCHEME,
        "source_snapshot_key": request.source_snapshot.snapshot_key,
        "worksheet": request.worksheet,
    }


def _source_locator(outcome: DataS1ImportOutcome) -> dict[str, Any]:
    return {
        "excel_row": outcome.locator.excel_row,
        "label": outcome.locator.label,
        "worksheet": outcome.locator.worksheet,
    }


def _process_run_key(request: DataS1StagingRequest) -> str:
    return stable_key("process-run:data-s1", {"import_run_key": request.execution.run_key})


def _study_lineage_payload(request: DataS1StagingRequest) -> dict[str, Any]:
    return {
        "authority_namespace": "zhao-biorxiv-v4-viral-major-taxon",
        "source_artifact_sha256": request.data_artifact.verified_sha256,
        "terms": [_VIRAL_MAJOR_TAXON],
        "version": request.source_snapshot.source_version,
    }


def _study_lineage_snapshot_key(request: DataS1StagingRequest) -> str:
    return stable_key("lineage-snapshot:study-viral", _study_lineage_payload(request))


def _assertion_key(request: DataS1StagingRequest, row: _PreparedRow, assertion_type: str) -> str:
    if assertion_type == "hcvr":
        predicate_key = "source:hcvr"
        asserted_object: dict[str, Any] = {
            "asserted_value": row.source_label,
            "source_confidence": row.outcome.source_assessment,
        }
        scheme_snapshot = {
            "kind": "source_assessment_scheme",
            "key": row.outcome.source_assessment_scheme,
        }
    elif assertion_type == "viral_major_taxon":
        predicate_key = "source:viral-major-taxon"
        asserted_object = {
            "asserted_value": row.viral_major_taxon,
            "lineage_term_key": "study-viral-major-taxon:orthopolintovirales",
        }
        scheme_snapshot = {
            "kind": "lineage_snapshot",
            "key": _study_lineage_snapshot_key(request),
        }
    elif assertion_type == "vr_type":
        predicate_key = "source:vr-type"
        asserted_object = {"asserted_value": row.vr_type}
        scheme_snapshot = {
            "kind": "source_snapshot",
            "key": request.source_snapshot.snapshot_key,
        }
    else:
        raise StagingInputError(
            "unsupported_assertion_type",
            f"cannot derive key for assertion type {assertion_type!r}",
        )
    return stable_key(
        "assertion:eve:v1",
        {
            "assertion_type": assertion_type,
            "method_run_key": _process_run_key(request),
            "object": asserted_object,
            "predicate_key": predicate_key,
            "scheme_snapshot": scheme_snapshot,
            "source_locator": _source_locator(row.outcome),
            "subject": {
                "call_key": row.outcome.record_key,
                "locus_key": (row.outcome.locus_key if row.persist_locus else None),
            },
        },
    )


def _evidence_key(request: DataS1StagingRequest, record_key: str) -> str:
    return stable_key(
        "evidence:data-s1-row",
        {"record_key": record_key, "release_key": request.release.release_key},
    )


def _quarantine_issue_key(request: DataS1StagingRequest, row: _PreparedRow, index: int) -> str:
    issue = row.issues[index]
    return stable_key(
        "quarantine-issue:data-s1",
        {
            "code": issue.code,
            "field": issue.field,
            "ordinal": index,
            "record_key": row.outcome.record_key,
            "run_key": request.execution.run_key,
        },
    )


def _result(
    request: DataS1StagingRequest,
    rows: Sequence[_PreparedRow],
    replayed: bool,
    created: Counter[str],
    reused: Counter[str],
) -> StagingPersistenceResult:
    normalized = sum(row.terminal_outcome == _NORMALIZED for row in rows)
    quarantined = len(rows) - normalized
    accounted = sum(
        issue.code == "viral_contig_policy_quarantine" and issue.status == "resolved"
        for row in rows
        for issue in row.issues
    )
    open_issues = sum(issue.status == "open" for row in rows for issue in row.issues)
    return StagingPersistenceResult(
        run_key=request.execution.run_key,
        release_key=request.release.release_key,
        replayed=replayed,
        input_rows=len(rows),
        normalized_candidates=normalized,
        quarantined_rows=quarantined,
        accounted_policy_quarantines=accounted,
        open_quarantine_issues=open_issues,
        created_counts=MappingProxyType(dict(sorted(created.items()))),
        reused_counts=MappingProxyType(dict(sorted(reused.items()))),
    )


def _positive_integer_or_none(value: str) -> int | None:
    parsed = _nonnegative_integer_or_none(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_integer_or_none(value: str) -> int | None:
    if not value or not value.isascii() or not value.isdigit():
        return None
    return int(value)


def _require_sha256(name: str, value: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise StagingInputError("invalid_sha256", f"{name} must be a full lowercase SHA-256 digest")


def _require_token(name: str, value: str) -> None:
    if not value or value != value.strip():
        raise StagingInputError("invalid_token", f"{name} must be a non-empty exact token")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StagingInputError("naive_datetime", f"{name} must be timezone-aware")
