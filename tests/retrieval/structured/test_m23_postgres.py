from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from eve_relation_rag.config import get_settings
from eve_relation_rag.db import Base
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
    LineageAlias,
    LineageClosure,
    LineageSnapshot,
    LineageTerm,
    MethodDefinition,
    ProcessRun,
    ReleaseAssemblyMembership,
    ReleaseAssertionMembership,
    ReleaseLineageSnapshot,
    ReleaseLocusMembership,
    ReleaseMethodDefinition,
    ReleaseSourceSnapshot,
    ScientificAssertion,
    SourceArtifact,
    SourceRecord,
    SourceSnapshot,
)
from eve_relation_rag.planning.query_plans import (
    PLAN_VERSION,
    AggregatePlan,
    AssemblyDetailPlan,
    AssemblyFilter,
    EntireReleaseScope,
    ExtractedCondition,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    LocusDetailPlan,
    LocusFilter,
    PageSpec,
    PlanningAudit,
    QueryFilter,
    SourceLineageFilter,
    ViralLineageFilter,
)
from eve_relation_rag.planning.resolver import LineageReference, ResolutionFailure
from eve_relation_rag.planning.sqlalchemy_resolver import SqlAlchemyReleaseResolverFactory
from eve_relation_rag.retrieval.structured.capability import (
    LineageDependencyBinding,
    LineageRole,
    SourceDependencyBinding,
)
from eve_relation_rag.retrieval.structured.compiler import StructuredQueryCompiler
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate
from eve_relation_rag.retrieval.structured.repository import (
    AssemblyPageSlice,
    LocusPageSlice,
    SourceTaxonPageSlice,
    StructuredRepository,
)
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    ErrorResponse,
    LocusDetailData,
)
from eve_relation_rag.retrieval.structured.semantic import ValidatedQuery
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService

ROOT = Path(__file__).resolve().parents[3]
RELEASE_KEY = "release:endoviho-rag:v0:20260827:998"
NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
PUBLIC_LOCUS_KEYS = tuple(f"locus:eve:v1:sha256:{character * 64}" for character in ("1", "2", "3"))
CANDIDATE_ONLY_LOCUS_KEY = f"locus:eve:v1:sha256:{'4' * 64}"


@dataclass(frozen=True, slots=True)
class TestsOnlyQueryableRelease:
    """Protocol double: this object is never returned as a real published release."""

    __test__ = False

    release_id: int
    dataset_key: Literal["dataset:endoviho-rag"]
    release_key: str
    status: Literal["published"]
    schema_version: str
    published_at: datetime
    manifest_sha256: str
    validation_receipt_key: str
    validation_receipt_sha256: str
    source_dependencies: Mapping[str, SourceDependencyBinding]
    lineage_dependencies: Mapping[LineageRole, LineageDependencyBinding]
    complete_lineage_closure_roles: frozenset[LineageRole]


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.environ.get("EVE_RAG_TEST_DATABASE_URL", get_settings().database_url)
    admin_engine = create_engine(database_url, poolclass=NullPool)
    schema = f"test_m23_{uuid4().hex}"
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    except OperationalError as exc:
        admin_engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc.orig}")

    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
        connection.commit()
        _upgrade_to_head(connection)

    def set_fixture_search_path(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        try:
            cursor.execute(f'SET search_path TO "{schema}", public')
        finally:
            cursor.close()
        dbapi_connection.commit()  # type: ignore[union-attr]

    event.listen(admin_engine, "connect", set_fixture_search_path)
    admin_engine.dispose()
    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    try:
        with Session(engine) as session:
            _insert_public_membership_fixture(session)
            session.commit()
        yield engine
    finally:
        engine.dispose()
        event.remove(admin_engine, "connect", set_fixture_search_path)
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def capability() -> TestsOnlyQueryableRelease:
    return TestsOnlyQueryableRelease(
        release_id=10,
        dataset_key="dataset:endoviho-rag",
        release_key=RELEASE_KEY,
        status="published",
        schema_version="synthetic-m2-v1",
        published_at=NOW,
        manifest_sha256=SHA_A,
        validation_receipt_key="tests-only:receipt",
        validation_receipt_sha256=SHA_B,
        source_dependencies={
            "primary_data": SourceDependencyBinding(
                role="primary_data",
                source_snapshot_id=20,
                snapshot_key="snapshot:fixture:source",
                verified_manifest_sha256=SHA_A,
            )
        },
        lineage_dependencies={
            "assembly_source_taxonomy": LineageDependencyBinding(
                role="assembly_source_taxonomy",
                snapshot_id=200,
                snapshot_key="snapshot:fixture:host",
                domain="host",
                scheme_kind="formal_taxonomy",
                authority_namespace="NCBI-Taxonomy",
                version="synthetic-v1",
                snapshot_sha256=SHA_A,
            ),
            "study_viral_lineage": LineageDependencyBinding(
                role="study_viral_lineage",
                snapshot_id=202,
                snapshot_key="snapshot:fixture:viral-study",
                domain="viral",
                scheme_kind="study_defined",
                authority_namespace="Zhao-2025-v4",
                version="v4",
                snapshot_sha256=SHA_B,
            ),
        },
        complete_lineage_closure_roles=frozenset(
            {"assembly_source_taxonomy", "study_viral_lineage"}
        ),
    )


def test_all_five_metrics_are_exact_and_membership_rooted(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    repository = StructuredRepository(postgres_engine)
    expected = {
        "distinct_included_locus_count": 3,
        "distinct_contig_count": 2,
        "distinct_assembly_count": 2,
        "distinct_source_taxon_count": 2,
        "detection_call_count": 4,
    }

    for metric_key, expected_value in expected.items():
        plan = AggregatePlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=RELEASE_KEY,
            original_question=f"Count {metric_key} across the entire release",
            intent="aggregate",
            scope=_entire_release(),
            metric_key=metric_key,
        )
        result = repository.query(_validated(capability, plan))
        assert isinstance(result, AggregateData)
        assert result.metric_key == metric_key
        assert result.value == expected_value


def test_assembly_detail_uses_only_assemblies_represented_by_public_loci(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = AssemblyDetailPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="Show assembly GCA_000000001.1",
        intent="assembly_detail",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                AssemblyFilter(
                    filter_type="assembly",
                    assembly_key="assembly:ncbi:GCA_000000001.1",
                ),
            ),
        ),
    )

    result = StructuredRepository(postgres_engine).query(_validated(capability, plan))

    assert isinstance(result, AssemblyDetailData)
    assert result.assembly.assembly_key == "assembly:ncbi:GCA_000000001.1"
    assert result.assembly.included_locus_count == 2


