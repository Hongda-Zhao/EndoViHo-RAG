from __future__ import annotations

import pytest

from eve_relation_rag.planning.query_plans import (
    AssemblyFilter,
    EntireReleaseScope,
    FilteredScope,
    ListAssembliesPlan,
    ListLociPlan,
    ListSourceTaxaPlan,
    PageSpec,
)
from eve_relation_rag.retrieval.structured.cursors import CursorPlanMismatchError
from eve_relation_rag.retrieval.structured.pagination import (
    PaginationInvariantError,
    build_page_info,
    decode_plan_cursor,
    sort_values_for_item,
)
from eve_relation_rag.retrieval.structured.results import (
    AssemblySummary,
    ExactPlacement,
    LineageRef,
    LocusSummary,
    SourceTaxonSummary,
)

RELEASE = "release:endoviho-rag:v0:20260827:001"
MANIFEST = "a" * 64
TEST_SECRET = b"x" * 32


def _scope() -> EntireReleaseScope:
    return EntireReleaseScope(scope_type="entire_release", explicitly_requested=True)


def _locus_plan(
    *,
    limit: int = 2,
    cursor: str | None = None,
    question: str = "List all loci in this release.",
) -> ListLociPlan:
    return ListLociPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="list_loci",
        original_question=question,
        scope=_scope(),
        page=PageSpec(limit=limit, cursor=cursor),
    )


def _assembly_plan(*, limit: int = 2, cursor: str | None = None) -> ListAssembliesPlan:
    return ListAssembliesPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="list_assemblies",
        original_question="List all assemblies in this release.",
        scope=_scope(),
        page=PageSpec(limit=limit, cursor=cursor),
    )


def _source_plan(*, limit: int = 2, cursor: str | None = None) -> ListSourceTaxaPlan:
    return ListSourceTaxaPlan(
        plan_version="endoviho-query-plan-v0.1",
        route="structured",
        release_key=RELEASE,
        intent="list_source_taxa",
        original_question="List all source taxa in this release.",
        scope=_scope(),
        page=PageSpec(limit=limit, cursor=cursor),
    )


def _source_lineage(*, suffix: str = "1") -> LineageRef:
    return LineageRef(
        term_key=f"lineage-term:ncbi:taxid-{suffix}",
        canonical_name=f"Source taxon {suffix}",
        rank="species",
        snapshot_key="lineage-snapshot:ncbi-taxonomy:test",
        authority_namespace="ncbi-taxonomy",
        snapshot_version="test-v1",
        scheme_kind="formal_taxonomy",
        role="assembly_source_taxonomy",
    )


def _locus(hex_character: str, *, assembly_number: int = 1) -> LocusSummary:
    accession = f"GCA_{assembly_number}.1"
    contig = f"AB{assembly_number}.1"
    return LocusSummary(
        locus_key=f"locus:eve:v1:sha256:{hex_character * 64}",
        assembly_key=f"assembly:ncbi:{accession}",
        assembly_accession_version=accession,
        source_organism_name="Test organism",
        source_taxon=_source_lineage(),
        placement=ExactPlacement(
            sequence_key=f"sequence:insdc:{contig}",
            sequence_accession_version=contig,
            start0=10,
            end0=20,
            strand="unknown",
        ),
    )


def _assembly(number: int) -> AssemblySummary:
    accession = f"GCA_{number}.1"
    return AssemblySummary(
        assembly_key=f"assembly:ncbi:{accession}",
        assembly_accession_version=accession,
        source_organism_name=f"Test organism {number}",
        source_taxon=_source_lineage(suffix=str(number)),
        included_locus_count=1,
    )


def _source_taxon(suffix: str) -> SourceTaxonSummary:
    return SourceTaxonSummary(
        lineage=_source_lineage(suffix=suffix),
        represented_assembly_count=1,
        included_locus_count=1,
    )


def test_next_cursor_round_trip_returns_only_the_bound_keyset_tuple() -> None:
    first_plan = _locus_plan()
    first_items = (_locus("1"), _locus("2", assembly_number=2))
    page = build_page_info(
        first_plan,
        release_manifest_sha256=MANIFEST,
        items=first_items,
        total_count=3,
        has_more=True,
        secret=TEST_SECRET,
    )
    assert page.returned_count == 2
    assert page.total_count == 3
    assert page.next_cursor is not None

    next_plan = _locus_plan(cursor=page.next_cursor)
    assert decode_plan_cursor(
        next_plan,
        release_manifest_sha256=MANIFEST,
        secret=TEST_SECRET,
    ) == (first_items[-1].locus_key,)

    final_page = build_page_info(
        next_plan,
        release_manifest_sha256=MANIFEST,
        items=(_locus("3", assembly_number=3),),
        total_count=3,
        has_more=False,
        secret=TEST_SECRET,
    )
    assert final_page.total_count == page.total_count
    assert final_page.next_cursor is None


