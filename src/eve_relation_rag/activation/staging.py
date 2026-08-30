"""Atomic, candidate-only import of an exact structured activation packet."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    FLANK_WINDOW_BP,
    AdjudicationCohortManifest,
    AssemblyTaxonAssignmentManifest,
    FlankEvidenceManifest,
    FlankEvidenceRequestPlan,
    FullSequenceBundleManifest,
    IctvArtifactManifest,
    InclusionDecisionManifest,
    NcbiTaxonomyArtifactManifest,
    PublicAssertionMembershipManifest,
    PublicLocusMembershipManifest,
    StructuredActivationManifest,
    StructuredAdjudicationManifest,
    StudyFormalMappingManifest,
    TaxonomySnapshotManifest,
    canonical_model_sha256,
    canonical_revalidate,
)
from eve_relation_rag.activation.taxonomy import (
    import_taxonomy_snapshot,
    validate_study_formal_mapping,
)
from eve_relation_rag.db.models import (
    AssemblySequence,
    AssertionEvidence,
    DatasetRelease,
    EVELocus,
    EVELocusPlacement,
    EvidenceItem,
    FlankAssessment,
    GenomeAssembly,
    ImportLedger,
    InclusionDecision,
    ReleaseAssertionMembership,
    ReleaseLocusMembership,
    ReleaseSourceSnapshot,
    ScientificAssertion,
    SourceArtifact,
    SourceRecord,
    SourceSnapshot,
)
from eve_relation_rag.domain.keys import stable_key
from eve_relation_rag.releases.dependencies import release_dependency_graph_sha256

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SEQUENCE_ROLE = "flank_sequence_evidence"
_SEQUENCE_LICENSE = "NCBI-PUBLIC-DOMAIN-US-GOVERNMENT-WORK"


class StructuredActivationStagingError(RuntimeError):
    """Raised when a candidate packet is incomplete, drifting, or non-idempotent."""


class StructuredActivationStagingReport(BaseModel):
    """Self-checksummed result of one atomic candidate-only staging transaction."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    report_schema_version: Literal["structured-activation-staging-report-v1"]
    release_key: str
    release_status: Literal["candidate"]
    structured_activation_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    dependency_graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    ncbi_term_count: int = Field(gt=0)
    ictv_term_count: int = Field(gt=0)
    taxonomy_assignment_count: int = Field(gt=0)
    mapping_count: int = Field(gt=0)
    flank_assessment_count: int = Field(gt=0)
    inclusion_decision_count: int = Field(gt=0)
    public_locus_membership_count: int = Field(gt=0)
    public_assertion_membership_count: int = Field(gt=0)
    created_row_count: int = Field(ge=0)
    replayed_row_count: int = Field(ge=0)
    publication_performed: Literal[False]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        payload = self.model_dump(mode="json")
        del payload["report_sha256"]
        if self.report_sha256 != canonical_model_sha256(payload):
            raise ValueError("staging report checksum does not match")
        return self