@pytest.mark.parametrize(
    "plan",
    (
        AssemblyDetailPlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=RELEASE_KEY,
            original_question="Show an assembly absent from public membership",
            intent="assembly_detail",
            scope=FilteredScope(
                scope_type="filtered",
                filters=(
                    AssemblyFilter(
                        filter_type="assembly",
                        assembly_key="assembly:ncbi:GCA_000000003.1",
                    ),
                ),
            ),
        ),
        LocusDetailPlan(
            plan_version=PLAN_VERSION,
            route="structured",
            release_key=RELEASE_KEY,
            original_question="Show a locus absent from public membership",
            intent="locus_detail",
            scope=FilteredScope(
                scope_type="filtered",
                filters=(
                    LocusFilter(
                        filter_type="locus",
                        locus_key=CANDIDATE_ONLY_LOCUS_KEY,
                    ),
                ),
            ),
        ),
    ),
    ids=("assembly-detail", "locus-detail"),
)
def test_semantically_validated_missing_detail_is_a_post_fact_integrity_error(
    plan: AssemblyDetailPlan | LocusDetailPlan,
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    with pytest.raises(RetrievalRefusal) as error:
        StructuredRepository(postgres_engine).query(_validated(capability, plan))

    assert error.value.code == "result_integrity_error"
    assert error.value.fact_retrieval_executed is True


@pytest.mark.parametrize(
    ("case_id", "filters", "expected_locus_keys"),
    (
        (
            "assembly-exact",
            (
                AssemblyFilter(
                    filter_type="assembly",
                    assembly_key="assembly:ncbi:GCA_000000001.1",
                ),
            ),
            PUBLIC_LOCUS_KEYS[:2],
        ),
        (
            "source-exact",
            (
                SourceLineageFilter(
                    filter_type="source_lineage",
                    snapshot_key="snapshot:fixture:host",
                    term_key="taxon:child-a",
                    role="assembly_source_taxonomy",
                    include_descendants=False,
                ),
            ),
            PUBLIC_LOCUS_KEYS[:2],
        ),
        (
            "source-descendants",
            (
                SourceLineageFilter(
                    filter_type="source_lineage",
                    snapshot_key="snapshot:fixture:host",
                    term_key="taxon:bivalvia",
                    role="assembly_source_taxonomy",
                    include_descendants=True,
                ),
            ),
            PUBLIC_LOCUS_KEYS,
        ),
        (
            "viral-exact",
            (
                ViralLineageFilter(
                    filter_type="viral_lineage",
                    snapshot_key="snapshot:fixture:viral-study",
                    term_key="viral:orthopolintovirales",
                    role="study_viral_lineage",
                    include_descendants=False,
                ),
            ),
            PUBLIC_LOCUS_KEYS[:2],
        ),
        (
            "source-and-viral",
            (
                SourceLineageFilter(
                    filter_type="source_lineage",
                    snapshot_key="snapshot:fixture:host",
                    term_key="taxon:child-a",
                    role="assembly_source_taxonomy",
                    include_descendants=False,
                ),
                ViralLineageFilter(
                    filter_type="viral_lineage",
                    snapshot_key="snapshot:fixture:viral-study",
                    term_key="viral:orthopolintovirales",
                    role="study_viral_lineage",
                    include_descendants=False,
                ),
            ),
            PUBLIC_LOCUS_KEYS[:2],
        ),
    ),
)
def test_production_repository_filter_matrix_has_exact_public_fact_sets(
    case_id: str,
    filters: tuple[QueryFilter, ...],
    expected_locus_keys: tuple[str, ...],
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question=f"Production fact matrix: {case_id}",
        intent="list_loci",
        scope=FilteredScope(scope_type="filtered", filters=filters),
        page=PageSpec(limit=100),
    )

    result = StructuredRepository(postgres_engine).query(_validated(capability, plan))

    assert isinstance(result, LocusPageSlice)
    assert tuple(item.locus_key for item in result.items) == expected_locus_keys
    assert result.total_count == len(expected_locus_keys)


def test_production_resolver_factory_uses_pinned_terms_and_public_suggestions(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    factory = SqlAlchemyReleaseResolverFactory(postgres_engine)

    resolver = factory.create(capability)
    cached = factory.create(capability)
    ancestor = resolver.resolve_lineage(
        LineageReference(
            original_input="Bivalvia",
            entity_kind="source_lineage",
            role="assembly_source_taxonomy",
            name="Bivalvia",
        )
    )

    assert cached is resolver
    assert resolver.resolve_assembly("GCA_000000001.1").entity_kind == "assembly"
    assert resolver.resolve_locus(PUBLIC_LOCUS_KEYS[0]).entity_kind == "locus"
    assert ancestor.stable_key == "taxon:bivalvia"
    suggestion_keys = {
        item.stable_key
        for item in resolver.suggest(
            "source_lineage",
            "Species",
            role="assembly_source_taxonomy",
        )
    }
    assert suggestion_keys == {"taxon:child-a", "taxon:child-b"}
    assert "taxon:bivalvia" not in suggestion_keys
    with pytest.raises(ResolutionFailure) as alias_collision:
        resolver.resolve_lineage(
            LineageReference(
                original_input="Shared bivalve alias",
                entity_kind="source_lineage",
                role="assembly_source_taxonomy",
                name="Shared bivalve alias",
            )
        )
    assert alias_collision.value.code == "entity_ambiguous"


def test_real_candidate_gate_refuses_before_public_fact_repository(
    postgres_engine: Engine,
) -> None:
    class RepositorySpy:
        def __init__(self) -> None:
            self.calls = 0

        def query(
            self,
            _validated_query: ValidatedQuery,
            *,
            page_after: tuple[str, ...] | None = None,
        ) -> AggregateData:
            del page_after
            self.calls += 1
            return AggregateData(
                metric_key="distinct_included_locus_count",
                value=3,
                unit="loci",
                deduplication_key="release_key+locus_key",
            )

    plan = AggregatePlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="Count distinct included loci across the entire release",
        intent="aggregate",
        scope=_entire_release(),
        metric_key="distinct_included_locus_count",
    )
    repository = RepositorySpy()
    service = StructuredRetrievalService(
        gate=PublishedReleaseGate(postgres_engine),
        repository=repository,
        cursor_secret=b"m2-postgres-cursor-secret-is-at-least-32-bytes",
    )

    response = service.query(plan, _validated_audit(plan))

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "release_not_published"
    assert response.fact_retrieval_executed is False
    assert repository.calls == 0


def test_public_pages_exclude_candidate_loci_and_allowlist_only_assemblies(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    repository = StructuredRepository(postgres_engine)
    locus_plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List all loci across the entire release",
        intent="list_loci",
        scope=_entire_release(),
        page=PageSpec(limit=100),
    )
    locus_result = repository.query(_validated(capability, locus_plan))
    assert isinstance(locus_result, LocusPageSlice)
    assert tuple(item.locus_key for item in locus_result.items) == PUBLIC_LOCUS_KEYS
    assert CANDIDATE_ONLY_LOCUS_KEY not in {item.locus_key for item in locus_result.items}
    assert locus_result.total_count == 3

    assembly_plan = ListAssembliesPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List all assemblies across the entire release",
        intent="list_assemblies",
        scope=_entire_release(),
        page=PageSpec(limit=100),
    )
    assembly_result = repository.query(_validated(capability, assembly_plan))
    assert isinstance(assembly_result, AssemblyPageSlice)
    assert [item.assembly_accession_version for item in assembly_result.items] == [
        "GCA_000000001.1",
        "GCA_000000002.1",
    ]
    assert [item.included_locus_count for item in assembly_result.items] == [2, 1]
    assert assembly_result.total_count == 2


