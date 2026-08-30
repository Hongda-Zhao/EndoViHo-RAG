from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Literal, cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from eve_relation_rag.planning.query_plans import (
    PLAN_VERSION,
    AggregatePlan,
    EntireReleaseScope,
    ExtractedCondition,
    FilteredScope,
    ListLociPlan,
    PageSpec,
    PlanningAudit,
    SourceLineageFilter,
)
from eve_relation_rag.retrieval.structured.capability import (
    LineageDependencyBinding,
    LineageRole,
    SourceDependencyBinding,
)
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.gate import PublishedReleaseGate
from eve_relation_rag.retrieval.structured.repository import RepositoryResult
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    ErrorResponse,
    QuerySuccess,
    ResolvedEntity,
)
from eve_relation_rag.retrieval.structured.semantic import ValidatedQuery
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService

RELEASE_KEY = "release:endoviho-rag:v0:20260827:999"
SECRET = b"m2-test-cursor-secret-is-at-least-32-bytes"
AGGREGATE_QUESTION = "Count distinct included loci in this release"
DESCENDANT_QUESTION = "List loci assigned to source lineage Bivalvia including descendants"


@dataclass(frozen=True, slots=True)
class TestsOnlyQueryableRelease:
    __test__: ClassVar[bool] = False

    release_id: int = 17
    dataset_key: Literal["dataset:endoviho-rag"] = "dataset:endoviho-rag"
    release_key: str = RELEASE_KEY
    status: Literal["published", "validation_candidate"] = "published"
    schema_version: str = "synthetic-m2-v1"
    published_at: datetime = datetime(2026, 8, 27, tzinfo=UTC)
    manifest_sha256: str = "a" * 64
    validation_receipt_key: str = "tests-only:receipt"
    validation_receipt_sha256: str = "b" * 64
    candidate_validation_input_sha256: str | None = None
    candidate_capability_sha256: str | None = None
    source_dependencies: dict[str, SourceDependencyBinding] | None = None
    lineage_dependencies: dict[LineageRole, LineageDependencyBinding] | None = None
    complete_lineage_closure_roles: frozenset[LineageRole] = frozenset()

    def __post_init__(self) -> None:
        if self.source_dependencies is None:
            object.__setattr__(self, "source_dependencies", {})
        if self.lineage_dependencies is None:
            object.__setattr__(
                self,
                "lineage_dependencies",
                {
                    "assembly_source_taxonomy": LineageDependencyBinding(
                        role="assembly_source_taxonomy",
                        snapshot_id=31,
                        snapshot_key="snapshot:ncbi-taxonomy:synthetic",
                        domain="host",
                        scheme_kind="formal_taxonomy",
                        authority_namespace="NCBI-Taxonomy",
                        version="synthetic",
                        snapshot_sha256="c" * 64,
                    )
                },
            )


class FakeGate:
    def __init__(
        self,
        release: TestsOnlyQueryableRelease | None = None,
        refusal: RetrievalRefusal | None = None,
    ) -> None:
        self.release = release or TestsOnlyQueryableRelease()
        self.refusal = refusal
        self.calls: list[str] = []

    def authorize(self, release_key: str) -> TestsOnlyQueryableRelease:
        self.calls.append(release_key)
        if self.refusal is not None:
            raise self.refusal
        return self.release


class FakeRepository:
    def __init__(self, result: RepositoryResult) -> None:
        self.result = result
        self.calls: list[tuple[ValidatedQuery, tuple[str, ...] | None]] = []

    def query(
        self,
        validated: ValidatedQuery,
        *,
        page_after: tuple[str, ...] | None = None,
    ) -> RepositoryResult:
        self.calls.append((validated, page_after))
        return self.result


def _aggregate_plan() -> AggregatePlan:
    return AggregatePlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question=AGGREGATE_QUESTION,
        intent="aggregate",
        scope=EntireReleaseScope(
            scope_type="entire_release",
            explicitly_requested=True,
        ),
        metric_key="distinct_included_locus_count",
    )


def _complete_audit() -> PlanningAudit:
    conditions = (
        _condition(
            AGGREGATE_QUESTION,
            condition_id="condition:intent",
            condition_kind="intent",
            source_text="Count",
            mapped_target="intent:aggregate",
        ),
        _condition(
            AGGREGATE_QUESTION,
            condition_id="condition:metric",
            condition_kind="metric",
            source_text="distinct included loci",
            mapped_target="metric_key:distinct_included_locus_count",
        ),
        _condition(
            AGGREGATE_QUESTION,
            condition_id="condition:scope",
            condition_kind="scope",
            source_text="in this release",
            mapped_target="scope:entire_release",
        ),
    )
    return PlanningAudit(
        extracted_conditions=conditions,
        mapped_condition_ids=tuple(item.condition_id for item in conditions),
    )


