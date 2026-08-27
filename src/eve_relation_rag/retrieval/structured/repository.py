"""Read-only repository for fixed structured retrieval statements."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from eve_relation_rag.planning.query_plans import (
    AggregatePlan,
    AssemblyDetailPlan,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
)
from eve_relation_rag.retrieval.structured.compiler import StructuredQueryCompiler
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblySummary,
    CallDetail,
    EvidenceDetail,
    ExactPlacement,
    LineageRef,
    LocusDetailData,
    LocusSummary,
    MetricUnit,
    PublicAssertionDetail,
    SourceTaxonSummary,
)
from eve_relation_rag.retrieval.structured.semantic import ValidatedQuery


@dataclass(frozen=True, slots=True)
class LocusPageSlice:
    """Repository-owned page values before cursor encoding."""

    items: tuple[LocusSummary, ...]
    total_count: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class AssemblyPageSlice:
    """Repository-owned assembly page before cursor encoding."""

    items: tuple[AssemblySummary, ...]
    total_count: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class SourceTaxonPageSlice:
    """Repository-owned source-taxon page before cursor encoding."""

    items: tuple[SourceTaxonSummary, ...]
    total_count: int
    has_more: bool


type RepositoryResult = (
    AssemblyDetailData
    | LocusDetailData
    | LocusPageSlice
    | AssemblyPageSlice
    | SourceTaxonPageSlice
    | AggregateData
)

_METRIC_METADATA = {
    "distinct_included_locus_count": ("loci", "release_key+locus_key"),
    "distinct_contig_count": (
        "contigs",
        "assembly_accession_version+sequence_accession_version",
    ),
    "distinct_assembly_count": ("assemblies", "assembly_accession_version"),
    "distinct_source_taxon_count": ("source_taxa", "snapshot_key+term_key"),
    "detection_call_count": ("source_calls", "release_key+call_key"),
}


class StructuredRepository:
    """Execute fixed queries in one read-only repeatable-read transaction.

    The only public operation accepts a :class:`ValidatedQuery`; callers cannot
    pass a bare ``release_id``, a statement, raw SQL, selected columns, or an
    order expression.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        compiler: StructuredQueryCompiler | None = None,
    ) -> None:
        self._engine = engine
        self._compiler = compiler or StructuredQueryCompiler()

    def query(
        self,
        validated: ValidatedQuery,
        *,
        page_after: tuple[str, ...] | None = None,
    ) -> RepositoryResult:
        compiled = self._compiler.compile(
            validated.release,
            validated.plan,
            page_after=page_after,
        )
        fact_retrieval_executed = False
        try:
            connection_options: dict[str, Any] = {
                "isolation_level": "REPEATABLE READ",
                "postgresql_readonly": True,
            }
            with self._engine.connect().execution_options(**connection_options) as connection:
                with Session(bind=connection) as session, session.begin():
                    fact_retrieval_executed = True
                    integrity_issue = session.execute(
                        self._compiler.assignment_integrity_statement(validated.release)
                    ).first()
                    if integrity_issue is not None:
                        raise RetrievalRefusal(
                            "result_integrity_error",
                            "a public-member assembly does not have exactly one source taxon",
                            fact_retrieval_executed=True,
                        )
                    return self._execute_compiled(session, validated, compiled)
        except RetrievalRefusal:
            raise
        except ValidationError as exc:
            raise RetrievalRefusal(
                "result_integrity_error",
                "database projection violates the structured result contract",
                fact_retrieval_executed=True,
            ) from exc
        except Exception as exc:
            raise RetrievalRefusal(
                "structured_query_failed",
                "structured retrieval failed",
                fact_retrieval_executed=fact_retrieval_executed,
            ) from exc

    def _execute_compiled(
        self,
        session: Session,
        validated: ValidatedQuery,
        compiled: Any,
    ) -> RepositoryResult:
        plan = validated.plan
        if isinstance(plan, AssemblyDetailPlan):
            rows = session.execute(compiled.primary).all()
            if not rows:
                raise self._integrity(
                    "validated assembly detail disappeared from public release membership"
                )
            if len(rows) != 1:
                raise self._integrity("assembly detail returned multiple assemblies")
            summaries = self._assembly_summaries(session, validated, rows)
            return AssemblyDetailData(assembly=summaries[0])

        if isinstance(plan, LocusDetailPlan):
            rows = session.execute(compiled.primary).all()
            if not rows:
                raise self._integrity(
                    "validated locus detail disappeared from public release membership"
                )
            if len(rows) != 1:
                raise self._integrity("locus detail returned multiple loci")
            locus_id = int(rows[0].locus_id)
            summary = self._locus_summaries(session, validated, (locus_id,))[0]
            calls = tuple(
                CallDetail(
                    call_key=row.call_key,
                    source_method_key=row.source_method_key,
                    process_run_key=row.process_run_key,
                    source_record_key=row.source_record_key,
                    artifact_key=row.artifact_key,
                    artifact_sha256=row.artifact_sha256,
                    worksheet=row.worksheet,
                    row_number=int(row.row_number),
                )
                for row in session.execute(
                    self._compiler.call_detail_statement(validated.release, locus_id)
                )
            )
            assertions = tuple(
                self._public_assertion(row)
                for row in session.execute(
                    self._compiler.assertion_detail_statement(validated.release, locus_id)
                )
            )
            return LocusDetailData(
                locus=summary,
                calls=calls,
                public_assertions=assertions,
            )

        if isinstance(plan, ListLociPlan):
            total = self._required_scalar(session, compiled.total)
            rows = session.execute(compiled.primary).all()
            has_more = len(rows) > plan.page.limit
            rows = rows[: plan.page.limit]
            locus_ids = tuple(int(row.locus_id) for row in rows)
            locus_items = self._locus_summaries(session, validated, locus_ids) if locus_ids else ()
            return LocusPageSlice(
                items=locus_items,
                total_count=total,
                has_more=has_more,
            )

        if isinstance(plan, ListAssembliesPlan):
            total = self._required_scalar(session, compiled.total)
            rows = session.execute(compiled.primary).all()
            has_more = len(rows) > plan.page.limit
            rows = rows[: plan.page.limit]
            assembly_items = self._assembly_summaries(session, validated, rows) if rows else ()
            return AssemblyPageSlice(
                items=assembly_items,
                total_count=total,
                has_more=has_more,
            )

        if isinstance(plan, ListSourceTaxaPlan):
            total = self._required_scalar(session, compiled.total)
            rows = session.execute(compiled.primary).all()
            has_more = len(rows) > plan.page.limit
            rows = rows[: plan.page.limit]
            source_taxon_items = tuple(self._source_taxon_summary(row) for row in rows)
            return SourceTaxonPageSlice(
                items=source_taxon_items,
                total_count=total,
                has_more=has_more,
            )

        if isinstance(plan, AggregatePlan):
            value = self._required_scalar(session, compiled.primary)
            unit, deduplication_key = _METRIC_METADATA[plan.metric_key]
            return AggregateData(
                metric_key=plan.metric_key,
                value=value,
                unit=cast(MetricUnit, unit),
                deduplication_key=deduplication_key,
            )

        raise RetrievalRefusal(
            "compiler_constraint_unmapped",
            "query intent has no repository mapping",
        )

    def _locus_summaries(
        self,
        session: Session,
        validated: ValidatedQuery,
        locus_ids: tuple[int, ...],
    ) -> tuple[LocusSummary, ...]:
        projection_rows = session.execute(
            self._compiler.locus_projection_statement(validated.release, locus_ids)
        ).all()
        if len(projection_rows) != len(locus_ids):
            raise self._integrity("membership-selected locus placement is missing or duplicated")

        assembly_ids = tuple(sorted({int(row.assembly_id) for row in projection_rows}))
        taxon_rows = session.execute(
            self._compiler.assembly_taxon_statement(validated.release, assembly_ids)
        ).all()
        taxon_by_assembly = {int(row.assembly_id): self._lineage_ref(row) for row in taxon_rows}
        if len(taxon_by_assembly) != len(assembly_ids) or len(taxon_rows) != len(assembly_ids):
            raise self._integrity("effective source-taxon projection is missing or duplicated")

        viral_by_locus: dict[int, list[LineageRef]] = {locus_id: [] for locus_id in locus_ids}
        for row in session.execute(
            self._compiler.viral_lineage_statement(validated.release, locus_ids)
        ):
            viral_by_locus[int(row.locus_id)].append(self._lineage_ref(row))

        summaries = tuple(
            LocusSummary(
                locus_key=row.locus_key,
                assembly_key=row.assembly_key,
                assembly_accession_version=row.assembly_accession_version,
                source_organism_name=row.source_organism_name,
                source_taxon=taxon_by_assembly[int(row.assembly_id)],
                placement=ExactPlacement(
                    sequence_key=row.sequence_key,
                    sequence_accession_version=row.sequence_accession_version,
                    start0=int(row.start0),
                    end0=int(row.end0),
                    strand=row.strand,
                    coordinate_system=row.coordinate_system,
                    precision=row.precision,
                ),
                viral_lineages=tuple(viral_by_locus[int(row.locus_id)]),
            )
            for row in projection_rows
        )
        if tuple(item.locus_key for item in summaries) != tuple(
            sorted(item.locus_key for item in summaries)
        ):
            raise self._integrity("locus projection is not in canonical order")
        return summaries

    def _assembly_summaries(
        self,
        session: Session,
        validated: ValidatedQuery,
        rows: Sequence[Any],
    ) -> tuple[AssemblySummary, ...]:
        assembly_ids = tuple(int(row.assembly_id) for row in rows)
        taxon_rows = session.execute(
            self._compiler.assembly_taxon_statement(validated.release, assembly_ids)
        ).all()
        taxon_by_assembly = {int(row.assembly_id): self._lineage_ref(row) for row in taxon_rows}
        if len(taxon_by_assembly) != len(assembly_ids) or len(taxon_rows) != len(assembly_ids):
            raise self._integrity("effective source-taxon projection is missing or duplicated")
        return tuple(
            AssemblySummary(
                assembly_key=row.assembly_key,
                assembly_accession_version=row.assembly_accession_version,
                source_organism_name=row.source_organism_name,
                source_taxon=taxon_by_assembly[int(row.assembly_id)],
                included_locus_count=int(row.included_locus_count),
            )
            for row in rows
        )

    @staticmethod
    def _source_taxon_summary(row: Any) -> SourceTaxonSummary:
        return SourceTaxonSummary(
            lineage=StructuredRepository._lineage_ref(row),
            represented_assembly_count=int(row.represented_assembly_count),
            included_locus_count=int(row.included_locus_count),
        )

    @staticmethod
    def _lineage_ref(row: Any) -> LineageRef:
        return LineageRef(
            term_key=row.term_key,
            canonical_name=row.canonical_name,
            rank=row.rank,
            snapshot_key=row.snapshot_key,
            authority_namespace=row.authority_namespace,
            snapshot_version=row.snapshot_version,
            scheme_kind=row.scheme_kind,
            role=row.role,
        )

    @staticmethod
    def _public_assertion(row: Any) -> PublicAssertionDetail:
        lineage = None
        if row.lineage_term_key is not None:
            lineage = LineageRef(
                term_key=row.lineage_term_key,
                canonical_name=row.lineage_canonical_name,
                rank=row.lineage_rank,
                snapshot_key=row.lineage_snapshot_key,
                authority_namespace=row.lineage_authority_namespace,
                snapshot_version=row.lineage_snapshot_version,
                scheme_kind=row.lineage_scheme_kind,
                role=row.lineage_role,
            )
        return PublicAssertionDetail(
            assertion_key=row.assertion_key,
            assertion_type=row.assertion_type,
            predicate_key=row.predicate_key,
            asserted_value=row.asserted_value,
            source_label=row.source_label,
            source_confidence=row.source_confidence,
            lineage=lineage,
            method_definition_key=row.method_definition_key,
            method_version=row.method_version,
            process_run_key=row.process_run_key,
            supporting_evidence=EvidenceDetail(
                evidence_key=row.evidence_key,
                evidence_type=row.evidence_type,
                evidence_sha256=row.evidence_sha256,
                source_locator=row.source_locator,
                summary=row.summary,
                artifact_key=row.artifact_key,
                artifact_sha256=row.artifact_sha256,
                source_uri=row.source_uri,
                verified_license_key=row.verified_license_key,
            ),
        )

    @staticmethod
    def _required_scalar(session: Session, statement: Any | None) -> int:
        if statement is None:
            raise StructuredRepository._integrity("required count statement is missing")
        value = session.scalar(statement)
        if value is None:
            raise StructuredRepository._integrity("required aggregate value is missing")
        return int(value)

    @staticmethod
    def _integrity(message: str) -> RetrievalRefusal:
        return RetrievalRefusal(
            "result_integrity_error",
            message,
            fact_retrieval_executed=True,
        )
