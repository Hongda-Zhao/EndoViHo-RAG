"""Reproducible full-source entry point for Milestone 1 candidate staging."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy.orm import Session

from eve_relation_rag.contracts.source_manifest import (
    Milestone1SourceManifest,
    load_source_manifest,
)
from eve_relation_rag.domain.keys import canonical_json_sha256, stable_key
from eve_relation_rag.importers.audit import (
    APPROVED_DATA_S1_EXPECTED_COUNTS,
    APPROVED_DATA_S1_KEY_DIGESTS,
    DataS1AuditReport,
    audit_data_s1_outcomes,
)
from eve_relation_rag.importers.data_s1 import (
    DATA_S1_ARTIFACT_BYTE_SIZE,
    DATA_S1_ARTIFACT_SHA256,
    DATA_S1_ASSEMBLY_ALLOWLIST,
    DATA_S1_METHOD_RUN_IDENTITY,
    DATA_S1_SOURCE_SNAPSHOT_KEY,
    NcbiResolutionIndex,
    iter_canonical_data_s1_import,
    verify_file_bytes,
)
from eve_relation_rag.ingestion.staging import (
    AssemblySpec,
    DataS1StagingRequest,
    DatasetReleaseSpec,
    ImportExecutionSpec,
    SourceArtifactSpec,
    SourceSnapshotSpec,
    StagingExpectation,
    StagingPersistenceResult,
    persist_data_s1_staging,
)

MILESTONE1_ENTRY_SCHEMA: Final = "endoviho-milestone1-staging-entry-v1"
APPROVED_MANIFEST_SHA256: Final = (
    "afa5982542c592aaec6ec1033e0ac9ebbd3786e881baed0d81a1a602a30adf0d"
)
APPROVED_IMPORTER_SHA256: Final = (
    "e9ff3cfcbcb3f20a6971b245ddd2d7fbbbe552a96021f33c8ac70a6f8c7be514"
)
APPROVED_AUDIT_MODULE_SHA256: Final = (
    "93cbead58cdfca828a97c33bed1ab21a6d31c6115e52835a67e139f40f640b98"
)
APPROVED_STAGING_MODULE_SHA256: Final = (
    "d66113be75cd02dc5353fb2cce7784d0a7e6c7f6597cf89ed805913569a97ffb"
)
APPROVED_EXECUTION_CODE_SHA256: Final = canonical_json_sha256(
    {
        "audit_module_sha256": APPROVED_AUDIT_MODULE_SHA256,
        "importer_sha256": APPROVED_IMPORTER_SHA256,
        "staging_module_sha256": APPROVED_STAGING_MODULE_SHA256,
    }
)
DEFAULT_RELEASE_KEY: Final = "release:endoviho-rag:v0:20260826:001"
DEFAULT_DATASET_KEY: Final = "dataset:endoviho-rag"
DEFAULT_DATASET_TITLE: Final = "EndoViHo RAG"
DEFAULT_SCHEMA_VERSION: Final = "milestone-1-v1"
_IMPORTER_VERSION: Final = DATA_S1_METHOD_RUN_IDENTITY
_SOFTWARE_AGENT_KEY: Final = "eve-relation-rag:milestone1-stage-v1"
_NCBI_DATASETS_API: Final = (
    "https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession"
)

APPROVED_STAGING_EXPECTATION: Final = StagingExpectation(
    source_records=39_495,
    source_high=71,
    source_low=39_424,
    normalized_candidates=38_968,
    quarantined_rows=527,
    loci=39_495,
    placements=38_968,
    quarantine_issues=527,
    call_key_set_sha256=APPROVED_DATA_S1_KEY_DIGESTS["sorted_call_keys_sha256"],
    locus_key_set_sha256=APPROVED_DATA_S1_KEY_DIGESTS["sorted_locus_keys_sha256"],
)


class Milestone1EntryError(RuntimeError):
    """Fail-closed orchestration error with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FrozenMilestone1Inputs:
    """Paths to the four ignored/frozen inputs consumed by the full run."""

    __slots__ = (
        "assembly_report_path",
        "manifest_path",
        "sequence_report_path",
        "workbook_path",
    )

    def __init__(
        self,
        *,
        manifest_path: str | Path,
        workbook_path: str | Path,
        assembly_report_path: str | Path,
        sequence_report_path: str | Path,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.workbook_path = Path(workbook_path)
        self.assembly_report_path = Path(assembly_report_path)
        self.sequence_report_path = Path(sequence_report_path)


def stage_milestone1(
    session: Session,
    inputs: FrozenMilestone1Inputs,
    *,
    release_key: str = DEFAULT_RELEASE_KEY,
    batch_size: int = 1_000,
) -> dict[str, object]:
    """Verify, audit, and atomically stage the complete frozen pilot source.

    No public release membership is created. The deterministic release/run
    identities make an exact replay a read/verify operation with zero inserts.
    """

    manifest_observation = verify_file_bytes(
        inputs.manifest_path,
        expected_sha256=APPROVED_MANIFEST_SHA256,
    )
    manifest = load_source_manifest(inputs.manifest_path)
    _validate_approved_manifest(manifest)
    _verify_approved_python_sources()

    resolution = manifest.assembly_resolution
    resolution_index = NcbiResolutionIndex.from_jsonl_reports(
        inputs.assembly_report_path,
        inputs.sequence_report_path,
        expected_assembly_report_sha256=resolution.assembly_report.sha256,
        expected_assembly_report_byte_size=resolution.assembly_report.byte_size,
        expected_sequence_report_sha256=resolution.sequence_report.sha256,
        expected_sequence_report_byte_size=resolution.sequence_report.byte_size,
    )
    _validate_resolution_index(manifest, resolution_index)
    assemblies = assembly_specs_from_resolution_index(
        resolution_index,
        allowlist=manifest.selection.assembly_allowlist,
    )

    outcomes = tuple(
        iter_canonical_data_s1_import(
            inputs.workbook_path,
            source_snapshot_key=manifest.source_snapshot_key,
            identity_policy_key=manifest.identity_policy.key,
            method_run_identity=manifest.call_identity_policy.method_run_identity,
            resolution_index=resolution_index,
        )
    )
    audit = audit_data_s1_outcomes(
        outcomes,
        manifest.expected_counts.model_dump(mode="python"),
    )
    request = build_milestone1_staging_request(
        manifest,
        manifest_sha256=manifest_observation.sha256,
        resolution_index=resolution_index,
        assemblies=assemblies,
        release_key=release_key,
    )
    persistence = persist_data_s1_staging(
        session,
        request,
        outcomes,
        batch_size=batch_size,
    )
    return _machine_report(inputs, manifest, audit, persistence)


def build_milestone1_staging_request(
    manifest: Milestone1SourceManifest,
    *,
    manifest_sha256: str,
    resolution_index: NcbiResolutionIndex,
    assemblies: Sequence[AssemblySpec],
    release_key: str = DEFAULT_RELEASE_KEY,
) -> DataS1StagingRequest:
    """Project verified manifest/index objects into the final staging API."""

    if manifest_sha256 != APPROVED_MANIFEST_SHA256:
        raise Milestone1EntryError(
            "manifest_checksum_mismatch",
            "request construction requires the approved frozen manifest digest",
        )
    _validate_approved_manifest(manifest)
    _validate_resolution_index(manifest, resolution_index)
    if {item.accession_version for item in assemblies} != set(
        manifest.selection.assembly_allowlist
    ):
        raise Milestone1EntryError(
            "assembly_spec_allowlist_mismatch",
            "assembly specifications do not exactly cover the frozen allow-list",
        )

    artifact = manifest.artifact
    retrieved_at = _parse_datetime(artifact.retrieved_at, "artifact.retrieved_at")
    remote_verification_at = _parse_datetime(
        _http_metadata_text(artifact.http_metadata, "headers_verified_at"),
        "artifact.http_metadata.headers_verified_at",
    )
    media_type = _http_metadata_text(artifact.http_metadata, "content_type")
    assert artifact.media_url is not None

    resolution = manifest.assembly_resolution
    resolution_retrieved_at = _parse_datetime(
        resolution.retrieved_at, "assembly_resolution.retrieved_at"
    )
    resolution_manifest_sha256 = canonical_json_sha256(
        resolution.model_dump(mode="json")
    )
    resolution_license = resolution.license_or_usage_basis.key
    execution_time = retrieved_at
    run_key = stable_key(
        "import-run:data-s1",
        {
            "artifact_sha256": artifact.sha256,
            "audit_module_sha256": APPROVED_AUDIT_MODULE_SHA256,
            "entry_schema": MILESTONE1_ENTRY_SCHEMA,
            "execution_code_sha256": APPROVED_EXECUTION_CODE_SHA256,
            "importer_sha256": APPROVED_IMPORTER_SHA256,
            "manifest_sha256": manifest_sha256,
            "release_key": release_key,
            "resolution_assembly_sha256": resolution.assembly_report.sha256,
            "resolution_sequence_sha256": resolution.sequence_report.sha256,
            "staging_module_sha256": APPROVED_STAGING_MODULE_SHA256,
        },
    )

    return DataS1StagingRequest(
        release=DatasetReleaseSpec(
            dataset_key=DEFAULT_DATASET_KEY,
            dataset_title=DEFAULT_DATASET_TITLE,
            release_key=release_key,
            schema_version=DEFAULT_SCHEMA_VERSION,
            manifest_sha256=manifest_sha256,
        ),
        source_snapshot=SourceSnapshotSpec(
            snapshot_key=manifest.source_snapshot_key,
            source_name="Zhao et al. Supplementary Data S1",
            source_version="bioRxiv 10.1101/2025.04.19.649669 v4",
            source_uri=artifact.media_url,
            retrieved_at=retrieved_at,
            declared_manifest_sha256=manifest_sha256,
            verified_manifest_sha256=manifest_sha256,
            declared_license_key=artifact.license_key,
            verified_license_key=artifact.license_key,
        ),
        data_artifact=SourceArtifactSpec(
            artifact_key=stable_key(
                "source-artifact:biorxiv-data-s1",
                {"filename": artifact.native_filename, "sha256": artifact.sha256},
            ),
            filename=artifact.native_filename,
            media_type=media_type,
            byte_size=artifact.byte_size,
            declared_sha256=artifact.sha256,
            verified_sha256=artifact.sha256,
            source_uri=artifact.media_url,
            retrieved_at=retrieved_at,
            declared_license_key=artifact.license_key,
            verified_license_key=artifact.license_key,
            remote_checksum_verified=True,
            remote_verification_at=remote_verification_at,
            remote_verification_uri=artifact.media_url,
        ),
        resolution_snapshot=SourceSnapshotSpec(
            snapshot_key=resolution.source_snapshot_key,
            source_name=resolution.authority,
            source_version=f"NCBI Datasets v2 {resolution.datasets_cli_version}",
            source_uri=_NCBI_DATASETS_API,
            retrieved_at=resolution_retrieved_at,
            declared_manifest_sha256=resolution_manifest_sha256,
            verified_manifest_sha256=resolution_manifest_sha256,
            declared_license_key=resolution_license,
            verified_license_key=resolution_license,
        ),
        assembly_report_artifact=_resolution_artifact_spec(
            resolution.source_snapshot_key,
            resolution.assembly_report,
            resolution_retrieved_at,
            resolution_license,
        ),
        sequence_report_artifact=_resolution_artifact_spec(
            resolution.source_snapshot_key,
            resolution.sequence_report,
            resolution_retrieved_at,
            resolution_license,
        ),
        assemblies=tuple(sorted(assemblies, key=lambda item: item.accession_version)),
        resolution_index=resolution_index,
        execution=ImportExecutionSpec(
            run_key=run_key,
            importer_name="eve_relation_rag.importers.data_s1",
            importer_version=_IMPORTER_VERSION,
            code_sha256=APPROVED_EXECUTION_CODE_SHA256,
            software_agent_key=_SOFTWARE_AGENT_KEY,
            started_at=execution_time,
            finished_at=execution_time,
        ),
        expectation=APPROVED_STAGING_EXPECTATION,
        worksheet=artifact.worksheet,
    )


def assembly_specs_from_resolution_index(
    index: NcbiResolutionIndex,
    *,
    allowlist: Sequence[str],
) -> tuple[AssemblySpec, ...]:
    """Project exact organism names/TaxIds from the same byte-bound report parse."""

    allowed = set(allowlist)
    if len(allowed) != 10:
        raise Milestone1EntryError(
            "assembly_allowlist_mismatch", "expected ten unique assembly accessions"
        )
    if not index.byte_bound:
        raise Milestone1EntryError(
            "resolution_index_not_byte_bound",
            "assembly specifications require a byte-bound NCBI report index",
        )
    if set(index.assembly_organisms) != allowed:
        missing = sorted(allowed.difference(index.assembly_organisms))
        raise Milestone1EntryError(
            "assembly_report_allowlist_mismatch",
            "byte-bound assembly metadata does not exactly cover the allow-list; missing="
            + ",".join(missing),
        )
    result = tuple(
        AssemblySpec(accession, *index.assembly_organisms[accession])
        for accession in sorted(allowed)
    )
    if len({item.source_tax_id for item in result}) != 9:
        raise Milestone1EntryError(
            "assembly_taxon_count_mismatch",
            "the ten assemblies must retain the frozen nine exact NCBI TaxIds",
        )
    return result


def _validate_approved_manifest(manifest: Milestone1SourceManifest) -> None:
    artifact = manifest.artifact
    resolution = manifest.assembly_resolution
    expected_counts = manifest.expected_counts.model_dump(mode="python")
    checks: tuple[tuple[bool, str], ...] = (
        (
            manifest.manifest_status
            == "staging-source-verified-release-evidence-pending",
            "manifest_status",
        ),
        (manifest.source_snapshot_key == DATA_S1_SOURCE_SNAPSHOT_KEY, "source_snapshot_key"),
        (artifact.sha256 == DATA_S1_ARTIFACT_SHA256, "artifact.sha256"),
        (artifact.byte_size == DATA_S1_ARTIFACT_BYTE_SIZE, "artifact.byte_size"),
        (artifact.worksheet == "S3", "artifact.worksheet"),
        (artifact.remote_checksum_verified is True, "artifact.remote_checksum_verified"),
        (artifact.media_url is not None, "artifact.media_url"),
        (artifact.retrieved_at is not None, "artifact.retrieved_at"),
        (
            manifest.call_identity_policy.method_run_identity
            == DATA_S1_METHOD_RUN_IDENTITY,
            "call_identity_policy.method_run_identity",
        ),
        (
            manifest.call_identity_policy.key_schema
            == "zhao-data-s1-detection-call-v2",
            "call_identity_policy.key_schema",
        ),
        (
            manifest.source_record_identity_policy.key_schema
            == "zhao-data-s1-source-record-v1",
            "source_record_identity_policy.key_schema",
        ),
        (
            set(manifest.selection.assembly_allowlist)
            == set(DATA_S1_ASSEMBLY_ALLOWLIST),
            "selection.assembly_allowlist",
        ),
        (
            expected_counts == dict(APPROVED_DATA_S1_EXPECTED_COUNTS),
            "expected_counts",
        ),
        (resolution.assembly_report.records == 10, "assembly_report.records"),
        (resolution.sequence_report.records == 220_512, "sequence_report.records"),
    )
    mismatches = [field for passed, field in checks if not passed]
    if mismatches:
        raise Milestone1EntryError(
            "noncanonical_manifest",
            "manifest differs from the approved staging contract: "
            + ", ".join(mismatches),
        )
    _http_metadata_text(artifact.http_metadata, "headers_verified_at")
    _http_metadata_text(artifact.http_metadata, "content_type")


def _validate_resolution_index(
    manifest: Milestone1SourceManifest, index: NcbiResolutionIndex
) -> None:
    resolution = manifest.assembly_resolution
    expected = {
        "assembly_report_records": resolution.assembly_report.records,
        "sequence_report_records": resolution.sequence_report.records,
        "assembly_report_sha256": resolution.assembly_report.sha256,
        "assembly_report_byte_size": resolution.assembly_report.byte_size,
        "sequence_report_sha256": resolution.sequence_report.sha256,
        "sequence_report_byte_size": resolution.sequence_report.byte_size,
    }
    observed = {name: getattr(index, name) for name in expected}
    mismatches = [
        name for name, value in expected.items() if observed[name] != value
    ]
    if set(index.assemblies) != set(manifest.selection.assembly_allowlist):
        mismatches.append("assemblies")
    if not index.byte_bound:
        mismatches.append("byte_bound")
    if set(index.assembly_organisms) != set(manifest.selection.assembly_allowlist):
        mismatches.append("assembly_organisms")
    if mismatches:
        raise Milestone1EntryError(
            "resolution_index_mismatch",
            "NCBI resolution index differs from frozen provenance: "
            + ", ".join(mismatches),
        )


def _verify_approved_python_sources() -> None:
    package_root = Path(__file__).parents[1]
    verify_file_bytes(
        package_root / "importers" / "data_s1.py",
        expected_sha256=APPROVED_IMPORTER_SHA256,
    )
    verify_file_bytes(
        package_root / "importers" / "audit.py",
        expected_sha256=APPROVED_AUDIT_MODULE_SHA256,
    )
    verify_file_bytes(
        package_root / "ingestion" / "staging.py",
        expected_sha256=APPROVED_STAGING_MODULE_SHA256,
    )


def _resolution_artifact_spec(
    source_snapshot_key: str,
    report: Any,
    retrieved_at: datetime,
    license_key: str,
) -> SourceArtifactSpec:
    filename = Path(report.local_audit_path).name
    return SourceArtifactSpec(
        artifact_key=stable_key(
            "source-artifact:ncbi-datasets-report",
            {"filename": filename, "sha256": report.sha256},
        ),
        filename=filename,
        media_type=report.media_type,
        byte_size=report.byte_size,
        declared_sha256=report.sha256,
        verified_sha256=report.sha256,
        source_uri=f"urn:{source_snapshot_key}:{filename}",
        retrieved_at=retrieved_at,
        declared_license_key=license_key,
        verified_license_key=license_key,
        remote_checksum_verified=False,
    )


def _machine_report(
    inputs: FrozenMilestone1Inputs,
    manifest: Milestone1SourceManifest,
    audit: DataS1AuditReport,
    persistence: StagingPersistenceResult,
) -> dict[str, object]:
    resolution = manifest.assembly_resolution
    return {
        "schema_version": MILESTONE1_ENTRY_SCHEMA,
        "status": "ok",
        "candidate_only": True,
        "public_release_membership_created": False,
        "release_key": persistence.release_key,
        "run_key": persistence.run_key,
        "replayed": persistence.replayed,
        "verified_inputs": {
            "manifest": {
                "path": str(inputs.manifest_path.resolve()),
                "sha256": APPROVED_MANIFEST_SHA256,
            },
            "data_s1": {
                "path": str(inputs.workbook_path.resolve()),
                "sha256": manifest.artifact.sha256,
                "byte_size": manifest.artifact.byte_size,
                "remote_checksum_verified": manifest.artifact.remote_checksum_verified,
            },
            "assembly_report": {
                "path": str(inputs.assembly_report_path.resolve()),
                "sha256": resolution.assembly_report.sha256,
                "byte_size": resolution.assembly_report.byte_size,
            },
            "sequence_report": {
                "path": str(inputs.sequence_report_path.resolve()),
                "sha256": resolution.sequence_report.sha256,
                "byte_size": resolution.sequence_report.byte_size,
            },
        },
        "tools": {
            "importer_sha256": APPROVED_IMPORTER_SHA256,
            "audit_module_sha256": APPROVED_AUDIT_MODULE_SHA256,
            "staging_module_sha256": APPROVED_STAGING_MODULE_SHA256,
            "execution_code_sha256": APPROVED_EXECUTION_CODE_SHA256,
            "ncbi_datasets_cli_version": resolution.datasets_cli_version,
            "ncbi_datasets_cli_binary_sha256": (
                resolution.datasets_cli_binary_sha256
            ),
        },
        "audit": audit.to_dict(),
        "persistence": _persistence_dict(persistence),
    }


def _persistence_dict(result: StagingPersistenceResult) -> dict[str, object]:
    return {
        "run_key": result.run_key,
        "release_key": result.release_key,
        "replayed": result.replayed,
        "input_rows": result.input_rows,
        "normalized_candidates": result.normalized_candidates,
        "quarantined_rows": result.quarantined_rows,
        "accounted_policy_quarantines": result.accounted_policy_quarantines,
        "open_quarantine_issues": result.open_quarantine_issues,
        "created_counts": dict(result.created_counts),
        "reused_counts": dict(result.reused_counts),
    }


def _http_metadata_text(metadata: Mapping[str, Any] | None, key: str) -> str:
    if metadata is None:
        raise Milestone1EntryError(
            "remote_verification_metadata_missing",
            "canonical artifact has no HTTP verification metadata",
        )
    value = metadata.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise Milestone1EntryError(
            "remote_verification_metadata_missing",
            f"canonical artifact HTTP metadata has no exact {key}",
        )
    return value


def _parse_datetime(value: str | None, field: str) -> datetime:
    if value is None:
        raise Milestone1EntryError(
            "provenance_datetime_missing", f"missing provenance datetime: {field}"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Milestone1EntryError(
            "provenance_datetime_invalid", f"invalid provenance datetime: {field}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Milestone1EntryError(
            "provenance_datetime_invalid", f"provenance datetime is not aware: {field}"
        )
    return parsed
