"""Exact database projection for the structured candidate validation request."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eve_relation_rag.activation.contracts import (
    IctvArtifactManifest,
    NcbiTaxonomyArtifactManifest,
    StructuredActivationManifest,
    TaxonomySnapshotManifest,
    canonical_revalidate,
)
from eve_relation_rag.activation.membership import M1GateEvidence
from eve_relation_rag.db.models import (
    DatasetRelease,
    ImportLedger,
    LineageSnapshot,
    QuarantineIssue,
    ReleaseLineageSnapshot,
    ReleaseSourceSnapshot,
    SourceArtifact,
    SourceSnapshot,
)
from eve_relation_rag.importers.audit import (
    APPROVED_DATA_S1_EXPECTED_COUNTS,
    APPROVED_DATA_S1_KEY_DIGESTS,
)
from eve_relation_rag.releases.dependencies import (
    project_release_membership_candidates,
)
from eve_relation_rag.releases.validator import (
    IctvReleaseEvidence,
    NcbiTaxonomyEvidence,
    ReleaseValidationRequest,
    SourceAuditEvidence,
    SourceManifestEvidence,
    validate_release,
)


class ReleaseValidationRequestExportError(RuntimeError):
    """Raised when live candidate truth cannot produce one passing request."""


def build_candidate_release_validation_request(
    session: Session,
    *,
    activation: StructuredActivationManifest,
    m1_gate: M1GateEvidence,
    ncbi_artifact: NcbiTaxonomyArtifactManifest,
    ncbi_snapshot: TaxonomySnapshotManifest,
    ictv_artifact: IctvArtifactManifest,
    ictv_snapshot: TaxonomySnapshotManifest,
) -> ReleaseValidationRequest:
    """Build and replay the request from an already staged exact candidate packet."""

    try:
        activation = canonical_revalidate(activation)
        ncbi_artifact = canonical_revalidate(ncbi_artifact)
        ncbi_snapshot = canonical_revalidate(ncbi_snapshot)
        ictv_artifact = canonical_revalidate(ictv_artifact)
        ictv_snapshot = canonical_revalidate(ictv_snapshot)
    except Exception as exc:
        raise ReleaseValidationRequestExportError(
            "validation request manifests failed canonical validation"
        ) from exc
    manifest_pairs = (
        (activation.source_manifest_sha256, m1_gate.source_manifest_sha256),
        (activation.source_audit_sha256, m1_gate.source_audit_sha256),
        (activation.ncbi_artifact_manifest_sha256, ncbi_artifact.manifest_sha256),
        (activation.ncbi_snapshot_manifest_sha256, ncbi_snapshot.manifest_sha256),
        (activation.ictv_artifact_manifest_sha256, ictv_artifact.manifest_sha256),
        (activation.ictv_snapshot_manifest_sha256, ictv_snapshot.manifest_sha256),
    )
    if any(observed != expected for observed, expected in manifest_pairs):
        raise ReleaseValidationRequestExportError(
            "validation request inputs do not belong to the activation manifest"
        )
    release = session.scalar(
        select(DatasetRelease)
        .where(DatasetRelease.release_key == activation.release_key)
        .with_for_update(read=True)
    )
    if (
        release is None
        or release.status != "candidate"
        or release.manifest_sha256 != activation.manifest_sha256
    ):
        raise ReleaseValidationRequestExportError(
            "validation request requires the exact staged candidate manifest"
        )

    source = _source_evidence(session, release.id, activation)
    _require_lineage_binding(
        session,
        release_id=release.id,
        role="assembly_source_taxonomy",
        snapshot=ncbi_snapshot,
    )
    _require_artifact_binding(
        session,
        release_id=release.id,
        artifact_key=ncbi_artifact.archive.artifact_key,
        sha256=ncbi_artifact.archive.sha256,
    )
    _require_lineage_binding(
        session,
        release_id=release.id,
        role="formal_viral_taxonomy",
        snapshot=ictv_snapshot,
    )
    _require_artifact_binding(
        session,
        release_id=release.id,
        artifact_key=ictv_artifact.msl.artifact_key,
        sha256=ictv_artifact.msl.sha256,
    )
    _require_artifact_binding(
        session,
        release_id=release.id,
        artifact_key=ictv_artifact.corrected_vmr.artifact_key,
        sha256=ictv_artifact.corrected_vmr.sha256,
    )
    accounted_quarantine = _accounted_quarantine_count(session, release.id)
    request = ReleaseValidationRequest(
        release_key=release.release_key,
        source=source,
        source_audit=SourceAuditEvidence(
            audit_schema="endoviho-milestone1-source-audit-v1",
            audit_artifact_sha256=m1_gate.source_audit_sha256,
            verified_audit_artifact_sha256=m1_gate.source_audit_sha256,
            passed=m1_gate.passed,
            expected_source_record_count=APPROVED_DATA_S1_EXPECTED_COUNTS[
                "source_records"
            ],
            observed_source_record_count=m1_gate.source_records,
            expected_accounted_quarantine_count=APPROVED_DATA_S1_EXPECTED_COUNTS[
                "vr_type_viral_contig"
            ],
            expected_call_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
                "sorted_call_keys_sha256"
            ],
            observed_call_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
                "sorted_call_keys_sha256"
            ],
            expected_locus_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
                "sorted_locus_keys_sha256"
            ],
            observed_locus_keys_sha256=APPROVED_DATA_S1_KEY_DIGESTS[
                "sorted_locus_keys_sha256"
            ],
        ),
        ncbi_taxonomy=NcbiTaxonomyEvidence(
            snapshot_key=ncbi_snapshot.snapshot_key,
            authority="NCBI Taxonomy",
            version=ncbi_snapshot.version,
            artifact_key=ncbi_artifact.archive.artifact_key,
            artifact_sha256=ncbi_artifact.archive.sha256,
            verified_artifact_sha256=ncbi_artifact.archive.sha256,
            provenance_uri=ncbi_artifact.archive.source_uri,
            usage_basis_key=ncbi_artifact.usage_policy.usage_basis_key,
            retrieved_at=_utc(ncbi_artifact.archive.retrieved_at),
            release_bound=True,
            merged_history_included=(
                ncbi_snapshot.ncbi_history is not None
                and ncbi_snapshot.ncbi_history.merged_tax_id_count > 0
            ),
            deleted_history_included=(
                ncbi_snapshot.ncbi_history is not None
                and ncbi_snapshot.ncbi_history.deleted_tax_id_count > 0
            ),
        ),
        ictv=IctvReleaseEvidence(
            msl_snapshot_key=ictv_snapshot.snapshot_key,
            msl_version=ictv_snapshot.version,
            msl_artifact_key=ictv_artifact.msl.artifact_key,
            msl_artifact_sha256=ictv_artifact.msl.sha256,
            verified_msl_artifact_sha256=ictv_artifact.msl.sha256,
            vmr_artifact_key=ictv_artifact.corrected_vmr.artifact_key,
            vmr_artifact_sha256=ictv_artifact.corrected_vmr.sha256,
            verified_vmr_artifact_sha256=ictv_artifact.corrected_vmr.sha256,
            provenance_uri=ictv_artifact.msl.source_uri,
            license_key=ictv_artifact.msl.license_key,
            retrieved_at=_utc(ictv_artifact.msl.retrieved_at),
            msl_release_bound=True,
            vmr_release_bound=True,
            vmr_corrected=(ictv_artifact.vmr_revision == "MSL41.v1.20260729"),
        ),
        candidates=project_release_membership_candidates(session, release.id),
        accounted_quarantine_count=accounted_quarantine,
    )
    report = validate_release(request)
    if not report.valid:
        codes = ",".join(issue.code for issue in report.errors)
        raise ReleaseValidationRequestExportError(
            f"projected release validation request does not pass: {codes}"
        )
    return request


def _source_evidence(
    session: Session,
    release_id: int,
    activation: StructuredActivationManifest,
) -> SourceManifestEvidence:
    row = session.execute(
        select(SourceSnapshot, SourceArtifact)
        .select_from(ReleaseSourceSnapshot)
        .join(SourceSnapshot, SourceSnapshot.id == ReleaseSourceSnapshot.source_snapshot_id)
        .join(SourceArtifact, SourceArtifact.snapshot_id == SourceSnapshot.id)
        .where(
            ReleaseSourceSnapshot.release_id == release_id,
            ReleaseSourceSnapshot.role == "data_s1_input",
        )
    ).one_or_none()
    if row is None:
        raise ReleaseValidationRequestExportError("M1 source artifact is not release-bound")
    snapshot = row.SourceSnapshot
    artifact = row.SourceArtifact
    if (
        snapshot.verified_manifest_sha256 != activation.source_manifest_sha256
        or not artifact.remote_checksum_verified
        or artifact.remote_verification_at is None
        or artifact.remote_verification_uri is None
    ):
        raise ReleaseValidationRequestExportError(
            "M1 source artifact verification is incomplete"
        )
    return SourceManifestEvidence(
        source_snapshot_key=snapshot.snapshot_key,
        manifest_sha256=activation.source_manifest_sha256,
        verified_manifest_sha256=snapshot.verified_manifest_sha256,
        artifact_key=artifact.artifact_key,
        artifact_sha256=artifact.verified_sha256,
        verified_artifact_sha256=artifact.verified_sha256,
        license_key=artifact.verified_license_key,
        verified_license_key=artifact.verified_license_key,
        provenance_uri=artifact.source_uri,
        remote_artifact_verified=artifact.remote_checksum_verified,
        remote_artifact_uri=artifact.remote_verification_uri,
        remote_retrieved_at=artifact.remote_verification_at,
    )


def _require_lineage_binding(
    session: Session,
    *,
    release_id: int,
    role: str,
    snapshot: TaxonomySnapshotManifest,
) -> None:
    observed = session.execute(
        select(LineageSnapshot.snapshot_key, LineageSnapshot.snapshot_sha256)
        .join(
            ReleaseLineageSnapshot,
            ReleaseLineageSnapshot.snapshot_id == LineageSnapshot.id,
        )
        .where(
            ReleaseLineageSnapshot.release_id == release_id,
            ReleaseLineageSnapshot.role == role,
        )
    ).one_or_none()
    if observed != (snapshot.snapshot_key, snapshot.manifest_sha256):
        raise ReleaseValidationRequestExportError(
            "taxonomy lineage snapshot is not exactly release-bound"
        )


def _require_artifact_binding(
    session: Session,
    *,
    release_id: int,
    artifact_key: str,
    sha256: str,
) -> None:
    observed = session.execute(
        select(SourceArtifact.artifact_key, SourceArtifact.verified_sha256)
        .join(SourceSnapshot, SourceSnapshot.id == SourceArtifact.snapshot_id)
        .join(
            ReleaseSourceSnapshot,
            ReleaseSourceSnapshot.source_snapshot_id == SourceSnapshot.id,
        )
        .where(
            ReleaseSourceSnapshot.release_id == release_id,
            SourceArtifact.artifact_key == artifact_key,
        )
    ).one_or_none()
    if observed != (artifact_key, sha256):
        raise ReleaseValidationRequestExportError(
            "taxonomy source artifact is not exactly release-bound"
        )


def _accounted_quarantine_count(session: Session, release_id: int) -> int:
    terminal = int(
        session.scalar(
            select(func.count())
            .select_from(ImportLedger)
            .where(
                ImportLedger.release_id == release_id,
                ImportLedger.outcome == "quarantine",
            )
        )
        or 0
    )
    evidenced = int(
        session.scalar(
            select(func.count(func.distinct(ImportLedger.id)))
            .select_from(ImportLedger)
            .join(QuarantineIssue, QuarantineIssue.ledger_id == ImportLedger.id)
            .where(
                ImportLedger.release_id == release_id,
                ImportLedger.outcome == "quarantine",
            )
        )
        or 0
    )
    if terminal != evidenced:
        raise ReleaseValidationRequestExportError(
            "terminal quarantine ledgers lack structured issue evidence"
        )
    return terminal


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ReleaseValidationRequestExportError("artifact timestamp is not UTC")
    return parsed


__all__ = [
    "ReleaseValidationRequestExportError",
    "build_candidate_release_validation_request",
]