def stage_structured_activation_candidate(
    session: Session,
    *,
    expected_activation_manifest_sha256: str,
    ncbi_artifact: NcbiTaxonomyArtifactManifest,
    ncbi_snapshot: TaxonomySnapshotManifest,
    assembly_assignments: AssemblyTaxonAssignmentManifest,
    ictv_artifact: IctvArtifactManifest,
    ictv_snapshot: TaxonomySnapshotManifest,
    study_formal_mapping: StudyFormalMappingManifest,
    cohort: AdjudicationCohortManifest,
    full_sequence_bundle: FullSequenceBundleManifest,
    flank_request_plan: FlankEvidenceRequestPlan,
    flanks: FlankEvidenceManifest,
    inclusions: InclusionDecisionManifest,
    adjudication: StructuredAdjudicationManifest,
    public_loci: PublicLocusMembershipManifest,
    public_assertions: PublicAssertionMembershipManifest,
    activation: StructuredActivationManifest,
) -> StructuredActivationStagingReport:
    """Stage one exact packet without committing, validating, or publishing it.

    The caller owns the transaction.  Exact replay is idempotent.  Any partial or
    semantically different existing row aborts the complete transaction.
    """

    packet = _canonical_packet(
        ncbi_artifact=ncbi_artifact,
        ncbi_snapshot=ncbi_snapshot,
        assembly_assignments=assembly_assignments,
        ictv_artifact=ictv_artifact,
        ictv_snapshot=ictv_snapshot,
        study_formal_mapping=study_formal_mapping,
        cohort=cohort,
        full_sequence_bundle=full_sequence_bundle,
        flank_request_plan=flank_request_plan,
        flanks=flanks,
        inclusions=inclusions,
        adjudication=adjudication,
        public_loci=public_loci,
        public_assertions=public_assertions,
        activation=activation,
    )
    if activation.manifest_sha256 != expected_activation_manifest_sha256:
        raise StructuredActivationStagingError(
            "expected structured activation checksum does not match"
        )
    _validate_packet_bindings(**packet)

    release = session.scalar(
        select(DatasetRelease)
        .where(DatasetRelease.release_key == ACTIVATION_RELEASE_KEY)
        .with_for_update()
    )
    if release is None or release.status != "candidate":
        raise StructuredActivationStagingError(
            "structured staging requires the exact candidate release"
        )
    allowed_manifests = {
        activation.source_manifest_sha256,
        activation.manifest_sha256,
    }
    if release.manifest_sha256 not in allowed_manifests:
        raise StructuredActivationStagingError(
            "candidate release manifest is neither the M1 baseline nor this activation"
        )

    ncbi_report = import_taxonomy_snapshot(
        session,
        artifact_manifest=ncbi_artifact,
        snapshot_manifest=ncbi_snapshot,
        assignment_manifest=assembly_assignments,
        replace_candidate_placeholder=True,
    )
    ictv_report = import_taxonomy_snapshot(
        session,
        artifact_manifest=ictv_artifact,
        snapshot_manifest=ictv_snapshot,
    )
    mapping_report = validate_study_formal_mapping(session, study_formal_mapping)
    sequence_artifact, source_created = _stage_sequence_bundle(
        session, release, full_sequence_bundle
    )
    created, replayed = _stage_policy_rows(
        session,
        release=release,
        sequence_artifact=sequence_artifact,
        cohort=cohort,
        flanks=flanks,
        inclusions=inclusions,
        adjudication=adjudication,
        public_loci=public_loci,
        public_assertions=public_assertions,
    )
    created += source_created
    replayed += 3 - source_created

    release.manifest_sha256 = activation.manifest_sha256
    session.flush()
    graph_sha256 = release_dependency_graph_sha256(session, release.id)
    counts = _terminal_counts(session, release.id)
    if counts != (
        len(tuple(row for row in flanks.records if row.placement_key is not None)) * 2,
        len(inclusions.decisions),
        public_loci.membership_count,
        public_assertions.membership_count,
    ):
        raise StructuredActivationStagingError(
            "staged terminal counts differ from the activation packet"
        )
    payload: dict[str, object] = {
        "report_schema_version": "structured-activation-staging-report-v1",
        "release_key": release.release_key,
        "release_status": "candidate",
        "structured_activation_manifest_sha256": activation.manifest_sha256,
        "dependency_graph_sha256": graph_sha256,
        "ncbi_term_count": ncbi_report.term_count,
        "ictv_term_count": ictv_report.term_count,
        "taxonomy_assignment_count": ncbi_report.assignment_count,
        "mapping_count": mapping_report.mapping_count,
        "flank_assessment_count": counts[0],
        "inclusion_decision_count": counts[1],
        "public_locus_membership_count": counts[2],
        "public_assertion_membership_count": counts[3],
        "created_row_count": created,
        "replayed_row_count": replayed,
        "publication_performed": False,
    }
    payload["report_sha256"] = canonical_model_sha256(payload)
    return StructuredActivationStagingReport.model_validate(payload)


def _canonical_packet(**values: BaseModel) -> dict[str, Any]:
    try:
        return {name: canonical_revalidate(value) for name, value in values.items()}
    except Exception as exc:
        raise StructuredActivationStagingError(
            "structured activation packet failed canonical validation"
        ) from exc