def test_source_taxon_and_public_assertion_projections_are_exact(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    repository = StructuredRepository(postgres_engine)
    taxon_plan = ListSourceTaxaPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List all source taxa across the entire release",
        intent="list_source_taxa",
        scope=_entire_release(),
        page=PageSpec(limit=100),
    )
    taxon_result = repository.query(_validated(capability, taxon_plan))
    assert isinstance(taxon_result, SourceTaxonPageSlice)
    assert [item.lineage.term_key for item in taxon_result.items] == [
        "taxon:child-a",
        "taxon:child-b",
    ]
    assert [item.included_locus_count for item in taxon_result.items] == [2, 1]

    detail_plan = LocusDetailPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question=f"Show locus {PUBLIC_LOCUS_KEYS[0]}",
        intent="locus_detail",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(LocusFilter(filter_type="locus", locus_key=PUBLIC_LOCUS_KEYS[0]),),
        ),
    )
    detail = repository.query(_validated(capability, detail_plan))
    assert isinstance(detail, LocusDetailData)
    assert [item.call_key for item in detail.calls] == ["call:1", "call:2"]
    assert [item.assertion_key for item in detail.public_assertions] == [
        "assertion:1",
        "assertion:duplicate",
    ]
    assert [item.term_key for item in detail.locus.viral_lineages] == ["viral:orthopolintovirales"]
    assert detail.public_assertions[0].supporting_evidence.evidence_key == "evidence:1"
    assert detail.public_assertions[1].supporting_evidence.evidence_key == "evidence:duplicate"
    assert "assertion:unpublished" not in {item.assertion_key for item in detail.public_assertions}
    assert "evidence:extra-unselected" not in {
        item.supporting_evidence.evidence_key for item in detail.public_assertions
    }


