"""Fail-closed composition of release authorization and structured retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from eve_relation_rag.planning.query_plans import (
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    PlanningAudit,
    StructuredPlan,
    canonical_plan_sha256,
)
from eve_relation_rag.retrieval.structured.capability import ReleaseCapability
from eve_relation_rag.retrieval.structured.cursors import CursorError
from eve_relation_rag.retrieval.structured.errors import RetrievalRefusal
from eve_relation_rag.retrieval.structured.pagination import (
    PaginationInvariantError,
    build_page_info,
    decode_plan_cursor,
)
from eve_relation_rag.retrieval.structured.repository import (
    AssemblyPageSlice,
    LocusPageSlice,
    RepositoryResult,
    SourceTaxonPageSlice,
)
from eve_relation_rag.retrieval.structured.results import (
    AggregateData,
    AssemblyDetailData,
    AssemblyPageData,
    ErrorCode,
    ErrorResponse,
    Limitation,
    LimitationCode,
    LocusDetailData,
    LocusPageData,
    PublishedReleaseRef,
    QuerySuccess,
    ResolvedEntity,
    SourceTaxonPageData,
    StructuredError,
    StructuredResult,
    ValidationCandidateReleaseRef,
)
from eve_relation_rag.retrieval.structured.semantic import (
    StructuredSemanticValidator,
    ValidatedQuery,
)


class ReleaseGate(Protocol):
    """Minimal service dependency implemented by PublishedReleaseGate."""

    def authorize(self, release_key: str) -> ReleaseCapability: ...


class FactRepository(Protocol):
    """Closed repository dependency; it accepts no statements or release IDs."""

    def query(
        self,
        validated: ValidatedQuery,
        *,
        page_after: tuple[str, ...] | None = None,
    ) -> RepositoryResult: ...


@dataclass(frozen=True, slots=True)
class _PreparedQuery:
    """One release-bound query that passed every pre-fact validation."""

    validated: ValidatedQuery
    page_after: tuple[str, ...] | None


_LIMITATION_MESSAGES: dict[LimitationCode, str] = {
    "assembly_source_taxon_is_not_ancient_host": (
        "Assembly source taxonomy does not identify the ancient biological host."
    ),
    "assembly_local_locus_is_not_independent_integration_event": (
        "An assembly-local locus is not evidence of an independent integration event."
    ),
    "zero_matches_do_not_establish_biological_absence": (
        "Zero public matches do not establish biological absence."
    ),
    "source_confidence_is_not_release_validation": (
        "Source confidence is a versioned source assessment, not release validation."
    ),
    "coordinates_are_zero_based_half_open": (
        "Coordinates use the zero-based, half-open convention."
    ),
    "detection_calls_are_not_loci": "Detection calls are source records, not EVE loci.",
}

_POST_FACT_ERROR_CODES: frozenset[ErrorCode] = frozenset(
    {"result_integrity_error", "structured_query_failed"}
)


class StructuredRetrievalService:
    """Query a validated plan through the production safety boundaries.

    Expected refusals are returned as :class:`ErrorResponse`; callers do not
    need to translate gate, semantic, cursor, or repository exceptions.
    """

    def __init__(
        self,
        *,
        gate: ReleaseGate,
        repository: FactRepository,
        cursor_secret: bytes,
        semantic_validator: StructuredSemanticValidator | None = None,
    ) -> None:
        if type(cursor_secret) is not bytes or len(cursor_secret) < 32:
            raise ValueError("cursor_secret must contain at least 32 bytes")
        self._gate = gate
        self._repository = repository
        self._cursor_secret = cursor_secret
        self._semantic_validator = semantic_validator or StructuredSemanticValidator()

    def query(
        self,
        plan: StructuredPlan,
        planning_audit: PlanningAudit,
        resolved_entities: tuple[ResolvedEntity, ...] = (),
    ) -> QuerySuccess | ErrorResponse:
        """Authorize, validate, retrieve, and bind one typed result envelope."""

        try:
            release = self._gate.authorize(plan.release_key)
        except RetrievalRefusal as refusal:
            return self._error_response(
                refusal,
                plan=plan,
                planning_audit=planning_audit,
                resolved_entities=(),
            )

        return self.query_authorized(
            release,
            plan,
            planning_audit,
            resolved_entities,
        )

    def query_authorized(
        self,
        release: ReleaseCapability,
        plan: StructuredPlan,
        planning_audit: PlanningAudit,
        resolved_entities: tuple[ResolvedEntity, ...] = (),
    ) -> QuerySuccess | ErrorResponse:
        """Continue after the same request already passed its release gate.

        This is an internal orchestration seam for gate -> public resolver ->
        retrieval.  ``ReleaseCapability`` is not a request model and must never
        be accepted from HTTP or CLI input.
        """

        prepared = self._prepare_authorized(
            release,
            plan,
            planning_audit,
            resolved_entities,
        )
        if isinstance(prepared, ErrorResponse):
            return prepared

        try:
            repository_result = self._repository.query(
                prepared.validated,
                page_after=prepared.page_after,
            )
            data = self._bind_repository_result(release, plan, repository_result)
            release_ref: PublishedReleaseRef | ValidationCandidateReleaseRef
            if release.status == "validation_candidate":
                candidate_input_sha256 = release.candidate_validation_input_sha256
                candidate_capability_sha256 = release.candidate_capability_sha256
                if candidate_input_sha256 is None or candidate_capability_sha256 is None:
                    raise RetrievalRefusal(
                        "result_integrity_error",
                        "validation candidate provenance is incomplete",
                        fact_retrieval_executed=True,
                    )
                release_ref = ValidationCandidateReleaseRef(
                    dataset_key=release.dataset_key,
                    release_key=release.release_key,
                    schema_version=release.schema_version,
                    status="validation_candidate",
                    manifest_sha256=release.manifest_sha256,
                    candidate_created_at=release.published_at,
                    candidate_validation_input_sha256=candidate_input_sha256,
                    candidate_capability_sha256=candidate_capability_sha256,
                )
            else:
                release_ref = PublishedReleaseRef(
                    dataset_key=release.dataset_key,
                    release_key=release.release_key,
                    schema_version=release.schema_version,
                    status="published",
                    manifest_sha256=release.manifest_sha256,
                    published_at=release.published_at,
                )
            result = StructuredResult(
                plan_sha256=canonical_plan_sha256(plan),
                release=release_ref,
                data=data,
                limitations=self._limitations_for(data),
            )
            return QuerySuccess(
                query_plan=plan,
                planning_audit=planning_audit,
                resolved_entities=resolved_entities,
                structured_result=result,
            )
        except RetrievalRefusal as refusal:
            return self._error_response(
                refusal,
                plan=plan,
                planning_audit=planning_audit,
                resolved_entities=resolved_entities,
            )
        except PaginationInvariantError as exc:
            return self._error_response(
                RetrievalRefusal(
                    "result_integrity_error",
                    str(exc),
                    fact_retrieval_executed=True,
                ),
                plan=plan,
                planning_audit=planning_audit,
                resolved_entities=resolved_entities,
            )
        except ValidationError:
            return self._error_response(
                RetrievalRefusal(
                    "result_integrity_error",
                    "repository result violates the structured result contract",
                    fact_retrieval_executed=True,
                ),
                plan=plan,
                planning_audit=planning_audit,
                resolved_entities=resolved_entities,
            )
        except Exception:
            return self._error_response(
                RetrievalRefusal(
                    "structured_query_failed",
                    "structured retrieval failed",
                    fact_retrieval_executed=True,
                ),
                plan=plan,
                planning_audit=planning_audit,
                resolved_entities=resolved_entities,
            )

    def preflight_authorized(
        self,
        release: ReleaseCapability,
        plan: StructuredPlan,
        planning_audit: PlanningAudit,
        resolved_entities: tuple[ResolvedEntity, ...] = (),
    ) -> ErrorResponse | None:
        """Validate semantics and cursor context without executing public facts."""

        prepared = self._prepare_authorized(
            release,
            plan,
            planning_audit,
            resolved_entities,
        )
        return prepared if isinstance(prepared, ErrorResponse) else None

    def _prepare_authorized(
        self,
        release: ReleaseCapability,
        plan: StructuredPlan,
        planning_audit: PlanningAudit,
        resolved_entities: tuple[ResolvedEntity, ...],
    ) -> _PreparedQuery | ErrorResponse:
        """Share the complete pre-fact boundary between plan and query calls."""

        try:
            validated = self._semantic_validator.validate(
                release,
                plan,
                planning_audit,
                resolved_entities,
            )
            page_after: tuple[str, ...] | None = None
            if isinstance(plan, (ListLociPlan, ListAssembliesPlan, ListSourceTaxaPlan)):
                page_after = decode_plan_cursor(
                    plan,
                    release_manifest_sha256=release.manifest_sha256,
                    secret=self._cursor_secret,
                )
            return _PreparedQuery(validated=validated, page_after=page_after)
        except RetrievalRefusal as refusal:
            return self._error_response(
                refusal,
                plan=plan,
                planning_audit=planning_audit,
                resolved_entities=resolved_entities,
            )
        except CursorError as exc:
            return self._error_response(
                RetrievalRefusal(exc.code, exc.message),
                plan=plan,
                planning_audit=planning_audit,
                resolved_entities=resolved_entities,
            )

    def _bind_repository_result(
        self,
        release: ReleaseCapability,
        plan: StructuredPlan,
        result: RepositoryResult,
    ) -> (
        AssemblyDetailData
        | LocusDetailData
        | LocusPageData
        | AssemblyPageData
        | SourceTaxonPageData
        | AggregateData
    ):
        if isinstance(plan, ListLociPlan) and isinstance(result, LocusPageSlice):
            page = build_page_info(
                plan,
                release_manifest_sha256=release.manifest_sha256,
                items=result.items,
                total_count=result.total_count,
                has_more=result.has_more,
                secret=self._cursor_secret,
            )
            return LocusPageData(items=result.items, page=page)
        if isinstance(plan, ListAssembliesPlan) and isinstance(result, AssemblyPageSlice):
            page = build_page_info(
                plan,
                release_manifest_sha256=release.manifest_sha256,
                items=result.items,
                total_count=result.total_count,
                has_more=result.has_more,
                secret=self._cursor_secret,
            )
            return AssemblyPageData(items=result.items, page=page)
        if isinstance(plan, ListSourceTaxaPlan) and isinstance(result, SourceTaxonPageSlice):
            page = build_page_info(
                plan,
                release_manifest_sha256=release.manifest_sha256,
                items=result.items,
                total_count=result.total_count,
                has_more=result.has_more,
                secret=self._cursor_secret,
            )
            return SourceTaxonPageData(items=result.items, page=page)
        if isinstance(result, (AssemblyDetailData, LocusDetailData, AggregateData)):
            return result
        raise RetrievalRefusal(
            "result_integrity_error",
            "repository result kind does not match the query plan",
            fact_retrieval_executed=True,
        )

    @staticmethod
    def _limitations_for(
        data: (
            AssemblyDetailData
            | LocusDetailData
            | LocusPageData
            | AssemblyPageData
            | SourceTaxonPageData
            | AggregateData
        ),
    ) -> tuple[Limitation, ...]:
        codes: set[LimitationCode] = set()
        if isinstance(data, AssemblyDetailData):
            codes.add("assembly_source_taxon_is_not_ancient_host")
        elif isinstance(data, LocusDetailData):
            codes.update(
                {
                    "assembly_source_taxon_is_not_ancient_host",
                    "assembly_local_locus_is_not_independent_integration_event",
                    "coordinates_are_zero_based_half_open",
                }
            )
            if data.calls:
                codes.add("detection_calls_are_not_loci")
            if any(item.source_confidence is not None for item in data.public_assertions):
                codes.add("source_confidence_is_not_release_validation")
        elif isinstance(data, LocusPageData):
            if data.page.total_count:
                codes.update(
                    {
                        "assembly_source_taxon_is_not_ancient_host",
                        "assembly_local_locus_is_not_independent_integration_event",
                        "coordinates_are_zero_based_half_open",
                    }
                )
            else:
                codes.add("zero_matches_do_not_establish_biological_absence")
        elif isinstance(data, (AssemblyPageData, SourceTaxonPageData)):
            if data.page.total_count:
                codes.add("assembly_source_taxon_is_not_ancient_host")
            else:
                codes.add("zero_matches_do_not_establish_biological_absence")
        else:
            if data.metric_key == "distinct_source_taxon_count":
                codes.add("assembly_source_taxon_is_not_ancient_host")
            elif data.metric_key in {
                "distinct_included_locus_count",
                "distinct_contig_count",
            }:
                codes.add("assembly_local_locus_is_not_independent_integration_event")
            elif data.metric_key == "detection_call_count":
                codes.add("detection_calls_are_not_loci")
            if data.value == 0:
                codes.add("zero_matches_do_not_establish_biological_absence")
        return tuple(
            Limitation(code=code, message=_LIMITATION_MESSAGES[code]) for code in sorted(codes)
        )

    @staticmethod
    def _error_response(
        refusal: RetrievalRefusal,
        *,
        plan: StructuredPlan,
        planning_audit: PlanningAudit,
        resolved_entities: tuple[ResolvedEntity, ...],
    ) -> ErrorResponse:
        if refusal.fact_retrieval_executed and refusal.code not in _POST_FACT_ERROR_CODES:
            refusal = RetrievalRefusal(
                "result_integrity_error",
                "public fact retrieval ended in an invalid state",
                fact_retrieval_executed=True,
            )
        release_error_codes: frozenset[ErrorCode] = frozenset(
            {
                "release_required",
                "release_key_invalid",
                "release_alias_forbidden",
                "release_not_found",
                "release_not_published",
                "release_dependencies_incomplete",
                "release_manifest_invalid",
            }
        )
        return ErrorResponse(
            query_plan=plan,
            planning_audit=planning_audit,
            resolved_entities=(() if refusal.code in release_error_codes else resolved_entities),
            error=StructuredError(code=refusal.code, message=refusal.message),
            fact_retrieval_executed=refusal.fact_retrieval_executed,
        )


__all__ = [
    "FactRepository",
    "ReleaseGate",
    "StructuredRetrievalService",
]