def _validate_packet_bindings(**packet: Any) -> None:
    activation: StructuredActivationManifest = packet["activation"]
    expected = (
        (activation.ncbi_artifact_manifest_sha256, packet["ncbi_artifact"].manifest_sha256),
        (activation.ncbi_snapshot_manifest_sha256, packet["ncbi_snapshot"].manifest_sha256),
        (
            activation.assembly_taxon_assignment_manifest_sha256,
            packet["assembly_assignments"].manifest_sha256,
        ),
        (activation.ictv_artifact_manifest_sha256, packet["ictv_artifact"].manifest_sha256),
        (activation.ictv_snapshot_manifest_sha256, packet["ictv_snapshot"].manifest_sha256),
        (
            activation.study_formal_mapping_manifest_sha256,
            packet["study_formal_mapping"].manifest_sha256,
        ),
        (activation.cohort_manifest_sha256, packet["cohort"].manifest_sha256),
        (
            activation.full_sequence_bundle_manifest_sha256,
            packet["full_sequence_bundle"].manifest_sha256,
        ),
        (
            activation.flank_request_plan_manifest_sha256,
            packet["flank_request_plan"].manifest_sha256,
        ),
        (activation.flank_manifest_sha256, packet["flanks"].manifest_sha256),
        (activation.inclusion_manifest_sha256, packet["inclusions"].manifest_sha256),
        (activation.adjudication_manifest_sha256, packet["adjudication"].manifest_sha256),
        (
            activation.public_locus_membership_manifest_sha256,
            packet["public_loci"].manifest_sha256,
        ),
        (
            activation.public_assertion_membership_manifest_sha256,
            packet["public_assertions"].manifest_sha256,
        ),
        (activation.source_manifest_sha256, packet["cohort"].source_manifest_sha256),
        (activation.source_audit_sha256, packet["cohort"].source_audit_sha256),
        (packet["flanks"].cohort_manifest_sha256, packet["cohort"].manifest_sha256),
        (
            packet["flanks"].request_plan_manifest_sha256,
            packet["flank_request_plan"].manifest_sha256,
        ),
        (packet["inclusions"].cohort_manifest_sha256, packet["cohort"].manifest_sha256),
        (packet["inclusions"].flank_manifest_sha256, packet["flanks"].manifest_sha256),
        (
            packet["adjudication"].inclusion_manifest_sha256,
            packet["inclusions"].manifest_sha256,
        ),
        (
            packet["public_loci"].adjudication_manifest_sha256,
            packet["adjudication"].manifest_sha256,
        ),
        (
            packet["public_assertions"].locus_membership_manifest_sha256,
            packet["public_loci"].manifest_sha256,
        ),
    )
    if any(observed != required for observed, required in expected):
        raise StructuredActivationStagingError(
            "structured activation packet manifests do not form one graph"
        )
    decisions = {row.decision_sha256: row for row in packet["inclusions"].decisions}
    if {row.decision_sha256 for row in packet["adjudication"].selections} != set(decisions):
        raise StructuredActivationStagingError(
            "adjudication selection does not equal the inclusion decision set"
        )
    included = {row.locus_key for row in decisions.values() if row.decision == "include"}
    public = {row.locus_key for row in packet["public_loci"].memberships}
    if included != public:
        raise StructuredActivationStagingError(
            "public locus membership does not equal all include decisions"
        )