def test_viral_filter_uses_only_public_assertion_membership(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List loci with study viral lineage Orthopolintovirales",
        intent="list_loci",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                ViralLineageFilter(
                    filter_type="viral_lineage",
                    snapshot_key="snapshot:fixture:viral-study",
                    term_key="viral:orthopolintovirales",
                    role="study_viral_lineage",
                    include_descendants=False,
                ),
            ),
        ),
        page=PageSpec(limit=100),
    )

    result = StructuredRepository(postgres_engine).query(_validated(capability, plan))

    assert isinstance(result, LocusPageSlice)
    assert tuple(item.locus_key for item in result.items) == PUBLIC_LOCUS_KEYS[:2]
    assert CANDIDATE_ONLY_LOCUS_KEY not in {item.locus_key for item in result.items}


def test_duplicate_assertions_evidence_aliases_and_lineage_joins_do_not_inflate_metric(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = AggregatePlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="Count loci with the duplicated public viral lineage assertion",
        intent="aggregate",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                ViralLineageFilter(
                    filter_type="viral_lineage",
                    snapshot_key="snapshot:fixture:viral-study",
                    term_key="viral:orthopolintovirales",
                    role="study_viral_lineage",
                    include_descendants=False,
                ),
            ),
        ),
        metric_key="distinct_included_locus_count",
    )

    result = StructuredRepository(postgres_engine).query(_validated(capability, plan))

    assert isinstance(result, AggregateData)
    assert result.value == 2


def test_attested_source_descendant_filter_uses_same_snapshot_closure(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List loci assigned to descendants of Bivalvia",
        intent="list_loci",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                SourceLineageFilter(
                    filter_type="source_lineage",
                    snapshot_key="snapshot:fixture:host",
                    term_key="taxon:bivalvia",
                    role="assembly_source_taxonomy",
                    include_descendants=True,
                ),
            ),
        ),
        page=PageSpec(limit=100),
    )

    result = StructuredRepository(postgres_engine).query(_validated(capability, plan))

    assert isinstance(result, LocusPageSlice)
    assert tuple(item.locus_key for item in result.items) == PUBLIC_LOCUS_KEYS


def test_repository_keyset_pages_have_stable_total_without_duplicates(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List all loci across the entire release",
        intent="list_loci",
        scope=_entire_release(),
        page=PageSpec(limit=2),
    )
    repository = StructuredRepository(postgres_engine)

    first = repository.query(_validated(capability, plan))
    assert isinstance(first, LocusPageSlice)
    assert first.has_more is True
    assert first.total_count == 3
    assert len(first.items) == 2

    second = repository.query(
        _validated(capability, plan),
        page_after=(first.items[-1].locus_key,),
    )
    assert isinstance(second, LocusPageSlice)
    assert second.has_more is False
    assert second.total_count == first.total_count
    concatenated = tuple(item.locus_key for item in (*first.items, *second.items))
    assert concatenated == PUBLIC_LOCUS_KEYS
    assert len(concatenated) == len(set(concatenated))


def test_missing_or_multiple_source_assignment_is_an_integrity_error(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = AggregatePlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="Count distinct included loci across the entire release",
        intent="aggregate",
        scope=_entire_release(),
        metric_key="distinct_included_locus_count",
    )
    validated = _validated(capability, plan)
    repository = StructuredRepository(postgres_engine)

    with Session(postgres_engine) as session:
        assignment = session.get(AssemblyTaxonAssignment, 520)
        assert assignment is not None
        session.delete(assignment)
        session.commit()
    try:
        with pytest.raises(RetrievalRefusal) as missing:
            repository.query(validated)
        assert missing.value.code == "result_integrity_error"
    finally:
        with Session(postgres_engine) as session:
            session.add(_source_assignment(520, "assignment:520", "policy:fixture-source-taxon"))
            session.commit()

    with Session(postgres_engine) as session:
        session.add(_source_assignment(523, "assignment:523", "policy:second-source-taxon"))
        session.commit()
    try:
        with pytest.raises(RetrievalRefusal) as multiple:
            repository.query(validated)
        assert multiple.value.code == "result_integrity_error"
    finally:
        with Session(postgres_engine) as session:
            assignment = session.get(AssemblyTaxonAssignment, 523)
            assert assignment is not None
            session.delete(assignment)
            session.commit()


