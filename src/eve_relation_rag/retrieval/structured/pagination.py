"""Pure helpers for fixed forward keyset pagination.

These helpers validate/serialize page positions but never issue a query.  A
repository is expected to apply the returned tuple with fixed SQLAlchemy keyset
expressions, calculate the unpaginated total in the same read-only transaction,
and return at most ``limit`` canonically ordered items.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from eve_relation_rag.planning.query_plans import (
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    canonical_plan_sha256,
)
from eve_relation_rag.retrieval.structured.cursors import (
    SORT_KEY_BY_INTENT,
    CursorContext,
    ListIntent,
    decode_cursor,
    encode_cursor,
    validate_cursor_secret,
)
from eve_relation_rag.retrieval.structured.results import (
    AssemblySummary,
    LocusSummary,
    PageInfo,
    SourceTaxonSummary,
)

type ListPlan = ListLociPlan | ListAssembliesPlan | ListSourceTaxaPlan
type PageItem = LocusSummary | AssemblySummary | SourceTaxonSummary
type PageAfter = tuple[str, ...]
type ResultSortKey = Literal["locus_key", "assembly_accession", "source_taxon_key"]

_RESULT_SORT_KEY_BY_INTENT: Final[dict[ListIntent, ResultSortKey]] = {
    "list_loci": "locus_key",
    "list_assemblies": "assembly_accession",
    "list_source_taxa": "source_taxon_key",
}


class PaginationInvariantError(ValueError):
    """Repository page data violates the fixed M2 pagination contract."""


def cursor_context_for_plan(
    plan: ListPlan,
    *,
    release_manifest_sha256: str,
) -> CursorContext:
    """Derive the complete cursor context from one canonical list plan."""

    return CursorContext(
        release_key=plan.release_key,
        release_manifest_sha256=release_manifest_sha256,
        plan_sha256=canonical_plan_sha256(plan),
        intent=plan.intent,
        canonical_sort_key=SORT_KEY_BY_INTENT[plan.intent],
    )


def decode_plan_cursor(
    plan: ListPlan,
    *,
    release_manifest_sha256: str,
    secret: bytes,
) -> PageAfter | None:
    """Return the authenticated keyset tuple before any public fact query runs."""

    token = plan.page.cursor
    if token is None:
        return None
    context = cursor_context_for_plan(
        plan,
        release_manifest_sha256=release_manifest_sha256,
    )
    return decode_cursor(token, expected_context=context, secret=secret).last_sort_values


def sort_values_for_item(plan: ListPlan, item: PageItem) -> PageAfter:
    """Project one result item to its fixed, public keyset order."""

    if isinstance(plan, ListLociPlan) and isinstance(item, LocusSummary):
        return (item.locus_key,)
    if isinstance(plan, ListAssembliesPlan) and isinstance(item, AssemblySummary):
        return (item.assembly_accession_version, item.assembly_key)
    if isinstance(plan, ListSourceTaxaPlan) and isinstance(item, SourceTaxonSummary):
        return (item.lineage.snapshot_key, item.lineage.term_key)
    raise PaginationInvariantError("page item type does not match the list intent")


def validate_page_items(plan: ListPlan, items: Sequence[PageItem]) -> tuple[PageAfter, ...]:
    """Require exact item type, ascending canonical order, and no duplicates."""

    keys = tuple(sort_values_for_item(plan, item) for item in items)
    if keys != tuple(sorted(keys)):
        raise PaginationInvariantError("page items are not in canonical ascending order")
    if len(keys) != len(set(keys)):
        raise PaginationInvariantError("page items contain a duplicate keyset value")
    return keys


def build_page_info(
    plan: ListPlan,
    *,
    release_manifest_sha256: str,
    items: Sequence[PageItem],
    total_count: int,
    has_more: bool,
    secret: bytes,
) -> PageInfo:
    """Validate one repository page and sign its forward position when needed.

    ``total_count`` is the distinct count after filters and before cursor/limit.
    ``has_more`` must be determined by the repository (normally with a
    ``limit + 1`` probe) and is never inferred from the total alone.
    """

    if type(total_count) is not int or total_count < 0:
        raise PaginationInvariantError("total_count must be a non-negative strict integer")
    if type(has_more) is not bool:
        raise PaginationInvariantError("has_more must be a strict boolean")
    validate_cursor_secret(secret)

    item_tuple = tuple(items)
    keys = validate_page_items(plan, item_tuple)
    returned_count = len(item_tuple)
    if returned_count > plan.page.limit:
        raise PaginationInvariantError("repository returned more items than the page limit")
    if returned_count > total_count:
        raise PaginationInvariantError("returned item count exceeds the unpaginated total")
    if not item_tuple and total_count != 0:
        raise PaginationInvariantError("a valid empty page requires an empty match set")
    if has_more and returned_count != plan.page.limit:
        raise PaginationInvariantError("has_more requires a full current page")
    if has_more and total_count <= returned_count:
        raise PaginationInvariantError("has_more requires additional unpaginated results")

    next_cursor: str | None = None
    if has_more:
        context = cursor_context_for_plan(
            plan,
            release_manifest_sha256=release_manifest_sha256,
        )
        next_cursor = encode_cursor(
            context,
            last_sort_values=keys[-1],
            secret=secret,
        )

    return PageInfo(
        limit=plan.page.limit,
        returned_count=returned_count,
        total_count=total_count,
        next_cursor=next_cursor,
        sort_key=_RESULT_SORT_KEY_BY_INTENT[plan.intent],
        sort_direction="asc",
    )


__all__ = [
    "ListPlan",
    "PageAfter",
    "PageItem",
    "PaginationInvariantError",
    "build_page_info",
    "cursor_context_for_plan",
    "decode_plan_cursor",
    "sort_values_for_item",
    "validate_page_items",
]