def _stage_sequence_bundle(
    session: Session,
    release: DatasetRelease,
    bundle: FullSequenceBundleManifest,
) -> tuple[SourceArtifact, int]:
    snapshot_key = stable_key(
        "source-snapshot:ncbi-full-sequence-bundle",
        {"manifest_sha256": bundle.manifest_sha256},
    )
    retrieved_at = _utc(bundle.retrieved_at)
    snapshot_values = {
        "snapshot_key": snapshot_key,
        "source_name": "NCBI nuccore full-sequence bundle",
        "source_version": bundle.retrieved_at,
        "source_uri": bundle.source_uri,
        "retrieved_at": retrieved_at,
        "declared_manifest_sha256": None,
        "verified_manifest_sha256": bundle.manifest_sha256,
        "declared_license_key": None,
        "verified_license_key": _SEQUENCE_LICENSE,
    }
    snapshot = session.scalar(
        select(SourceSnapshot).where(SourceSnapshot.snapshot_key == snapshot_key)
    )
    created = 0
    if snapshot is None:
        snapshot = SourceSnapshot(**snapshot_values)
        session.add(snapshot)
        session.flush()
        created += 1
    else:
        _require_values(snapshot, snapshot_values, "sequence source snapshot")

    artifact_key = stable_key(
        "source-artifact:ncbi-full-sequence-bundle",
        {
            "manifest_sha256": bundle.manifest_sha256,
            "sha256": bundle.artifact_sha256,
        },
    )
    artifact_values = {
        "snapshot_id": snapshot.id,
        "artifact_key": artifact_key,
        "filename": "source_high_full_sequences.json",
        "media_type": "application/json",
        "byte_size": bundle.artifact_byte_size,
        "declared_sha256": None,
        "verified_sha256": bundle.artifact_sha256,
        "source_uri": bundle.source_uri,
        "retrieved_at": retrieved_at,
        "declared_license_key": None,
        "verified_license_key": _SEQUENCE_LICENSE,
        "remote_checksum_verified": False,
        "remote_verification_at": None,
        "remote_verification_uri": None,
    }
    artifact = session.scalar(
        select(SourceArtifact).where(SourceArtifact.artifact_key == artifact_key)
    )
    if artifact is None:
        artifact = SourceArtifact(**artifact_values)
        session.add(artifact)
        session.flush()
        created += 1
    else:
        _require_values(artifact, artifact_values, "sequence source artifact")
    binding = session.scalar(
        select(ReleaseSourceSnapshot).where(
            ReleaseSourceSnapshot.release_id == release.id,
            ReleaseSourceSnapshot.role == _SEQUENCE_ROLE,
        )
    )
    binding_values = {
        "release_id": release.id,
        "source_snapshot_id": snapshot.id,
        "role": _SEQUENCE_ROLE,
    }
    if binding is None:
        binding = ReleaseSourceSnapshot(**binding_values)
        session.add(binding)
        session.flush()
        created += 1
    else:
        _require_values(binding, binding_values, "sequence release binding")
    return artifact, created


def _stage_policy_rows(
    session: Session,
    *,
    release: DatasetRelease,
    sequence_artifact: SourceArtifact,
    cohort: AdjudicationCohortManifest,
    flanks: FlankEvidenceManifest,
    inclusions: InclusionDecisionManifest,
    adjudication: StructuredAdjudicationManifest,
    public_loci: PublicLocusMembershipManifest,
    public_assertions: PublicAssertionMembershipManifest,
) -> tuple[int, int]:
    del adjudication  # Its exact identity and decision set were checked above.
    records = {
        row.locus_key: row
        for row in (
            *cohort.primary_records,
            *(item for queue in cohort.expansion_queues for item in queue.records),
        )
    }
    flank_by_locus = {row.locus_key: row for row in flanks.records}
    public_by_locus = {row.locus_key: row for row in public_loci.memberships}
    created = replayed = 0
    public_rows: dict[str, tuple[EVELocus, EVELocusPlacement, InclusionDecision]] = {}
    for decision in inclusions.decisions:
        record = records.get(decision.locus_key)
        flank = flank_by_locus.get(decision.locus_key)
        if record is None or flank is None:
            raise StructuredActivationStagingError(
                "inclusion decision lacks its exact cohort or flank record"
            )
        locus, placement, ledger = _resolve_candidate_row(
            session, release.id, record, decision.import_outcome
        )
        side_rows: dict[str, FlankAssessment] = {}
        if placement is not None:
            for side_name in ("left", "right"):
                row, was_created = _stage_flank(
                    session,
                    release_id=release.id,
                    locus=locus,
                    placement=placement,
                    artifact=sequence_artifact,
                    flank=flank,
                    side_name=side_name,
                )
                side_rows[side_name] = row
                created += was_created
                replayed += 1 - was_created
        elif decision.decision == "include":
            raise StructuredActivationStagingError("include decision has no exact placement")

        decision_row, was_created = _stage_decision(
            session,
            release_id=release.id,
            locus=locus,
            placement=placement,
            ledger=ledger,
            decision=decision,
            decided_at=_utc(flank.assessed_at),
        )
        created += was_created
        replayed += 1 - was_created
        if decision.decision != "include":
            continue
        public = public_by_locus.get(decision.locus_key)
        if public is None or set(side_rows) != {"left", "right"} or placement is None:
            raise StructuredActivationStagingError(
                "included locus lacks exact public or flank evidence"
            )
        _verify_public_locus(public, locus, placement, decision, flank)
        was_created = _stage_locus_membership(
            session,
            release_id=release.id,
            locus=locus,
            placement=placement,
            decision=decision_row,
            left=side_rows["left"],
            right=side_rows["right"],
        )
        created += was_created
        replayed += 1 - was_created
        public_rows[locus.locus_key] = (locus, placement, decision_row)

    assertion_created, assertion_replayed = _stage_assertion_memberships(
        session,
        release_id=release.id,
        public_rows=public_rows,
        manifest=public_assertions,
    )
    return created + assertion_created, replayed + assertion_replayed


