"""Database-bound dependency graph and completeness checks for structured releases."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from eve_relation_rag.db import Base
from eve_relation_rag.db.models import (
    AssemblySequence,
    AssemblyTaxonAssignment,
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
    LineageAlias,
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
from eve_relation_rag.domain.keys import LocusIdentity, canonical_json_sha256, stable_key
from eve_relation_rag.releases.validator import (
    FlankEvidence,
    InclusionEvidence,
    PlacementEvidence,
    ReleaseMembershipCandidate,
    ReleaseValidationRequest,
)
from eve_relation_rag.retrieval.structured.capability import (
    LineageDependencyBinding,
    LineageRole,
    SourceDependencyBinding,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

STRUCTURED_RELEASE_SCOPED_TABLES: tuple[str, ...] = (
    "release_source_snapshot",
    "release_lineage_snapshot",
    "release_assembly_membership",
    "assembly_taxon_assignment",
    "import_run",
    "eve_locus",
    "detection_call",
    "source_assessment",
    "import_ledger",
    "eve_locus_placement",
    "flank_assessment",
    "inclusion_decision",
    "release_locus_membership",
    "release_method_definition",
    "process_run",
    "evidence_item",
    "scientific_assertion",
    "assertion_evidence",
    "release_assertion_membership",
)

_OPERATIONAL_TIMESTAMP_FIELDS = frozenset(
    {
        "created_at",
        "started_at",
        "finished_at",
        "processed_at",
        "assessed_at",
        "decided_at",
    }
)

_FK_TARGET_OVERRIDES: dict[tuple[str, str], str] = {
    ("release_source_snapshot", "source_snapshot_id"): "source_snapshot",
    ("release_lineage_snapshot", "snapshot_id"): "lineage_snapshot",
    ("release_assembly_membership", "assembly_id"): "genome_assembly",
    ("assembly_taxon_assignment", "assembly_id"): "genome_assembly",
    ("assembly_taxon_assignment", "snapshot_id"): "lineage_snapshot",
    ("assembly_taxon_assignment", "term_id"): "lineage_term",
    ("assembly_taxon_assignment", "source_artifact_id"): "source_artifact",
    ("import_run", "source_snapshot_id"): "source_snapshot",
    ("import_run", "source_artifact_id"): "source_artifact",
    ("eve_locus", "assembly_id"): "genome_assembly",
    ("eve_locus", "sequence_id"): "assembly_sequence",
    ("eve_locus", "source_snapshot_id"): "source_snapshot",
    ("eve_locus", "source_record_id"): "source_record",
    ("detection_call", "source_snapshot_id"): "source_snapshot",
    ("detection_call", "source_record_id"): "source_record",
    ("detection_call", "locus_id"): "eve_locus",
    ("detection_call", "process_run_id"): "process_run",
    ("source_assessment", "call_id"): "detection_call",
    ("source_assessment", "process_run_id"): "process_run",
    ("source_assessment", "source_artifact_id"): "source_artifact",
    ("import_ledger", "run_id"): "import_run",
    ("import_ledger", "source_record_id"): "source_record",
    ("import_ledger", "call_id"): "detection_call",
    ("import_ledger", "locus_id"): "eve_locus",
    ("eve_locus_placement", "locus_id"): "eve_locus",
    ("eve_locus_placement", "assembly_id"): "genome_assembly",
    ("eve_locus_placement", "sequence_id"): "assembly_sequence",
    ("eve_locus_placement", "source_artifact_id"): "source_artifact",
    ("flank_assessment", "locus_id"): "eve_locus",
    ("flank_assessment", "placement_id"): "eve_locus_placement",
    ("flank_assessment", "evidence_artifact_id"): "source_artifact",
    ("inclusion_decision", "locus_id"): "eve_locus",
    ("inclusion_decision", "placement_id"): "eve_locus_placement",
    ("inclusion_decision", "import_ledger_id"): "import_ledger",
    ("release_locus_membership", "locus_id"): "eve_locus",
    ("release_locus_membership", "placement_id"): "eve_locus_placement",
    ("release_locus_membership", "inclusion_decision_id"): "inclusion_decision",
    ("release_locus_membership", "left_flank_assessment_id"): "flank_assessment",
    ("release_locus_membership", "right_flank_assessment_id"): "flank_assessment",
    ("release_method_definition", "method_definition_id"): "method_definition",
    ("process_run", "method_definition_id"): "method_definition",
    ("process_run", "import_run_id"): "import_run",
    ("evidence_item", "source_snapshot_id"): "source_snapshot",
    ("evidence_item", "source_artifact_id"): "source_artifact",
    ("scientific_assertion", "call_id"): "detection_call",
    ("scientific_assertion", "locus_id"): "eve_locus",
    ("scientific_assertion", "process_run_id"): "process_run",
    ("scientific_assertion", "source_assessment_id"): "source_assessment",
    ("scientific_assertion", "lineage_snapshot_id"): "lineage_snapshot",
    ("scientific_assertion", "lineage_term_id"): "lineage_term",
    ("assertion_evidence", "assertion_id"): "scientific_assertion",
    ("assertion_evidence", "evidence_id"): "evidence_item",
    ("release_assertion_membership", "assertion_id"): "scientific_assertion",
    ("release_assertion_membership", "locus_id"): "eve_locus",
    ("release_assertion_membership", "process_run_id"): "process_run",
    ("release_assertion_membership", "supporting_evidence_id"): "evidence_item",
    ("quarantine_issue", "ledger_id"): "import_ledger",
    ("source_artifact", "snapshot_id"): "source_snapshot",
    ("source_record", "snapshot_id"): "source_snapshot",
    ("source_record", "artifact_id"): "source_artifact",
    ("lineage_term", "snapshot_id"): "lineage_snapshot",
    ("lineage_alias", "snapshot_id"): "lineage_snapshot",
    ("lineage_alias", "term_id"): "lineage_term",
    ("lineage_closure", "snapshot_id"): "lineage_snapshot",
    ("lineage_closure", "ancestor_term_id"): "lineage_term",
    ("lineage_closure", "descendant_term_id"): "lineage_term",
    ("assembly_sequence", "assembly_id"): "genome_assembly",
    ("assembly_sequence", "source_artifact_id"): "source_artifact",
}


class ReleaseDependencyError(ValueError):
    """Raised when live database dependencies disagree with approved evidence."""


def _public_locus_ids(release_id: int) -> Any:
    return select(ReleaseLocusMembership.locus_id).where(
        ReleaseLocusMembership.release_id == release_id
    )


def _quarantine_ledger_ids(release_id: int) -> Any:
    return (
        select(QuarantineIssue.ledger_id)
        .join(ImportLedger, ImportLedger.id == QuarantineIssue.ledger_id)
        .where(ImportLedger.release_id == release_id)
    )


def _selected_locus_ids(release_id: int) -> Any:
    return _public_locus_ids(release_id).union(
        select(ImportLedger.locus_id).where(
            ImportLedger.release_id == release_id,
            ImportLedger.id.in_(_quarantine_ledger_ids(release_id)),
            ImportLedger.locus_id.is_not(None),
        )
    )


def _public_assertion_ids(release_id: int) -> Any:
    return select(ReleaseAssertionMembership.assertion_id).where(
        ReleaseAssertionMembership.release_id == release_id
    )


def _public_evidence_ids(release_id: int) -> Any:
    return select(ReleaseAssertionMembership.supporting_evidence_id).where(
        ReleaseAssertionMembership.release_id == release_id
    )


def _selected_source_record_ids(release_id: int) -> Any:
    return (
        select(EVELocus.source_record_id)
        .where(EVELocus.id.in_(_selected_locus_ids(release_id)))
        .union(
            select(ImportLedger.source_record_id).where(
                ImportLedger.release_id == release_id,
                ImportLedger.id.in_(_quarantine_ledger_ids(release_id)),
            )
        )
    )


def _release_scope_predicate(table_name: str, release_id: int) -> Any:
    """Limit the graph to public truth plus all terminal quarantine evidence."""

    table = Base.metadata.tables[table_name]
    selected_loci = _selected_locus_ids(release_id)
    public_assertions = _public_assertion_ids(release_id)
    public_evidence = _public_evidence_ids(release_id)
    if table_name == "eve_locus":
        return table.c.id.in_(selected_loci)
    if table_name in {"detection_call", "eve_locus_placement", "flank_assessment"}:
        return (table.c.release_id == release_id) & table.c.locus_id.in_(selected_loci)
    if table_name == "source_assessment":
        selected_calls = select(DetectionCall.id).where(
            DetectionCall.release_id == release_id,
            DetectionCall.locus_id.in_(selected_loci),
        )
        return (table.c.release_id == release_id) & table.c.call_id.in_(selected_calls)
    if table_name == "import_ledger":
        return (table.c.release_id == release_id) & (
            table.c.locus_id.in_(selected_loci) | table.c.id.in_(_quarantine_ledger_ids(release_id))
        )
    if table_name == "inclusion_decision":
        return (table.c.release_id == release_id) & table.c.locus_id.in_(selected_loci)
    if table_name == "scientific_assertion":
        return (table.c.release_id == release_id) & table.c.id.in_(public_assertions)
    if table_name == "assertion_evidence":
        return (table.c.release_id == release_id) & table.c.assertion_id.in_(public_assertions)
    if table_name == "evidence_item":
        return (table.c.release_id == release_id) & table.c.id.in_(public_evidence)
    if "release_id" in table.c:
        return table.c.release_id == release_id
    return false()


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ReleaseDependencyError("release graph contains a naive datetime")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(child) for child in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise ReleaseDependencyError(f"release graph contains unsupported value {type(value).__name__}")


def _semantic_identity_maps(session: Session, release_id: int) -> dict[tuple[str, int], str]:
    """Resolve every surrogate key reachable from a release to a stable identity."""

    identities: dict[tuple[str, int], str] = {}

    def add(table_name: str, rows: Sequence[tuple[int, str]]) -> None:
        for row_id, identity in rows:
            key = (table_name, row_id)
            if key in identities and identities[key] != identity:
                raise ReleaseDependencyError("surrogate identity mapping is inconsistent")
            identities[key] = identity

    release = session.get(DatasetRelease, release_id)
    if release is None:
        raise ReleaseDependencyError("release dependency graph target was not found")
    add("dataset_release", ((release.id, release.release_key),))
    add(
        "source_snapshot",
        tuple(session.execute(select(SourceSnapshot.id, SourceSnapshot.snapshot_key)).tuples()),
    )
    add(
        "source_artifact",
        tuple(session.execute(select(SourceArtifact.id, SourceArtifact.artifact_key)).tuples()),
    )
    add(
        "source_record",
        tuple(
            session.execute(
                select(SourceRecord.id, SourceRecord.source_record_key).where(
                    SourceRecord.id.in_(_selected_source_record_ids(release_id))
                )
            ).tuples()
        ),
    )
    add(
        "lineage_snapshot",
        tuple(session.execute(select(LineageSnapshot.id, LineageSnapshot.snapshot_key)).tuples()),
    )
    add(
        "lineage_term",
        tuple(
            session.execute(
                select(
                    LineageTerm.id,
                    LineageSnapshot.snapshot_key.concat("|").concat(LineageTerm.term_key),
                ).join(LineageSnapshot, LineageSnapshot.id == LineageTerm.snapshot_id)
            ).tuples()
        ),
    )
    add(
        "genome_assembly",
        tuple(session.execute(select(GenomeAssembly.id, GenomeAssembly.assembly_key)).tuples()),
    )
    add(
        "assembly_sequence",
        tuple(
            session.execute(
                select(
                    AssemblySequence.id,
                    GenomeAssembly.assembly_key.concat("|").concat(
                        AssemblySequence.accession_version
                    ),
                ).join(GenomeAssembly, GenomeAssembly.id == AssemblySequence.assembly_id)
            ).tuples()
        ),
    )
    add(
        "method_definition",
        tuple(
            session.execute(
                select(MethodDefinition.id, MethodDefinition.method_definition_key)
            ).tuples()
        ),
    )
    for table_name, model, key_column in (
        ("assembly_taxon_assignment", AssemblyTaxonAssignment, "assignment_key"),
        ("import_run", ImportRun, "run_key"),
        ("eve_locus", EVELocus, "locus_key"),
        ("detection_call", DetectionCall, "call_key"),
        ("source_assessment", SourceAssessment, "assessment_key"),
        ("eve_locus_placement", EVELocusPlacement, "placement_key"),
        ("flank_assessment", FlankAssessment, "assessment_key"),
        ("inclusion_decision", InclusionDecision, "decision_key"),
        ("process_run", ProcessRun, "process_run_key"),
        ("evidence_item", EvidenceItem, "evidence_key"),
        ("scientific_assertion", ScientificAssertion, "assertion_key"),
        ("quarantine_issue", QuarantineIssue, "issue_key"),
    ):
        table = model.__table__
        statement = select(table.c.id, table.c[key_column])
        if table_name == "quarantine_issue":
            statement = statement.join(
                ImportLedger, ImportLedger.id == QuarantineIssue.ledger_id
            ).where(ImportLedger.release_id == release_id)
        else:
            statement = statement.where(_release_scope_predicate(table_name, release_id))
        add(table_name, tuple(session.execute(statement).tuples()))

    ledger_rows = session.execute(
        select(
            ImportLedger.id,
            ImportRun.run_key,
            SourceRecord.source_record_key,
            DetectionCall.call_key,
            EVELocus.locus_key,
            ImportLedger.outcome,
            ImportLedger.result_sha256,
        )
        .join(ImportRun, ImportRun.id == ImportLedger.run_id)
        .join(SourceRecord, SourceRecord.id == ImportLedger.source_record_id)
        .outerjoin(DetectionCall, DetectionCall.id == ImportLedger.call_id)
        .outerjoin(EVELocus, EVELocus.id == ImportLedger.locus_id)
        .where(_release_scope_predicate("import_ledger", release_id))
    )
    add(
        "import_ledger",
        tuple(
            (
                row.id,
                stable_key(
                    "import-ledger",
                    {
                        "run_key": row.run_key,
                        "source_record_key": row.source_record_key,
                        "call_key": row.call_key,
                        "locus_key": row.locus_key,
                        "outcome": row.outcome,
                        "result_sha256": row.result_sha256,
                    },
                ),
            )
            for row in ledger_rows
        ),
    )
    return identities


def _semantic_row(
    table_name: str,
    row: Mapping[str, object],
    identities: Mapping[tuple[str, int], str],
) -> dict[str, object]:
    """Replace DB-local IDs and operational clocks with rebuild-stable semantics."""

    table = Base.metadata.tables[table_name]
    result: dict[str, object] = {}
    for column_name, value in row.items():
        if column_name == "id" or column_name in _OPERATIONAL_TIMESTAMP_FIELDS:
            continue
        column = table.c[column_name]
        if value is not None and (column_name == "release_id" or column.foreign_keys):
            if type(value) is not int:
                result[column_name] = _json_value(value)
                continue
            target_names = tuple(
                sorted({foreign_key.column.table.name for foreign_key in column.foreign_keys})
            )
            if column_name == "release_id":
                target_names = ("dataset_release",)
            elif override := _FK_TARGET_OVERRIDES.get((table_name, column_name)):
                target_names = (override,)
            semantic = next(
                (
                    identities[(target_name, value)]
                    for target_name in target_names
                    if (target_name, value) in identities
                ),
                None,
            )
            if semantic is None:
                raise ReleaseDependencyError(
                    f"cannot resolve semantic identity for {table_name}.{column_name}"
                )
            result[column_name.removesuffix("_id") + "_key"] = semantic
            continue
        result[column_name] = _json_value(value)
    return result


def _semantic_rows_digest(
    session: Session,
    table_name: str,
    statement: Any,
    identities: Mapping[tuple[str, int], str],
) -> dict[str, object]:
    """Summarize exact semantic rows without retaining the large payload in memory."""

    row_sha256s = sorted(
        canonical_json_sha256(_semantic_row(table_name, dict(row), identities))
        for row in session.execute(statement).mappings()
    )
    return {
        "row_count": len(row_sha256s),
        "row_set_sha256": canonical_json_sha256(
            {
                "row_digest_schema_version": "semantic-row-digest-v1",
                "table_name": table_name,
                "row_sha256s": row_sha256s,
            }
        ),
    }


def _release_scoped_rows(
    session: Session,
    table_name: str,
    release_id: int,
    identities: Mapping[tuple[str, int], str],
) -> dict[str, object]:
    table = Base.metadata.tables[table_name]
    statement = select(table).where(_release_scope_predicate(table_name, release_id))
    return _semantic_rows_digest(session, table_name, statement, identities)


def _dependency_identity_payload(
    session: Session,
    release_id: int,
    identities: Mapping[tuple[str, int], str],
) -> dict[str, object]:
    source_rows = session.execute(
        select(
            ReleaseSourceSnapshot.role,
            SourceSnapshot.snapshot_key,
            SourceSnapshot.source_name,
            SourceSnapshot.source_version,
            SourceSnapshot.source_uri,
            SourceSnapshot.retrieved_at,
            SourceSnapshot.declared_manifest_sha256,
            SourceSnapshot.verified_manifest_sha256,
            SourceSnapshot.declared_license_key,
            SourceSnapshot.verified_license_key,
        )
        .join(SourceSnapshot, SourceSnapshot.id == ReleaseSourceSnapshot.source_snapshot_id)
        .where(ReleaseSourceSnapshot.release_id == release_id)
        .order_by(ReleaseSourceSnapshot.role, SourceSnapshot.snapshot_key)
    ).mappings()
    lineage_rows = session.execute(
        select(
            ReleaseLineageSnapshot.role,
            LineageSnapshot.snapshot_key,
            LineageSnapshot.domain,
            LineageSnapshot.scheme_kind,
            LineageSnapshot.authority_namespace,
            LineageSnapshot.version,
            LineageSnapshot.snapshot_sha256,
        )
        .join(LineageSnapshot, LineageSnapshot.id == ReleaseLineageSnapshot.snapshot_id)
        .where(ReleaseLineageSnapshot.release_id == release_id)
        .order_by(ReleaseLineageSnapshot.role, LineageSnapshot.snapshot_key)
    ).mappings()
    method_rows = session.execute(
        select(
            ReleaseMethodDefinition.role,
            MethodDefinition.method_definition_key,
            MethodDefinition.method_key,
            MethodDefinition.version,
            MethodDefinition.method_kind,
            MethodDefinition.definition_sha256,
            MethodDefinition.parameter_schema,
            MethodDefinition.output_schema,
        )
        .join(
            MethodDefinition,
            MethodDefinition.id == ReleaseMethodDefinition.method_definition_id,
        )
        .where(ReleaseMethodDefinition.release_id == release_id)
        .order_by(ReleaseMethodDefinition.role, MethodDefinition.method_definition_key)
    ).mappings()
    assembly_rows = session.execute(
        select(
            GenomeAssembly.assembly_key,
            GenomeAssembly.namespace,
            GenomeAssembly.accession_version,
            GenomeAssembly.source_organism_name,
        )
        .join(
            ReleaseAssemblyMembership,
            ReleaseAssemblyMembership.assembly_id == GenomeAssembly.id,
        )
        .where(ReleaseAssemblyMembership.release_id == release_id)
        .order_by(GenomeAssembly.assembly_key)
    ).mappings()
    bound_source_ids = select(ReleaseSourceSnapshot.source_snapshot_id).where(
        ReleaseSourceSnapshot.release_id == release_id
    )
    bound_lineage_ids = select(ReleaseLineageSnapshot.snapshot_id).where(
        ReleaseLineageSnapshot.release_id == release_id
    )
    bound_assembly_ids = select(ReleaseAssemblyMembership.assembly_id).where(
        ReleaseAssemblyMembership.release_id == release_id
    )

    return {
        "sources": [_json_value(dict(row)) for row in source_rows],
        "lineages": [_json_value(dict(row)) for row in lineage_rows],
        "methods": [_json_value(dict(row)) for row in method_rows],
        "assemblies": [_json_value(dict(row)) for row in assembly_rows],
        "bound_global_rows": {
            "source_artifact": _semantic_rows_digest(
                session,
                "source_artifact",
                select(SourceArtifact.__table__)
                .where(SourceArtifact.snapshot_id.in_(bound_source_ids))
                .order_by(SourceArtifact.id),
                identities,
            ),
            "source_record": _semantic_rows_digest(
                session,
                "source_record",
                select(SourceRecord.__table__)
                .where(
                    SourceRecord.snapshot_id.in_(bound_source_ids),
                    SourceRecord.id.in_(_selected_source_record_ids(release_id)),
                )
                .order_by(SourceRecord.id),
                identities,
            ),
            "lineage_term": _semantic_rows_digest(
                session,
                "lineage_term",
                select(LineageTerm.__table__)
                .where(LineageTerm.snapshot_id.in_(bound_lineage_ids))
                .order_by(LineageTerm.id),
                identities,
            ),
            "lineage_alias": _semantic_rows_digest(
                session,
                "lineage_alias",
                select(LineageAlias.__table__)
                .where(LineageAlias.snapshot_id.in_(bound_lineage_ids))
                .order_by(LineageAlias.id),
                identities,
            ),
            "lineage_closure": _semantic_rows_digest(
                session,
                "lineage_closure",
                select(LineageClosure.__table__)
                .where(LineageClosure.snapshot_id.in_(bound_lineage_ids))
                .order_by(
                    LineageClosure.snapshot_id,
                    LineageClosure.ancestor_term_id,
                    LineageClosure.descendant_term_id,
                ),
                identities,
            ),
            "assembly_sequence": _semantic_rows_digest(
                session,
                "assembly_sequence",
                select(AssemblySequence.__table__)
                .where(AssemblySequence.assembly_id.in_(bound_assembly_ids))
                .order_by(AssemblySequence.id),
                identities,
            ),
        },
    }


def release_dependency_graph_sha256(session: Session, release_id: int) -> str:
    """Hash every release-scoped row plus exact global dependency identities."""

    release = session.get(DatasetRelease, release_id)
    if release is None:
        raise ReleaseDependencyError("release dependency graph target was not found")
    identities = _semantic_identity_maps(session, release_id)
    quarantine_statement = (
        select(QuarantineIssue.__table__)
        .join(ImportLedger, ImportLedger.id == QuarantineIssue.ledger_id)
        .where(ImportLedger.release_id == release_id)
    )
    payload = {
        "graph_schema_version": "dataset-release-dependency-graph-v2",
        "release_key": release.release_key,
        "release_schema_version": release.schema_version,
        "manifest_sha256": release.manifest_sha256,
        "dependencies": _dependency_identity_payload(session, release_id, identities),
        "release_scoped_tables": {
            table_name: _release_scoped_rows(session, table_name, release_id, identities)
            for table_name in STRUCTURED_RELEASE_SCOPED_TABLES
        },
        "quarantine_issues": _semantic_rows_digest(
            session, "quarantine_issue", quarantine_statement, identities
        ),
    }
    return canonical_json_sha256(payload)


def _candidate_binding_payload(
    session: Session,
    *,
    release_id: int,
    locus_key: str,
) -> dict[str, object]:
    core = session.execute(
        select(
            EVELocus.id.label("locus_id"),
            EVELocus.locus_key,
            EVELocus.native_vr_token,
            EVELocus.identity_policy_key,
            GenomeAssembly.accession_version.label("assembly_accession_version"),
            AssemblySequence.accession_version.label("contig_accession_version"),
            AssemblySequence.sequence_length.label("contig_length"),
            SourceSnapshot.snapshot_key.label("source_snapshot_key"),
            SourceRecord.source_record_key,
            ReleaseLocusMembership.placement_id,
            ReleaseLocusMembership.inclusion_decision_id,
            ReleaseLocusMembership.left_flank_assessment_id,
            ReleaseLocusMembership.right_flank_assessment_id,
        )
        .select_from(ReleaseLocusMembership)
        .join(
            EVELocus,
            (EVELocus.release_id == ReleaseLocusMembership.release_id)
            & (EVELocus.id == ReleaseLocusMembership.locus_id),
        )
        .join(GenomeAssembly, GenomeAssembly.id == EVELocus.assembly_id)
        .join(AssemblySequence, AssemblySequence.id == EVELocus.sequence_id)
        .join(SourceSnapshot, SourceSnapshot.id == EVELocus.source_snapshot_id)
        .join(SourceRecord, SourceRecord.id == EVELocus.source_record_id)
        .where(
            ReleaseLocusMembership.release_id == release_id,
            EVELocus.locus_key == locus_key,
        )
    ).one_or_none()
    if core is None:
        raise ReleaseDependencyError("approved candidate is not a public membership")

    placement = session.get(EVELocusPlacement, core.placement_id)
    decision = session.get(InclusionDecision, core.inclusion_decision_id)
    if (
        placement is None
        or placement.release_id != release_id
        or placement.locus_id != core.locus_id
        or decision is None
        or decision.release_id != release_id
        or decision.locus_id != core.locus_id
        or decision.placement_id != placement.id
    ):
        raise ReleaseDependencyError("public membership gates are incomplete")

    ledger = session.execute(
        select(
            ImportRun.run_key,
            DetectionCall.source_method_key,
            DetectionCall.raw_result,
            SourceAssessment.confidence,
        )
        .select_from(ImportLedger)
        .join(
            ImportRun,
            (ImportRun.id == ImportLedger.run_id)
            & (ImportRun.release_id == ImportLedger.release_id),
        )
        .join(
            DetectionCall,
            (DetectionCall.id == ImportLedger.call_id)
            & (DetectionCall.release_id == ImportLedger.release_id),
        )
        .join(
            SourceAssessment,
            (SourceAssessment.release_id == DetectionCall.release_id)
            & (SourceAssessment.call_id == DetectionCall.id)
            & (SourceAssessment.assessment_type == "hcvr"),
        )
        .where(
            ImportLedger.id == decision.import_ledger_id,
            ImportLedger.release_id == release_id,
            ImportLedger.locus_id == core.locus_id,
            ImportLedger.outcome == "normalized_candidate",
        )
    ).one_or_none()
    if ledger is None or not isinstance(ledger.raw_result, Mapping):
        raise ReleaseDependencyError("public membership import evidence is incomplete")

    flank_rows = session.execute(
        select(
            FlankAssessment.id,
            FlankAssessment.side,
            FlankAssessment.verdict,
            FlankAssessment.assessment_policy_key,
            FlankAssessment.inspection_window_bp,
            FlankAssessment.available_bp,
            FlankAssessment.inspected_bp,
            FlankAssessment.method_or_curator_key,
            SourceArtifact.artifact_key,
            SourceArtifact.verified_sha256,
        )
        .join(SourceArtifact, SourceArtifact.id == FlankAssessment.evidence_artifact_id)
        .where(
            FlankAssessment.id.in_(
                (
                    core.left_flank_assessment_id,
                    core.right_flank_assessment_id,
                )
            ),
            FlankAssessment.release_id == release_id,
            FlankAssessment.locus_id == core.locus_id,
            FlankAssessment.placement_id == placement.id,
        )
        .order_by(FlankAssessment.side)
    ).all()
    if len(flank_rows) != 2 or {row.side for row in flank_rows} != {"left", "right"}:
        raise ReleaseDependencyError("public membership flank evidence is incomplete")

    return {
        "locus_key": core.locus_key,
        "identity": {
            "source_snapshot_key": core.source_snapshot_key,
            "assembly_accession_version": core.assembly_accession_version,
            "contig_accession_version": core.contig_accession_version,
            "native_vr_token": core.native_vr_token,
            "identity_policy_version": core.identity_policy_key,
        },
        "assembly_accession_version": core.assembly_accession_version,
        "assembly_resolution": ledger.raw_result.get("assembly_resolution"),
        "contig_accession_version": core.contig_accession_version,
        "contig_resolution": ledger.raw_result.get("contig_resolution"),
        "contig_length": core.contig_length,
        "source_record_key": core.source_record_key,
        "method_key": ledger.source_method_key,
        "import_run_key": ledger.run_key,
        "source_assessment": ledger.confidence,
        "placements": [
            {
                "contig_accession_version": core.contig_accession_version,
                "start0": placement.start0,
                "end0": placement.end0,
                "precision": placement.precision,
                "coordinate_system": placement.coordinate_system,
                "provenance_attested": bool(placement.source_locator),
            }
        ],
        "flank_assessments": [
            {
                "side": row.side,
                "verdict": row.verdict,
                "policy_key": row.assessment_policy_key,
                "evidence_key": row.artifact_key,
                "inspection_window_bp": row.inspection_window_bp,
                "available_bp": row.available_bp,
                "inspected_bp": row.inspected_bp,
                "method_or_curator_key": row.method_or_curator_key,
                "evidence_sha256": row.verified_sha256,
            }
            for row in flank_rows
        ],
        "inclusion": {
            "decision": decision.decision_code,
            "policy_key": decision.policy_key,
            "authorized_by": decision.authorized_by,
        },
    }


def _approved_candidate_binding_payload(
    candidate: ReleaseMembershipCandidate,
) -> dict[str, object]:
    return {
        "locus_key": candidate.locus_key,
        "identity": {
            "source_snapshot_key": candidate.identity.source_snapshot_key,
            "assembly_accession_version": (candidate.identity.assembly_accession_version),
            "contig_accession_version": candidate.identity.contig_accession_version,
            "native_vr_token": candidate.identity.native_vr_token,
            "identity_policy_version": candidate.identity.identity_policy_version,
        },
        "assembly_accession_version": candidate.assembly_accession_version,
        "assembly_resolution": candidate.assembly_resolution,
        "contig_accession_version": candidate.contig_accession_version,
        "contig_resolution": candidate.contig_resolution,
        "contig_length": candidate.contig_length,
        "source_record_key": candidate.source_record_key,
        "method_key": candidate.method_key,
        "import_run_key": candidate.import_run_key,
        "source_assessment": candidate.source_assessment,
        "placements": [
            {
                "contig_accession_version": placement.contig_accession_version,
                "start0": placement.start0,
                "end0": placement.end0,
                "precision": placement.precision,
                "coordinate_system": placement.coordinate_system,
                "provenance_attested": bool(placement.provenance_key),
            }
            for placement in candidate.placements
        ],
        "flank_assessments": [
            {
                "side": flank.side,
                "verdict": flank.verdict,
                "policy_key": flank.policy_key,
                "evidence_key": flank.evidence_key,
                "inspection_window_bp": flank.inspection_window_bp,
                "available_bp": flank.available_bp,
                "inspected_bp": flank.inspected_bp,
                "method_or_curator_key": flank.method_or_curator_key,
                "evidence_sha256": flank.evidence_sha256,
            }
            for flank in sorted(candidate.flank_assessments, key=lambda item: item.side)
        ],
        "inclusion": (
            {
                "decision": candidate.inclusion.decision,
                "policy_key": candidate.inclusion.policy_key,
                "authorized_by": candidate.inclusion.authorized_by,
            }
            if candidate.inclusion is not None
            else None
        ),
    }


def project_release_membership_candidates(
    session: Session, release_id: int
) -> tuple[ReleaseMembershipCandidate, ...]:
    """Project exact public database memberships into validator DTOs."""

    locus_keys = tuple(
        session.scalars(
            select(EVELocus.locus_key)
            .join(
                ReleaseLocusMembership,
                (ReleaseLocusMembership.release_id == EVELocus.release_id)
                & (ReleaseLocusMembership.locus_id == EVELocus.id),
            )
            .where(ReleaseLocusMembership.release_id == release_id)
            .order_by(EVELocus.locus_key)
        )
    )
    candidates: list[ReleaseMembershipCandidate] = []
    for locus_key in locus_keys:
        payload = _candidate_binding_payload(
            session, release_id=release_id, locus_key=locus_key
        )
        identity = cast(dict[str, object], payload["identity"])
        placements = cast(list[dict[str, object]], payload["placements"])
        flanks = cast(list[dict[str, object]], payload["flank_assessments"])
        inclusion = cast(dict[str, object], payload["inclusion"])
        candidate = ReleaseMembershipCandidate(
            locus_key=cast(str, payload["locus_key"]),
            identity=LocusIdentity(
                source_snapshot_key=cast(str, identity["source_snapshot_key"]),
                assembly_accession_version=cast(
                    str, identity["assembly_accession_version"]
                ),
                contig_accession_version=cast(
                    str, identity["contig_accession_version"]
                ),
                native_vr_token=cast(str, identity["native_vr_token"]),
                identity_policy_version=cast(
                    str, identity["identity_policy_version"]
                ),
            ),
            assembly_accession_version=cast(
                str, payload["assembly_accession_version"]
            ),
            assembly_resolution=cast(str, payload["assembly_resolution"]),
            contig_accession_version=cast(str, payload["contig_accession_version"]),
            contig_resolution=cast(str, payload["contig_resolution"]),
            contig_length=cast(int, payload["contig_length"]),
            source_record_key=cast(str, payload["source_record_key"]),
            method_key=cast(str, payload["method_key"]),
            import_run_key=cast(str, payload["import_run_key"]),
            source_assessment=cast(str, payload["source_assessment"]),
            placements=tuple(
                PlacementEvidence(
                    contig_accession_version=cast(
                        str, item["contig_accession_version"]
                    ),
                    start0=cast(int, item["start0"]),
                    end0=cast(int, item["end0"]),
                    precision=cast(str, item["precision"]),
                    coordinate_system=cast(str, item["coordinate_system"]),
                    provenance_key="database-bound-placement-provenance",
                )
                for item in placements
            ),
            flank_assessments=tuple(
                FlankEvidence(
                    side=cast(str, item["side"]),
                    verdict=cast(str, item["verdict"]),
                    policy_key=cast(str, item["policy_key"]),
                    evidence_key=cast(str, item["evidence_key"]),
                    inspection_window_bp=cast(int, item["inspection_window_bp"]),
                    available_bp=cast(int, item["available_bp"]),
                    inspected_bp=cast(int, item["inspected_bp"]),
                    method_or_curator_key=cast(
                        str, item["method_or_curator_key"]
                    ),
                    evidence_sha256=cast(str, item["evidence_sha256"]),
                )
                for item in flanks
            ),
            inclusion=InclusionEvidence(
                decision=cast(str, inclusion["decision"]),
                policy_key=cast(str, inclusion["policy_key"]),
                authorized_by=cast(str, inclusion["authorized_by"]),
            ),
        )
        if _approved_candidate_binding_payload(candidate) != payload:
            raise ReleaseDependencyError(
                "projected candidate does not replay its database binding"
            )
        candidates.append(candidate)
    return tuple(candidates)


def load_source_dependency_bindings(
    session: Session, release_id: int
) -> dict[str, SourceDependencyBinding]:
    """Load exact role-qualified source dependencies for a capability."""

    rows = session.execute(
        select(
            ReleaseSourceSnapshot.role,
            SourceSnapshot.id.label("source_snapshot_id"),
            SourceSnapshot.snapshot_key,
            SourceSnapshot.verified_manifest_sha256,
        )
        .join(SourceSnapshot, SourceSnapshot.id == ReleaseSourceSnapshot.source_snapshot_id)
        .where(ReleaseSourceSnapshot.release_id == release_id)
        .order_by(ReleaseSourceSnapshot.role)
    )
    bindings: dict[str, SourceDependencyBinding] = {}
    for row in rows:
        if (
            not row.role
            or row.role in bindings
            or _SHA256_RE.fullmatch(row.verified_manifest_sha256) is None
        ):
            raise ReleaseDependencyError("source dependency binding is incomplete")
        bindings[row.role] = SourceDependencyBinding(
            role=row.role,
            source_snapshot_id=row.source_snapshot_id,
            snapshot_key=row.snapshot_key,
            verified_manifest_sha256=row.verified_manifest_sha256,
        )
    if not bindings:
        raise ReleaseDependencyError("release has no source dependency binding")
    return bindings


def load_lineage_dependency_bindings(
    session: Session, release_id: int
) -> dict[LineageRole, LineageDependencyBinding]:
    """Load exact role-qualified lineage dependencies for a capability."""

    rows = session.execute(
        select(
            ReleaseLineageSnapshot.role,
            LineageSnapshot.id.label("snapshot_id"),
            LineageSnapshot.snapshot_key,
            LineageSnapshot.domain,
            LineageSnapshot.scheme_kind,
            LineageSnapshot.authority_namespace,
            LineageSnapshot.version,
            LineageSnapshot.snapshot_sha256,
        )
        .join(LineageSnapshot, LineageSnapshot.id == ReleaseLineageSnapshot.snapshot_id)
        .where(ReleaseLineageSnapshot.release_id == release_id)
        .order_by(ReleaseLineageSnapshot.role)
    )
    bindings: dict[LineageRole, LineageDependencyBinding] = {}
    allowed_roles: frozenset[str] = frozenset(
        {"assembly_source_taxonomy", "formal_viral_taxonomy", "study_viral_lineage"}
    )
    for row in rows:
        if (
            row.role not in allowed_roles
            or row.role in bindings
            or _SHA256_RE.fullmatch(row.snapshot_sha256) is None
        ):
            raise ReleaseDependencyError("lineage dependency binding is incomplete")
        role = cast(LineageRole, row.role)
        bindings[role] = LineageDependencyBinding(
            role=role,
            snapshot_id=row.snapshot_id,
            snapshot_key=row.snapshot_key,
            domain=cast(Literal["host", "viral"], row.domain),
            scheme_kind=cast(Literal["formal_taxonomy", "study_defined"], row.scheme_kind),
            authority_namespace=row.authority_namespace,
            version=row.version,
            snapshot_sha256=row.snapshot_sha256,
        )
    return bindings


def verify_release_evidence_bindings(
    session: Session,
    *,
    release_id: int,
    request: ReleaseValidationRequest,
    complete_lineage_closure_roles: tuple[LineageRole, ...],
) -> tuple[
    dict[str, SourceDependencyBinding],
    dict[LineageRole, LineageDependencyBinding],
]:
    """Bind approved scientific evidence to live public membership and dependencies."""

    source_bindings = load_source_dependency_bindings(session, release_id)
    if not any(
        binding.snapshot_key == request.source.source_snapshot_key
        and binding.verified_manifest_sha256 == request.source.verified_manifest_sha256
        for binding in source_bindings.values()
    ):
        raise ReleaseDependencyError("approved source evidence is not release-bound")

    lineage_bindings = load_lineage_dependency_bindings(session, release_id)
    ncbi = request.ncbi_taxonomy
    ictv = request.ictv
    if ncbi is None or ictv is None:
        raise ReleaseDependencyError("formal lineage evidence is unavailable")
    host = lineage_bindings.get("assembly_source_taxonomy")
    viral = lineage_bindings.get("formal_viral_taxonomy")
    if host is None or host.snapshot_key != ncbi.snapshot_key:
        raise ReleaseDependencyError("NCBI taxonomy evidence is not release-bound")
    if viral is None or viral.snapshot_key != ictv.msl_snapshot_key:
        raise ReleaseDependencyError("ICTV evidence is not release-bound")

    for role in complete_lineage_closure_roles:
        binding = lineage_bindings.get(role)
        if binding is None:
            raise ReleaseDependencyError("attested lineage closure role is not release-bound")
        term_count = session.scalar(
            select(func.count())
            .select_from(LineageTerm)
            .where(LineageTerm.snapshot_id == binding.snapshot_id)
        )
        missing_self = session.scalar(
            select(func.count())
            .select_from(LineageTerm)
            .where(
                LineageTerm.snapshot_id == binding.snapshot_id,
                ~select(LineageClosure.snapshot_id)
                .where(
                    LineageClosure.snapshot_id == binding.snapshot_id,
                    LineageClosure.ancestor_term_id == LineageTerm.id,
                    LineageClosure.descendant_term_id == LineageTerm.id,
                    LineageClosure.depth == 0,
                )
                .exists(),
            )
        )
        if not term_count or missing_self:
            raise ReleaseDependencyError("attested lineage closure is structurally incomplete")

    membership_keys = tuple(
        session.scalars(
            select(EVELocus.locus_key)
            .join(
                ReleaseLocusMembership,
                (ReleaseLocusMembership.release_id == EVELocus.release_id)
                & (ReleaseLocusMembership.locus_id == EVELocus.id),
            )
            .where(ReleaseLocusMembership.release_id == release_id)
            .order_by(EVELocus.locus_key)
        )
    )
    request_keys = tuple(sorted(candidate.locus_key for candidate in request.candidates))
    if membership_keys != request_keys:
        raise ReleaseDependencyError(
            "approved validation candidates do not equal public release memberships"
        )
    for candidate in request.candidates:
        if _candidate_binding_payload(
            session,
            release_id=release_id,
            locus_key=candidate.locus_key,
        ) != _approved_candidate_binding_payload(candidate):
            raise ReleaseDependencyError(
                "approved candidate evidence does not equal its database membership"
            )
    return source_bindings, lineage_bindings


__all__ = [
    "ReleaseDependencyError",
    "STRUCTURED_RELEASE_SCOPED_TABLES",
    "load_lineage_dependency_bindings",
    "load_source_dependency_bindings",
    "project_release_membership_candidates",
    "release_dependency_graph_sha256",
    "verify_release_evidence_bindings",
]
