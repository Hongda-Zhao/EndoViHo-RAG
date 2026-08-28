"""Canonical JSON serialization for strict Milestone 4 responses."""

from __future__ import annotations

from pydantic import TypeAdapter

from eve_relation_rag.generation.context import (
    build_hybrid_context,
    build_literature_context,
)
from eve_relation_rag.generation.rendering import (
    render_hybrid_answer_text,
    render_hybrid_insufficient_answer_text,
    render_literature_answer_text,
    render_structured_answer_text,
)
from eve_relation_rag.generation.validators import validate_generation_composition
from eve_relation_rag.hybrid.contracts import (
    HybridRouteAnswer,
    LiteratureRouteAnswer,
    RagErrorResponse,
    RagQueryRequest,
    RagResponse,
    RouteDecision,
    StructuredRouteAnswer,
    canonical_model_json,
)
from eve_relation_rag.planning.query_plans import PageSpec
from eve_relation_rag.planning.router import DeterministicRouter
from eve_relation_rag.retrieval.structured.results import QuerySuccess

_RAG_RESPONSE_ADAPTER: TypeAdapter[RagResponse] = TypeAdapter(RagResponse)


def _revalidate_structured_selectors(
    request: RagQueryRequest,
    decision: RouteDecision,
    success: QuerySuccess,
) -> None:
    """Bind server-derived M2 selectors back to the exact client request."""

    if decision.structured_question != success.query_plan.original_question:
        raise ValueError("structured question is not bound to the original request selector")

    plan_page = getattr(success.query_plan, "page", None)
    if plan_page is None:
        if request.page is not None:
            raise ValueError("structured page is not bound to the original request selector")
        return

    expected_page = request.page or PageSpec()
    if plan_page != expected_page:
        raise ValueError("structured page is not bound to the original request selector")


def _revalidate_literature_selectors(
    request: RagQueryRequest,
    decision: RouteDecision,
    response: LiteratureRouteAnswer | HybridRouteAnswer,
) -> None:
    """Bind server-derived literature selectors back to the exact client request."""

    if decision.literature_question != request.question:
        raise ValueError("literature question is not bound to the original request selector")
    if decision.effective_literature_top_k != response.retrieved_chunks.requested_top_k:
        raise ValueError("literature top_k is not bound to the original request selector")


def revalidate_rag_response(response: RagResponse) -> RagResponse:
    """Round-trip and deterministically re-bind one complete public response."""

    serialized = canonical_model_json(response)
    validated = _RAG_RESPONSE_ADAPTER.validate_json(serialized)
    if canonical_model_json(validated) != serialized:
        raise ValueError("routed response changed during strict round-trip validation")
    if isinstance(validated, StructuredRouteAnswer):
        decision = DeterministicRouter().route(validated.original_request)
        if decision.route != validated.route:
            raise ValueError("response route is not bound to the original request selectors")
        _revalidate_structured_selectors(
            validated.original_request,
            decision,
            validated.query_success,
        )
        expected = render_structured_answer_text(validated.query_success)
        if validated.structured_text != expected:
            raise ValueError("structured response text is not the canonical rendering")
    elif isinstance(validated, LiteratureRouteAnswer):
        decision = DeterministicRouter().route(validated.original_request)
        if decision.route != validated.route:
            raise ValueError("response route is not bound to the original request selectors")
        _revalidate_literature_selectors(validated.original_request, decision, validated)
        context = build_literature_context(
            original_question=validated.original_request.question,
            retrieved_chunks=validated.retrieved_chunks,
        )
        composition = validate_generation_composition(context, validated.generation)
        if composition != validated.generation:
            raise ValueError("literature composition changed during response validation")
        if validated.answer_text != render_literature_answer_text(composition):
            raise ValueError("literature answer text is not the canonical rendering")
    elif isinstance(validated, HybridRouteAnswer):
        decision = DeterministicRouter().route(validated.original_request)
        if decision.route != validated.route:
            raise ValueError("response route is not bound to the original request selectors")
        _revalidate_structured_selectors(
            validated.original_request,
            decision,
            validated.query_success,
        )
        _revalidate_literature_selectors(validated.original_request, decision, validated)
        context = build_hybrid_context(
            original_question=validated.original_request.question,
            query_success=validated.query_success,
            retrieved_chunks=validated.retrieved_chunks,
        )
        if validated.generation is None:
            expected = render_hybrid_insufficient_answer_text(validated.query_success)
        else:
            composition = validate_generation_composition(context, validated.generation)
            if composition != validated.generation:
                raise ValueError("hybrid composition changed during response validation")
            expected = render_hybrid_answer_text(
                validated.query_success.structured_result,
                composition,
            )
        if validated.answer_text != expected:
            raise ValueError("hybrid answer text is not the canonical rendering")
    elif not isinstance(validated, RagErrorResponse):  # pragma: no cover - closed union
        raise TypeError("unsupported routed response variant")
    return validated


def serialize_rag_response(response: RagResponse) -> str:
    """Revalidate and serialize one response as canonical JSON."""

    validated = revalidate_rag_response(response)
    return canonical_model_json(validated)


__all__ = ["revalidate_rag_response", "serialize_rag_response"]