def _resolve_candidate_row(
    session: Session,
    release_id: int,
    record: Any,
    expected_outcome: str,
) -> tuple[EVELocus, EVELocusPlacement | None, ImportLedger]:
    rows = session.execute(
        select(
            EVELocus,
            EVELocusPlacement,
            ImportLedger,
            SourceRecord,
            GenomeAssembly,
            AssemblySequence,
        )
        .select_from(EVELocus)
        .join(SourceRecord, SourceRecord.id == EVELocus.source_record_id)
        .join(GenomeAssembly, GenomeAssembly.id == EVELocus.assembly_id)
        .join(AssemblySequence, AssemblySequence.id == EVELocus.sequence_id)
        .join(
            ImportLedger,
            (ImportLedger.release_id == EVELocus.release_id)
            & (ImportLedger.source_record_id == EVELocus.source_record_id)
            & (ImportLedger.locus_id == EVELocus.id),
        )
        .outerjoin(
            EVELocusPlacement,
            (EVELocusPlacement.release_id == EVELocus.release_id)
            & (EVELocusPlacement.locus_id == EVELocus.id),
        )
        .where(
            EVELocus.release_id == release_id,
            EVELocus.locus_key == record.locus_key,
            SourceRecord.source_record_key == record.source_record_key,
            ImportLedger.outcome == expected_outcome,
        )
    ).all()
    if len(rows) != 1:
        raise StructuredActivationStagingError(
            "candidate locus does not resolve to one exact import outcome"
        )
    locus, placement, ledger, source, assembly, sequence = rows[0]
    expected = (
        (source.row_number, record.source_row),
        (assembly.accession_version, record.assembly_accession_version),
        (sequence.accession_version, record.sequence_accession_version),
        (sequence.sequence_length, record.sequence_length),
        (placement.placement_key if placement else None, record.placement_key),
        (placement.placement_sha256 if placement else None, record.placement_sha256),
        (placement.start0 if placement else int(source.raw_payload["Start"]), record.start0),
        (placement.end0 if placement else int(source.raw_payload["End"]), record.end0),
    )
    if any(observed != required for observed, required in expected):
        raise StructuredActivationStagingError(
            "candidate locus database identity differs from its cohort record"
        )
    return locus, placement, ledger


