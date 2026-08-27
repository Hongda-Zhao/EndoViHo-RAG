"""Fixed SQLAlchemy compiler for membership-rooted structured retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Select, and_, distinct, exists, func, literal, select, tuple_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.selectable import Subquery

from eve_relation_rag.db.models import (
    AssemblySequence,
    AssemblyTaxonAssignment,
    DetectionCall,
    EVELocus,
    EVELocusPlacement,
    EvidenceItem,
    GenomeAssembly,
    LineageClosure,
    LineageSnapshot,
    LineageTerm,
    MethodDefinition,
    ProcessRun,
    ReleaseAssertionMembership,
    ReleaseLocusMembership,
    ScientificAssertion,
    SourceArtifact,
    SourceRecord,
)
from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
    LocusFilter,
    SourceLineageFilter,
    StructuredPlan,
    ViralLineageFilter,
)
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal

type CompiledIntent = Literal[
    "assembly_detail",
    "locus_detail",
    "list_loci",
    "list_assemblies",
    "list_source_taxa",
    "aggregate",
]


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    """Closed compiler output; statements contain bound values only."""

    intent: CompiledIntent
    matched_loci: Subquery
    primary: Select[Any]
    total: Select[Any] | None = None


class StructuredQueryCompiler:
    """Compile only approved plan variants to fixed SQLAlchemy shapes."""

    def compile(
        self,
        release: ReleaseCapability,
        plan: StructuredPlan,
        *,
        page_after: tuple[str, ...] | None = None,
    ) -> CompiledQuery:
        matched = self._matched_loci(release, plan)

        if isinstance(plan, AssemblyDetailPlan):
            return CompiledQuery(
                intent="assembly_detail",
                matched_loci=matched,
                primary=self._assembly_rows(matched),
            )
        if isinstance(plan, LocusDetailPlan):
            return CompiledQuery(
                intent="locus_detail",
                matched_loci=matched,
                primary=select(matched.c.locus_id, matched.c.locus_key).order_by(
                    matched.c.locus_key.asc()
                ),
            )
        if isinstance(plan, ListLociPlan):
            self._require_page_after_arity(page_after, 1)
            primary = select(matched.c.locus_id, matched.c.locus_key)
            if page_after is not None:
                primary = primary.where(matched.c.locus_key > page_after[0])
            return CompiledQuery(
                intent="list_loci",
                matched_loci=matched,
                primary=primary.order_by(matched.c.locus_key.asc()).limit(plan.page.limit + 1),
                total=self._total(matched),
            )
        if isinstance(plan, ListAssembliesPlan):
            self._require_page_after_arity(page_after, 2)
            rows = self._assembly_rows(matched).subquery("matched_assemblies")
            primary = select(rows)
            if page_after is not None:
                primary = primary.where(
                    tuple_(rows.c.assembly_accession_version, rows.c.assembly_key)
                    > tuple_(literal(page_after[0]), literal(page_after[1]))
                )
            return CompiledQuery(
                intent="list_assemblies",
                matched_loci=matched,
                primary=primary.order_by(
                    rows.c.assembly_accession_version.asc(), rows.c.assembly_key.asc()
                ).limit(plan.page.limit + 1),
                total=select(func.count()).select_from(rows),
            )
        if isinstance(plan, ListSourceTaxaPlan):
            self._require_page_after_arity(page_after, 2)
            rows = self._source_taxon_rows(matched, release).subquery("matched_source_taxa")
            primary = select(rows)
            if page_after is not None:
                primary = primary.where(
                    tuple_(rows.c.snapshot_key, rows.c.term_key)
                    > tuple_(literal(page_after[0]), literal(page_after[1]))
                )
            return CompiledQuery(
                intent="list_source_taxa",
                matched_loci=matched,
                primary=primary.order_by(rows.c.snapshot_key.asc(), rows.c.term_key.asc()).limit(
                    plan.page.limit + 1
                ),
                total=select(func.count()).select_from(rows),
            )
        if isinstance(plan, AggregatePlan):
            return CompiledQuery(
                intent="aggregate",
                matched_loci=matched,
                primary=self._aggregate(matched, release, plan.metric_key),
            )
        raise RetrievalRefusal(
            "compiler_constraint_unmapped",
            "query intent has no fixed compiler mapping",
        )

    def assignment_integrity_statement(self, release: ReleaseCapability) -> Select[Any]:
        """Find public-member assemblies without exactly one source assignment."""

        public_assemblies = (
            select(EVELocus.assembly_id.label("assembly_id"))
            .select_from(ReleaseLocusMembership)
            .join(
                EVELocus,
                and_(
                    EVELocus.release_id == ReleaseLocusMembership.release_id,
                    EVELocus.id == ReleaseLocusMembership.locus_id,
                ),
            )
            .where(ReleaseLocusMembership.release_id == release.release_id)
            .distinct()
            .subquery("public_assemblies")
        )
        return (
            select(
                public_assemblies.c.assembly_id,
                func.count(AssemblyTaxonAssignment.id).label("assignment_count"),
            )
            .select_from(public_assemblies)
            .outerjoin(
                AssemblyTaxonAssignment,
                and_(
                    AssemblyTaxonAssignment.release_id == release.release_id,
                    AssemblyTaxonAssignment.assembly_id == public_assemblies.c.assembly_id,
                    AssemblyTaxonAssignment.snapshot_role == "assembly_source_taxonomy",
                ),
            )
            .group_by(public_assemblies.c.assembly_id)
            .having(func.count(AssemblyTaxonAssignment.id) != 1)
        )

    @staticmethod
    def locus_projection_statement(
        release: ReleaseCapability, locus_ids: tuple[int, ...]
    ) -> Select[Any]:
        return (
            select(
                EVELocus.id.label("locus_id"),
                EVELocus.locus_key,
                EVELocus.assembly_id,
                GenomeAssembly.assembly_key,
                GenomeAssembly.accession_version.label("assembly_accession_version"),
                GenomeAssembly.source_organism_name,
                AssemblySequence.sequence_key,
                AssemblySequence.accession_version.label("sequence_accession_version"),
                EVELocusPlacement.start0,
                EVELocusPlacement.end0,
                EVELocusPlacement.strand,
                EVELocusPlacement.coordinate_system,
                EVELocusPlacement.precision,
            )
            .select_from(ReleaseLocusMembership)
            .join(
                EVELocus,
                and_(
                    EVELocus.release_id == ReleaseLocusMembership.release_id,
                    EVELocus.id == ReleaseLocusMembership.locus_id,
                ),
            )
            .join(GenomeAssembly, GenomeAssembly.id == EVELocus.assembly_id)
            .join(
                AssemblySequence,
                and_(
                    AssemblySequence.id == EVELocus.sequence_id,
                    AssemblySequence.assembly_id == EVELocus.assembly_id,
                ),
            )
            .join(
                EVELocusPlacement,
                and_(
                    EVELocusPlacement.id == ReleaseLocusMembership.placement_id,
                    EVELocusPlacement.release_id == ReleaseLocusMembership.release_id,
                    EVELocusPlacement.locus_id == ReleaseLocusMembership.locus_id,
                ),
            )
            .where(
                ReleaseLocusMembership.release_id == release.release_id,
                EVELocus.id.in_(locus_ids),
            )
            .order_by(EVELocus.locus_key.asc())
        )

    @staticmethod
    def assembly_taxon_statement(
        release: ReleaseCapability, assembly_ids: tuple[int, ...]
    ) -> Select[Any]:
        return (
            select(
                AssemblyTaxonAssignment.assembly_id,
                LineageTerm.term_key,
                LineageTerm.canonical_name,
                LineageTerm.rank,
                LineageSnapshot.snapshot_key,
                LineageSnapshot.authority_namespace,
                LineageSnapshot.version.label("snapshot_version"),
                LineageSnapshot.scheme_kind,
                AssemblyTaxonAssignment.snapshot_role.label("role"),
            )
            .select_from(AssemblyTaxonAssignment)
            .join(
                LineageTerm,
                and_(
                    LineageTerm.snapshot_id == AssemblyTaxonAssignment.snapshot_id,
                    LineageTerm.id == AssemblyTaxonAssignment.term_id,
                ),
            )
            .join(LineageSnapshot, LineageSnapshot.id == AssemblyTaxonAssignment.snapshot_id)
            .where(
                AssemblyTaxonAssignment.release_id == release.release_id,
                AssemblyTaxonAssignment.snapshot_role == "assembly_source_taxonomy",
                AssemblyTaxonAssignment.assembly_id.in_(assembly_ids),
            )
            .order_by(AssemblyTaxonAssignment.assembly_id.asc())
        )

    @staticmethod
    def viral_lineage_statement(
        release: ReleaseCapability, locus_ids: tuple[int, ...]
    ) -> Select[Any]:
        return (
            select(
                ReleaseAssertionMembership.locus_id,
                LineageTerm.term_key,
                LineageTerm.canonical_name,
                LineageTerm.rank,
                LineageSnapshot.snapshot_key,
                LineageSnapshot.authority_namespace,
                LineageSnapshot.version.label("snapshot_version"),
                LineageSnapshot.scheme_kind,
                ScientificAssertion.lineage_snapshot_role.label("role"),
            )
            .select_from(ReleaseAssertionMembership)
            .join(
                ScientificAssertion,
                and_(
                    ScientificAssertion.release_id == ReleaseAssertionMembership.release_id,
                    ScientificAssertion.id == ReleaseAssertionMembership.assertion_id,
                ),
            )
            .join(
                LineageTerm,
                and_(
                    LineageTerm.snapshot_id == ScientificAssertion.lineage_snapshot_id,
                    LineageTerm.id == ScientificAssertion.lineage_term_id,
                ),
            )
            .join(LineageSnapshot, LineageSnapshot.id == ScientificAssertion.lineage_snapshot_id)
            .where(
                ReleaseAssertionMembership.release_id == release.release_id,
                ReleaseAssertionMembership.locus_id.in_(locus_ids),
                ScientificAssertion.assertion_type == "viral_major_taxon",
            )
            .distinct()
            .order_by(
                ReleaseAssertionMembership.locus_id.asc(),
                LineageSnapshot.snapshot_key.asc(),
                LineageTerm.term_key.asc(),
                ScientificAssertion.lineage_snapshot_role.asc(),
            )
        )

    @staticmethod
    def call_detail_statement(release: ReleaseCapability, locus_id: int) -> Select[Any]:
        return (
            select(
                DetectionCall.call_key,
                DetectionCall.source_method_key,
                ProcessRun.process_run_key,
                SourceRecord.source_record_key,
                SourceArtifact.artifact_key,
                SourceArtifact.verified_sha256.label("artifact_sha256"),
                SourceRecord.worksheet,
                SourceRecord.row_number,
            )
            .select_from(DetectionCall)
            .join(ProcessRun, ProcessRun.id == DetectionCall.process_run_id)
            .join(SourceRecord, SourceRecord.id == DetectionCall.source_record_id)
            .join(SourceArtifact, SourceArtifact.id == SourceRecord.artifact_id)
            .where(
                DetectionCall.release_id == release.release_id,
                DetectionCall.locus_id == locus_id,
            )
            .order_by(DetectionCall.call_key.asc())
        )

    @staticmethod
    def assertion_detail_statement(release: ReleaseCapability, locus_id: int) -> Select[Any]:
        lineage_term = aliased(LineageTerm, name="assertion_lineage_term")
        lineage_snapshot = aliased(LineageSnapshot, name="assertion_lineage_snapshot")
        return (
            select(
                ScientificAssertion.assertion_key,
                ScientificAssertion.assertion_type,
                ScientificAssertion.predicate_key,
                ScientificAssertion.asserted_value,
                ScientificAssertion.source_label,
                ScientificAssertion.source_confidence,
                ScientificAssertion.lineage_snapshot_role.label("lineage_role"),
                lineage_term.term_key.label("lineage_term_key"),
                lineage_term.canonical_name.label("lineage_canonical_name"),
                lineage_term.rank.label("lineage_rank"),
                lineage_snapshot.snapshot_key.label("lineage_snapshot_key"),
                lineage_snapshot.authority_namespace.label("lineage_authority_namespace"),
                lineage_snapshot.version.label("lineage_snapshot_version"),
                lineage_snapshot.scheme_kind.label("lineage_scheme_kind"),
                MethodDefinition.method_definition_key,
                MethodDefinition.version.label("method_version"),
                ProcessRun.process_run_key,
                EvidenceItem.evidence_key,
                EvidenceItem.evidence_type,
                EvidenceItem.evidence_sha256,
                EvidenceItem.source_locator,
                EvidenceItem.summary,
                SourceArtifact.artifact_key,
                SourceArtifact.verified_sha256.label("artifact_sha256"),
                SourceArtifact.source_uri,
                SourceArtifact.verified_license_key,
            )
            .select_from(ReleaseAssertionMembership)
            .join(
                ScientificAssertion,
                and_(
                    ScientificAssertion.release_id == ReleaseAssertionMembership.release_id,
                    ScientificAssertion.id == ReleaseAssertionMembership.assertion_id,
                ),
            )
            .join(ProcessRun, ProcessRun.id == ReleaseAssertionMembership.process_run_id)
            .join(MethodDefinition, MethodDefinition.id == ProcessRun.method_definition_id)
            .join(
                EvidenceItem,
                and_(
                    EvidenceItem.release_id == ReleaseAssertionMembership.release_id,
                    EvidenceItem.id == ReleaseAssertionMembership.supporting_evidence_id,
                ),
            )
            .join(SourceArtifact, SourceArtifact.id == EvidenceItem.source_artifact_id)
            .outerjoin(
                lineage_term,
                and_(
                    lineage_term.snapshot_id == ScientificAssertion.lineage_snapshot_id,
                    lineage_term.id == ScientificAssertion.lineage_term_id,
                ),
            )
            .outerjoin(
                lineage_snapshot,
                lineage_snapshot.id == ScientificAssertion.lineage_snapshot_id,
            )
            .where(
                ReleaseAssertionMembership.release_id == release.release_id,
                ReleaseAssertionMembership.locus_id == locus_id,
                ReleaseAssertionMembership.evidence_relation == "supports",
            )
            .order_by(ScientificAssertion.assertion_key.asc())
        )

    def _matched_loci(self, release: ReleaseCapability, plan: StructuredPlan) -> Subquery:
        statement = (
            select(
                EVELocus.id.label("locus_id"),
                EVELocus.locus_key.label("locus_key"),
                EVELocus.assembly_id.label("assembly_id"),
                EVELocus.sequence_id.label("sequence_id"),
                ReleaseLocusMembership.placement_id.label("placement_id"),
            )
            .select_from(ReleaseLocusMembership)
            .join(
                EVELocus,
                and_(
                    EVELocus.release_id == ReleaseLocusMembership.release_id,
                    EVELocus.id == ReleaseLocusMembership.locus_id,
                ),
            )
            .where(ReleaseLocusMembership.release_id == release.release_id)
        )
        if isinstance(plan.scope, FilteredScope):
            for query_filter in plan.scope.filters:
                if isinstance(query_filter, AssemblyFilter):
                    statement = statement.where(
                        exists(
                            select(1).where(
                                GenomeAssembly.id == EVELocus.assembly_id,
                                GenomeAssembly.assembly_key == query_filter.assembly_key,
                            )
                        )
                    )
                elif isinstance(query_filter, LocusFilter):
                    statement = statement.where(EVELocus.locus_key == query_filter.locus_key)
                elif isinstance(query_filter, SourceLineageFilter):
                    statement = statement.where(
                        self._source_lineage_constraint(release, query_filter)
                    )
                elif isinstance(query_filter, ViralLineageFilter):
                    statement = statement.where(
                        self._viral_lineage_constraint(release, query_filter)
                    )
                else:  # pragma: no cover - Pydantic's closed union prevents this.
                    raise RetrievalRefusal(
                        "compiler_constraint_unmapped",
                        "query filter has no fixed compiler mapping",
                    )
        return statement.distinct().subquery("matched_public_loci")

    @staticmethod
    def _source_lineage_constraint(
        release: ReleaseCapability, query_filter: SourceLineageFilter
    ) -> Any:
        binding = release.lineage_dependencies[query_filter.role]
        if not query_filter.include_descendants:
            return exists(
                select(1)
                .select_from(AssemblyTaxonAssignment)
                .join(
                    LineageTerm,
                    and_(
                        LineageTerm.snapshot_id == AssemblyTaxonAssignment.snapshot_id,
                        LineageTerm.id == AssemblyTaxonAssignment.term_id,
                    ),
                )
                .where(
                    AssemblyTaxonAssignment.release_id == release.release_id,
                    AssemblyTaxonAssignment.assembly_id == EVELocus.assembly_id,
                    AssemblyTaxonAssignment.snapshot_id == binding.snapshot_id,
                    AssemblyTaxonAssignment.snapshot_role == query_filter.role,
                    LineageTerm.term_key == query_filter.term_key,
                )
            )

        ancestor = aliased(LineageTerm, name="source_ancestor")
        return exists(
            select(1)
            .select_from(AssemblyTaxonAssignment)
            .join(
                LineageClosure,
                and_(
                    LineageClosure.snapshot_id == AssemblyTaxonAssignment.snapshot_id,
                    LineageClosure.descendant_term_id == AssemblyTaxonAssignment.term_id,
                ),
            )
            .join(
                ancestor,
                and_(
                    ancestor.snapshot_id == LineageClosure.snapshot_id,
                    ancestor.id == LineageClosure.ancestor_term_id,
                ),
            )
            .where(
                AssemblyTaxonAssignment.release_id == release.release_id,
                AssemblyTaxonAssignment.assembly_id == EVELocus.assembly_id,
                AssemblyTaxonAssignment.snapshot_id == binding.snapshot_id,
                AssemblyTaxonAssignment.snapshot_role == query_filter.role,
                ancestor.term_key == query_filter.term_key,
            )
        )

    @staticmethod
    def _viral_lineage_constraint(
        release: ReleaseCapability, query_filter: ViralLineageFilter
    ) -> Any:
        binding = release.lineage_dependencies[query_filter.role]
        base = (
            select(1)
            .select_from(ReleaseAssertionMembership)
            .join(
                ScientificAssertion,
                and_(
                    ScientificAssertion.release_id == ReleaseAssertionMembership.release_id,
                    ScientificAssertion.id == ReleaseAssertionMembership.assertion_id,
                    ScientificAssertion.locus_id == ReleaseAssertionMembership.locus_id,
                ),
            )
            .where(
                ReleaseAssertionMembership.release_id == release.release_id,
                ReleaseAssertionMembership.locus_id == EVELocus.id,
                ScientificAssertion.assertion_type == "viral_major_taxon",
                ScientificAssertion.lineage_snapshot_id == binding.snapshot_id,
                ScientificAssertion.lineage_snapshot_role == query_filter.role,
            )
        )
        if not query_filter.include_descendants:
            return exists(
                base.join(
                    LineageTerm,
                    and_(
                        LineageTerm.snapshot_id == ScientificAssertion.lineage_snapshot_id,
                        LineageTerm.id == ScientificAssertion.lineage_term_id,
                    ),
                ).where(LineageTerm.term_key == query_filter.term_key)
            )

        ancestor = aliased(LineageTerm, name="viral_ancestor")
        return exists(
            base.join(
                LineageClosure,
                and_(
                    LineageClosure.snapshot_id == ScientificAssertion.lineage_snapshot_id,
                    LineageClosure.descendant_term_id == ScientificAssertion.lineage_term_id,
                ),
            )
            .join(
                ancestor,
                and_(
                    ancestor.snapshot_id == LineageClosure.snapshot_id,
                    ancestor.id == LineageClosure.ancestor_term_id,
                ),
            )
            .where(ancestor.term_key == query_filter.term_key)
        )

    @staticmethod
    def _total(matched: Subquery) -> Select[Any]:
        return select(func.count()).select_from(matched)

    @staticmethod
    def _assembly_rows(matched: Subquery) -> Select[Any]:
        return (
            select(
                matched.c.assembly_id,
                GenomeAssembly.assembly_key,
                GenomeAssembly.accession_version.label("assembly_accession_version"),
                GenomeAssembly.source_organism_name,
                func.count(matched.c.locus_id).label("included_locus_count"),
            )
            .select_from(matched)
            .join(GenomeAssembly, GenomeAssembly.id == matched.c.assembly_id)
            .group_by(
                matched.c.assembly_id,
                GenomeAssembly.assembly_key,
                GenomeAssembly.accession_version,
                GenomeAssembly.source_organism_name,
            )
        )

    @staticmethod
    def _source_taxon_rows(matched: Subquery, release: ReleaseCapability) -> Select[Any]:
        return (
            select(
                AssemblyTaxonAssignment.snapshot_id,
                AssemblyTaxonAssignment.term_id,
                LineageSnapshot.snapshot_key,
                LineageTerm.term_key,
                LineageTerm.canonical_name,
                LineageTerm.rank,
                LineageSnapshot.authority_namespace,
                LineageSnapshot.version.label("snapshot_version"),
                LineageSnapshot.scheme_kind,
                AssemblyTaxonAssignment.snapshot_role.label("role"),
                func.count(distinct(matched.c.assembly_id)).label("represented_assembly_count"),
                func.count(matched.c.locus_id).label("included_locus_count"),
            )
            .select_from(matched)
            .join(
                AssemblyTaxonAssignment,
                and_(
                    AssemblyTaxonAssignment.release_id == release.release_id,
                    AssemblyTaxonAssignment.assembly_id == matched.c.assembly_id,
                    AssemblyTaxonAssignment.snapshot_role == "assembly_source_taxonomy",
                ),
            )
            .join(
                LineageTerm,
                and_(
                    LineageTerm.snapshot_id == AssemblyTaxonAssignment.snapshot_id,
                    LineageTerm.id == AssemblyTaxonAssignment.term_id,
                ),
            )
            .join(LineageSnapshot, LineageSnapshot.id == AssemblyTaxonAssignment.snapshot_id)
            .group_by(
                AssemblyTaxonAssignment.snapshot_id,
                AssemblyTaxonAssignment.term_id,
                LineageSnapshot.snapshot_key,
                LineageTerm.term_key,
                LineageTerm.canonical_name,
                LineageTerm.rank,
                LineageSnapshot.authority_namespace,
                LineageSnapshot.version,
                LineageSnapshot.scheme_kind,
                AssemblyTaxonAssignment.snapshot_role,
            )
        )

    @staticmethod
    def _aggregate(
        matched: Subquery,
        release: ReleaseCapability,
        metric_key: str,
    ) -> Select[Any]:
        if metric_key == "distinct_included_locus_count":
            return select(func.count()).select_from(matched)
        if metric_key == "distinct_contig_count":
            universe = (
                select(matched.c.assembly_id, matched.c.sequence_id)
                .distinct()
                .subquery("matched_contigs")
            )
            return select(func.count()).select_from(universe)
        if metric_key == "distinct_assembly_count":
            universe = (
                select(matched.c.assembly_id).distinct().subquery("matched_assemblies_metric")
            )
            return select(func.count()).select_from(universe)
        if metric_key == "distinct_source_taxon_count":
            universe = (
                select(
                    AssemblyTaxonAssignment.snapshot_id,
                    AssemblyTaxonAssignment.term_id,
                )
                .select_from(matched)
                .join(
                    AssemblyTaxonAssignment,
                    and_(
                        AssemblyTaxonAssignment.release_id == release.release_id,
                        AssemblyTaxonAssignment.assembly_id == matched.c.assembly_id,
                        AssemblyTaxonAssignment.snapshot_role == "assembly_source_taxonomy",
                    ),
                )
                .distinct()
                .subquery("matched_source_taxa_metric")
            )
            return select(func.count()).select_from(universe)
        if metric_key == "detection_call_count":
            universe = (
                select(DetectionCall.call_key)
                .select_from(matched)
                .join(
                    DetectionCall,
                    and_(
                        DetectionCall.release_id == release.release_id,
                        DetectionCall.locus_id == matched.c.locus_id,
                    ),
                )
                .distinct()
                .subquery("matched_calls")
            )
            return select(func.count()).select_from(universe)
        raise RetrievalRefusal(
            "compiler_constraint_unmapped",
            "aggregate metric has no fixed compiler mapping",
        )

    @staticmethod
    def _require_page_after_arity(page_after: tuple[str, ...] | None, expected: int) -> None:
        if page_after is not None and len(page_after) != expected:
            raise RetrievalRefusal(
                "cursor_plan_mismatch",
                "cursor sort key does not match the query intent",
            )