def test_cursor_binds_question_limit_and_manifest_through_plan_context() -> None:
    first = _locus_plan()
    page = build_page_info(
        first,
        release_manifest_sha256=MANIFEST,
        items=(_locus("1"), _locus("2", assembly_number=2)),
        total_count=3,
        has_more=True,
        secret=TEST_SECRET,
    )
    assert page.next_cursor is not None

    mismatches = (
        (_locus_plan(limit=3, cursor=page.next_cursor), MANIFEST),
        (_locus_plan(cursor=page.next_cursor, question="List every locus."), MANIFEST),
        (
            ListLociPlan(
                plan_version="endoviho-query-plan-v0.1",
                route="structured",
                release_key=RELEASE,
                intent="list_loci",
                original_question="List all loci in this release.",
                scope=FilteredScope(
                    scope_type="filtered",
                    filters=(
                        AssemblyFilter(
                            filter_type="assembly",
                            assembly_key="assembly:ncbi:GCA_1.1",
                        ),
                    ),
                ),
                page=PageSpec(limit=2, cursor=page.next_cursor),
            ),
            MANIFEST,
        ),
        (_locus_plan(cursor=page.next_cursor), "b" * 64),
    )
    for plan, manifest in mismatches:
        with pytest.raises(CursorPlanMismatchError):
            decode_plan_cursor(
                plan,
                release_manifest_sha256=manifest,
                secret=TEST_SECRET,
            )


def test_all_list_intents_use_their_fixed_sort_tuple_and_page_label() -> None:
    assembly_plan = _assembly_plan()
    assemblies = (_assembly(1), _assembly(2))
    assembly_page = build_page_info(
        assembly_plan,
        release_manifest_sha256=MANIFEST,
        items=assemblies,
        total_count=3,
        has_more=True,
        secret=TEST_SECRET,
    )
    assert assembly_page.sort_key == "assembly_accession"
    assert assembly_page.next_cursor is not None
    assert sort_values_for_item(assembly_plan, assemblies[0]) == (
        "GCA_1.1",
        "assembly:ncbi:GCA_1.1",
    )
    assert decode_plan_cursor(
        _assembly_plan(cursor=assembly_page.next_cursor),
        release_manifest_sha256=MANIFEST,
        secret=TEST_SECRET,
    ) == ("GCA_2.1", "assembly:ncbi:GCA_2.1")

    source_plan = _source_plan()
    source_taxa = (_source_taxon("1"), _source_taxon("2"))
    source_page = build_page_info(
        source_plan,
        release_manifest_sha256=MANIFEST,
        items=source_taxa,
        total_count=3,
        has_more=True,
        secret=TEST_SECRET,
    )
    assert source_page.sort_key == "source_taxon_key"
    assert source_page.next_cursor is not None
    assert sort_values_for_item(source_plan, source_taxa[0]) == (
        "lineage-snapshot:ncbi-taxonomy:test",
        "lineage-term:ncbi:taxid-1",
    )
    assert decode_plan_cursor(
        _source_plan(cursor=source_page.next_cursor),
        release_manifest_sha256=MANIFEST,
        secret=TEST_SECRET,
    ) == (
        "lineage-snapshot:ncbi-taxonomy:test",
        "lineage-term:ncbi:taxid-2",
    )


def test_no_cursor_is_serialized_as_no_page_after_constraint() -> None:
    assert (
        decode_plan_cursor(
            _locus_plan(),
            release_manifest_sha256=MANIFEST,
            secret=TEST_SECRET,
        )
        is None
    )

    with pytest.raises(ValueError, match="at least 32 bytes"):
        build_page_info(
            _locus_plan(),
            release_manifest_sha256=MANIFEST,
            items=(),
            total_count=0,
            has_more=False,
            secret=b"short",
        )


def test_page_builder_rejects_repository_order_count_and_type_drift() -> None:
    plan = _locus_plan()
    first = _locus("1")
    second = _locus("2", assembly_number=2)

    invalid_pages = (
        ((second, first), 2, False, "canonical ascending order"),
        ((first, first), 2, False, "duplicate keyset"),
        ((first,), 0, False, "exceeds the unpaginated total"),
        ((), 1, False, "empty page"),
        ((first,), 2, True, "full current page"),
    )
    for items, total_count, has_more, message in invalid_pages:
        with pytest.raises(PaginationInvariantError, match=message):
            build_page_info(
                plan,
                release_manifest_sha256=MANIFEST,
                items=items,
                total_count=total_count,
                has_more=has_more,
                secret=TEST_SECRET,
            )

    with pytest.raises(PaginationInvariantError, match="item type"):
        build_page_info(
            plan,
            release_manifest_sha256=MANIFEST,
            items=(_assembly(1),),
            total_count=1,
            has_more=False,
            secret=TEST_SECRET,
        )
