"""Question-first Milestone 2 orchestration shared by every public adapter."""

from __future__ import annotations

from typing import Protocol

from eve_relation_rag.planning.parser import ControlledEnglishPlanner, StructuredQueryRequest
from eve_relation_rag.planning.resolver import ReleaseScopedEntityResolver
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.results import (
    ErrorResponse,
    PlanSuccess,
    QuerySuccess,
    StructuredError,
)
from eve_relation_rag.retrieval.structured.service import (
    ReleaseGate,
    StructuredRetrievalService,
)


class ReleaseResolverFactory(Protocol):
    """Construct metadata resolution only for an already authorized release."""

    def create(self, release: ReleaseCapability) -> ReleaseScopedEntityResolver: ...


class StructuredQueryApplication:
    """Gate, resolve, plan, and optionally retrieve one question-first request.

    The only public input is :class:`StructuredQueryRequest`; neither this service
    nor its HTTP/CLI adapters accept a client-authored plan or release capability.
    """

    def __init__(
        self,
        *,
        gate: ReleaseGate,
        resolver_factory: ReleaseResolverFactory,
        retrieval: StructuredRetrievalService | None,
        planner: ControlledEnglishPlanner | None = None,
    ) -> None:
        self._gate = gate
        self._resolver_factory = resolver_factory
        self._retrieval = retrieval
        self._planner = planner or ControlledEnglishPlanner()

    def plan(self, request: StructuredQueryRequest) -> PlanSuccess | ErrorResponse:
        """Return the exact interpretation without executing public facts."""

        authorized = self._authorize_and_plan(request)
        if isinstance(authorized, ErrorResponse):
            return authorized
        release, response = authorized
        if self._retrieval is None:
            return self._retrieval_unavailable(response)
        preflight_error = self._retrieval.preflight_authorized(
            release,
            response.query_plan,
            response.planning_audit,
            response.resolved_entities,
        )
        if preflight_error is not None:
            return preflight_error
        return response

    def query(self, request: StructuredQueryRequest) -> QuerySuccess | ErrorResponse:
        """Run the same interpretation through authorized structured retrieval."""

        authorized = self._authorize_and_plan(request)
        if isinstance(authorized, ErrorResponse):
            return authorized
        release, planned = authorized
        if self._retrieval is None:
            return self._retrieval_unavailable(planned)
        return self._retrieval.query_authorized(
            release,
            planned.query_plan,
            planned.planning_audit,
            planned.resolved_entities,
        )

    def _authorize_and_plan(
        self,
        request: StructuredQueryRequest,
    ) -> tuple[ReleaseCapability, PlanSuccess] | ErrorResponse:
        try:
            release = self._gate.authorize(request.release_key)
            resolver = self._resolver_factory.create(release)
        except RetrievalRefusal as refusal:
            return self._refusal(refusal)

        planned = self._planner.plan(request, resolver)
        if isinstance(planned, ErrorResponse):
            return planned
        return release, planned

    @staticmethod
    def _refusal(refusal: RetrievalRefusal) -> ErrorResponse:
        return ErrorResponse(
            error=StructuredError(code=refusal.code, message=refusal.message),
            fact_retrieval_executed=refusal.fact_retrieval_executed,
        )

    @staticmethod
    def _retrieval_unavailable(planned: PlanSuccess) -> ErrorResponse:
        return ErrorResponse(
            query_plan=planned.query_plan,
            planning_audit=planned.planning_audit,
            resolved_entities=planned.resolved_entities,
            error=StructuredError(
                code="unsupported_capability",
                message="Structured query cursor authentication is not configured.",
            ),
        )