def test_compiler_uses_bound_values_and_repository_sets_read_only_repeatable_read(
    postgres_engine: Engine,
    capability: TestsOnlyQueryableRelease,
) -> None:
    plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List all loci across the entire release",
        intent="list_loci",
        scope=_entire_release(),
        page=PageSpec(limit=2),
    )
    compiled = StructuredQueryCompiler().compile(capability, plan)
    sql = str(compiled.primary.compile(dialect=postgres_engine.dialect))
    assert "release_locus_membership" in sql
    assert RELEASE_KEY not in sql
    assert " LIMIT " in sql

    injection_shaped_term = "taxon:x';DROP_TABLE"
    filtered_plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question="List loci assigned exactly to a source lineage",
        intent="list_loci",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                SourceLineageFilter(
                    filter_type="source_lineage",
                    snapshot_key="snapshot:fixture:host",
                    term_key=injection_shaped_term,
                    role="assembly_source_taxonomy",
                    include_descendants=False,
                ),
            ),
        ),
        page=PageSpec(limit=2),
    )
    bound_compilation = StructuredQueryCompiler().compile(capability, filtered_plan)
    compiled_sql = bound_compilation.primary.compile(dialect=postgres_engine.dialect)
    assert injection_shaped_term not in str(compiled_sql)
    assert injection_shaped_term in compiled_sql.params.values()

    observed_options: list[Mapping[str, object]] = []

    def capture_options(connection: object, *_args: object) -> None:
        if hasattr(connection, "get_execution_options"):
            observed_options.append(connection.get_execution_options())  # type: ignore[union-attr]

    event.listen(postgres_engine, "before_execute", capture_options)
    try:
        result = StructuredRepository(postgres_engine).query(_validated(capability, plan))
    finally:
        event.remove(postgres_engine, "before_execute", capture_options)

    assert isinstance(result, LocusPageSlice)
    assert observed_options
    assert all(item.get("postgresql_readonly") is True for item in observed_options)
    assert all(item.get("isolation_level") == "REPEATABLE READ" for item in observed_options)


def _entire_release() -> EntireReleaseScope:
    return EntireReleaseScope(scope_type="entire_release", explicitly_requested=True)


def _source_assignment(
    assignment_id: int,
    assignment_key: str,
    policy_key: str,
) -> AssemblyTaxonAssignment:
    return AssemblyTaxonAssignment(
        id=assignment_id,
        assignment_key=assignment_key,
        release_id=10,
        assembly_id=500,
        snapshot_id=200,
        snapshot_role="assembly_source_taxonomy",
        term_id=301,
        assignment_policy_key=policy_key,
        source_artifact_id=21,
        source_locator={"fixture": assignment_id},
    )


def _validated(
    release: TestsOnlyQueryableRelease,
    plan: (
        AggregatePlan
        | AssemblyDetailPlan
        | ListLociPlan
        | ListAssembliesPlan
        | ListSourceTaxaPlan
        | LocusDetailPlan
    ),
) -> ValidatedQuery:
    audit = _validated_audit(plan)
    return ValidatedQuery(
        release=release,
        plan=plan,
        planning_audit=audit,
        resolved_entities=(),
    )


def _validated_audit(
    plan: (
        AggregatePlan
        | AssemblyDetailPlan
        | ListLociPlan
        | ListAssembliesPlan
        | ListSourceTaxaPlan
        | LocusDetailPlan
    ),
) -> PlanningAudit:
    condition = ExtractedCondition(
        condition_id="condition:intent",
        condition_kind="intent",
        source_text="query",
        source_start=0,
        source_end=5,
        mapped_target=f"intent:{plan.intent}",
    )
    return PlanningAudit(
        extracted_conditions=(condition,),
        mapped_condition_ids=(condition.condition_id,),
    )


def _upgrade_to_head(connection: object) -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revisions = list(reversed(list(script.walk_revisions(base="base", head="heads"))))
    for revision in revisions:
        with connection.begin():  # type: ignore[union-attr]
            context = MigrationContext.configure(
                connection,  # type: ignore[arg-type]
                opts={"target_metadata": Base.metadata},
            )
            with Operations.context(context):
                revision.module.upgrade()


