"""Tests-only Milestone 2 capability and service composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from eve_relation_rag.application import StructuredQueryApplication
from eve_relation_rag.planning.resolver import CatalogReleaseResolver
from eve_relation_rag.retrieval.structured.capability import (
    LineageDependencyBinding,
    LineageRole,
    SourceDependencyBinding,
)
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.repository import RepositoryResult
from eve_relation_rag.retrieval.structured.results import AggregateData
from eve_relation_rag.retrieval.structured.semantic import ValidatedQuery
from eve_relation_rag.retrieval.structured.service import StructuredRetrievalService

TEST_RELEASE_KEY = "release:endoviho-rag:v0:20991231:999"
TEST_CURSOR_SECRET = b"tests-only-m2-cursor-secret-at-least-32-bytes"


@dataclass(frozen=True, slots=True)
class TestsOnlyQueryableRelease:
    """Protocol double that cannot be imported from the production package."""

    __test__ = False

    release_id: int = 9_999
    dataset_key: Literal["dataset:endoviho-rag"] = "dataset:endoviho-rag"
    release_key: str = TEST_RELEASE_KEY
    status: Literal["published"] = "published"
    schema_version: str = "tests-only-m2-v1"
    published_at: datetime = datetime(2099, 12, 31, tzinfo=UTC)
    manifest_sha256: str = "a" * 64
    validation_receipt_key: str = "tests-only:receipt"
    validation_receipt_sha256: str = "b" * 64
    source_dependencies: dict[str, SourceDependencyBinding] | None = None
    lineage_dependencies: dict[LineageRole, LineageDependencyBinding] | None = None
    complete_lineage_closure_roles: frozenset[LineageRole] = frozenset()

    def __post_init__(self) -> None:
        if self.source_dependencies is None:
            object.__setattr__(self, "source_dependencies", {})
        if self.lineage_dependencies is None:
            object.__setattr__(self, "lineage_dependencies", {})


class FakeGate:
    def __init__(
        self,
        *,
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


class FakeResolverFactory:
    def __init__(self, release_key: str = TEST_RELEASE_KEY) -> None:
        self.resolver = CatalogReleaseResolver(release_key=release_key)
        self.calls: list[str] = []

    def create(self, release: TestsOnlyQueryableRelease) -> CatalogReleaseResolver:
        self.calls.append(release.release_key)
        return self.resolver


class FakeAggregateRepository:
    def __init__(self, value: int = 3) -> None:
        self.value = value
        self.calls: list[tuple[ValidatedQuery, tuple[str, ...] | None]] = []

    def query(
        self,
        validated: ValidatedQuery,
        *,
        page_after: tuple[str, ...] | None = None,
    ) -> RepositoryResult:
        self.calls.append((validated, page_after))
        return AggregateData(
            metric_key="distinct_included_locus_count",
            value=self.value,
            unit="loci",
            deduplication_key="release_key+locus_key",
        )


def make_aggregate_application(
    *,
    value: int = 3,
) -> tuple[
    StructuredQueryApplication,
    FakeGate,
    FakeResolverFactory,
    FakeAggregateRepository,
]:
    gate = FakeGate()
    factory = FakeResolverFactory()
    repository = FakeAggregateRepository(value)
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
    return application, gate, factory, repository
