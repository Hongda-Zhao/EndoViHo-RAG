from __future__ import annotations

from eve_relation_rag.application import StructuredQueryApplication
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import PageSpec
from eve_relation_rag.planning.resolver import CatalogReleaseResolver, LineageResolverRecord
from eve_relation_rag.retrieval.structured.capability import LineageDependencyBinding
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.results import ErrorResponse, PlanSuccess, QuerySuccess
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService
from tests.support.m2 import (
    TEST_CURSOR_SECRET,
    TEST_RELEASE_KEY,
    FakeAggregateRepository,
    FakeGate,
    FakeResolverFactory,
    make_aggregate_application,
)
from tests.support.m2 import (
    TestsOnlyQueryableRelease as SyntheticReleaseCapability,
)


def _aggregate_request() -> StructuredQueryRequest:
    return StructuredQueryRequest(
        release_key=TEST_RELEASE_KEY,
        question="Count distinct included loci in this release.",
    )


def test_release_is_gated_before_resolver_or_fact_repository() -> None:
    gate = FakeGate(
        refusal=RetrievalRefusal(
            "release_not_published",
            "The requested release is not published.",
        )
    )
    factory = FakeResolverFactory()
    repository = FakeAggregateRepository()
    application = StructuredQueryApplication(
        gate=gate,
        resolver_factory=factory,
        retrieval=None,
    )

    response = application.query(_aggregate_request())

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "release_not_published"
    assert response.fact_retrieval_executed is False
    assert response.query_plan is None
    assert gate.calls == [TEST_RELEASE_KEY]
    assert factory.calls == []
    assert repository.calls == []


def test_plan_gates_and_resolves_but_never_executes_facts() -> None:
    application, gate, factory, repository = make_aggregate_application()

    response = application.plan(_aggregate_request())

    assert isinstance(response, PlanSuccess)
    assert response.query_plan.intent == "aggregate"
    assert response.fact_retrieval_executed is False
    assert gate.calls == [TEST_RELEASE_KEY]
    assert factory.calls == [TEST_RELEASE_KEY]
    assert repository.calls == []


def test_plan_authenticates_cursor_during_shared_pre_fact_validation() -> None:
    application, gate, factory, repository = make_aggregate_application()
    request = StructuredQueryRequest(
        release_key=TEST_RELEASE_KEY,
        question="List all loci in this release.",
        page=PageSpec(cursor="abc"),
    )

    response = application.plan(request)

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "cursor_invalid"
    assert response.fact_retrieval_executed is False
    assert gate.calls == [TEST_RELEASE_KEY]
    assert factory.calls == [TEST_RELEASE_KEY]
    assert repository.calls == []


def test_plan_runs_lineage_capability_validation_without_fact_retrieval() -> None:
    snapshot_key = "lineage-snapshot:ncbi-taxonomy:synthetic"
    dependency = LineageDependencyBinding(
        role="assembly_source_taxonomy",
        snapshot_id=1,
        snapshot_key=snapshot_key,
        domain="host",
        scheme_kind="formal_taxonomy",
        authority_namespace="ncbi-taxonomy",
        version="synthetic-v1",
        snapshot_sha256="c" * 64,
    )
    gate = FakeGate(
        release=SyntheticReleaseCapability(
            lineage_dependencies={"assembly_source_taxonomy": dependency}
        )
    )
    factory = FakeResolverFactory()
    factory.resolver = CatalogReleaseResolver(
        release_key=TEST_RELEASE_KEY,
        lineages=(
            LineageResolverRecord(
                entity_kind="source_lineage",
                term_key="ncbi-taxonomy:taxid:6544",
                canonical_name="Bivalvia",
                snapshot_key=snapshot_key,
                authority_namespace="ncbi-taxonomy",
                snapshot_version="synthetic-v1",
                scheme_kind="formal_taxonomy",
                role="assembly_source_taxonomy",
            ),
        ),
    )
    repository = FakeAggregateRepository()
    retrieval = StructuredRetrievalService(
        gate=gate,
        repository=repository,
        cursor_secret=TEST_CURSOR_SECRET,
    )
    application = StructuredQueryApplication(
        gate=gate,
        resolver_factory=factory,
        retrieval=retrieval,
    )
    request = StructuredQueryRequest(
        release_key=TEST_RELEASE_KEY,
        question=("List loci assigned to source lineage Bivalvia including descendants."),
    )

    response = application.plan(request)

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "lineage_closure_incomplete"
    assert response.fact_retrieval_executed is False
    assert repository.calls == []


def test_query_reuses_the_single_gate_capability_and_executes_once() -> None:
    application, gate, factory, repository = make_aggregate_application(value=7)

    response = application.query(_aggregate_request())

    assert isinstance(response, QuerySuccess)
    assert response.structured_result.data.kind == "aggregate"
    assert response.structured_result.data.value == 7
    assert gate.calls == [TEST_RELEASE_KEY]
    assert factory.calls == [TEST_RELEASE_KEY]
    assert len(repository.calls) == 1


def test_m4_pre_fact_hook_runs_after_complete_plan_and_before_repository() -> None:
    application, gate, factory, repository = make_aggregate_application(value=7)
    observed: list[str] = []

    def hook(release: SyntheticReleaseCapability, planned: PlanSuccess) -> None:
        observed.append("pre_fact_hook")
        assert release.release_key == TEST_RELEASE_KEY
        assert planned.query_plan.intent == "aggregate"
        assert gate.calls == [TEST_RELEASE_KEY]
        assert factory.calls == [TEST_RELEASE_KEY]
        assert repository.calls == []

    response = application.query_with_pre_fact_hook(_aggregate_request(), hook)

    assert isinstance(response, QuerySuccess)
    assert observed == ["pre_fact_hook"]
    assert gate.calls == [TEST_RELEASE_KEY]
    assert factory.calls == [TEST_RELEASE_KEY]
    assert len(repository.calls) == 1


def test_missing_runtime_cursor_secret_fails_after_planning_without_fact_query() -> None:
    gate = FakeGate()
    factory = FakeResolverFactory()
    application = StructuredQueryApplication(
        gate=gate,
        resolver_factory=factory,
        retrieval=None,
    )

    response = application.query(_aggregate_request())

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "unsupported_capability"
    assert response.query_plan is not None
    assert response.planning_audit is not None
    assert response.fact_retrieval_executed is False


def test_plan_also_fails_closed_when_pre_fact_service_is_unavailable() -> None:
    gate = FakeGate()
    factory = FakeResolverFactory()
    application = StructuredQueryApplication(
        gate=gate,
        resolver_factory=factory,
        retrieval=None,
    )

    response = application.plan(_aggregate_request())

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "unsupported_capability"
    assert response.fact_retrieval_executed is False