def _descendant_audit() -> PlanningAudit:
    conditions = (
        _condition(
            DESCENDANT_QUESTION,
            condition_id="condition:intent",
            condition_kind="intent",
            source_text="List loci",
            mapped_target="intent:list_loci",
        ),
        _condition(
            DESCENDANT_QUESTION,
            condition_id="condition:entity",
            condition_kind="entity",
            source_text="source lineage Bivalvia",
            mapped_target="scope.filter:source_lineage.term",
        ),
        _condition(
            DESCENDANT_QUESTION,
            condition_id="condition:scope",
            condition_kind="scope",
            source_text="including descendants",
            mapped_target="scope.filter:source_lineage.include_descendants",
        ),
    )
    return PlanningAudit(
        extracted_conditions=conditions,
        mapped_condition_ids=tuple(item.condition_id for item in conditions),
    )


def _condition(
    question: str,
    *,
    condition_id: str,
    condition_kind: Literal["intent", "entity", "metric", "scope"],
    source_text: str,
    mapped_target: str,
) -> ExtractedCondition:
    start = question.index(source_text)
    return ExtractedCondition(
        condition_id=condition_id,
        condition_kind=condition_kind,
        source_text=source_text,
        source_start=start,
        source_end=start + len(source_text),
        mapped_target=mapped_target,
    )


def _audit_from_conditions(
    conditions: tuple[ExtractedCondition, ...],
) -> PlanningAudit:
    return PlanningAudit(
        extracted_conditions=conditions,
        mapped_condition_ids=tuple(item.condition_id for item in conditions),
    )