def _insert_public_membership_fixture(session: Session) -> None:
    session.add(Dataset(id=1, dataset_key="dataset:endoviho-rag", title="Synthetic M2"))
    session.add(
        DatasetRelease(
            id=10,
            dataset_id=1,
            release_key=RELEASE_KEY,
            schema_version="synthetic-m2-v1",
            status="candidate",
            manifest_sha256=SHA_A,
        )
    )
    session.flush()

    session.add(
        SourceSnapshot(
            id=20,
            snapshot_key="snapshot:fixture:source",
            source_name="Synthetic fixture",
            source_version="v1",
            source_uri="https://example.invalid/source",
            retrieved_at=NOW,
            verified_manifest_sha256=SHA_A,
            verified_license_key="CC0-1.0",
        )
    )
    session.flush()
    session.add(
        SourceArtifact(
            id=21,
            snapshot_id=20,
            artifact_key="artifact:fixture",
            filename="fixture.json",
            media_type="application/json",
            byte_size=1,
            verified_sha256=SHA_B,
            source_uri="https://example.invalid/fixture.json",
            retrieved_at=NOW,
            verified_license_key="CC0-1.0",
            remote_checksum_verified=False,
        )
    )
    session.flush()
    session.add(ReleaseSourceSnapshot(release_id=10, source_snapshot_id=20, role="primary_data"))
    session.add(
        MethodDefinition(
            id=30,
            method_definition_key="method-definition:fixture:v1",
            method_key="method:fixture",
            version="v1",
            method_kind="source_assessment",
            definition_artifact_id=21,
            definition_sha256=SHA_C,
            parameter_schema={},
            output_schema={},
        )
    )
    session.flush()
    session.add(
        ReleaseMethodDefinition(
            release_id=10,
            method_definition_id=30,
            role="source_assessment",
        )
    )
    session.add(
        ImportRun(
            id=40,
            run_key="import-run:fixture",
            release_id=10,
            source_snapshot_id=20,
            source_artifact_id=21,
            importer_name="fixture",
            importer_version="v1",
            code_sha256=SHA_A,
            parameters={},
            parameters_sha256=SHA_B,
            status="succeeded",
            started_at=NOW,
            finished_at=NOW,
        )
    )
    session.flush()
    session.add_all(
        ProcessRun(
            id=process_id,
            process_run_key=f"process-run:{ordinal}",
            release_id=10,
            method_definition_id=30,
            method_role="source_assessment",
            import_run_id=40,
            execution_status="succeeded",
            software_agent_key="fixture-agent",
            parameters={},
            parameters_sha256=character * 64,
            started_at=NOW,
            finished_at=NOW,
        )
        for process_id, ordinal, character in ((50, 1, "1"), (51, 2, "2"))
    )

    session.add_all(
        (
            LineageSnapshot(
                id=200,
                snapshot_key="snapshot:fixture:host",
                domain="host",
                scheme_kind="formal_taxonomy",
                authority_namespace="NCBI-Taxonomy",
                version="synthetic-v1",
                source_artifact_id=21,
                snapshot_sha256=SHA_A,
            ),
            LineageSnapshot(
                id=202,
                snapshot_key="snapshot:fixture:viral-study",
                domain="viral",
                scheme_kind="study_defined",
                authority_namespace="Zhao-2025-v4",
                version="v4",
                source_artifact_id=21,
                snapshot_sha256=SHA_B,
            ),
        )
    )
    session.flush()
    session.add_all(
        (
            ReleaseLineageSnapshot(
                release_id=10,
                snapshot_id=200,
                role="assembly_source_taxonomy",
                domain="host",
                scheme_kind="formal_taxonomy",
            ),
            ReleaseLineageSnapshot(
                release_id=10,
                snapshot_id=202,
                role="study_viral_lineage",
                domain="viral",
                scheme_kind="study_defined",
            ),
        )
    )
    session.add_all(
        (
            LineageTerm(
                id=300,
                snapshot_id=200,
                term_key="taxon:bivalvia",
                canonical_name="Bivalvia",
                rank="class",
            ),
            LineageTerm(
                id=301,
                snapshot_id=200,
                term_key="taxon:child-a",
                canonical_name="Species alpha",
                rank="species",
            ),
            LineageTerm(
                id=302,
                snapshot_id=200,
                term_key="taxon:child-b",
                canonical_name="Species beta",
                rank="species",
            ),
            LineageTerm(
                id=402,
                snapshot_id=202,
                term_key="viral:orthopolintovirales",
                canonical_name="Orthopolintovirales",
                rank="order",
            ),
        )
    )
    session.flush()
    # Deliberate curated-alias collision: fact queries must never join aliases in a way that
    # multiplies public loci or metrics, and resolution must retain the ambiguity.
    session.add_all(
        (
            LineageAlias(
                id=450,
                snapshot_id=200,
                term_id=301,
                alias="Shared bivalve alias",
                normalized_alias="shared bivalve alias",
                alias_type="curated_english",
                locale="en",
            ),
            LineageAlias(
                id=451,
                snapshot_id=200,
                term_id=302,
                alias="Shared bivalve alias",
                normalized_alias="shared bivalve alias",
                alias_type="curated_english",
                locale="en",
            ),
        )
    )
    session.add_all(
        (
            LineageClosure(snapshot_id=200, ancestor_term_id=300, descendant_term_id=300, depth=0),
            LineageClosure(snapshot_id=200, ancestor_term_id=301, descendant_term_id=301, depth=0),
            LineageClosure(snapshot_id=200, ancestor_term_id=302, descendant_term_id=302, depth=0),
            LineageClosure(snapshot_id=200, ancestor_term_id=300, descendant_term_id=301, depth=1),
            LineageClosure(snapshot_id=200, ancestor_term_id=300, descendant_term_id=302, depth=1),
            LineageClosure(snapshot_id=202, ancestor_term_id=402, descendant_term_id=402, depth=0),
        )
    )

    assembly_specs = (
        (500, "GCA_000000001.1", "Species alpha", 510, "AA000001.1"),
        (501, "GCA_000000002.1", "Species beta", 511, "AA000002.1"),
        (502, "GCA_000000003.1", "Candidate only", 512, "AA000003.1"),
    )
    for assembly_id, accession, organism, sequence_id, contig in assembly_specs:
        session.add(
            GenomeAssembly(
                id=assembly_id,
                assembly_key=f"assembly:ncbi:{accession}",
                namespace="ncbi",
                accession_version=accession,
                source_organism_name=organism,
                source_artifact_id=21,
            )
        )
        session.flush()
        session.add(
            AssemblySequence(
                id=sequence_id,
                assembly_id=assembly_id,
                sequence_key=f"sequence:insdc:{contig}",
                namespace="insdc",
                accession_version=contig,
                sequence_length=10_000,
                source_artifact_id=21,
            )
        )
        session.add(ReleaseAssemblyMembership(release_id=10, assembly_id=assembly_id))
    session.flush()
    for assignment_id, assembly_id, term_id in (
        (520, 500, 301),
        (521, 501, 302),
        (522, 502, 301),
    ):
        session.add(
            AssemblyTaxonAssignment(
                id=assignment_id,
                assignment_key=f"assignment:{assignment_id}",
                release_id=10,
                assembly_id=assembly_id,
                snapshot_id=200,
                snapshot_role="assembly_source_taxonomy",
                term_id=term_id,
                assignment_policy_key="policy:fixture-source-taxon",
                source_artifact_id=21,
                source_locator={"fixture": assignment_id},
            )
        )

    occurrence_specs = (
        (600, 500, 510, "GCA_000000001.1", "AA000001.1", PUBLIC_LOCUS_KEYS[0], 700),
        (601, 500, 510, "GCA_000000001.1", "AA000001.1", PUBLIC_LOCUS_KEYS[1], 701),
        (602, 501, 511, "GCA_000000002.1", "AA000002.1", PUBLIC_LOCUS_KEYS[2], 702),
        (603, 502, 512, "GCA_000000003.1", "AA000003.1", CANDIDATE_ONLY_LOCUS_KEY, 703),
    )
    for ordinal, (
        source_record_id,
        assembly_id,
        sequence_id,
        assembly_accession,
        contig_accession,
        locus_key,
        placement_id,
    ) in enumerate(occurrence_specs, start=1):
        session.add(
            SourceRecord(
                id=source_record_id,
                source_record_key=f"source-record:{ordinal}",
                snapshot_id=20,
                artifact_id=21,
                worksheet="S3",
                row_number=ordinal + 1,
                native_vr_token=f"vr-{ordinal}",
                assembly_accession_version=assembly_accession,
                sequence_accession_version=contig_accession,
                source_locator={"worksheet": "S3", "row": ordinal + 1},
                raw_payload={"fixture": ordinal},
                raw_payload_sha256=str(ordinal) * 64,
            )
        )
        session.flush()
        session.add(
            EVELocus(
                id=610 + ordinal - 1,
                locus_key=locus_key,
                release_id=10,
                assembly_id=assembly_id,
                sequence_id=sequence_id,
                source_snapshot_id=20,
                source_record_id=source_record_id,
                native_vr_token=f"vr-{ordinal}",
                identity_policy_key="policy:locus-v1",
            )
        )
        session.flush()
        session.add(
            EVELocusPlacement(
                id=placement_id,
                placement_key=f"placement:{ordinal}",
                release_id=10,
                locus_id=610 + ordinal - 1,
                assembly_id=assembly_id,
                sequence_id=sequence_id,
                start0=ordinal * 100,
                end0=ordinal * 100 + 50,
                strand="+",
                precision="exact",
                coordinate_system="0-based-half-open",
                source_artifact_id=21,
                source_locator={"fixture": ordinal},
                placement_sha256=str(ordinal + 4) * 64,
            )
        )
    session.flush()

    call_specs = (
        (800, "call:1", 600, 610, 50),
        (801, "call:2", 600, 610, 51),
        (802, "call:3", 601, 611, 50),
        (803, "call:4", 602, 612, 50),
        (804, "call:candidate", 603, 613, 50),
    )
    for call_id, call_key, source_record_id, locus_id, process_run_id in call_specs:
        session.add(
            DetectionCall(
                id=call_id,
                call_key=call_key,
                release_id=10,
                source_snapshot_id=20,
                source_record_id=source_record_id,
                locus_id=locus_id,
                process_run_id=process_run_id,
                process_run_status="succeeded",
                source_method_key="method:fixture",
                source_locator={"call": call_key},
                raw_result={"fixture": True},
            )
        )
    session.flush()

    for ordinal, (source_record_id, locus_id, call_id) in enumerate(
        ((600, 610, 800), (601, 611, 802), (602, 612, 803), (603, 613, 804)),
        start=1,
    ):
        session.add(
            ImportLedger(
                id=900 + ordinal,
                run_id=40,
                release_id=10,
                source_record_id=source_record_id,
                call_id=call_id,
                locus_id=locus_id,
                outcome="normalized_candidate",
                result_payload={"fixture": ordinal},
                result_sha256=str(ordinal + 5) * 64,
                processed_at=NOW,
            )
        )
    session.flush()

    for ordinal, (locus_id, placement_id) in enumerate(
        ((610, 700), (611, 701), (612, 702)), start=1
    ):
        left_id = 1000 + ordinal * 2
        right_id = left_id + 1
        for assessment_id, side in ((left_id, "left"), (right_id, "right")):
            session.add(
                FlankAssessment(
                    id=assessment_id,
                    assessment_key=f"flank:{ordinal}:{side}",
                    release_id=10,
                    locus_id=locus_id,
                    placement_id=placement_id,
                    side=side,
                    verdict="supported",
                    inspection_window_bp=100,
                    available_bp=100,
                    inspected_bp=100,
                    assessment_policy_key="policy:flank-v1",
                    method_or_curator_key="fixture-curator",
                    evidence_artifact_id=21,
                    evidence_locator={"side": side},
                    assessed_at=NOW,
                )
            )
        decision_id = 1100 + ordinal
        session.add(
            InclusionDecision(
                id=decision_id,
                decision_key=f"decision:{ordinal}",
                release_id=10,
                locus_id=locus_id,
                placement_id=placement_id,
                import_ledger_id=900 + ordinal,
                import_outcome="normalized_candidate",
                decision_code="include",
                policy_key="policy:include-v1",
                authorized_by="fixture-curator",
                reason_code="fixture_supported",
                rationale="Synthetic constrained repository fixture.",
                decided_at=NOW,
            )
        )
        session.flush()
        session.add(
            ReleaseLocusMembership(
                release_id=10,
                locus_id=locus_id,
                placement_id=placement_id,
                placement_precision="exact",
                inclusion_decision_id=decision_id,
                decision_code="include",
                left_flank_assessment_id=left_id,
                left_flank_side="left",
                left_flank_verdict="supported",
                right_flank_assessment_id=right_id,
                right_flank_side="right",
                right_flank_verdict="supported",
            )
        )
    session.flush()

    assertion_specs = (
        (1201, "assertion:1", 800, 610, 50, 1301, "evidence:1"),
        (1202, "assertion:2", 802, 611, 50, 1302, "evidence:2"),
        (1203, "assertion:candidate", 804, 613, 50, 1303, "evidence:candidate"),
        (1204, "assertion:duplicate", 800, 610, 50, 1304, "evidence:duplicate"),
        (1205, "assertion:unpublished", 800, 610, 50, 1305, "evidence:unpublished"),
    )
    for (
        assertion_id,
        assertion_key,
        call_id,
        locus_id,
        process_id,
        evidence_id,
        evidence_key,
    ) in assertion_specs:
        session.add(
            EvidenceItem(
                id=evidence_id,
                evidence_key=evidence_key,
                release_id=10,
                source_snapshot_id=20,
                source_artifact_id=21,
                evidence_type="source_row",
                source_locator={"assertion": assertion_key},
                evidence_sha256=str((evidence_id % 9) + 1) * 64,
                summary="Synthetic evidence.",
            )
        )
        session.add(
            ScientificAssertion(
                id=assertion_id,
                assertion_key=assertion_key,
                release_id=10,
                call_id=call_id,
                locus_id=locus_id,
                process_run_id=process_id,
                process_run_status="succeeded",
                assertion_type="viral_major_taxon",
                predicate_key="source:viral-major-taxon",
                asserted_value="Orthopolintovirales",
                lineage_snapshot_id=202,
                lineage_snapshot_role="study_viral_lineage",
                lineage_term_id=402,
                result_payload={"fixture": True},
            )
        )
        session.flush()
        session.add(
            AssertionEvidence(
                release_id=10,
                assertion_id=assertion_id,
                evidence_id=evidence_id,
                relation="supports",
            )
        )
    session.flush()
    session.add(
        EvidenceItem(
            id=1306,
            evidence_key="evidence:extra-unselected",
            release_id=10,
            source_snapshot_id=20,
            source_artifact_id=21,
            evidence_type="source_row",
            source_locator={"assertion": "assertion:1", "selected": False},
            evidence_sha256="6" * 64,
            summary="Extra evidence edge that public membership did not select.",
        )
    )
    session.flush()
    session.add(
        AssertionEvidence(
            release_id=10,
            assertion_id=1201,
            evidence_id=1306,
            relation="supports",
        )
    )
    session.flush()
    for assertion_id, locus_id, process_id, evidence_id in (
        (1201, 610, 50, 1301),
        (1202, 611, 50, 1302),
        (1204, 610, 50, 1304),
    ):
        session.add(
            ReleaseAssertionMembership(
                release_id=10,
                assertion_id=assertion_id,
                locus_id=locus_id,
                process_run_id=process_id,
                process_run_status="succeeded",
                supporting_evidence_id=evidence_id,
                evidence_relation="supports",
            )
        )
