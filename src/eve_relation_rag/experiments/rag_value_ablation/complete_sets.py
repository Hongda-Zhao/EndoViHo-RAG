"""Collect one controlled locus query without widening its scope or hiding partial pages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import ListLociPlan, PageSpec, canonical_plan_sha256
from eve_relation_rag.planning.scope_policy import contains_forbidden_topic
from eve_relation_rag.retrieval.structured.results import (
    ErrorResponse,
    LocusPageData,
    LocusSummary,
    QuerySuccess,
)


class CompleteSetError(ValueError):
    """An incomplete collection must never be scored as a complete or empty set."""


@dataclass(frozen=True, slots=True)
class CompleteLocusSet:
    """All pages of one exact query; no claim of whole-release or literature coverage."""

    request: StructuredQueryRequest
    pages: tuple[QuerySuccess, ...]
    items: tuple[LocusSummary, ...]


def collect_complete_loci(
    request: StructuredQueryRequest,
    query: Callable[[StructuredQueryRequest], QuerySuccess | ErrorResponse],
    *,
    max_pages: int = 1000,
) -> CompleteLocusSet:
    """Use the production application's query method, including its cursor/release gates.

    The callback is an internal dependency, not a client-authored plan. This collector
    adds no trust authority. The execution gate must already have admitted the runtime.
    A refusal stops all subsequent calls; no replacement or broader query is attempted.
    """
    request = StructuredQueryRequest.model_validate_json(request.model_dump_json())
    if max_pages < 1 or (request.page is not None and request.page.cursor is not None):
        raise CompleteSetError("complete collection requires the first page and a positive limit")
    if contains_forbidden_topic(request.question):
        raise CompleteSetError("scope refused before downstream calls")
    pages: list[QuerySuccess] = []
    items: list[LocusSummary] = []
    cursors: set[str] = set()
    current = request
    total = None
    identity = None
    for _ in range(max_pages):
        response = query(current)
        if type(response) is not QuerySuccess:
            raise CompleteSetError("query refused or failed; no complete result exists")
        response = QuerySuccess.model_validate_json(response.model_dump_json())
        result, plan = response.structured_result, response.query_plan
        if not isinstance(result.data, LocusPageData) or not isinstance(plan, ListLociPlan):
            raise CompleteSetError("complete locus collection requires an existing list-loci query")
        if plan.original_question != request.question or plan.release_key != request.release_key:
            raise CompleteSetError("response does not match the requested scope")
        observed = (canonical_plan_sha256(plan), result.release)
        if identity is not None and observed != identity:
            raise CompleteSetError("query plan or release changed between pages")
        identity = observed
        data = result.data
        if total is not None and data.page.total_count != total:
            raise CompleteSetError("unpaginated total changed between pages")
        total = data.page.total_count
        if items and data.items and items[-1].locus_key >= data.items[0].locus_key:
            raise CompleteSetError("overlapping or unordered page boundary")
        pages.append(response)
        items.extend(data.items)
        if len(items) > total:
            raise CompleteSetError("page contents exceed the exact total")
        cursor = data.page.next_cursor
        if cursor is None:
            if len(items) != total:
                raise CompleteSetError("terminal page does not complete the exact total")
            return CompleteLocusSet(request=request, pages=tuple(pages), items=tuple(items))
        if cursor in cursors:
            raise CompleteSetError("pagination cursor cycle")
        cursors.add(cursor)
        current = request.model_copy(
            update={"page": PageSpec(limit=data.page.limit, cursor=cursor)}
        )
    raise CompleteSetError("page budget exhausted; partial results are not a complete set")
