"""Pure deterministic Milestone 4 route selection.

The router recognizes only the approved outer grammar.  It does not authorize a
release, resolve an entity, parse the inner Milestone 2 controlled-English clause,
or construct a database/model provider.  The unchanged M2 planner remains the
authority for the complete structured grammar after this side-effect-free step.
"""

from __future__ import annotations

import re
from typing import Final

from eve_relation_rag.hybrid.contracts import (
    HYBRID_SUFFIXES,
    RagQueryRequest,
    RouteDecision,
    RouteRefusalCode,
)
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic

_LITERATURE_PREFIXES: Final = (
    "explain the literature evidence for ",
    "explain the literature methods for ",
    "explain the literature limitations for ",
)
_STRUCTURED_FAMILY_RE: Final = re.compile(r"^(?:show|list|count) +\S", re.IGNORECASE)


class DeterministicRouter:
    """Select one approved outer route without I/O or scientific inference."""

    def route(self, request: RagQueryRequest) -> RouteDecision:
        """Return a strict decision while preserving the original question exactly."""

        question = request.question
        if contains_forbidden_topic(question):
            return _unsupported(request, refusal_code="unsupported_request")

        if _literature_topic(question) is not None:
            if not _literature_fields_match(request):
                return _unsupported(request, refusal_code="route_request_mismatch")
            return RouteDecision(
                route_schema_version="rag-route-decision-v1",
                route="literature",
                original_question=question,
                structured_question=None,
                literature_question=question,
                effective_literature_top_k=request.literature_top_k or 8,
                refusal_code=None,
            )

        hybrid_suffix_count = _hybrid_suffix_count(question)
        if hybrid_suffix_count:
            hybrid_clause = _hybrid_structured_clause(question)
            if hybrid_clause is None:
                return _unsupported(request, refusal_code="unsupported_request")
            if not _hybrid_fields_match(request):
                return _unsupported(request, refusal_code="route_request_mismatch")
            return RouteDecision(
                route_schema_version="rag-route-decision-v1",
                route="hybrid",
                original_question=question,
                structured_question=hybrid_clause,
                literature_question=question,
                effective_literature_top_k=request.literature_top_k or 8,
                refusal_code=None,
            )

        if _is_structured_family(question):
            if not _structured_fields_match(request):
                return _unsupported(request, refusal_code="route_request_mismatch")
            return RouteDecision(
                route_schema_version="rag-route-decision-v1",
                route="structured",
                original_question=question,
                structured_question=question,
                literature_question=None,
                effective_literature_top_k=None,
                refusal_code=None,
            )

        return _unsupported(request, refusal_code="unsupported_request")


def _is_structured_family(question: str) -> bool:
    return _STRUCTURED_FAMILY_RE.match(question) is not None


def _literature_topic(question: str) -> str | None:
    folded = question.casefold()
    for prefix in _LITERATURE_PREFIXES:
        if folded.startswith(prefix):
            topic = question[len(prefix) :]
            return topic if topic.strip(" ") else None
    return None


def _hybrid_structured_clause(question: str) -> str | None:
    folded = question.casefold()
    if _hybrid_suffix_count(question) != 1:
        return None
    for suffix in HYBRID_SUFFIXES:
        if folded.endswith(suffix):
            structured_clause = question[: -len(suffix)]
            if _is_structured_family(structured_clause):
                return structured_clause
            return None
    return None


def _hybrid_suffix_count(question: str) -> int:
    folded = question.casefold()
    return sum(folded.count(suffix) for suffix in HYBRID_SUFFIXES)


def _structured_fields_match(request: RagQueryRequest) -> bool:
    return (
        request.release_key is not None
        and request.corpus_release_key is None
        and request.literature_top_k is None
    )


def _literature_fields_match(request: RagQueryRequest) -> bool:
    return (
        request.release_key is None
        and request.corpus_release_key is not None
        and request.page is None
    )


def _hybrid_fields_match(request: RagQueryRequest) -> bool:
    return request.release_key is not None and request.corpus_release_key is not None


def _unsupported(
    request: RagQueryRequest,
    *,
    refusal_code: RouteRefusalCode,
) -> RouteDecision:
    return RouteDecision(
        route_schema_version="rag-route-decision-v1",
        route="unsupported",
        original_question=request.question,
        structured_question=None,
        literature_question=None,
        effective_literature_top_k=None,
        refusal_code=refusal_code,
    )


__all__ = ["DeterministicRouter"]