def _stage_flank(
    session: Session,
    *,
    release_id: int,
    locus: EVELocus,
    placement: EVELocusPlacement,
    artifact: SourceArtifact,
    flank: Any,
    side_name: Literal["left", "right"],
) -> tuple[FlankAssessment, int]:
    side = getattr(flank, side_name)
    assessment_key = stable_key(
        "flank-assessment",
        {
            "flank_record_sha256": flank.record_sha256,
            "locus_key": locus.locus_key,
            "placement_key": placement.placement_key,
            "release_key": ACTIVATION_RELEASE_KEY,
            "side": side_name,
        },
    )
    values = {
        "assessment_key": assessment_key,
        "release_id": release_id,
        "locus_id": locus.id,
        "placement_id": placement.id,
        "side": side_name,
        "verdict": side.verdict,
        "inspection_window_bp": FLANK_WINDOW_BP,
        "available_bp": side.available_bp,
        "inspected_bp": side.inspected_bp,
        "assessment_policy_key": flank.assessment_policy_key,
        "method_or_curator_key": flank.assessed_by,
        "evidence_artifact_id": artifact.id,
        "evidence_locator": {
            "flank_record_sha256": flank.record_sha256,
            "normalized_sequence_sha256": flank.normalized_sequence_sha256,
            "reason_code": side.reason_code,
            "request_sha256": flank.request_sha256,
            "response_sha256": flank.response_sha256,
            "side": side_name,
            "wrapper_file_sha256": flank.wrapper_file_sha256,
            "wrapper_sha256": flank.wrapper_sha256,
        },
        "notes": json.dumps(
            {
                "ambiguity_fraction": side.ambiguity_fraction,
                "ambiguous_bp": side.ambiguous_bp,
                "boundary_base": side.boundary_base,
                "longest_ambiguity_run": side.longest_ambiguity_run,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        "assessed_at": _utc(flank.assessed_at),
    }
    existing = session.scalar(
        select(FlankAssessment).where(FlankAssessment.assessment_key == assessment_key)
    )
    if existing is not None:
        _require_values(existing, values, "flank assessment")
        return existing, 0
    row = FlankAssessment(**values)
    session.add(row)
    session.flush()
    return row, 1


def _stage_decision(
    session: Session,
    *,
    release_id: int,
    locus: EVELocus,
    placement: EVELocusPlacement | None,
    ledger: ImportLedger,
    decision: Any,
    decided_at: datetime,
) -> tuple[InclusionDecision, int]:
    decision_key = stable_key(
        "inclusion-decision",
        {
            "decision_sha256": decision.decision_sha256,
            "locus_key": locus.locus_key,
            "release_key": ACTIVATION_RELEASE_KEY,
        },
    )
    values = {
        "decision_key": decision_key,
        "release_id": release_id,
        "locus_id": locus.id,
        "placement_id": placement.id if placement is not None else None,
        "import_ledger_id": ledger.id,
        "import_outcome": ledger.outcome,
        "decision_code": decision.decision,
        "policy_key": decision.policy_key,
        "authorized_by": decision.authorized_by,
        "reason_code": decision.reason_codes[0],
        "rationale": json.dumps(
            {
                "conflict_codes": decision.conflict_codes,
                "decision_sha256": decision.decision_sha256,
                "quarantine_issue_codes": decision.quarantine_issue_codes,
                "reason_codes": decision.reason_codes,
                "unresolved_issue_codes": decision.unresolved_issue_codes,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        "decided_at": decided_at,
    }
    existing = session.scalar(
        select(InclusionDecision).where(InclusionDecision.decision_key == decision_key)
    )
    if existing is not None:
        _require_values(existing, values, "inclusion decision")
        return existing, 0
    row = InclusionDecision(**values)
    session.add(row)
    session.flush()
    return row, 1


def _verify_public_locus(
    public: Any,
    locus: EVELocus,
    placement: EVELocusPlacement,
    decision: Any,
    flank: Any,
) -> None:
    expected = (
        (public.locus_key, locus.locus_key),
        (public.placement_key, placement.placement_key),
        (public.start0, placement.start0),
        (public.end0, placement.end0),
        (public.coordinate_system, placement.coordinate_system),
        (public.inclusion_decision_sha256, decision.decision_sha256),
        (public.left_flank_record_sha256, canonical_model_sha256(flank.left)),
        (public.right_flank_record_sha256, canonical_model_sha256(flank.right)),
    )
    if any(observed != required for observed, required in expected):
        raise StructuredActivationStagingError(
            "public locus membership differs from its exact database evidence"
        )


def _stage_locus_membership(
    session: Session,
    *,
    release_id: int,
    locus: EVELocus,
    placement: EVELocusPlacement,
    decision: InclusionDecision,
    left: FlankAssessment,
    right: FlankAssessment,
) -> int:
    values = {
        "release_id": release_id,
        "locus_id": locus.id,
        "placement_id": placement.id,
        "placement_precision": "exact",
        "inclusion_decision_id": decision.id,
        "decision_code": "include",
        "left_flank_assessment_id": left.id,
        "left_flank_side": "left",
        "left_flank_verdict": "supported",
        "right_flank_assessment_id": right.id,
        "right_flank_side": "right",
        "right_flank_verdict": "supported",
    }
    existing = session.get(ReleaseLocusMembership, (release_id, locus.id))
    if existing is not None:
        _require_values(existing, values, "public locus membership")
        return 0
    session.add(ReleaseLocusMembership(**values))
    session.flush()
    return 1


def _stage_assertion_memberships(
    session: Session,
    *,
    release_id: int,
    public_rows: dict[str, tuple[EVELocus, EVELocusPlacement, InclusionDecision]],
    manifest: PublicAssertionMembershipManifest,
) -> tuple[int, int]:
    created = replayed = 0
    for record in manifest.memberships:
        public = public_rows.get(record.locus_key)
        if public is None:
            raise StructuredActivationStagingError(
                "public assertion targets a non-public locus"
            )
        rows = session.execute(
            select(ScientificAssertion, EvidenceItem)
            .join(
                AssertionEvidence,
                (AssertionEvidence.release_id == ScientificAssertion.release_id)
                & (AssertionEvidence.assertion_id == ScientificAssertion.id)
                & (AssertionEvidence.relation == "supports"),
            )
            .join(
                EvidenceItem,
                (EvidenceItem.release_id == AssertionEvidence.release_id)
                & (EvidenceItem.id == AssertionEvidence.evidence_id),
            )
            .where(
                ScientificAssertion.release_id == release_id,
                ScientificAssertion.assertion_key == record.assertion_key,
            )
            .order_by(EvidenceItem.evidence_sha256)
        ).all()
        if tuple(row.EvidenceItem.evidence_sha256 for row in rows) != record.evidence_sha256s:
            raise StructuredActivationStagingError(
                "public assertion evidence set differs from the manifest"
            )
        if len(rows) != 1:
            raise StructuredActivationStagingError(
                "database membership schema requires one exact supporting evidence item"
            )
        assertion = rows[0].ScientificAssertion
        evidence = rows[0].EvidenceItem
        locus = public[0]
        if (
            assertion.locus_id != locus.id
            or assertion.assertion_type != record.assertion_type
            or assertion.predicate_key != record.predicate_key
            or assertion.process_run_status != "succeeded"
        ):
            raise StructuredActivationStagingError(
                "public assertion identity differs from the manifest"
            )
        values = {
            "release_id": release_id,
            "assertion_id": assertion.id,
            "locus_id": locus.id,
            "process_run_id": assertion.process_run_id,
            "process_run_status": "succeeded",
            "supporting_evidence_id": evidence.id,
            "evidence_relation": "supports",
        }
        existing = session.get(ReleaseAssertionMembership, (release_id, assertion.id))
        if existing is not None:
            _require_values(existing, values, "public assertion membership")
            replayed += 1
        else:
            session.add(ReleaseAssertionMembership(**values))
            session.flush()
            created += 1
    return created, replayed


def _terminal_counts(session: Session, release_id: int) -> tuple[int, int, int, int]:
    return tuple(
        int(
            session.scalar(
                select(func.count()).select_from(model).where(model.release_id == release_id)
            )
            or 0
        )
        for model in (
            FlankAssessment,
            InclusionDecision,
            ReleaseLocusMembership,
            ReleaseAssertionMembership,
        )
    )  # type: ignore[return-value]


def _require_values(row: object, expected: dict[str, object], label: str) -> None:
    if any(getattr(row, name) != value for name, value in expected.items()):
        raise StructuredActivationStagingError(f"existing {label} differs from packet")


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise StructuredActivationStagingError("activation timestamp is not UTC")
    return parsed


__all__ = [
    "StructuredActivationStagingError",
    "StructuredActivationStagingReport",
    "stage_structured_activation_candidate",
]