def test_release_refusal_returns_error_and_executes_zero_fact_queries() -> None:
    gate = FakeGate(
        refusal=RetrievalRefusal(
            "release_not_published",
            "release is not published and cannot be queried",
        )
    )
    repository = FakeRepository(
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=1,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )
    )
    service = StructuredRetrievalService(
        gate=gate,
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query(_aggregate_plan(), _complete_audit())

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "release_not_published"
    assert response.fact_retrieval_executed is False
    assert response.resolved_entities == ()
    assert gate.calls == [RELEASE_KEY]
    assert repository.calls == []


def test_query_success_binds_release_plan_and_scientific_limitation() -> None:
    gate = FakeGate()
    repository = FakeRepository(
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=3,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )
    )
    service = StructuredRetrievalService(
        gate=gate,
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query(_aggregate_plan(), _complete_audit())

    assert isinstance(response, QuerySuccess)
    assert response.structured_result.release.release_key == RELEASE_KEY
    assert response.structured_result.data.value == 3
    assert [item.code for item in response.structured_result.limitations] == [
        "assembly_local_locus_is_not_independent_integration_event"
    ]
    assert len(repository.calls) == 1


@pytest.mark.parametrize(
    ("audit", "case"),
    (
        (
            _audit_from_conditions(_complete_audit().extracted_conditions[:-1]),
            "missing scope target",
        ),
        (
            _audit_from_conditions(
                (
                    *_complete_audit().extracted_conditions[:2],
                    _condition(
                        AGGREGATE_QUESTION,
                        condition_id="condition:extra-entity",
                        condition_kind="entity",
                        source_text="in this release",
                        mapped_target="scope.filter:source_lineage.term",
                    ),
                )
            ),
            "extra target",
        ),
        (
            _audit_from_conditions(
                (
                    _complete_audit().extracted_conditions[0],
                    _condition(
                        AGGREGATE_QUESTION,
                        condition_id="condition:metric-as-entity",
                        condition_kind="entity",
                        source_text="distinct included loci",
                        mapped_target="metric_key:distinct_included_locus_count",
                    ),
                    _complete_audit().extracted_conditions[2],
                )
            ),
            "wrong condition kind",
        ),
        (
            _audit_from_conditions(
                (
                    _complete_audit().extracted_conditions[0],
                    ExtractedCondition(
                        condition_id="condition:forged-span",
                        condition_kind="metric",
                        source_text="distinct included loci",
                        source_start=0,
                        source_end=len("distinct included loci"),
                        mapped_target="metric_key:distinct_included_locus_count",
                    ),
                    _complete_audit().extracted_conditions[2],
                )
            ),
            "forged source span",
        ),
    ),
)
def test_semantic_audit_mismatch_fails_before_public_repository(
    audit: PlanningAudit,
    case: str,
) -> None:
    repository = FakeRepository(
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=3,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )
    )
    service = StructuredRetrievalService(
        gate=FakeGate(),
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query(_aggregate_plan(), audit)

    assert case
    assert isinstance(response, ErrorResponse)
    assert response.error.code == "condition_unmapped"
    assert response.fact_retrieval_executed is False
    assert repository.calls == []


def test_descendant_query_without_receipt_attestation_fails_before_repository() -> None:
    release = TestsOnlyQueryableRelease()
    gate = FakeGate(release=release)
    repository = FakeRepository(
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=3,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )
    )
    plan = ListLociPlan(
        plan_version=PLAN_VERSION,
        route="structured",
        release_key=RELEASE_KEY,
        original_question=DESCENDANT_QUESTION,
        intent="list_loci",
        scope=FilteredScope(
            scope_type="filtered",
            filters=(
                SourceLineageFilter(
                    filter_type="source_lineage",
                    snapshot_key="snapshot:ncbi-taxonomy:synthetic",
                    term_key="taxon:6544",
                    role="assembly_source_taxonomy",
                    include_descendants=True,
                ),
            ),
        ),
        page=PageSpec(limit=50),
    )
    entity = ResolvedEntity(
        original_input="Bivalvia",
        entity_kind="source_lineage",
        match_mode="exact_canonical_name",
        stable_key="taxon:6544",
        canonical_name="Bivalvia",
        snapshot_key="snapshot:ncbi-taxonomy:synthetic",
        authority_namespace="NCBI-Taxonomy",
        snapshot_version="synthetic",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
    )
    service = StructuredRetrievalService(
        gate=gate,
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query(plan, _descendant_audit(), (entity,))

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "lineage_closure_incomplete"
    assert response.fact_retrieval_executed is False
    assert repository.calls == []


def test_query_authorized_does_not_call_gate_again() -> None:
    release = TestsOnlyQueryableRelease()
    gate = FakeGate(release=release)
    repository = FakeRepository(
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=0,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )
    )
    service = StructuredRetrievalService(
        gate=gate,
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query_authorized(release, _aggregate_plan(), _complete_audit())

    assert isinstance(response, QuerySuccess)
    assert gate.calls == []
    assert response.structured_result.data.value == 0
    assert {item.code for item in response.structured_result.limitations} == {
        "assembly_local_locus_is_not_independent_integration_event",
        "zero_matches_do_not_establish_biological_absence",
    }


def test_validation_candidate_result_never_claims_published_provenance() -> None:
    release = TestsOnlyQueryableRelease(
        status="validation_candidate",
        validation_receipt_key="validation-candidate:no-receipt",
        validation_receipt_sha256="0" * 64,
        candidate_validation_input_sha256="c" * 64,
        candidate_capability_sha256="d" * 64,
    )
    repository = FakeRepository(
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=1,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )
    )
    service = StructuredRetrievalService(
        gate=FakeGate(release=release),
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query_authorized(release, _aggregate_plan(), _complete_audit())

    assert isinstance(response, QuerySuccess)
    release_ref = response.structured_result.release
    assert release_ref.status == "validation_candidate"
    assert release_ref.candidate_validation_input_sha256 == "c" * 64
    assert release_ref.candidate_capability_sha256 == "d" * 64
    assert "published_at" not in release_ref.model_dump()


def test_validation_candidate_without_exact_identity_fails_before_query() -> None:
    release = TestsOnlyQueryableRelease(
        status="validation_candidate",
        validation_receipt_key="validation-candidate:no-receipt",
        validation_receipt_sha256="0" * 64,
    )
    repository = FakeRepository(
        AggregateData(
            metric_key="distinct_included_locus_count",
            value=1,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )
    )
    service = StructuredRetrievalService(
        gate=FakeGate(release=release),
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query_authorized(release, _aggregate_plan(), _complete_audit())

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "release_dependencies_incomplete"
    assert response.fact_retrieval_executed is False
    assert repository.calls == []


def test_post_fact_entity_miss_is_normalized_to_integrity_error() -> None:
    class PostFactEntityMissRepository:
        def __init__(self) -> None:
            self.calls = 0

        def query(
            self,
            _validated: ValidatedQuery,
            *,
            page_after: tuple[str, ...] | None = None,
        ) -> RepositoryResult:
            del page_after
            self.calls += 1
            raise RetrievalRefusal(
                "entity_not_in_release",
                "detail row disappeared",
                fact_retrieval_executed=True,
            )

    repository = PostFactEntityMissRepository()
    service = StructuredRetrievalService(
        gate=FakeGate(),
        repository=repository,
        cursor_secret=SECRET,
    )

    response = service.query(_aggregate_plan(), _complete_audit())

    assert isinstance(response, ErrorResponse)
    assert response.error.code == "result_integrity_error"
    assert response.error.message == "public fact retrieval ended in an invalid state"
    assert response.fact_retrieval_executed is True
    assert repository.calls == 1


def test_gate_database_failure_is_public_safe_and_pre_fact() -> None:
    class BrokenEngine:
        def connect(self) -> None:
            raise OperationalError("SELECT", {}, RuntimeError("private database detail"))

    gate = PublishedReleaseGate(cast(Engine, BrokenEngine()))

    with pytest.raises(RetrievalRefusal) as error:
        gate.authorize(RELEASE_KEY)

    assert error.value.code == "structured_query_failed"
    assert error.value.message == "release authorization failed"
    assert error.value.fact_retrieval_executed is False
    assert "private database detail" not in error.value.message
