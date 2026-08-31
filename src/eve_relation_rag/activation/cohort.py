"""Read-only export and deterministic selection of the V0 adjudication cohort."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import BigInteger, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from eve_relation_rag.activation.contracts import (
    ACTIVATION_RELEASE_KEY,
    APPROVED_ASSEMBLIES,
    AdjudicationCohortManifest,
    AdjudicationSelectionRecord,
    AssemblyAdjudicationOutcome,
    AssemblyExpansionQueue,
    CohortRecord,
    InclusionDecisionRecord,
    canonical_revalidate,
    seal_manifest_payload,
)
from eve_relation_rag.db.models import (
    AssemblySequence,
    DatasetRelease,
    DetectionCall,
    EVELocus,
    EVELocusPlacement,
    GenomeAssembly,
    ImportLedger,
    QuarantineIssue,
    SourceAssessment,
    SourceRecord,
)
from eve_relation_rag.domain.keys import stable_key


class CohortExportError(RuntimeError):
    """Raised when database state cannot produce the preregistered cohort."""


class AdjudicationSelectionError(ValueError):
    """Raised when assessed rows do not form the preregistered expansion prefix."""


@dataclass(frozen=True, slots=True)
class CohortProjection:
    """Database-neutral row used to test and build a frozen cohort."""

    release_status: str
    source_record_key: str
    source_row: int
    locus_key: str
    placement_key: str | None
    placement_sha256: str | None
    assembly_accession_version: str
    sequence_accession_version: str
    sequence_length: int
    start0: int
    end0: int
    coordinate_system: str
    precision: str | None
    source_assessment: str
    import_outcome: str = "normalized_candidate"
    quarantine_issue_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectedAdjudication:
    """Validated final selection plus one terminal result for every assembly."""

    selections: tuple[AdjudicationSelectionRecord, ...]
    assembly_outcomes: tuple[AssemblyAdjudicationOutcome, ...]


def export_adjudication_cohort(
    session: Session,
    *,
    source_manifest_sha256: str,
    source_audit_sha256: str,
    release_key: str = ACTIVATION_RELEASE_KEY,
) -> AdjudicationCohortManifest:
    """Export candidate truth rows without flushing or mutating the database.

    The session must not contain pending ORM mutations because a read-only export must
    never depend on uncommitted Python-side state.  The emitted manifest contains all
    71 ``source_high`` records and complete, row-ordered exact-placement ``source_low``
    queues for the ten approved assemblies.
    """

    if release_key != ACTIVATION_RELEASE_KEY:
        raise CohortExportError("the approved activation release key is required")
    if session.new or session.dirty or session.deleted:
        raise CohortExportError("read-only cohort export requires a clean ORM session")

    source_start0 = cast(SourceRecord.raw_payload["Start"].as_string(), BigInteger)
    source_end0 = cast(SourceRecord.raw_payload["End"].as_string(), BigInteger)
    issue_codes = (
        select(
            ImportLedger.release_id.label("release_id"),
            ImportLedger.source_record_id.label("source_record_id"),
            func.array_agg(func.distinct(QuarantineIssue.issue_code)).label("codes"),
        )
        .select_from(ImportLedger)
        .join(QuarantineIssue, QuarantineIssue.ledger_id == ImportLedger.id)
        .group_by(ImportLedger.release_id, ImportLedger.source_record_id)
        .subquery()
    )
    statement = (
        select(
            DatasetRelease.status.label("release_status"),
            SourceRecord.source_record_key,
            SourceRecord.row_number.label("source_row"),
            EVELocus.locus_key,
            EVELocusPlacement.placement_key,
            EVELocusPlacement.placement_sha256,
            GenomeAssembly.accession_version.label("assembly_accession_version"),
            AssemblySequence.accession_version.label("sequence_accession_version"),
            AssemblySequence.sequence_length,
            func.coalesce(EVELocusPlacement.start0, source_start0).label("start0"),
            func.coalesce(EVELocusPlacement.end0, source_end0).label("end0"),
            EVELocusPlacement.coordinate_system,
            EVELocusPlacement.precision,
            SourceAssessment.confidence.label("source_assessment"),
            ImportLedger.outcome.label("import_outcome"),
            issue_codes.c.codes.label("quarantine_issue_codes"),
        )
        .select_from(DatasetRelease)
        .join(DetectionCall, DetectionCall.release_id == DatasetRelease.id)
        .join(
            SourceAssessment,
            (SourceAssessment.release_id == DetectionCall.release_id)
            & (SourceAssessment.call_id == DetectionCall.id),
        )
        .join(
            EVELocus,
            (EVELocus.release_id == DetectionCall.release_id)
            & (EVELocus.id == DetectionCall.locus_id),
        )
        .outerjoin(
            EVELocusPlacement,
            (EVELocusPlacement.release_id == EVELocus.release_id)
            & (EVELocusPlacement.locus_id == EVELocus.id),
        )
        .join(GenomeAssembly, GenomeAssembly.id == EVELocus.assembly_id)
        .join(
            AssemblySequence,
            (AssemblySequence.id == EVELocus.sequence_id)
            & (AssemblySequence.assembly_id == EVELocus.assembly_id),
        )
        .join(SourceRecord, SourceRecord.id == EVELocus.source_record_id)
        .outerjoin(
            issue_codes,
            (issue_codes.c.release_id == DatasetRelease.id)
            & (issue_codes.c.source_record_id == SourceRecord.id),
        )
        .join(
            ImportLedger,
            (ImportLedger.release_id == DetectionCall.release_id)
            & (ImportLedger.source_record_id == SourceRecord.id)
            & (ImportLedger.call_id == DetectionCall.id),
        )
        .where(
            DatasetRelease.release_key == release_key,
            or_(
                SourceAssessment.confidence == "source_high",
                and_(
                    SourceAssessment.confidence == "source_low",
                    ImportLedger.outcome == "normalized_candidate",
                    EVELocusPlacement.precision == "exact",
                    EVELocusPlacement.coordinate_system == "0-based-half-open",
                ),
            ),
        )
        .order_by(SourceRecord.row_number, EVELocus.locus_key)
    )
    with session.no_autoflush:
        rows = []
        for row in session.execute(statement).mappings():
            values = dict(row)
            values["coordinate_system"] = values["coordinate_system"] or "0-based-half-open"
            values["quarantine_issue_codes"] = tuple(sorted(values["quarantine_issue_codes"] or ()))
            rows.append(CohortProjection(**values))
    return build_adjudication_cohort(
        tuple(rows),
        source_manifest_sha256=source_manifest_sha256,
        source_audit_sha256=source_audit_sha256,
        release_key=release_key,
    )


def build_adjudication_cohort(
    rows: Iterable[CohortProjection],
    *,
    source_manifest_sha256: str,
    source_audit_sha256: str,
    release_key: str = ACTIVATION_RELEASE_KEY,
) -> AdjudicationCohortManifest:
    """Build a canonical cohort from already projected read-only database rows."""

    projected = tuple(rows)
    if not projected:
        raise CohortExportError("release has no exact-placement candidate rows")
    if {row.release_status for row in projected} != {"candidate"}:
        raise CohortExportError("cohort export is allowed only from the candidate release")

    records = tuple(_cohort_record(row) for row in projected)
    primary = tuple(
        sorted(
            (row for row in records if row.source_assessment == "source_high"),
            key=_row_key,
        )
    )
    if len(primary) != 71:
        raise CohortExportError(f"expected exactly 71 source_high records, observed {len(primary)}")

    queues = tuple(
        AssemblyExpansionQueue(
            assembly_accession_version=assembly,
            records=tuple(
                sorted(
                    (
                        row
                        for row in records
                        if row.source_assessment == "source_low"
                        and row.assembly_accession_version == assembly
                    ),
                    key=_row_key,
                )
            ),
        )
        for assembly in APPROVED_ASSEMBLIES
    )
    payload: dict[str, object] = {
        "manifest_schema_version": "structured-adjudication-cohort-manifest-v1",
        "release_key": release_key,
        "source_manifest_sha256": source_manifest_sha256,
        "source_audit_sha256": source_audit_sha256,
        "selection_policy_key": "policy:v0-adjudication-cohort-v1",
        "primary_records": primary,
        "expansion_queues": queues,
    }
    return AdjudicationCohortManifest.model_validate(seal_manifest_payload(payload))


def select_final_adjudication(
    cohort: AdjudicationCohortManifest,
    decisions: Mapping[str, InclusionDecisionRecord],
) -> SelectedAdjudication:
    """Enforce all-primary plus deterministic expansion-prefix selection.

    This function is intentionally final-state only.  If an assembly has no passing
    primary locus, its expansion decisions must be a contiguous prefix ending at the
    first include, or the complete queue when no record passes.
    """

    try:
        cohort = canonical_revalidate(cohort)
        decisions = {
            locus_key: canonical_revalidate(decision) for locus_key, decision in decisions.items()
        }
    except ValidationError as exc:
        raise AdjudicationSelectionError("adjudication input failed canonical validation") from exc

    known_records = {
        row.locus_key: row
        for row in (
            *cohort.primary_records,
            *(record for queue in cohort.expansion_queues for record in queue.records),
        )
    }
    unknown = set(decisions).difference(known_records)
    if unknown:
        raise AdjudicationSelectionError("decisions contain loci outside the frozen cohort")

    selections: list[AdjudicationSelectionRecord] = []
    for row in cohort.primary_records:
        decision = decisions.get(row.locus_key)
        if decision is None:
            raise AdjudicationSelectionError("every source_high primary record must be assessed")
        _assert_decision_matches_record(decision, row)
        selections.append(_selection(row, decision, tier="primary", ordinal=None))

    outcomes: list[AssemblyAdjudicationOutcome] = []
    primary_by_assembly = {
        assembly: tuple(
            row for row in cohort.primary_records if row.assembly_accession_version == assembly
        )
        for assembly in APPROVED_ASSEMBLIES
    }
    for queue in cohort.expansion_queues:
        assembly = queue.assembly_accession_version
        primary_decisions = tuple(decisions[row.locus_key] for row in primary_by_assembly[assembly])
        include_count = sum(row.decision == "include" for row in primary_decisions)
        assessed_count = len(primary_decisions)
        expansion_decisions = tuple(decisions.get(row.locus_key) for row in queue.records)

        if include_count:
            if any(decision is not None for decision in expansion_decisions):
                raise AdjudicationSelectionError(
                    "expansion records cannot be assessed after a primary locus passes"
                )
        else:
            seen_gap = False
            seen_include = False
            for ordinal, (row, decision) in enumerate(
                zip(queue.records, expansion_decisions, strict=True), start=1
            ):
                if decision is None:
                    seen_gap = True
                    continue
                if seen_gap:
                    raise AdjudicationSelectionError(
                        "expansion decisions must form a contiguous source-row prefix"
                    )
                if seen_include:
                    raise AdjudicationSelectionError(
                        "expansion must stop immediately after the first passing locus"
                    )
                _assert_decision_matches_record(decision, row)
                selections.append(_selection(row, decision, tier="expansion", ordinal=ordinal))
                assessed_count += 1
                if decision.decision == "include":
                    include_count += 1
                    seen_include = True
            if not seen_include and any(decision is None for decision in expansion_decisions):
                raise AdjudicationSelectionError(
                    "an assembly without a passing locus must exhaust its expansion queue"
                )

        outcomes.append(
            AssemblyAdjudicationOutcome(
                assembly_accession_version=assembly,
                assessed_count=assessed_count,
                include_count=include_count,
                terminal_status=(
                    "passing_locus_found" if include_count else "assembly_exhausted_without_pass"
                ),
            )
        )
    return SelectedAdjudication(
        selections=tuple(selections),
        assembly_outcomes=tuple(outcomes),
    )


def _cohort_record(row: CohortProjection) -> CohortRecord:
    interval_key = row.placement_key or stable_key(
        "source-interval:v0-flank-evidence",
        {
            "coordinate_system": row.coordinate_system,
            "end0": row.end0,
            "source_record_key": row.source_record_key,
            "start0": row.start0,
        },
    )
    try:
        return CohortRecord.model_validate(
            {
                "source_record_key": row.source_record_key,
                "source_row": row.source_row,
                "locus_key": row.locus_key,
                "interval_key": interval_key,
                "placement_key": row.placement_key,
                "placement_sha256": row.placement_sha256,
                "assembly_accession_version": row.assembly_accession_version,
                "sequence_accession_version": row.sequence_accession_version,
                "sequence_length": row.sequence_length,
                "start0": row.start0,
                "end0": row.end0,
                "coordinate_system": row.coordinate_system,
                "interval_basis": (
                    "canonical_exact_placement"
                    if row.placement_key is not None
                    else "validated_source_quarantine_interval"
                ),
                "source_assessment": row.source_assessment,
                "import_outcome": row.import_outcome,
                "quarantine_issue_codes": row.quarantine_issue_codes,
            }
        )
    except ValueError as exc:
        raise CohortExportError(
            f"invalid exact-placement cohort projection at source row {row.source_row}"
        ) from exc


def _row_key(record: CohortRecord) -> tuple[int, str]:
    return record.source_row, record.locus_key


def _assert_decision_matches_record(
    decision: InclusionDecisionRecord, record: CohortRecord
) -> None:
    if (
        decision.source_record_key != record.source_record_key
        or decision.source_row != record.source_row
        or decision.locus_key != record.locus_key
        or decision.interval_key != record.interval_key
        or decision.placement_key != record.placement_key
    ):
        raise AdjudicationSelectionError("inclusion decision does not match the cohort record")


def _selection(
    record: CohortRecord,
    decision: InclusionDecisionRecord,
    *,
    tier: Literal["primary", "expansion"],
    ordinal: int | None,
) -> AdjudicationSelectionRecord:
    return AdjudicationSelectionRecord(
        source_record_key=record.source_record_key,
        source_row=record.source_row,
        locus_key=record.locus_key,
        assembly_accession_version=record.assembly_accession_version,
        selection_tier=tier,
        expansion_ordinal=ordinal,
        decision_sha256=decision.decision_sha256,
    )


__all__ = [
    "AdjudicationSelectionError",
    "CohortExportError",
    "CohortProjection",
    "SelectedAdjudication",
    "build_adjudication_cohort",
    "export_adjudication_cohort",
    "select_final_adjudication",
]
